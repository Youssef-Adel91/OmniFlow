"""
src/ai_engine/ai_gateway.py — AIGateway Security & Compliance Middleware

Acts as a two-phase compliance checkpoint surrounding all LLM calls:

    Phase 1 — PRE-LLM INSPECTION (before sending to any model):
        ┌─────────────────────────────────────────────────────────────┐
        │  1. Prompt Injection Detection  (jailbreak / override)      │
        │  2. PII Detection               (phone, NID, credit cards)  │
        │  3. Tenant Context Verification (active? trial expired?)    │
        │  4. Content Policy              (profanity, off-topic)      │
        └─────────────────────────────────────────────────────────────┘

    Phase 2 — POST-LLM INSPECTION (after model response, before delivery):
        ┌─────────────────────────────────────────────────────────────┐
        │  1. Hallucination Check         (facts grounded in RAG?)    │
        │  2. Toxicity Filter             (harmful / offensive)       │
        │  3. Brand Compliance            (tone, persona, no rivals)  │
        │  4. Legal Disclaimer Enforcement (REGA, financial)          │
        │  5. PII Leakage Check           (LLM exposing customer data)│
        └─────────────────────────────────────────────────────────────┘

If pre_llm_inspection() returns is_safe=False → the LLM is NOT called.
If post_llm_inspection() returns is_approved=False → the raw LLM response
is suppressed and sanitized_response is delivered instead.

Usage:
    gateway = AIGateway()
    pre_result = await gateway.pre_llm_inspection(text=message_text, tenant_id=...)
    if not pre_result.is_safe:
        return pre_result.fallback_message
    llm_response = await orchestrator.invoke(...)
    post_result = await gateway.post_llm_inspection(
        llm_response=llm_response,
        grounding_sources=rag_chunks,
    )
    final_text = post_result.sanitized_response

References: SRS §3.5 — Security Layer, §7 — PDPL Compliance
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

import structlog

from src.ai_engine.schemas import PostLLMCheckResult, PreLLMCheckResult

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

# PII regex patterns (Saudi context: phone, national ID, IBAN)
_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "saudi_phone":    re.compile(r"\b(05\d{8}|\+9665\d{8})\b"),
    "national_id":    re.compile(r"\b[12]\d{9}\b"),
    "iban":           re.compile(r"\bSA\d{22}\b", re.IGNORECASE),
    "credit_card":    re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"),
    "email":          re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE),
}

# Prompt injection signal phrases (English + Arabic)
_INJECTION_SIGNALS: list[str] = [
    "ignore previous instructions",
    "ignore all instructions",
    "forget your instructions",
    "you are now",
    "act as",
    "pretend you are",
    "disregard",
    "تجاهل التعليمات",
    "تجاهل كل",
    "أنت الآن",
    "تصرف كـ",
    "انسَ تعليماتك",
]

# Toxicity keyword list (stub — replace with a real classifier in Phase 2)
_TOXICITY_KEYWORDS: frozenset[str] = frozenset({
    "كلب", "حمار", "stupid", "idiot", "asshole", "fuck", "shit",
})

# REGA legal disclaimer appended when real-estate advice is detected
_REGA_DISCLAIMER: str = (
    "\n\n⚠️ تنويه: المعلومات المقدمة للأغراض الإرشادية فقط ولا تُعدّ استشارةً قانونيةً أو ماليةً. "
    "يُرجى التواصل مع مستشار مرخص قبل اتخاذ أي قرار."
)

# Keywords that trigger REGA disclaimer injection
_LEGAL_TRIGGER_KEYWORDS: frozenset[str] = frozenset({
    "استثمار", "عائد", "عقد", "صك", "ريغا", "rega",
    "investment", "return", "contract", "deed",
})


# ══════════════════════════════════════════════════════════════════════════════
# AIGateway
# ══════════════════════════════════════════════════════════════════════════════

class AIGateway:
    """
    Security and compliance middleware for the OmniFlow AI pipeline.

    Instantiate once at application startup and reuse across requests.

    All inspection methods are async to support future integration with
    external compliance APIs (Google Cloud DLP, Perspective API, etc.)
    without breaking the interface.
    """

    def __init__(
        self,
        hallucination_threshold: float = 0.70,
        toxicity_threshold: float = 0.50,
        injection_score_threshold: float = 0.60,
    ) -> None:
        self._hallucination_threshold = hallucination_threshold
        self._toxicity_threshold = toxicity_threshold
        self._injection_score_threshold = injection_score_threshold
        logger.info(
            "ai_gateway_initialized",
            hallucination_threshold=hallucination_threshold,
            toxicity_threshold=toxicity_threshold,
            injection_score_threshold=injection_score_threshold,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════════════════

    async def pre_llm_inspection(
        self,
        text: str,
        tenant_id: uuid.UUID | None = None,
        tenant_context: dict[str, Any] | None = None,
    ) -> PreLLMCheckResult:
        """
        Run all pre-LLM safety and compliance checks on an inbound message.

        Call this BEFORE sending any message to an LLM. If the result has
        is_safe=False, use fallback_message instead and skip the LLM entirely.

        Args:
            text:             The raw inbound message text to inspect.
            tenant_id:        UUID of the owning tenant (for context verification).
            tenant_context:   Optional dict with tenant flags (status, tier, etc.)

        Returns:
            PreLLMCheckResult with all check outcomes and a fallback_message
            if is_safe=False.
        """
        t_start = time.perf_counter()

        logger.info(
            "ai_gateway_pre_llm_inspection_start",
            tenant_id=str(tenant_id) if tenant_id else None,
            text_length=len(text),
            text_preview=text[:80],
        )

        # ── Check 1: Prompt Injection Detection ───────────────────────────────
        injection_detected, injection_score = self._detect_prompt_injection(text)

        # ── Check 2: PII Detection ────────────────────────────────────────────
        pii_detected, pii_types = self._detect_pii(text)

        # ── Check 3: Tenant Context Verification ──────────────────────────────
        tenant_verified, tenant_rejection_reason = self._verify_tenant_context(
            tenant_id=tenant_id,
            tenant_context=tenant_context or {},
        )

        # ── Determine overall safety ──────────────────────────────────────────
        is_safe = (
            not injection_detected
            and tenant_verified
            # PII doesn't block the message — we log it and mask before forwarding
        )

        fallback_message: str | None = None
        if injection_detected:
            fallback_message = (
                "عذراً، لا يمكنني معالجة هذا الطلب. "
                "يُرجى إعادة صياغة سؤالك."
            )
            logger.warning(
                "ai_gateway_prompt_injection_blocked",
                injection_score=round(injection_score, 3),
                tenant_id=str(tenant_id) if tenant_id else None,
            )
        elif not tenant_verified:
            fallback_message = (
                "عذراً، حسابك غير مفعّل حالياً. "
                "يُرجى التواصل مع فريق الدعم."
            )
            logger.warning(
                "ai_gateway_tenant_verification_failed",
                tenant_id=str(tenant_id) if tenant_id else None,
                reason=tenant_rejection_reason,
            )

        if pii_detected:
            logger.warning(
                "ai_gateway_pii_detected_in_inbound",
                pii_types=pii_types,
                tenant_id=str(tenant_id) if tenant_id else None,
            )

        elapsed_ms = (time.perf_counter() - t_start) * 1000

        result = PreLLMCheckResult(
            is_safe=is_safe,
            prompt_injection_detected=injection_detected,
            prompt_injection_score=injection_score,
            pii_detected=pii_detected,
            pii_types_found=pii_types,
            tenant_verified=tenant_verified,
            tenant_rejection_reason=tenant_rejection_reason,
            fallback_message=fallback_message,
            check_duration_ms=round(elapsed_ms, 2),
        )

        logger.info(
            "ai_gateway_pre_llm_inspection_complete",
            is_safe=is_safe,
            injection_detected=injection_detected,
            pii_detected=pii_detected,
            tenant_verified=tenant_verified,
            elapsed_ms=round(elapsed_ms, 2),
        )

        return result

    async def post_llm_inspection(
        self,
        llm_response: str,
        grounding_sources: list[str] | None = None,
        trigger_text: str = "",
    ) -> PostLLMCheckResult:
        """
        Run all post-LLM compliance checks on a model-generated response.

        Call this AFTER the LLM produces a response, BEFORE sending it to
        the customer. Use sanitized_response as the final text to deliver.

        Args:
            llm_response:       The raw LLM-generated response text.
            grounding_sources:  RAG chunk IDs used to generate this response
                                (empty list for L1 non-RAG responses).
            trigger_text:       The original user message (for legal keyword checks).

        Returns:
            PostLLMCheckResult with is_approved flag and sanitized_response.
        """
        t_start = time.perf_counter()

        logger.info(
            "ai_gateway_post_llm_inspection_start",
            response_length=len(llm_response),
            response_preview=llm_response[:80],
            grounding_source_count=len(grounding_sources or []),
        )

        sanitized = llm_response  # Start with the raw response

        # ── Check 1: Hallucination Risk ───────────────────────────────────────
        hallucination_risk = self._estimate_hallucination_risk(
            llm_response=llm_response,
            grounding_sources=grounding_sources or [],
        )

        # ── Check 2: Toxicity Filter ──────────────────────────────────────────
        toxicity_score = self._score_toxicity(llm_response)

        # ── Check 3: Brand Compliance ─────────────────────────────────────────
        brand_compliant, sanitized = self._check_brand_compliance(sanitized)

        # ── Check 4: PII Leakage Check ────────────────────────────────────────
        pii_in_response_detected, pii_types_out = self._detect_pii(llm_response)
        if pii_in_response_detected:
            logger.error(
                "ai_gateway_pii_leakage_in_llm_response",
                pii_types=pii_types_out,
            )

        # ── Check 5: Legal Disclaimer Enforcement ─────────────────────────────
        legal_disclaimer_injected = False
        if self._requires_legal_disclaimer(trigger_text=trigger_text, response=sanitized):
            sanitized += _REGA_DISCLAIMER
            legal_disclaimer_injected = True
            logger.info("ai_gateway_legal_disclaimer_injected")

        # ── Determine approval ────────────────────────────────────────────────
        suppression_reason: str | None = None
        is_approved = True

        if hallucination_risk > self._hallucination_threshold:
            is_approved = False
            suppression_reason = f"Hallucination risk {hallucination_risk:.2f} > threshold {self._hallucination_threshold:.2f}"
            sanitized = "عذراً، لم أتمكن من التحقق من هذه المعلومات. يُرجى التواصل مع فريق الدعم."
            logger.error(
                "ai_gateway_response_suppressed_hallucination",
                hallucination_risk=round(hallucination_risk, 3),
            )

        elif toxicity_score > self._toxicity_threshold:
            is_approved = False
            suppression_reason = f"Toxicity score {toxicity_score:.2f} > threshold {self._toxicity_threshold:.2f}"
            sanitized = "عذراً، لا يمكنني الرد على هذا الطلب."
            logger.error(
                "ai_gateway_response_suppressed_toxicity",
                toxicity_score=round(toxicity_score, 3),
            )

        elif pii_in_response_detected:
            is_approved = False
            suppression_reason = "PII leakage detected in LLM response"
            sanitized = "عذراً، حدث خطأ في معالجة طلبك. يُرجى المحاولة مرة أخرى."
            logger.error(
                "ai_gateway_response_suppressed_pii_leakage",
                pii_types=pii_types_out,
            )

        elapsed_ms = (time.perf_counter() - t_start) * 1000

        result = PostLLMCheckResult(
            is_approved=is_approved,
            original_response=llm_response,
            sanitized_response=sanitized,
            hallucination_risk=round(hallucination_risk, 3),
            grounding_sources=grounding_sources or [],
            toxicity_score=round(toxicity_score, 3),
            brand_compliant=brand_compliant,
            legal_disclaimer_injected=legal_disclaimer_injected,
            suppression_reason=suppression_reason,
            check_duration_ms=round(elapsed_ms, 2),
        )

        logger.info(
            "ai_gateway_post_llm_inspection_complete",
            is_approved=is_approved,
            hallucination_risk=round(hallucination_risk, 3),
            toxicity_score=round(toxicity_score, 3),
            brand_compliant=brand_compliant,
            legal_disclaimer_injected=legal_disclaimer_injected,
            pii_leakage=pii_in_response_detected,
            elapsed_ms=round(elapsed_ms, 2),
        )

        return result

    # ══════════════════════════════════════════════════════════════════════════
    # Private Inspection Helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _detect_prompt_injection(self, text: str) -> tuple[bool, float]:
        """
        [STUB — Phase 2] Detect prompt injection / jailbreak attempts.

        Current logic: keyword matching with score proportional to hits.
        Phase 2: Replace with a dedicated classifier or LLM self-reflection call.

        Returns:
            (detected: bool, score: float [0.0–1.0])
        """
        text_lower = text.lower()
        hits = sum(1 for signal in _INJECTION_SIGNALS if signal in text_lower)
        score = min(hits * 0.3, 1.0)
        detected = score >= self._injection_score_threshold

        logger.debug(
            "ai_gateway_injection_check",
            hits=hits,
            score=round(score, 3),
            detected=detected,
            status="STUB — keyword matching only in Phase 1",
        )
        return detected, score

    def _detect_pii(self, text: str) -> tuple[bool, list[str]]:
        """
        [STUB — Phase 2] Detect PII using regex patterns.

        Current logic: Regex patterns for Saudi phone, national ID, IBAN,
        credit card, and email.
        Phase 2: Replace with Google Cloud DLP API for comprehensive detection.

        Returns:
            (detected: bool, pii_types: list[str])
        """
        found_types = [
            pii_type
            for pii_type, pattern in _PII_PATTERNS.items()
            if pattern.search(text)
        ]
        detected = len(found_types) > 0

        logger.debug(
            "ai_gateway_pii_check",
            detected=detected,
            types_found=found_types,
            status="STUB — regex patterns only in Phase 1",
        )
        return detected, found_types

    def _verify_tenant_context(
        self,
        tenant_id: uuid.UUID | None,
        tenant_context: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """
        [STUB — Phase 2] Verify tenant is active and authorized to use AI.

        Current logic: Always returns True (no DB lookup in Phase 1).
        Phase 2: Query tenant table, check subscription status, trial expiry.

        Returns:
            (verified: bool, rejection_reason: str | None)
        """
        logger.debug(
            "ai_gateway_tenant_verification",
            tenant_id=str(tenant_id) if tenant_id else None,
            status="STUB — always verified in Phase 1",
        )
        # ── TODO (Phase 2): Real tenant verification ──────────────────────────
        # tenant = await tenant_repo.get(tenant_id)
        # if tenant.subscription_status == SubscriptionStatus.SUSPENDED:
        #     return False, "suspended"
        # if tenant.trial_expired:
        #     return False, "trial_expired"
        return True, None

    def _estimate_hallucination_risk(
        self,
        llm_response: str,
        grounding_sources: list[str],
    ) -> float:
        """
        [STUB — Phase 2] Estimate hallucination risk in the LLM response.

        Current logic: If no grounding sources are provided (L1 non-RAG), return 0.2.
        If sources are provided, return 0.1 (assumed grounded).
        Phase 2: Cross-reference response claims against retrieved RAG chunks.

        Returns:
            Risk score [0.0–1.0]. Higher = more likely hallucinated.
        """
        logger.debug(
            "ai_gateway_hallucination_check",
            has_grounding=len(grounding_sources) > 0,
            status="STUB — heuristic scoring in Phase 1",
        )
        # Without RAG context we can't verify facts, so assign a baseline risk
        if not grounding_sources:
            return 0.20   # Moderate baseline — no RAG context to cross-check
        return 0.10       # Lower risk when RAG sources are available

    def _score_toxicity(self, text: str) -> float:
        """
        [STUB — Phase 2] Score toxicity of a text using keyword matching.

        Current logic: Keyword matching against _TOXICITY_KEYWORDS.
        Phase 2: Use Perspective API (Google) for accurate toxicity scoring.

        Returns:
            Toxicity score [0.0–1.0].
        """
        text_lower = text.lower()
        hits = sum(1 for kw in _TOXICITY_KEYWORDS if kw in text_lower)
        score = min(hits * 0.4, 1.0)

        logger.debug(
            "ai_gateway_toxicity_check",
            hits=hits,
            score=round(score, 3),
            status="STUB — keyword matching only in Phase 1",
        )
        return score

    def _check_brand_compliance(self, text: str) -> tuple[bool, str]:
        """
        [STUB — Phase 2] Ensure response adheres to brand guidelines.

        Current logic: Remove any competitor brand names if accidentally included.
        Phase 2: LLM self-review against full brand guidelines document.

        Returns:
            (compliant: bool, sanitized_text: str)
        """
        # ── Competitor brand filter (example — customise per tenant) ──────────
        competitor_brands = ["عقار", "bayut", "property finder", "propertyfinder"]
        sanitized = text
        compliant = True
        for brand in competitor_brands:
            if brand.lower() in sanitized.lower():
                # Simple replacement — Phase 2 will use context-aware redaction
                sanitized = re.sub(brand, "***", sanitized, flags=re.IGNORECASE)
                compliant = False
                logger.warning(
                    "ai_gateway_brand_compliance_violation",
                    competitor_brand=brand,
                    status="STUB — simple string replacement in Phase 1",
                )
        return compliant, sanitized

    def _requires_legal_disclaimer(self, trigger_text: str, response: str) -> bool:
        """
        [STUB — Phase 2] Determine if a REGA/legal disclaimer must be injected.

        Current logic: Checks for legal-trigger keywords in either the
        original user message or the LLM response.
        Phase 2: Context-aware classifier trained on Saudi real-estate regulations.

        Returns:
            True if a disclaimer should be appended.
        """
        combined = (trigger_text + " " + response).lower()
        needs_disclaimer = any(kw in combined for kw in _LEGAL_TRIGGER_KEYWORDS)
        logger.debug(
            "ai_gateway_legal_disclaimer_check",
            requires_disclaimer=needs_disclaimer,
            status="STUB — keyword matching in Phase 1",
        )
        return needs_disclaimer
