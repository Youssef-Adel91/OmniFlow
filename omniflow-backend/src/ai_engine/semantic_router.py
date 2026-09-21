"""
src/ai_engine/semantic_router.py — SemanticRouter Facade

High-level routing decision engine that orchestrates the full routing cascade:

    L0  →  Deterministic regex rules (free, instant, 0ms)
    L1  →  Fast triage heuristic (Gemini Flash-Lite, ~300ms)
    L2  →  RAG-augmented reasoning (Gemini Flash + Qdrant, ~800ms)
    L3  →  Deep master reasoning (Gemini Pro, ~2-5s)
    HUMAN → Human agent escalation (bypass AI entirely)

This class is a clean FACADE over the lower-level SemanticClassifier found in
`src/ai_workers/semantic_router/classifier.py`. It adds:

    1. Intent Classification        — What does the user want?
    2. Complexity Estimation        — How deep does the reasoning need to go?
    3. Risk Scoring                 — Should we escalate to a human?
    4. Semantic Cache Probing       — Has this been answered before? (L0 hit)

Usage:
    router = SemanticRouter()
    decision = await router.decide_route(
        request=RoutingRequest(...)
    )

References: SRS §2.3 — AI Routing Pipeline (Steps 1-4)
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from src.ai_engine.schemas import RoutingDecision, RoutingRequest
from src.shared.core.enums import IntentCategory, RoutingTier

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

# Minimum confidence required to avoid HUMAN escalation
_CONFIDENCE_ESCALATION_THRESHOLD: float = 0.70

# Short message heuristic: ≤ this many chars → likely L1 (simple query)
_SHORT_MSG_CHAR_THRESHOLD: int = 80

# Complexity keywords that push a message from L1 → L2/L3
_HIGH_COMPLEXITY_SIGNALS: frozenset[str] = frozenset({
    "صك", "سند", "تفاوض", "استثمار", "عائد", "مقارنة",
    "negotiate", "deed", "legal", "roi", "investment", "compare",
})


# ══════════════════════════════════════════════════════════════════════════════
# SemanticRouter
# ══════════════════════════════════════════════════════════════════════════════

class SemanticRouter:
    """
    Primary routing engine for the OmniFlow AI platform.

    Instantiate once at application startup and reuse across requests.
    All methods are async-safe and stateless at the instance level —
    any state (semantic cache, Redis) is accessed via injected clients.

    Architecture:
        decide_route()
            ├── _probe_semantic_cache()    # L0: instant cache hit?
            ├── _classify_intent()         # What does the user want?
            ├── _estimate_complexity()     # How complex is this request?
            ├── _score_risk()              # Escalation risk assessment
            └── _build_decision()          # Assemble RoutingDecision
    """

    def __init__(
        self,
        confidence_threshold: float = _CONFIDENCE_ESCALATION_THRESHOLD,
    ) -> None:
        self._confidence_threshold = confidence_threshold
        logger.info(
            "semantic_router_initialized",
            confidence_threshold=confidence_threshold,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════════════════

    async def decide_route(
        self,
        request: RoutingRequest,
    ) -> RoutingDecision:
        """
        Main entry point. Runs the full routing cascade and returns a decision.

        Pipeline:
            1. Probe semantic cache (L0 shortcut)
            2. Classify intent
            3. Estimate complexity
            4. Score escalation risk
            5. Assemble and return RoutingDecision

        Args:
            request: The fully-populated RoutingRequest from the channel adapter.

        Returns:
            RoutingDecision with tier, intent, confidence, and audit metadata.
        """
        t_start = time.perf_counter()

        logger.info(
            "semantic_router_decide_route_start",
            request_id=str(request.request_id),
            tenant_id=str(request.tenant_id),
            platform_user_id=request.platform_user_id,
            channel=request.channel,
            message_type=request.message_type,
            message_preview=request.message_text[:80] if request.message_text else "<empty>",
        )

        # ── Step 1: Probe semantic cache ──────────────────────────────────────
        cache_result = await self._probe_semantic_cache(
            request.message_text,
            context={"tenant_id": str(request.tenant_id)},
        )
        if cache_result is not None:
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            logger.info(
                "semantic_router_l0_cache_hit",
                request_id=str(request.request_id),
                elapsed_ms=round(elapsed_ms, 2),
            )
            return RoutingDecision(
                request_id=request.request_id,
                tier=RoutingTier.L0_SEMANTIC_CACHE,
                intent=cache_result["intent"],
                confidence=cache_result["confidence"],
                reasoning="L0 semantic cache hit — no LLM invocation required.",
                requires_rag=False,
                escalate_to_human=False,
                matched_patterns=cache_result.get("matched_patterns", []),
            )

        # ── Step 2: Classify intent ───────────────────────────────────────────
        intent, intent_confidence, matched_patterns = await self._classify_intent(
            message_text=request.message_text,
            context=request.metadata,
        )

        # ── Step 3: Estimate complexity ───────────────────────────────────────
        tier, complexity_reasoning = await self._estimate_complexity(
            message_text=request.message_text,
            intent=intent,
            conversation_history=request.conversation_history,
        )

        # ── Step 4: Score escalation risk ─────────────────────────────────────
        risk_result = await self._score_risk(
            message_text=request.message_text,
            intent=intent,
            confidence=intent_confidence,
        )

        # Override tier to HUMAN if risk scoring demands it
        if risk_result["escalate_to_human"]:
            tier = RoutingTier.HUMAN_ESCALATION

        requires_rag = tier in (RoutingTier.L2_RAG, RoutingTier.L3_MASTER)

        # ── Step 5: Assemble decision ─────────────────────────────────────────
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        decision = RoutingDecision(
            request_id=request.request_id,
            tier=tier,
            intent=intent,
            confidence=intent_confidence,
            reasoning=(
                f"[ComplexityEstimator] {complexity_reasoning} | "
                f"[RiskScorer] escalate={risk_result['escalate_to_human']} "
                f"risk_score={risk_result['risk_score']:.2f}"
            ),
            requires_rag=requires_rag,
            escalate_to_human=risk_result["escalate_to_human"],
            matched_patterns=matched_patterns,
        )

        logger.info(
            "semantic_router_decide_route_complete",
            request_id=str(request.request_id),
            tier=tier,
            intent=intent,
            confidence=round(intent_confidence, 3),
            requires_rag=requires_rag,
            escalate_to_human=risk_result["escalate_to_human"],
            elapsed_ms=round(elapsed_ms, 2),
        )

        return decision

    # ══════════════════════════════════════════════════════════════════════════
    # Private Pipeline Steps
    # ══════════════════════════════════════════════════════════════════════════

    async def _probe_semantic_cache(
        self,
        message_text: str,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        [STUB — Phase 2] Semantic Cache Probe (L0 shortcut).

        In production this will:
            1. Embed message_text via the same embedder used for RAG.
            2. Perform a cosine-similarity search against a Redis vector cache
               keyed per tenant (tenant_id from context).
            3. If similarity > 0.95 → return the cached RoutingDecision fields.
            4. If no hit → return None (proceed to intent classification).

        Current behaviour:
            Always returns None (cache miss) to force full classification.

        Args:
            message_text: The inbound message.
            context:      Context dict (must contain 'tenant_id').

        Returns:
            Dict with {intent, confidence, matched_patterns} on cache hit,
            or None on cache miss.
        """
        logger.debug(
            "semantic_router_cache_probe",
            message_preview=message_text[:60],
            tenant_id=context.get("tenant_id"),
            status="STUB — always cache miss in Phase 1",
        )
        # ── TODO (Phase 2): Implement Redis vector cache lookup ───────────────
        # embedding = await embedder.embed(message_text)
        # result = await redis_client.vector_search(
        #     index=f"semantic_cache:{context['tenant_id']}",
        #     vector=embedding,
        #     top_k=1,
        # )
        # if result and result[0].score > 0.95:
        #     return result[0].payload
        return None

    async def _classify_intent(
        self,
        message_text: str,
        context: dict[str, Any],
    ) -> tuple[IntentCategory, float, list[str]]:
        """
        [STUB — Phase 2] Intent Classification.

        In production this will:
            1. Run deterministic regex rules from SemanticClassifier (L0).
            2. If no deterministic match → call Gemini Flash-Lite for
               classification (L1 fast path, ~300ms, low cost).
            3. Return (intent, confidence, matched_patterns).

        Current behaviour:
            Returns GENERAL_QUERY with confidence 0.75 for all inputs,
            with debug logging showing what rules would match.

        Args:
            message_text: The raw inbound message text.
            context:      Arbitrary metadata (feature flags, tenant config).

        Returns:
            Tuple of (IntentCategory, confidence_float, matched_pattern_strings).
        """
        logger.debug(
            "semantic_router_intent_classification",
            message_preview=message_text[:80],
            status="STUB — returning GENERAL_QUERY with 0.75 confidence in Phase 1",
        )

        # ── TODO (Phase 2): Run SemanticClassifier ────────────────────────────
        # from src.ai_workers.semantic_router.classifier import SemanticClassifier
        # classifier = SemanticClassifier()
        # result = await classifier.classify(event, conversation_history)
        # return result.intent, result.confidence, result.matched_patterns

        # Phase 1 stub: basic heuristics only
        text_lower = message_text.lower()

        if any(kw in text_lower for kw in ("مرحب", "أهلا", "هلا", "hi", "hello", "hey")):
            logger.debug("semantic_router_intent_heuristic_match", pattern="greeting")
            return IntentCategory.GREETING, 0.95, ["greeting_heuristic"]

        if any(kw in text_lower for kw in ("تقرير", "ملف", "وثيق", "report", "document")):
            logger.debug("semantic_router_intent_heuristic_match", pattern="vault_retrieval")
            return IntentCategory.VAULT_RETRIEVAL, 0.90, ["vault_heuristic"]

        if any(kw in text_lower for kw in ("شقة", "فيلا", "أرض", "عقار", "apartment", "villa", "land")):
            logger.debug("semantic_router_intent_heuristic_match", pattern="listing_search")
            return IntentCategory.LISTING_SEARCH, 0.85, ["listing_heuristic"]

        return IntentCategory.GENERAL_QUERY, 0.75, []

    async def _estimate_complexity(
        self,
        message_text: str,
        intent: IntentCategory,
        conversation_history: list[dict[str, str]],
    ) -> tuple[RoutingTier, str]:
        """
        [STUB — Phase 2] Complexity Estimator.

        Maps intent + message signals to the appropriate LLM tier:

            Intent=GREETING           → L0 (no LLM)
            Intent=VAULT_RETRIEVAL    → VAULT (no LLM)
            Short message (< 80 chars) + simple intent → L1
            Listing/property intent   → L2 (RAG)
            Negotiation/legal signals → L3
            Unknown / low confidence  → L1 (safe default)

        In production this will also consider:
            - Conversation depth (number of prior turns)
            - Presence of attachments / documents
            - Tenant-configured tier overrides

        Args:
            message_text:         Raw inbound text.
            intent:               Classified IntentCategory.
            conversation_history: Recent conversation turns.

        Returns:
            Tuple of (RoutingTier, reasoning_string).
        """
        logger.debug(
            "semantic_router_complexity_estimation",
            intent=intent,
            message_length=len(message_text),
            history_turns=len(conversation_history),
            status="STUB — heuristic tier assignment in Phase 1",
        )

        # ── L0 shortcuts ──────────────────────────────────────────────────────
        if intent == IntentCategory.GREETING:
            return RoutingTier.L0_SEMANTIC_CACHE, "Greeting → L0 (deterministic, no LLM)"

        if intent == IntentCategory.VAULT_RETRIEVAL:
            return RoutingTier.VAULT_RETRIEVAL, "Report/document request → VAULT (no LLM)"

        # ── L3 high-complexity signals ────────────────────────────────────────
        text_lower = message_text.lower()
        if any(kw in text_lower for kw in _HIGH_COMPLEXITY_SIGNALS):
            return (
                RoutingTier.L3_MASTER,
                f"High-complexity signals detected in message → L3 (Gemini Pro)",
            )

        # ── L2 for listing/property-specific queries ──────────────────────────
        if intent == IntentCategory.LISTING_SEARCH:
            return (
                RoutingTier.L2_RAG,
                "Listing/property search intent → L2 (RAG + Gemini Flash)",
            )

        # ── L1 for short/simple messages ──────────────────────────────────────
        if len(message_text) <= _SHORT_MSG_CHAR_THRESHOLD:
            return (
                RoutingTier.L1_TRIAGE,
                f"Short message ({len(message_text)} chars) → L1 (Gemini Flash-Lite)",
            )

        # ── Default: L1 (safe, cheap) ─────────────────────────────────────────
        return (
            RoutingTier.L1_TRIAGE,
            "No strong complexity signals → L1 default (Gemini Flash-Lite)",
        )

    async def _score_risk(
        self,
        message_text: str,
        intent: IntentCategory,
        confidence: float,
    ) -> dict[str, Any]:
        """
        [STUB — Phase 2] Risk Scorer & Escalation Evaluator.

        Determines whether the routing decision should be overridden and
        the conversation escalated to a human agent.

        Escalation triggers (current stubs):
            - Low classifier confidence (< threshold)
            - Explicit escalation intent (escalation_request)
            - Legal/financial risk patterns

        In production this will also check:
            - Active CRM flags (VIP customer, known complaint)
            - Tenant-configured escalation rules
            - PDPL data sensitivity flags
            - Consecutive AI failure count (Redis counter)

        Args:
            message_text: Raw inbound text.
            intent:       Classified intent.
            confidence:   Intent classification confidence.

        Returns:
            Dict with keys: {escalate_to_human: bool, risk_score: float, reason: str}
        """
        risk_score = 0.0
        escalate = False
        reason = "No escalation triggers detected."

        # ── Trigger 1: Low confidence ─────────────────────────────────────────
        if confidence < self._confidence_threshold:
            risk_score += 0.4
            escalate = True
            reason = f"Confidence {confidence:.2f} < threshold {self._confidence_threshold:.2f}"
            logger.warning(
                "semantic_router_low_confidence_escalation",
                confidence=confidence,
                threshold=self._confidence_threshold,
                intent=intent,
            )

        # ── Trigger 2: Explicit escalation request ────────────────────────────
        if intent == IntentCategory.ESCALATION_REQUEST:
            risk_score += 0.6
            escalate = True
            reason = "Customer explicitly requested human agent"
            logger.info(
                "semantic_router_explicit_escalation_request",
                intent=intent,
            )

        # ── Trigger 3: Complaint ──────────────────────────────────────────────
        if intent == IntentCategory.COMPLAINT:
            risk_score = min(risk_score + 0.3, 1.0)
            # Complaints don't auto-escalate — flagged for human review
            logger.info(
                "semantic_router_complaint_flagged",
                risk_score=risk_score,
            )

        logger.debug(
            "semantic_router_risk_score",
            risk_score=round(risk_score, 3),
            escalate_to_human=escalate,
            reason=reason,
        )

        return {
            "escalate_to_human": escalate,
            "risk_score": min(risk_score, 1.0),
            "reason": reason,
        }
