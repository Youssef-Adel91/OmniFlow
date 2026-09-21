"""
shared/qdrant_client/client.py — Async Qdrant Vector DB Client

Provides a singleton async wrapper around qdrant-client for:
  - Tenant-scoped property listing search (Vector RLS via payload filter)
  - Shared knowledge base search (common real-estate data)
  - Semantic cache lookup (L0 near-duplicate detection)
  - Collection management utilities (init, upsert, delete)

CRITICAL SECURITY INVARIANT — Vector RLS:
    Every single search() call MUST include a payload filter on tenant_id.
    Failure to do so would leak property data across tenants.
    The filter is built by _tenant_filter() and cannot be bypassed by callers.

Collection naming strategy:
    Per-tenant listings: "t_{tenant_id_no_dashes}_listings"
        → Each tenant's property vectors in isolation
        → Allows per-tenant HNSW tuning and easy data deletion (GDPR)
    Shared knowledge:    settings.qdrant_shared_knowledge_collection
        → Common FAQ, REGA regulations, neighborhood data
    Semantic cache:      settings.qdrant_semantic_cache_collection
        → Per-tenant cached Q&A pairs (payload filter still required)

Vector dimensions:
    text-embedding-3-small → 1536 dimensions (OpenAI default)
    All collections MUST be created with vector_size=1536.

References: SRS §3.3 — Vector Store, Sprint 7 spec
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Embedding dimension for text-embedding-3-small
EMBEDDING_DIM: int = 1536

# Property listing payload fields (must match the schema used during indexing)
_PAYLOAD_FIELDS = (
    "tenant_id",        # UUID str — mandatory for Vector RLS
    "listing_id",       # Internal UUID
    "rega_ad_number",   # REGA advertisement number (e.g., "1234567890")
    "title",            # Listing title / description headline
    "property_type",    # apartment | villa | land | commercial | ...
    "price_sar",        # Sale/rent price in SAR
    "area_sqm",         # Total area in sqm
    "bedrooms",         # Number of bedrooms (null for land/commercial)
    "bathrooms",        # Number of bathrooms
    "city",             # City name (Arabic + transliteration)
    "district",         # Neighborhood/district name
    "latitude",         # Geo coordinates (optional)
    "longitude",
    "status",           # VERIFIED_ACTIVE | SUSPENDED | ...
    "listing_url",      # Deep link to the listing detail page
    "summary",          # Short human-readable summary (injected into LLM prompt)
)


def _tenant_collection(tenant_id: str | uuid.UUID) -> str:
    """
    Build the per-tenant Qdrant collection name.

    Format: "t_{uuid_no_dashes}_listings"
    Dashes removed because Qdrant collection names must be valid identifiers.
    """
    clean_id = str(tenant_id).replace("-", "")
    return f"t_{clean_id}_listings"


def tenant_documents_collection(tenant_id: str | uuid.UUID) -> str:
    """
    Build the per-tenant Knowledge Base collection name (SRS §4.2).

    Format: "tenant_{uuid_no_dashes}_documents"

    Separate from the listings collection on purpose: listings are machine
    generated and re-indexed constantly, while knowledge documents are
    user-uploaded and long-lived. Keeping them apart means a full listings
    re-index can never wipe the tenant's uploaded knowledge.
    """
    clean_id = str(tenant_id).replace("-", "")
    return f"tenant_{clean_id}_documents"


# ══════════════════════════════════════════════════════════════════════════════
# QdrantManager — singleton
# ══════════════════════════════════════════════════════════════════════════════

class QdrantManager:
    """
    Async Qdrant client manager.

    Usage:
        # In worker on_startup():
        await qdrant_mgr.start()

        # In RAG retriever:
        results = await qdrant_mgr.search_properties(tenant_id, vector, limit=5)

        # In worker on_shutdown():
        await qdrant_mgr.stop()
    """

    def __init__(self) -> None:
        self._client: AsyncQdrantClient | None = None
        self._started: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize the async Qdrant client. Called once during worker startup."""
        if self._started:
            return

        self._client = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            grpc_port=settings.qdrant_grpc_port,
            prefer_grpc=settings.qdrant_use_grpc,
            api_key=settings.qdrant_api_key or None,
            timeout=settings.qdrant_timeout,
            https=False if settings.is_development else True,
        )
        self._started = True
        logger.info(
            "qdrant_client_started",
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            grpc=settings.qdrant_use_grpc,
        )

    async def stop(self) -> None:
        """Close the Qdrant client connection."""
        if self._client:
            await self._client.close()
            self._started = False
            logger.info("qdrant_client_stopped")

    @property
    def _c(self) -> AsyncQdrantClient:
        if not self._started or not self._client:
            raise RuntimeError(
                "QdrantManager not started. Call qdrant_mgr.start() first."
            )
        return self._client

    # ══════════════════════════════════════════════════════════════════════════
    # Vector RLS Helper — NEVER call search without this filter
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _tenant_filter(tenant_id: str | uuid.UUID) -> qmodels.Filter:
        """
        Build the mandatory tenant isolation payload filter.

        SECURITY: This filter ensures Qdrant only returns vectors
        belonging to the specified tenant. This is our Vector RLS.

        Implementation: Qdrant payload 'must' condition (logical AND).
        The 'tenant_id' field must be stored as a keyword in the payload
        when vectors are indexed (see upsert_property_listing()).
        """
        return qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="tenant_id",
                    match=qmodels.MatchValue(value=str(tenant_id)),
                )
            ]
        )

    @staticmethod
    def _tenant_and_status_filter(
        tenant_id: str | uuid.UUID,
        allowed_statuses: list[str] | None = None,
    ) -> qmodels.Filter:
        """
        Combined filter: tenant isolation + listing status whitelist.

        Default allowed statuses: only VERIFIED_ACTIVE listings are returned.
        This prevents surfacing suspended or unverified listings to customers.
        """
        must_conditions: list[qmodels.Condition] = [
            qmodels.FieldCondition(
                key="tenant_id",
                match=qmodels.MatchValue(value=str(tenant_id)),
            )
        ]

        statuses = allowed_statuses or ["VERIFIED_ACTIVE"]
        must_conditions.append(
            qmodels.FieldCondition(
                key="status",
                match=qmodels.MatchAny(any=statuses),
            )
        )

        return qmodels.Filter(must=must_conditions)

    # ══════════════════════════════════════════════════════════════════════════
    # Property Listing Search
    # ══════════════════════════════════════════════════════════════════════════

    async def search_properties(
        self,
        tenant_id: str | uuid.UUID,
        query_vector: list[float],
        *,
        limit: int = 5,
        score_threshold: float = 0.60,
        with_payload: bool = True,
    ) -> list[qmodels.ScoredPoint]:
        """
        Search property listings for a specific tenant using vector similarity.

        SECURITY: The query is always filtered by tenant_id (Vector RLS).
        Only VERIFIED_ACTIVE listings are returned.

        Args:
            tenant_id     — Tenant UUID (str or UUID object)
            query_vector  — 1536-dim embedding of the user's query
            limit         — Max number of results to return (default 5)
            score_threshold — Min cosine similarity score (0.0–1.0)
            with_payload  — Include payload fields in results

        Returns:
            List of ScoredPoint with .payload and .score attributes.
            Empty list if no relevant listings found or collection doesn't exist.

        Raises:
            RuntimeError if client not started.
        """
        collection = _tenant_collection(tenant_id)

        try:
            response = await self._c.query_points(
                collection_name=collection,
                query=query_vector,
                query_filter=self._tenant_and_status_filter(tenant_id),
                limit=limit,
                score_threshold=score_threshold,
                with_payload=with_payload,
                with_vectors=False,  # Don't return vectors — save bandwidth
            )
            results = response.points
            logger.debug(
                "qdrant_property_search",
                tenant_id=str(tenant_id),
                collection=collection,
                results_count=len(results),
                top_score=results[0].score if results else 0,
            )
            return results

        except UnexpectedResponse as exc:
            # Collection doesn't exist for this tenant (first-time setup)
            if exc.status_code == 404:
                logger.warning(
                    "qdrant_collection_not_found",
                    collection=collection,
                    tenant_id=str(tenant_id),
                )
                return []
            logger.error(
                "qdrant_search_error",
                collection=collection,
                error=str(exc),
            )
            raise
        except ResponseHandlingException as exc:
            logger.error("qdrant_connection_error", error=str(exc))
            raise
        except Exception as exc:
            logger.error("qdrant_unexpected_error", error=str(exc), exc_type=type(exc).__name__)
            raise

    async def search_shared_knowledge(
        self,
        query_vector: list[float],
        *,
        limit: int = 3,
        score_threshold: float = 0.70,
        topic_filter: str | None = None,
    ) -> list[qmodels.ScoredPoint]:
        """
        Search the shared knowledge base (REGA regulations, FAQ, neighborhoods).

        No tenant filter needed — this collection is public knowledge.
        Optional topic_filter (e.g., "rega_regulations") narrows results.
        """
        must_conditions: list[qmodels.Condition] = []
        if topic_filter:
            must_conditions.append(
                qmodels.FieldCondition(
                    key="topic",
                    match=qmodels.MatchValue(value=topic_filter),
                )
            )

        search_filter = qmodels.Filter(must=must_conditions) if must_conditions else None

        try:
            response = await self._c.query_points(
                collection_name=settings.qdrant_shared_knowledge_collection,
                query=query_vector,
                query_filter=search_filter,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
            return response.points
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                logger.warning(
                    "qdrant_shared_collection_not_found",
                    collection=settings.qdrant_shared_knowledge_collection,
                )
                return []
            raise

    async def search_semantic_cache(
        self,
        tenant_id: str | uuid.UUID,
        query_vector: list[float],
        *,
        threshold: float | None = None,
    ) -> qmodels.ScoredPoint | None:
        """
        Search the semantic cache for a near-duplicate query.

        Used by L0 routing to avoid redundant LLM calls for repeated questions.
        Returns the top result if score > threshold, else None.

        Threshold defaults to settings.semantic_cache_hit_threshold (0.95).
        """
        hit_threshold = threshold or settings.semantic_cache_hit_threshold

        try:
            response = await self._c.query_points(
                collection_name=settings.qdrant_semantic_cache_collection,
                query=query_vector,
                query_filter=self._tenant_filter(tenant_id),
                limit=1,
                score_threshold=hit_threshold,
                with_payload=True,
                with_vectors=False,
            )
            return response.points[0] if response.points else None
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return None
            raise

    # ══════════════════════════════════════════════════════════════════════════
    # Upsert — used by the Indexing Worker (Sprint 8)
    # ══════════════════════════════════════════════════════════════════════════

    async def upsert_property_listing(
        self,
        tenant_id: str | uuid.UUID,
        listing_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        """
        Upsert a single property listing vector.

        SECURITY: Caller MUST include tenant_id in payload. This method
        enforces it by injecting tenant_id regardless of what caller passes.

        Called by the Property Indexing Worker when a listing is created
        or updated (triggered via Kafka listing.events.v1 topic).
        """
        collection = _tenant_collection(tenant_id)
        # Force-inject tenant_id into payload — non-negotiable
        payload["tenant_id"] = str(tenant_id)

        await self._ensure_collection_exists(collection)

        await self._c.upsert(
            collection_name=collection,
            points=[
                qmodels.PointStruct(
                    id=listing_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )
        logger.debug(
            "qdrant_listing_upserted",
            tenant_id=str(tenant_id),
            listing_id=listing_id,
            collection=collection,
        )

    async def delete_property_listing(
        self, tenant_id: str | uuid.UUID, listing_id: str
    ) -> None:
        """Delete a specific listing vector (called on listing withdrawal/sale)."""
        collection = _tenant_collection(tenant_id)
        await self._c.delete(
            collection_name=collection,
            points_selector=qmodels.PointIdsList(points=[listing_id]),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Knowledge Base documents (SRS §4.2) — written by the Celery ingestion task
    # ══════════════════════════════════════════════════════════════════════════

    async def upsert_document_chunks(
        self,
        tenant_id: str | uuid.UUID,
        points: list[qmodels.PointStruct],
    ) -> None:
        """
        Bulk-upsert embedded chunks of an uploaded knowledge document.

        Each point's payload MUST already contain `tenant_id`, `document_id`,
        `chunk_index`, `source_title` and `text`. `tenant_id` is force-injected
        here anyway — the Vector RLS filter is worthless if a caller forgets it.
        """
        if not points:
            return

        collection = tenant_documents_collection(tenant_id)
        await self._ensure_collection_exists(collection)

        for point in points:
            payload = point.payload or {}
            payload["tenant_id"] = str(tenant_id)
            point.payload = payload

        await self._c.upsert(collection_name=collection, points=points)
        logger.info(
            "qdrant_document_chunks_upserted",
            tenant_id=str(tenant_id),
            collection=collection,
            chunks=len(points),
        )

    async def search_documents(
        self,
        tenant_id: str | uuid.UUID,
        query_vector: list[float],
        *,
        limit: int = 3,
        score_threshold: float = 0.55,
    ) -> list[qmodels.ScoredPoint]:
        """
        Semantic search over the tenant's uploaded knowledge documents.

        SECURITY: tenant filter is mandatory (Vector RLS) even though the
        collection is already per-tenant — defence in depth against a bad
        collection name.

        Returns [] when the collection does not exist yet (tenant has uploaded
        nothing), so callers never need to special-case first-run tenants.
        """
        collection = tenant_documents_collection(tenant_id)
        try:
            response = await self._c.query_points(
                collection_name=collection,
                query=query_vector,
                query_filter=self._tenant_filter(tenant_id),
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
            return response.points
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return []
            logger.error(
                "qdrant_document_search_error",
                collection=collection,
                error=str(exc),
            )
            return []
        except Exception as exc:
            logger.error("qdrant_document_search_failed", error=str(exc))
            return []

    async def delete_document_points(
        self,
        tenant_id: str | uuid.UUID,
        document_id: str | uuid.UUID,
    ) -> None:
        """
        Delete every chunk belonging to one uploaded document.

        Uses a payload filter (not point ids) so it works regardless of how
        many chunks the document produced. Missing collection is a no-op.
        """
        collection = tenant_documents_collection(tenant_id)
        selector = qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="tenant_id",
                        match=qmodels.MatchValue(value=str(tenant_id)),
                    ),
                    qmodels.FieldCondition(
                        key="document_id",
                        match=qmodels.MatchValue(value=str(document_id)),
                    ),
                ]
            )
        )
        try:
            await self._c.delete(
                collection_name=collection, points_selector=selector
            )
            logger.info(
                "qdrant_document_points_deleted",
                tenant_id=str(tenant_id),
                document_id=str(document_id),
                collection=collection,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return
            raise

    async def ensure_documents_collection(self, tenant_id: str | uuid.UUID) -> None:
        """Create the tenant's documents collection if it does not exist yet."""
        await self._ensure_collection_exists(tenant_documents_collection(tenant_id))

    async def delete_tenant_collection(self, tenant_id: str | uuid.UUID) -> None:
        """
        Delete ALL vectors for a tenant (GDPR right-to-erasure).

        WARNING: Irreversible. Call only on tenant account deletion.
        """
        collection = _tenant_collection(tenant_id)
        try:
            await self._c.delete_collection(collection_name=collection)
            logger.info(
                "qdrant_tenant_collection_deleted",
                tenant_id=str(tenant_id),
                collection=collection,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return  # Already doesn't exist
            raise

    # ══════════════════════════════════════════════════════════════════════════
    # Collection Management
    # ══════════════════════════════════════════════════════════════════════════

    async def _ensure_collection_exists(self, collection_name: str) -> None:
        """
        Create collection if it doesn't exist.

        Uses cosine distance (appropriate for normalized OpenAI embeddings).
        HNSW index with m=16, ef_construct=200 — balanced accuracy vs. memory.
        """
        try:
            await self._c.get_collection(collection_name=collection_name)
        except UnexpectedResponse as exc:
            if exc.status_code != 404:
                raise
            # Create it
            await self._c.create_collection(
                collection_name=collection_name,
                vectors_config=qmodels.VectorParams(
                    size=EMBEDDING_DIM,
                    distance=qmodels.Distance.COSINE,
                    hnsw_config=qmodels.HnswConfigDiff(
                        m=16,
                        ef_construct=200,
                        full_scan_threshold=10_000,
                    ),
                ),
                # Payload index for tenant_id (critical for filter performance)
                # Additional indexes created on first upsert if needed
                optimizers_config=qmodels.OptimizersConfigDiff(
                    indexing_threshold=20_000,
                ),
            )
            # Create keyword index on tenant_id for fast filter execution
            await self._c.create_payload_index(
                collection_name=collection_name,
                field_name="tenant_id",
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )
            await self._c.create_payload_index(
                collection_name=collection_name,
                field_name="status",
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )
            logger.info(
                "qdrant_collection_created",
                collection=collection_name,
                vector_dim=EMBEDDING_DIM,
            )

    async def ping(self) -> bool:
        """Health check — returns True if Qdrant is reachable."""
        try:
            await self._c.get_collections()
            return True
        except Exception:
            return False


# ── Module-level singleton ─────────────────────────────────────────────────────
qdrant_mgr = QdrantManager()
