"""
ai_workers/llm_invoker/client.py — Async Gemini LLM Client Singleton

Wraps the `google-generativeai` SDK with:
  - Model tier dispatch (L1=Flash-Lite, L2=Flash, L3=Pro)
  - System prompt injection (tenant persona + Arabic language instructions)
  - Conversation history formatting (Gemini multi-turn format)
  - Retry logic with exponential backoff (tenacity)
  - Token/cost tracking per response
  - Graceful timeout management (asyncio.wait_for)

Gemini model mapping:
  L0 — No LLM call (deterministic response in LLMInvokerWorker)
  L1 — gemini-2.0-flash-lite  (fast, cheapest, ~$0.075/1M tokens)
  L2 — gemini-2.0-flash       (balanced, ~$0.10/1M tokens)
  L3 — gemini-2.0-pro         (most capable, ~$1.25/1M tokens)

References: SRS §2.3 — AI Routing, SRS §4 — LLM Cost Control
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import google.genai as genai
from google.genai import types as genai_types
import structlog
from google.api_core.exceptions import (
    GoogleAPICallError,
    ResourceExhausted,
    ServiceUnavailable,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from src.shared.core.config import get_settings
from src.shared.core.enums import RoutingTier

logger = structlog.get_logger(__name__)
settings = get_settings()

# ─────────────────────────────────────────────────────────────────────────────
# Model tier → Gemini model name mapping
# ─────────────────────────────────────────────────────────────────────────────
_TIER_TO_MODEL: dict[str, str] = {
    RoutingTier.L1_TRIAGE: settings.gemini_l1_model,   # gemini-2.0-flash-lite
    RoutingTier.L2_RAG: "gemini-2.0-flash",             # balanced
    RoutingTier.L3_MASTER: settings.gemini_l3_model,   # gemini-2.0-pro
    RoutingTier.VAULT_RETRIEVAL: settings.gemini_l1_model,  # Vault uses L1 for brief responses
}

# Per-tier timeout (seconds) — Pro model takes longer
_TIER_TIMEOUT: dict[str, float] = {
    RoutingTier.L1_TRIAGE: 15.0,
    RoutingTier.L2_RAG: 25.0,
    RoutingTier.L3_MASTER: 45.0,
    RoutingTier.VAULT_RETRIEVAL: 10.0,
}

# Generation config per tier — L1 is faster/cheaper with lower output cap
_TIER_GENERATION_CONFIG: dict[str, dict[str, Any]] = {
    RoutingTier.L1_TRIAGE: {
        "temperature": 0.7,
        "max_output_tokens": 512,
        "top_p": 0.95,
    },
    RoutingTier.L2_RAG: {
        "temperature": 0.5,     # More factual for property data
        "max_output_tokens": 1024,
        "top_p": 0.90,
    },
    RoutingTier.L3_MASTER: {
        "temperature": 0.3,     # Low temperature for serious negotiations
        "max_output_tokens": 2048,
        "top_p": 0.85,
    },
    RoutingTier.VAULT_RETRIEVAL: {
        "temperature": 0.3,
        "max_output_tokens": 256,
        "top_p": 0.90,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Response dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Structured response from the LLM client."""
    text: str
    model_used: str
    tier: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int
    finish_reason: str   # "STOP" | "MAX_TOKENS" | "SAFETY" | "ERROR"

    @property
    def was_truncated(self) -> bool:
        return self.finish_reason == "MAX_TOKENS"

    @property
    def was_blocked(self) -> bool:
        return self.finish_reason == "SAFETY"


# ══════════════════════════════════════════════════════════════════════════════
# Gemini LLM Client
# ══════════════════════════════════════════════════════════════════════════════

class GeminiLLMClient:
    """
    Async Gemini API client with tier dispatch, retry, and cost tracking.

    Lifecycle:
        Call configure() once during worker startup (sets the API key).
        Then call generate_response() per message.

    Thread safety:
        google-generativeai is thread-safe for concurrent calls.
        Use a single GeminiLLMClient instance per worker process.
    """

    def __init__(self) -> None:
        self._configured = False

    def configure(self) -> None:
        """
        Initialize the Gemini SDK with the API key from settings.
        Call once during on_startup().
        """
        if not settings.google_ai_api_key:
            raise ValueError(
                "GOOGLE_AI_API_KEY is not set. "
                "Add it to .env before starting the LLM Invoker worker."
            )

        self._mock_mode = settings.google_ai_api_key.lower() == "mock"
        if self._mock_mode:
            logger.warning("gemini_client_mock_mode_enabled")
            self._configured = True
            return

        # google-genai uses a client instance (not global configure)
        self._client = genai.Client(api_key=settings.google_ai_api_key)
        self._configured = True
        logger.info(
            "gemini_client_configured",
            l1_model=settings.gemini_l1_model,
            l3_model=settings.gemini_l3_model,
        )

    async def generate_response(
        self,
        *,
        messages: list[dict[str, str]],
        tier: str,
        system_prompt: str,
        tenant_context: dict[str, Any] | None = None,
        rag_context: str | None = None,
    ) -> LLMResponse:
        """
        Generate a response using the appropriate Gemini model tier.

        Args:
            messages       — Chat history in OmniFlow format:
                             [{"role": "user"|"model", "parts": [str]}]
                             (Gemini uses "model" not "assistant")
            tier           — RoutingTier string ("L1", "L2", "L3", "VAULT")
            system_prompt  — Tenant persona + instructions (injected as system)
            tenant_context — Optional metadata (tenant_name, city, etc.)
            rag_context    — Property/document context for L2 queries (prepended)

        Returns:
            LLMResponse with text, token counts, and metadata.

        Raises:
            asyncio.TimeoutError — if the API call exceeds tier timeout
            ResourceExhausted    — after max_retries (rate limit hit)
        """
        if not self._configured:
            raise RuntimeError(
                "GeminiLLMClient.configure() must be called before generate_response()."
            )

        model_name = _TIER_TO_MODEL.get(tier, settings.gemini_l1_model)
        timeout = _TIER_TIMEOUT.get(tier, 20.0)
        gen_config = _TIER_GENERATION_CONFIG.get(tier, _TIER_GENERATION_CONFIG[RoutingTier.L1_TRIAGE])

        # Build the system instruction (tenant persona + optional RAG context)
        full_system = self._build_system_instruction(
            base_prompt=system_prompt,
            tenant_context=tenant_context,
            rag_context=rag_context,
        )

        if getattr(self, "_mock_mode", False):
            await asyncio.sleep(1.0)  # Simulate network latency

            # Sprint 15: Persona-aware mock responses — Ahmad Al-Sayegh voice
            user_text = " ".join(
                str(m.get("parts", [""])[0])
                for m in messages if m.get("role") == "user"
            ).lower()

            if "صوتية" in user_text or "صوت" in user_text:
                mock_text = (
                    "يا هلا!\n\n"
                    "بكل سرور سأشرح لك مميزات شقق النرجس:\n\n"
                    "🏠 *النرجس — أبرز المميزات:*\n"
                    "• موقع مميز شمال الرياض — قريب من الخدمات والمدارس الدولية\n"
                    "• وحدات متنوعة: ٣-٥ غرف بمساحات ٢٠٠-٤٠٠م²\n"
                    "• تشطيبات عالية الجودة مع إمكانية التخصيص\n"
                    "• أسعار تنافسية تبدأ من ١.٢ مليون ريال\n\n"
                    "هل تودّ تحديد موعد للمعاينة؟ أبشر بخدمتك."
                )
            elif "شقة" in user_text or "النرجس" in user_text or "فيلا" in user_text:
                mock_text = (
                    "أبشر!\n\n"
                    "نعم، لدينا خيارات ممتازة في حي النرجس. "
                    "يسعدني مشاركة التفاصيل:\n\n"
                    "• المساحات المتاحة: من ٢٠٠ إلى ٤٥٠ م²\n"
                    "• الأسعار: تبدأ من ١.١ مليون ريال للوحدات الاقتصادية\n"
                    "• الموقع: النرجس — قرب تقاطع الملك سلمان\n\n"
                    "هل تفضّل شقة أم فيلا؟ وما الميزانية التقريبية؟ 🔑"
                )
            elif "سعر" in user_text or "ميزانية" in user_text or "كم" in user_text:
                mock_text = (
                    "يا هلا بك!\n\n"
                    "الأسعار في حي النرجس تتراوح حسب المساحة والتشطيب:\n\n"
                    "• ٣ غرف (٢٠٠م²): من ١.١ إلى ١.٤ مليون ريال\n"
                    "• ٤ غرف (٢٨٠م²): من ١.٦ إلى ٢ مليون ريال\n"
                    "• فيلات (٤٠٠م²+): من ٢.٥ مليون ريال\n\n"
                    "ما هو حجم الميزانية المناسب لك حتى نعرض عليك الخيار الأمثل؟"
                )
            elif "صك" in user_text or "rega" in user_text or "قانون" in user_text:
                mock_text = (
                    "أبشر! هذا السؤال مهم جداً.\n\n"
                    "التحقق من صحة الصك يمر بعدة خطوات:\n\n"
                    "١. طلب نسخة الصك الأصلية من البائع\n"
                    "٢. التحقق عبر منصة REGA الرسمية\n"
                    "٣. مراجعة سجل الرهونات والحجوزات\n\n"
                    "يمكنني إعداد تقرير تفصيلي مدفوع (٢٩ ريال) عبر خدمة التحقق لدينا. "
                    "هل تريد المتابعة؟"
                )
            else:
                mock_text = (
                    "يا هلا!\n\n"
                    "أنا أحمد الصائغ، يسعدني مساعدتك في رحلتك العقارية. "
                    "نخبة العقارية تقدم لك أفضل الخيارات في أرقى أحياء الرياض.\n\n"
                    "بماذا يمكنني خدمتك اليوم؟ 🏠"
                )

            result = LLMResponse(
                text=mock_text,
                model_used="mock-gemini-ahmad-persona",
                tier=tier,
                input_tokens=150,
                output_tokens=80,
                total_tokens=230,
                latency_ms=1000,
                finish_reason="STOP",
            )
            logger.info("gemini_mock_response_generated", tier=tier, latency_ms=1000)
            return result

        start_ms = asyncio.get_event_loop().time()

        try:
            response = await asyncio.wait_for(
                self._call_gemini_async(
                    model_name=model_name,
                    messages=messages,
                    system_instruction=full_system,
                    generation_config=gen_config,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error(
                "gemini_timeout",
                tier=tier,
                model=model_name,
                timeout=timeout,
            )
            raise
        except ResourceExhausted as exc:
            logger.error("gemini_rate_limit", tier=tier, error=str(exc))
            raise
        except GoogleAPICallError as exc:
            logger.error("gemini_api_error", tier=tier, error=str(exc))
            raise

        latency_ms = int((asyncio.get_event_loop().time() - start_ms) * 1000)

        # Extract usage metadata
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        # Extract finish reason
        candidates = getattr(response, "candidates", [])
        finish_reason = "STOP"
        if candidates:
            fr = getattr(candidates[0], "finish_reason", None)
            if fr:
                finish_reason = str(fr.name) if hasattr(fr, "name") else str(fr)

        # Extract text
        text = self._extract_text(response)

        result = LLMResponse(
            text=text,
            model_used=model_name,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

        logger.info(
            "gemini_response_generated",
            tier=tier,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

        return result

    async def _call_gemini_async(
        self,
        *,
        model_name: str,
        messages: list[dict[str, str]],
        system_instruction: str,
        generation_config: dict[str, Any],
    ) -> Any:
        """
        Execute the Gemini API call using the new google-genai async client.

        Uses AsyncRetrying for transient errors (429, 503).
        """
        # Build google-genai Contents list
        contents = [
            genai_types.Content(
                role=msg["role"],
                parts=[genai_types.Part(text=part) for part in msg["parts"]]
            )
            for msg in messages
        ]

        generate_config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=generation_config.get("temperature", 0.7),
            max_output_tokens=generation_config.get("max_output_tokens", 512),
            top_p=generation_config.get("top_p", 0.95),
        )

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((ResourceExhausted, ServiceUnavailable)),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            reraise=True,
        ):
            with attempt:
                response = await self._client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=generate_config,
                )
                return response

    @staticmethod
    def _format_messages_for_gemini(
        messages: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """
        Convert OmniFlow history format to Gemini multi-turn format.

        OmniFlow format:  {"role": "user"|"assistant"|"system", "content": str}
        Gemini format:    {"role": "user"|"model", "parts": [str]}

        Notes:
          - Gemini uses "model" not "assistant"
          - System messages are handled via system_instruction, not history
          - Alternating user/model turns required — we enforce this by skipping
            consecutive same-role messages (keep the last one)
        """
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", msg.get("parts", ""))
            if isinstance(content, list):
                content = " ".join(str(p) for p in content)

            # Map to Gemini roles
            gemini_role = "model" if role in ("assistant", "model") else "user"

            # Enforce alternating turns (Gemini requirement)
            if formatted and formatted[-1]["role"] == gemini_role:
                # Merge into previous if same role (shouldn't happen in well-formed history)
                formatted[-1]["parts"][0] += f"\n\n{content}"
            else:
                formatted.append({"role": gemini_role, "parts": [content]})

        # Gemini requires history to end with a user message for chat continuation
        # The last message is sent separately via send_message, so history = all but last
        return formatted

    @staticmethod
    def _build_system_instruction(
        *,
        base_prompt: str,
        tenant_context: dict[str, Any] | None,
        rag_context: str | None,
    ) -> str:
        """
        Assemble the full system instruction from components.

        Structure:
            [BASE PERSONA]
            [TENANT CONTEXT BLOCK]  — optional
            [RAG CONTEXT BLOCK]     — optional (property/document data)
            [RESPONSE RULES]        — always appended
        """
        parts: list[str] = [base_prompt.strip()]

        if tenant_context:
            ctx_lines = [
                f"- اسم المكتب العقاري: {tenant_context.get('business_name', 'غير محدد')}",
                f"- المدينة: {tenant_context.get('city', 'غير محدد')}",
                f"- رقم فال: {tenant_context.get('fal_license_number', 'غير محدد')}",
            ]
            if tenant_context.get("agent_name"):
                ctx_lines.append(f"- المسؤول: {tenant_context['agent_name']}")
            parts.append("\n## معلومات المكتب:\n" + "\n".join(ctx_lines))

        if rag_context:
            parts.append(
                f"\n## سياق العقارات المتاحة (من قاعدة البيانات):\n{rag_context}"
            )

        # Universal response rules (always last)
        parts.append(
            "\n## قواعد الرد الإلزامية:\n"
            "- تكلم دائماً باللغة العربية ما لم يتكلم العميل بالإنجليزية\n"
            "- كن مهنياً وودوداً وموجزاً\n"
            "- لا تذكر أي معلومات غير موجودة في السياق المعطى\n"
            "- لا تعطي نصائح قانونية مباشرة، بل أحل للمختص\n"
            "- إذا لم تعرف الإجابة، قل ذلك بصدق وعرض التواصل مع الفريق البشري"
        )

        return "\n\n".join(parts)

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Safely extract text from Gemini response, handling blocked responses."""
        try:
            return response.text
        except ValueError:
            # Response was blocked by safety filters
            return (
                "أعتذر، لا يمكنني الإجابة على هذا السؤال. "
                "يرجى التواصل مع فريقنا مباشرة للمساعدة."
            )
        except AttributeError:
            return ""


# ── Module-level singleton ─────────────────────────────────────────────────────
gemini_client = GeminiLLMClient()
