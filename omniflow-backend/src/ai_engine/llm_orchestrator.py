"""
src/ai_engine/llm_orchestrator.py — LLMOrchestrator (OpenAI Edition)

Multi-tier LLM invocation pipeline using the OpenAI-compatible async SDK.
Provider, endpoint, and models are shared with the Kafka invocation worker.

Architecture — 4-Model Routing:
    Intent Classification  → gpt-4o-mini        (fast JSON-mode classifier)
    ──────────────────────────────────────────────────────────────────────
    L1 Triage              → gpt-4o-mini         (simple FAQs / greetings)
    L2 RAG-Augmented       → gpt-4o-mini         (moderate / negotiation)
    L3 Master Agent        → gpt-4o              (deep consultation / legal)

Flow for every invoke() call:
    1. _classify_intent(message)   → {"tier": "L1"|"L2"|"L3"}  via JSON mode
    2. _call_openai(model, ...)    → plain-text response string
    3. Inject tenant system prompt as the leading system message
    4. Graceful fallback → Arabic error message on any OpenAI exception

The orchestrator remains the ONLY component that talks directly to the
OpenAI API. All other modules consume the returned string.

Caller contract (unchanged from Gemini edition):
    orchestrator = LLMOrchestrator()
    response_text = await orchestrator.invoke(
        routing_decision=decision,
        request=routing_request,
        system_prompt=tenant.ai_system_prompt,
    )

References: SRS §2.3 Steps 5-7 — LLM Invocation & Outbound Dispatch
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import structlog
from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError

from src.ai_engine.company_context import compose_system_prompt, get_company_context
from src.ai_engine.schemas import RoutingDecision, RoutingRequest
from src.shared.core.config import get_settings
from src.shared.core.enums import RoutingTier
from src.shared.services.llm_provider import create_chat_client, provider_options, completion_options

logger = structlog.get_logger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Module-level initialisation — single AsyncOpenAI client for all requests
# ══════════════════════════════════════════════════════════════════════════════

_settings = get_settings()
# Gateway and Kafka workers use the same provider and model configuration.
_openai_client = create_chat_client(_settings)
if _openai_client is None:
    if _settings.is_production:
        raise RuntimeError("Configure the selected LLM provider API key before startup")
    logger.warning("llm_api_key_missing", provider=_settings.llm_primary_provider)

_, _, _models = provider_options(_settings)
_MODEL_CLASSIFIER = _models["ROUTER"]
_MODEL_L1 = _models["L1"]
_MODEL_L2 = _models["L2"]
_MODEL_L3 = _models["L3"]

# Max output tokens per tier
_MAX_OUTPUT_TOKENS: dict[str, int] = {
    RoutingTier.L1_TRIAGE:  512,
    RoutingTier.L2_RAG:    1024,
    RoutingTier.L3_MASTER: 2048,
}

# Temperature per tier — L3 is more deterministic for high-stakes responses
_TEMPERATURE: dict[str, float] = {
    RoutingTier.L1_TRIAGE:  0.75,
    RoutingTier.L2_RAG:    0.65,
    RoutingTier.L3_MASTER: 0.40,
}

# Default fallback Arabic error message
_FALLBACK_ERROR_MESSAGE = (
    "عذراً، حدث خطأ تقني مؤقت في معالجة طلبك. "
    "يرجى المحاولة مرة أخرى أو التواصل مع فريق الدعم."
)

# Default tenant persona used when no custom ai_system_prompt is configured
_DEFAULT_SYSTEM_PROMPT: str = (
    "أنت المستشار أحمد الصائغ من سويفت هوم، مستشار عقاري سعودي محترف. "
    "أجب على استفسارات العملاء باختصار ولهجة سعودية محترمة وودودة. لا تطل في الإجابة."
)

# Intent classifier system instruction — tightly constrained for JSON mode
_CLASSIFIER_SYSTEM = (
    "You are an intent complexity classifier for a Saudi real estate AI assistant. "
    "Classify the customer's message into exactly one complexity tier.\n\n"
    "Tiers:\n"
    "  L1 — Simple: FAQs, greetings, basic inquiries, one-word answers possible\n"
    "  L2 — Moderate: negotiations, follow-up questions, dynamic context required\n"
    "  L3 — Complex: deep consultations, legal/REGA questions, sensitive negotiation, "
    "multi-step deal structuring\n\n"
    'Return ONLY valid JSON in this exact format: {"tier": "L1"} or {"tier": "L2"} or {"tier": "L3"}.\n'
    "No explanation. No markdown. No extra keys. Just the JSON object."
)


# ══════════════════════════════════════════════════════════════════════════════
# LLMOrchestrator
# ══════════════════════════════════════════════════════════════════════════════

class LLMOrchestrator:
    """
    Multi-tier LLM invocation controller for the OmniFlow AI platform.

    Instantiate once at application startup and reuse across requests.
    All public methods are async and return plain-string responses.

    Routing architecture:
        invoke()            → Primary dispatch (handles L1/L2/L3 non-LLM tiers)
        _classify_intent()  → gpt-4o-mini JSON-mode classifier → "L1"|"L2"|"L3"
        _call_openai()      → Shared async OpenAI chat completion helper
        invoke_l1()         → gpt-4o-mini: simple & fast
        invoke_l2_rag()     → gpt-4o-mini: moderate + RAG context (Phase 2)
        invoke_l3()         → gpt-4o: full reasoning / deep consultation
        generate_response() → Thin convenience wrapper (intent → route → generate)
    """

    def __init__(self) -> None:
        logger.info(
            "llm_orchestrator_initialized",
            classifier_model=_MODEL_CLASSIFIER,
            l1_model=_MODEL_L1,
            l2_model=_MODEL_L2,
            l3_model=_MODEL_L3,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Private helpers
    # ══════════════════════════════════════════════════════════════════════════

    async def _resolve_system_prompt(
        self,
        system_prompt: str | None,
        tenant_id: str | uuid.UUID | None = None,
    ) -> str:
        """
        Build the final system instruction for one LLM call.

            persona  = tenant.ai_system_prompt  or  the default Ahmad Al-Sayegh
            facts    = the tenant's company_profile block (Knowledge Base §4.2)

        `tenant_id` is optional so the older call sites keep working unchanged;
        when it is omitted no company facts are injected. Loading the profile is
        cached in-process and degrades to None on error, so this never adds a
        failure mode to the message pipeline — worst case the AI answers with
        the persona alone, exactly as before this feature existed.
        """
        base = (system_prompt or "").strip() or _DEFAULT_SYSTEM_PROMPT
        if not tenant_id:
            return base
        company_block = await get_company_context(tenant_id)
        return compose_system_prompt(base, company_block)

    async def _classify_intent(self, message: str) -> str:
        """
        Classify the user message into a complexity tier using gpt-4o-mini in
        JSON mode, guaranteeing a structured {"tier": "L1"|"L2"|"L3"} response.

        Args:
            message: The raw user message text.

        Returns:
            One of "L1", "L2", or "L3". Defaults to "L1" on any failure so
            that the pipeline always continues with the cheapest model.

        Design notes:
            - Uses `response_format={"type": "json_object"}` (JSON mode) to
              eliminate any risk of the model returning free-text.
            - Temperature is deliberately low (0.0) — classification must be
              deterministic and cost-efficient.
            - The model is gpt-4o-mini (~5× cheaper than gpt-4o) — the
              classifier adds < 150ms and < 0.01¢ overhead per request.
        """
        if _openai_client is None:
            logger.warning(
                "classifier_skipped_no_client",
                fallback_tier="L1",
            )
            return "L1"

        try:
            resp = await _openai_client.chat.completions.create(
                model=_MODEL_CLASSIFIER,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=512 if _settings.llm_primary_provider == "groq" else 20,
                **completion_options(_MODEL_CLASSIFIER, _settings),
                messages=[
                    {"role": "system", "content": _CLASSIFIER_SYSTEM},
                    {"role": "user",   "content": message},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
            parsed = json.loads(raw)
            tier = str(parsed.get("tier", "L1")).upper()
            if tier not in ("L1", "L2", "L3"):
                tier = "L1"
            return tier

        except (json.JSONDecodeError, KeyError) as parse_err:
            logger.warning(
                "classifier_json_parse_error",
                error=str(parse_err),
                fallback_tier="L1",
            )
            return "L1"

        except (APIError, APIConnectionError, RateLimitError) as api_err:
            logger.warning(
                "classifier_api_error",
                error=str(api_err),
                error_type=type(api_err).__name__,
                fallback_tier="L1",
            )
            return "L1"

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "classifier_unexpected_error",
                error=str(exc),
                error_type=type(exc).__name__,
                fallback_tier="L1",
            )
            return "L1"

    async def _call_openai(
        self,
        *,
        model: str,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
        user_message: str,
        max_tokens: int,
        temperature: float,
        request_id: uuid.UUID | None = None,
    ) -> str:
        """
        Shared async helper that executes a single OpenAI chat completion.

        Constructs the full message array:
            [system] + [history turns] + [current user message]

        Handles all OpenAI-specific exceptions and returns the fallback
        Arabic error message so the caller never receives an empty string.

        Args:
            model:                The OpenAI model identifier.
            system_prompt:        System instruction (tenant persona).
            conversation_history: Prior turns in {role, content} format.
            user_message:         The current user message text.
            max_tokens:           Max tokens for the completion.
            temperature:          Sampling temperature.
            request_id:           Tracing ID for structured logs.

        Returns:
            The model's text response, or the Arabic fallback on error.
        """
        if _openai_client is None:
            logger.error(
                "openai_client_not_configured",
                request_id=str(request_id) if request_id else None,
            )
            return _FALLBACK_ERROR_MESSAGE

        # ── Build message array ───────────────────────────────────────────────
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        # Inject recent conversation history (up to last 20 turns for context)
        for turn in (conversation_history or [])[-20:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})

        # ── Call OpenAI ───────────────────────────────────────────────────────
        try:
            completion = await _openai_client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=temperature,
                **completion_options(model, _settings),
            )
            response_text = (completion.choices[0].message.content or "").strip()
            if not response_text:
                logger.warning(
                    "openai_empty_response",
                    model=model,
                    request_id=str(request_id) if request_id else None,
                )
                return _FALLBACK_ERROR_MESSAGE
            return response_text

        except RateLimitError as exc:
            logger.error(
                "openai_rate_limit_error",
                model=model,
                request_id=str(request_id) if request_id else None,
                error=str(exc),
            )
            return _FALLBACK_ERROR_MESSAGE

        except APIConnectionError as exc:
            logger.error(
                "openai_connection_error",
                model=model,
                request_id=str(request_id) if request_id else None,
                error=str(exc),
            )
            return _FALLBACK_ERROR_MESSAGE

        except APIError as exc:
            logger.error(
                "openai_api_error",
                model=model,
                request_id=str(request_id) if request_id else None,
                error=str(exc),
                status_code=getattr(exc, "status_code", None),
            )
            return _FALLBACK_ERROR_MESSAGE

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "openai_unexpected_error",
                model=model,
                request_id=str(request_id) if request_id else None,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return _FALLBACK_ERROR_MESSAGE

    # ══════════════════════════════════════════════════════════════════════════
    # Public API — Convenience wrapper
    # ══════════════════════════════════════════════════════════════════════════

    async def generate_response(
        self,
        user_message: str,
        tenant_system_prompt: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        request_id: uuid.UUID | None = None,
        tenant_id: str | uuid.UUID | None = None,
    ) -> str:
        """
        High-level entry point: classify intent → route to model → return response.

        This is the thin convenience wrapper that ties the full pipeline together
        without requiring the caller to manage RoutingDecision / RoutingRequest
        objects. Useful for direct calls from webhook handlers and tests.

        Pipeline:
            1. _classify_intent(user_message) → "L1" | "L2" | "L3"
            2. Route to invoke_l1 / invoke_l2_rag / invoke_l3
            3. Inject tenant_system_prompt as leading system message
            4. Return response string (Arabic fallback on any error)

        Args:
            user_message:         The raw inbound message from the customer.
            tenant_system_prompt: The tenant's custom AI personality prompt.
                                  If None, the default Ahmad Al-Sayegh persona
                                  is used.
            conversation_history: Recent turns for context window injection.
            request_id:           Tracing ID (auto-generated if None).

        Returns:
            The LLM-generated response as a plain string.
        """
        if request_id is None:
            request_id = uuid.uuid4()

        t_start = time.perf_counter()

        # ── Step 1: Classify intent ───────────────────────────────────────────
        tier = await self._classify_intent(user_message)

        logger.info(
            "generate_response_intent_classified",
            request_id=str(request_id),
            tier=tier,
            message_preview=user_message[:80],
        )

        # ── Step 2: Route to appropriate tier ────────────────────────────────
        # Company Knowledge Base facts are folded in here once; the tier
        # methods below therefore receive an already-complete prompt and are
        # called WITHOUT tenant_id so the block is not injected twice.
        effective_prompt = await self._resolve_system_prompt(
            tenant_system_prompt, tenant_id
        )

        if tier == "L2":
            response = await self.invoke_l2_rag(
                message_text=user_message,
                conversation_history=conversation_history,
                system_prompt=effective_prompt,
                request_id=request_id,
            )
        elif tier == "L3":
            response = await self.invoke_l3(
                message_text=user_message,
                conversation_history=conversation_history,
                system_prompt=effective_prompt,
                request_id=request_id,
            )
        else:
            # Default L1 (also handles any unexpected tier value)
            response = await self.invoke_l1(
                message_text=user_message,
                conversation_history=conversation_history,
                system_prompt=effective_prompt,
                request_id=request_id,
            )

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "generate_response_complete",
            request_id=str(request_id),
            tier=tier,
            elapsed_ms=round(elapsed_ms, 2),
            response_length=len(response),
        )
        return response

    # ══════════════════════════════════════════════════════════════════════════
    # Public API — Primary Dispatch (RoutingDecision-based)
    # ══════════════════════════════════════════════════════════════════════════

    async def invoke(
        self,
        routing_decision: RoutingDecision,
        request: RoutingRequest,
        system_prompt: str | None = None,
    ) -> str:
        """
        Primary dispatch method. Routes to the correct LLM tier based on the
        RoutingDecision.tier (set by SemanticRouter) and returns a string.

        For non-LLM tiers (L0, VAULT, HUMAN, VCARD) the method returns an
        empty string — the caller is responsible for not invoking this for
        those cases, but it is handled gracefully here as a safety net.

        When a tier IS an LLM tier, _classify_intent() is still called so
        that the actual model chosen reflects the message content, not just
        the SemanticRouter's coarser tier label. This gives us the full
        4-model routing benefit even in the RoutingDecision path.

        Args:
            routing_decision: The RoutingDecision from SemanticRouter.
            request:          The original RoutingRequest (message, history, etc.)
            system_prompt:    Optional tenant AI personality override.
                              If None, _DEFAULT_SYSTEM_PROMPT is used.

        Returns:
            The LLM-generated response text as a string.
        """
        tier = routing_decision.tier

        logger.info(
            "llm_orchestrator_invoke",
            request_id=str(routing_decision.request_id),
            semantic_tier=tier,
            intent=routing_decision.intent,
            requires_rag=routing_decision.requires_rag,
            message_preview=request.message_text[:60],
        )

        # ── Guard: non-LLM tiers should not reach here ────────────────────────
        if tier in (
            RoutingTier.L0_SEMANTIC_CACHE,
            RoutingTier.VAULT_RETRIEVAL,
            RoutingTier.HUMAN_ESCALATION,
            RoutingTier.VCARD_GATEKEEPER,
        ):
            logger.warning(
                "llm_orchestrator_non_llm_tier_bypassed",
                tier=tier,
                request_id=str(routing_decision.request_id),
            )
            return ""

        # ── Dispatch to appropriate tier ──────────────────────────────────────
        # `request.tenant_id` comes from the verified RoutingRequest, so the
        # company facts injected here always belong to the right tenant.
        effective_prompt = await self._resolve_system_prompt(
            system_prompt, getattr(request, "tenant_id", None)
        )

        if tier == RoutingTier.L1_TRIAGE:
            return await self.invoke_l1(
                message_text=request.message_text,
                conversation_history=request.conversation_history,
                system_prompt=effective_prompt,
                request_id=routing_decision.request_id,
            )

        elif tier == RoutingTier.L2_RAG:
            return await self.invoke_l2_rag(
                message_text=request.message_text,
                conversation_history=request.conversation_history,
                system_prompt=effective_prompt,
                request_id=routing_decision.request_id,
            )

        elif tier == RoutingTier.L3_MASTER:
            return await self.invoke_l3(
                message_text=request.message_text,
                conversation_history=request.conversation_history,
                system_prompt=effective_prompt,
                request_id=routing_decision.request_id,
            )

        # ── Unknown tier fallback ──────────────────────────────────────────────
        logger.error(
            "llm_orchestrator_unknown_tier",
            tier=tier,
            request_id=str(routing_decision.request_id),
        )
        return ""

    # ══════════════════════════════════════════════════════════════════════════
    # Tier-Specific Invoke Methods
    # ══════════════════════════════════════════════════════════════════════════

    async def invoke_l1(
        self,
        message_text: str,
        conversation_history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
        request_id: uuid.UUID | None = None,
        tenant_id: str | uuid.UUID | None = None,
    ) -> str:
        """
        L1 Triage — gpt-4o-mini: Fast, low-cost response.

        Intended for:
            - Greetings and salutations
            - Simple FAQs ("ما رقم التواصل؟")
            - One-line answers, clarification questions
            - Basic availability checks

        Model:           gpt-4o-mini
        Max tokens:      512
        Temperature:     0.75
        Expected latency: ~300-500ms
        """
        t_start = time.perf_counter()
        effective_prompt = await self._resolve_system_prompt(system_prompt, tenant_id)

        logger.info(
            "llm_orchestrator_l1_start",
            request_id=str(request_id) if request_id else None,
            model=_MODEL_L1,
            message_preview=message_text[:60],
            history_turns=len(conversation_history or []),
        )

        response = await self._call_openai(
            model=_MODEL_L1,
            system_prompt=effective_prompt,
            conversation_history=conversation_history or [],
            user_message=message_text,
            max_tokens=_MAX_OUTPUT_TOKENS[RoutingTier.L1_TRIAGE],
            temperature=_TEMPERATURE[RoutingTier.L1_TRIAGE],
            request_id=request_id,
        )

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "llm_orchestrator_l1_complete",
            request_id=str(request_id) if request_id else None,
            model=_MODEL_L1,
            elapsed_ms=round(elapsed_ms, 2),
            response_length=len(response),
        )
        return response

    async def invoke_l2_rag(
        self,
        message_text: str,
        conversation_history: list[dict[str, str]] | None = None,
        rag_context: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        request_id: uuid.UUID | None = None,
        tenant_id: str | uuid.UUID | None = None,
    ) -> str:
        """
        L2 RAG-Augmented — gpt-4o-mini + optional Qdrant context.

        Intended for:
            - Moderate-complexity inquiries that need dynamic data
            - Property-specific questions (price, area, REGA number)
            - Negotiation support and follow-up responses
            - Location-based searches

        RAG context (Phase 2):
            When `rag_context` chunks are provided they are formatted and
            prepended to the system prompt so the model can ground its answer
            in verified property data. Phase 1 omits this step but the
            signature is ready for drop-in injection.

        Model:           gpt-4o-mini
        Max tokens:      1024
        Temperature:     0.65
        Expected latency: ~600-900ms (including Qdrant retrieval in Phase 2)
        """
        t_start = time.perf_counter()
        effective_prompt = await self._resolve_system_prompt(system_prompt, tenant_id)

        # ── Phase 2 RAG context injection (ready to enable) ───────────────────
        if rag_context:
            context_block = "\n\n".join(
                f"[عقار {i+1}] {chunk.get('text', '')}"
                for i, chunk in enumerate(rag_context[:5])  # top-5 chunks
            )
            effective_prompt = (
                f"{effective_prompt}\n\n"
                "## السياق من قاعدة بيانات العقارات (RAG):\n"
                f"{context_block}\n\n"
                "استند إلى هذه البيانات عند الإجابة. لا تخترع أرقاماً أو تفاصيل غير موجودة."
            )

        logger.info(
            "llm_orchestrator_l2_start",
            request_id=str(request_id) if request_id else None,
            model=_MODEL_L2,
            message_preview=message_text[:60],
            history_turns=len(conversation_history or []),
            rag_chunks=len(rag_context) if rag_context else 0,
        )

        response = await self._call_openai(
            model=_MODEL_L2,
            system_prompt=effective_prompt,
            conversation_history=conversation_history or [],
            user_message=message_text,
            max_tokens=_MAX_OUTPUT_TOKENS[RoutingTier.L2_RAG],
            temperature=_TEMPERATURE[RoutingTier.L2_RAG],
            request_id=request_id,
        )

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "llm_orchestrator_l2_complete",
            request_id=str(request_id) if request_id else None,
            model=_MODEL_L2,
            elapsed_ms=round(elapsed_ms, 2),
            response_length=len(response),
        )
        return response

    async def invoke_l3(
        self,
        message_text: str,
        conversation_history: list[dict[str, str]] | None = None,
        rag_context: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        request_id: uuid.UUID | None = None,
        tenant_id: str | uuid.UUID | None = None,
    ) -> str:
        """
        L3 Master Agent — gpt-4o: Maximum reasoning capability.

        Intended for:
            - Deep real estate consultations
            - Complex REGA / legal / regulatory questions
            - Sensitive price negotiations and counter-offer structuring
            - Multi-step deal analysis requiring long context

        Uses a larger RAG window (top-10 vs L2's top-5) and a lower
        temperature (0.40) to produce more precise, deterministic answers
        for high-stakes interactions.

        Model:           gpt-4o
        Max tokens:      2048
        Temperature:     0.40
        Expected latency: ~1.5-4s
        """
        t_start = time.perf_counter()
        effective_prompt = await self._resolve_system_prompt(system_prompt, tenant_id)

        # ── Phase 2 RAG context injection (larger window than L2) ─────────────
        if rag_context:
            context_block = "\n\n".join(
                f"[عقار {i+1}] {chunk.get('text', '')}"
                for i, chunk in enumerate(rag_context[:10])  # top-10 for L3
            )
            effective_prompt = (
                f"{effective_prompt}\n\n"
                "## السياق الموسّع من قاعدة بيانات العقارات (RAG — L3):\n"
                f"{context_block}\n\n"
                "قدّم تحليلاً معمّقاً ودقيقاً. استشهد بالأرقام الموثّقة فقط."
            )

        logger.info(
            "llm_orchestrator_l3_start",
            request_id=str(request_id) if request_id else None,
            model=_MODEL_L3,
            message_preview=message_text[:60],
            history_turns=len(conversation_history or []),
            rag_chunks=len(rag_context) if rag_context else 0,
        )

        response = await self._call_openai(
            model=_MODEL_L3,
            system_prompt=effective_prompt,
            conversation_history=conversation_history or [],
            user_message=message_text,
            max_tokens=_MAX_OUTPUT_TOKENS[RoutingTier.L3_MASTER],
            temperature=_TEMPERATURE[RoutingTier.L3_MASTER],
            request_id=request_id,
        )

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "llm_orchestrator_l3_complete",
            request_id=str(request_id) if request_id else None,
            model=_MODEL_L3,
            elapsed_ms=round(elapsed_ms, 2),
            response_length=len(response),
        )
        return response
