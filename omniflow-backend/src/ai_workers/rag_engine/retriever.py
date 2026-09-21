"""
ai_workers/rag_engine/retriever.py — RAG Retriever (Embed → Search → Format)

Combines EmbeddingService + QdrantManager to produce a structured
natural-language context block ready to be injected into the LLM prompt.

Pipeline:
    user_message (str)
        │
        ▼ EmbeddingService.embed_query()   ~150ms, cached after first call
        │  → list[float] 1536-dim vector
        │
        ▼ QdrantManager.search_properties()  ~5-20ms (gRPC, local Qdrant)
        │  → list[ScoredPoint] with payload  (tenant-isolated, status-filtered)
        │
        ▼ _format_context()                  ~0ms
        │  → str: structured Arabic/English context block
        │
        ▼ injected as rag_context into GeminiLLMClient.generate_response()

Context format (injected into system prompt):
    ## العقارات المتاحة (من قاعدة البيانات):
    1. [الاسم] | [النوع] | السعر: X ريال | المساحة: Y م² | [الموقع]
       التفاصيل: [الملخص]
       رقم الإعلان: [REGA_NUMBER]
    2. ...

The LLM is instructed in its system prompt to only reference listings
that appear in this context block — preventing hallucinated properties.

Fallback behavior:
  - Empty search results → returns None (no context injected, L1 fallback)
  - Qdrant unavailable → logs error, returns None (graceful degradation)
  - Embedding timeout → logs error, returns None (graceful degradation)

Multi-source retrieval:
  - Primary: tenant-specific property listings (most relevant)
  - Secondary: shared knowledge base (REGA laws, neighborhood data)
    Merged and de-duplicated before formatting.

References: SRS §3.3 — RAG Pipeline, Sprint 7 spec
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from qdrant_client.http import models as qmodels

from src.ai_workers.rag_engine.embedder import EmbeddingService, embedder
from src.shared.core.config import get_settings
from src.shared.qdrant_client.client import QdrantManager, qdrant_mgr

logger = structlog.get_logger(__name__)
settings = get_settings()

# Maximum number of property results to include in context
_MAX_PROPERTY_RESULTS: int = 5
_MAX_KNOWLEDGE_RESULTS: int = 2

# Tenant-uploaded Knowledge Base documents (SRS §4.2).
_MAX_DOCUMENT_RESULTS: int = 3

# Minimum score to include a result (prevents low-quality matches)
_MIN_PROPERTY_SCORE: float = 0.62
_MIN_KNOWLEDGE_SCORE: float = 0.72
# Lower than the shared knowledge floor on purpose: these documents were
# uploaded by this specific tenant about their own business, so a weaker
# semantic match is still far more likely to be relevant than a generic one.
_MIN_DOCUMENT_SCORE: float = 0.50

# Maximum context string length (chars) — prevents token overflow
_MAX_CONTEXT_CHARS: int = 4000


# ══════════════════════════════════════════════════════════════════════════════
# Context Formatters
# ══════════════════════════════════════════════════════════════════════════════

def _format_property_listing(index: int, point: qmodels.ScoredPoint) -> str:
    """
    Format a single property ScoredPoint into a structured text entry.

    Handles missing fields gracefully — any payload field can be None.
    Numbers are formatted in Arabic-friendly notation.

    Example output:
        1. شقة في حي النرجس، الرياض | 3 غرف | السعر: 750,000 ريال | المساحة: 180 م²
           رقم الإعلان (ريغا): 1100123456
           الوصف: شقة فاخرة بتشطيب عالي الجودة، مدخل مستقل، قريبة من الخدمات.
           [نقاط التطابق: 0.89]
    """
    p = point.payload or {}
    score = round(point.score, 3)

    # Core identity
    prop_type = _map_property_type(p.get("property_type", ""))
    city = p.get("city", "")
    district = p.get("district", "")
    location = f"{district}، {city}".strip("، ") if (district or city) else "الموقع غير محدد"

    # Specs
    price = p.get("price_sar")
    area = p.get("area_sqm")
    bedrooms = p.get("bedrooms")
    bathrooms = p.get("bathrooms")

    # Build headline
    specs_parts = []
    if bedrooms:
        specs_parts.append(f"{bedrooms} غرف")
    if bathrooms:
        specs_parts.append(f"{bathrooms} حمام")
    specs_str = " | ".join(specs_parts) if specs_parts else ""

    headline = f"{prop_type} في {location}"
    if specs_str:
        headline += f" | {specs_str}"
    if price:
        headline += f" | السعر: {int(price):,} ريال"
    if area:
        headline += f" | المساحة: {int(area)} م²"

    # Body
    rega_number = p.get("rega_ad_number", "")
    summary = p.get("summary") or p.get("title", "")
    listing_url = p.get("listing_url", "")

    lines = [f"{index}. {headline}"]
    if rega_number:
        lines.append(f"   رقم الإعلان (ريغا): {rega_number}")
    if summary:
        # Truncate to prevent token overflow
        truncated = summary[:300] + "..." if len(summary) > 300 else summary
        lines.append(f"   الوصف: {truncated}")
    if listing_url:
        lines.append(f"   الرابط: {listing_url}")
    lines.append(f"   [نقاط التطابق: {score}]")

    return "\n".join(lines)


def _format_knowledge_entry(index: int, point: qmodels.ScoredPoint) -> str:
    """Format a shared knowledge base result (REGA law, neighborhood info, FAQ)."""
    p = point.payload or {}
    title = p.get("title", "معلومة")
    content = p.get("content", p.get("text", ""))
    truncated = content[:400] + "..." if len(content) > 400 else content
    return f"[معلومة {index}] {title}: {truncated}"


def _map_property_type(raw: str) -> str:
    """Map English property_type enum to Arabic label."""
    _MAP = {
        "apartment":     "شقة",
        "villa":         "فيلا",
        "land":          "أرض",
        "commercial":    "وحدة تجارية",
        "daily_rental":  "شقة مفروشة (إيجار يومي)",
        "office":        "مكتب",
        "warehouse":     "مستودع",
    }
    return _MAP.get(raw.lower(), raw or "عقار")


def _format_document_chunk(index: int, point: qmodels.ScoredPoint) -> str:
    """
    Format one chunk retrieved from a tenant-uploaded knowledge document.

    The source title is always shown so the model can attribute the answer
    ("حسب كتالوج المشاريع لدينا…") instead of stating it as an anonymous fact.
    """
    p = point.payload or {}
    title = p.get("source_title") or "مستند الشركة"
    content = (p.get("text") or "").strip()
    truncated = content[:600] + "..." if len(content) > 600 else content
    return f"[{index}] من «{title}»:\n{truncated}"


def _build_context_string(
    property_results: list[qmodels.ScoredPoint],
    knowledge_results: list[qmodels.ScoredPoint],
    document_results: list[qmodels.ScoredPoint] | None = None,
) -> str | None:
    """
    Combine property listings and knowledge base results into a single
    context string for LLM injection.

    Returns None if both result lists are empty (no context to inject).
    """
    sections: list[str] = []

    if property_results:
        property_lines = ["## العقارات المتاحة المطابقة لطلبك:\n"]
        for i, point in enumerate(property_results, start=1):
            property_lines.append(_format_property_listing(i, point))
        sections.append("\n\n".join(property_lines))

    if document_results:
        document_lines = ["\n## من مستندات الشركة المعتمدة:\n"]
        for i, point in enumerate(document_results, start=1):
            document_lines.append(_format_document_chunk(i, point))
        sections.append("\n\n".join(document_lines))

    if knowledge_results:
        knowledge_lines = ["\n## معلومات ذات صلة:\n"]
        for i, point in enumerate(knowledge_results, start=1):
            knowledge_lines.append(_format_knowledge_entry(i, point))
        sections.append("\n".join(knowledge_lines))

    if not sections:
        return None

    context = "\n\n".join(sections)

    # Hard truncate to prevent token overflow
    if len(context) > _MAX_CONTEXT_CHARS:
        context = context[:_MAX_CONTEXT_CHARS] + "\n\n[تم اختصار النتائج لضمان الأداء]"

    return context


# ══════════════════════════════════════════════════════════════════════════════
# RAGRetriever — main entry point
# ══════════════════════════════════════════════════════════════════════════════

class RAGRetriever:
    """
    Orchestrates the full retrieval pipeline:
        Query → Embed → Search Qdrant → Format → Return context string

    Usage:
        retriever = RAGRetriever()  # or use module-level singleton
        context = await retriever.get_rag_context(
            tenant_id="...",
            user_message="أريد شقة 3 غرف في حي النرجس"
        )
        # Returns formatted string or None if no relevant results found

    Initialization:
        The retriever uses the module-level `embedder` and `qdrant_mgr`
        singletons. Both must be started (configure()/start()) before use.
        Call retriever.configure() in worker on_startup() to validate.
    """

    def __init__(
        self,
        _embedder: EmbeddingService | None = None,
        _qdrant: QdrantManager | None = None,
    ) -> None:
        # Allow dependency injection for testing
        self._embedder = _embedder or embedder
        self._qdrant = _qdrant or qdrant_mgr

    def configure(self) -> None:
        """Validate that the embedder is configured (client set up)."""
        if not self._embedder._client:
            self._embedder.configure()

    async def get_rag_context(
        self,
        tenant_id: str | uuid.UUID,
        user_message: str,
        *,
        include_shared_knowledge: bool = True,
        intent_hint: str | None = None,
    ) -> str | None:
        """
        Full RAG pipeline: embed → search → format.

        Args:
            tenant_id             — Tenant UUID for Vector RLS isolation
            user_message          — Raw user message text to embed
            include_shared_knowledge — Also search shared knowledge base
            intent_hint           — Optional topic filter for knowledge search
                                    (e.g., "rega_regulations", "neighborhoods")

        Returns:
            Formatted context string (Arabic) ready for LLM injection.
            Returns None if no results found or on error (graceful degradation).

        Performance targets:
            Embedding:  ~150ms (cache hit: ~0ms)
            Qdrant:     ~10ms  (gRPC, local deployment)
            Formatting: ~1ms
            Total:      ~161ms (well within 200ms SLA for L2 RAG path)
        """
        if not user_message or not user_message.strip():
            return None

        # ── 1. Embed the query ────────────────────────────────────────────────
        try:
            query_vector = await self._embedder.embed_query(user_message)
        except Exception as exc:
            logger.error(
                "rag_embedding_failed",
                tenant_id=str(tenant_id),
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            return None  # Graceful degradation — LLM proceeds without RAG context

        # ── 2. Parallel search: properties + tenant documents + shared KB ─────
        #
        # NOTE: the previous version built the "skip shared knowledge" branch
        # with `asyncio.coroutine(...)`, which was removed in Python 3.11 and
        # would raise AttributeError whenever include_shared_knowledge=False.
        # Replaced with a plain no-op coroutine.
        import asyncio

        async def _empty() -> list[qmodels.ScoredPoint]:
            return []

        property_results, document_results, knowledge_results = await asyncio.gather(
            self._search_properties_safe(tenant_id, query_vector),
            self._search_documents_safe(tenant_id, query_vector),
            (
                self._search_knowledge_safe(query_vector, intent_hint)
                if include_shared_knowledge
                else _empty()
            ),
        )

        total_results = (
            len(property_results) + len(document_results) + len(knowledge_results)
        )
        logger.info(
            "rag_retrieval_complete",
            tenant_id=str(tenant_id),
            property_count=len(property_results),
            document_count=len(document_results),
            knowledge_count=len(knowledge_results),
            total=total_results,
        )

        if total_results == 0:
            logger.debug(
                "rag_no_results",
                tenant_id=str(tenant_id),
                message_preview=user_message[:60],
            )
            return None

        # ── 3. Format into context string ─────────────────────────────────────
        return _build_context_string(
            property_results, knowledge_results, document_results
        )

    async def _search_documents_safe(
        self,
        tenant_id: str | uuid.UUID,
        query_vector: list[float],
    ) -> list[qmodels.ScoredPoint]:
        """
        Knowledge Base document search with error isolation — never raises.

        Returns [] for tenants who have not uploaded anything (the Qdrant
        collection simply does not exist yet).
        """
        try:
            return await self._qdrant.search_documents(
                tenant_id=tenant_id,
                query_vector=query_vector,
                limit=_MAX_DOCUMENT_RESULTS,
                score_threshold=_MIN_DOCUMENT_SCORE,
            )
        except Exception as exc:
            logger.warning(
                "rag_document_search_failed",
                tenant_id=str(tenant_id),
                error=str(exc),
            )
            return []

    async def _search_properties_safe(
        self,
        tenant_id: str | uuid.UUID,
        query_vector: list[float],
    ) -> list[qmodels.ScoredPoint]:
        """Property search with error isolation — never raises."""
        try:
            return await self._qdrant.search_properties(
                tenant_id=tenant_id,
                query_vector=query_vector,
                limit=_MAX_PROPERTY_RESULTS,
                score_threshold=_MIN_PROPERTY_SCORE,
            )
        except Exception as exc:
            logger.error(
                "rag_property_search_failed",
                tenant_id=str(tenant_id),
                error=str(exc),
            )
            return []

    async def _search_knowledge_safe(
        self,
        query_vector: list[float],
        topic_filter: str | None,
    ) -> list[qmodels.ScoredPoint]:
        """Knowledge search with error isolation — never raises."""
        try:
            return await self._qdrant.search_shared_knowledge(
                query_vector=query_vector,
                limit=_MAX_KNOWLEDGE_RESULTS,
                score_threshold=_MIN_KNOWLEDGE_SCORE,
                topic_filter=topic_filter,
            )
        except Exception as exc:
            logger.warning(
                "rag_knowledge_search_failed",
                error=str(exc),
            )
            return []

    async def check_semantic_cache(
        self,
        tenant_id: str | uuid.UUID,
        user_message: str,
    ) -> dict[str, Any] | None:
        """
        Check if a cached answer exists for a near-identical query.

        Used by SemanticRouterWorker for L0 cache hit detection.
        Returns the cached response payload dict or None.

        Cache payload structure:
            {"response_text": str, "intent": str, "created_at": ISO str}
        """
        try:
            query_vector = await self._embedder.embed_query(user_message)
            hit = await self._qdrant.search_semantic_cache(
                tenant_id=tenant_id,
                query_vector=query_vector,
                threshold=settings.semantic_cache_hit_threshold,
            )
            if hit:
                logger.info(
                    "semantic_cache_hit",
                    tenant_id=str(tenant_id),
                    score=hit.score,
                )
                return hit.payload
            return None
        except Exception as exc:
            logger.warning("semantic_cache_check_failed", error=str(exc))
            return None


# ── Module-level singleton ─────────────────────────────────────────────────────
rag_retriever = RAGRetriever()
