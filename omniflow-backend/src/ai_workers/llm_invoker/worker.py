"""
ai_workers/llm_invoker/worker.py — LLM Invoker Worker

Consumes RoutingDecision payloads from `llm.routing.v1`, calls the
appropriate Gemini model tier, and publishes the AI response to
`messages.outgoing.v1`.

Pipeline position:
    Kafka: llm.routing.v1
        └─► LLMInvokerWorker (THIS FILE)
              ├─► Deserialize RoutingDecision
              ├─► Guard: skip if tier=HUMAN or tier=L0
              ├─► Fetch conversation history (Redis)
              ├─► Fetch tenant persona (Redis → DB)
              ├─► Inject RAG context if tier=L2 (Qdrant — Sprint 7)
              ├─► Call GeminiLLMClient.generate_response()
              ├─► Append AI message to Redis history
              ├─► Publish OutboundMessage to messages.outgoing.v1
              └─► Publish analytics event to analytics.events.v1

Outbound message schema is a Pydantic model published to Kafka.
The WhatsApp Sender Worker (Sprint 8) will consume it and call the
Meta Graph API to deliver it to the customer's phone.

Consumer group: omniflow.ai-workers.v1  (same group as SemanticRouterWorker)
Input topic:    llm.routing.v1          (settings.kafka_topic_llm_routing)
Output topics:  messages.outgoing.v1    (settings.kafka_topic_messages_outgoing)
                analytics.events.v1     (settings.kafka_topic_analytics_events)

References: SRS §2.3 Steps 5-7 — LLM Invocation & Outbound Dispatch
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from aiokafka.structs import ConsumerRecord
from pydantic import BaseModel, Field
from sqlalchemy import update

from src.ai_workers.llm_invoker.client import LLMResponse, gemini_client
from src.ai_workers.llm_invoker.persona import DEFAULT_SYSTEM_PROMPT, get_system_prompt
from src.ai_workers.rag_engine.retriever import rag_retriever
from src.ai_workers.semantic_router.worker import RoutingDecision
from src.shared.core.config import get_settings
from src.shared.core.enums import RoutingTier, MessageType, ConversationStatus
from src.shared.db.models import Conversation
from src.shared.db.session import get_tenant_session
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.kafka.producer import KafkaProducerManager
from src.shared.redis_client.client import redis_mgr
from src.shared.services.tts_client import TTSGenerationError, tts_client
from src.shared.services.conversation_state import load_conversation_state

logger = structlog.get_logger(__name__)
settings = get_settings()

# ─────────────────────────────────────────────────────────────────────────────
# Sprint 15: Persona is now fully managed by persona.py.
# DEFAULT_SYSTEM_PROMPT imported above is the pre-built Ahmad Al-Sayegh prompt.
# ─────────────────────────────────────────────────────────────────────────────

# L0 deterministic responses (no LLM needed)
_L0_RESPONSES: dict[str, str] = {
    "greeting": "أهلاً وسهلاً! 😊 أنا مساعدك العقاري الذكي. كيف يمكنني مساعدتك اليوم؟",
    "vcard_confirmation": "ممتاز! شكراً لحفظ رقمنا 🎉 يسعدنا خدمتك في أي وقت.",
    "general": "شكراً لتواصلك معنا! سأبحث لك عن أفضل الخيارات المتاحة. 🏠",
    # Item 14: no Whisper/Vision worker exists yet (see multimodal/__init__.py
    # stub) — sent instead of letting the LLM answer a placeholder string
    # like "[audio message — media: ...]" as if it understood the content.
    "multimodal_unsupported": (
        "عذرًا، لا يمكنني حاليًا الاستماع للرسائل الصوتية أو تحليل الصور تلقائيًا. "
        "يسعدني مساعدتك إذا كتبت طلبك نصيًا 🙏"
    ),
    # Sent when the LLM call fails twice in a row (see process_message) —
    # never leave the customer with total silence just because the model
    # provider had a bad moment.
    "llm_unavailable_fallback": (
        "عذرًا، حصل تأخير بسيط في الرد. سيتواصل معك فريقنا في أقرب وقت 🙏"
    ),
}


# ── Sprint 14: Voice-note trigger decision ────────────────────────────────────

def _should_use_voice_note(decision: "RoutingDecision", ai_text: str) -> bool:
    """
    Decide whether to synthesise a voice-note reply for this conversation turn.

    Returns True if ANY of the following conditions are met:

    1. **VIP customer** — session_state.is_vip is True.
       VIP clients receive premium personalised voice responses.

    2. **L3 routing tier** — deep RAG + premium model used.
       L3 implies complex consultation; voice makes the reply feel more personal.

    3. **Voice keyword** — the customer's own message contains a voice-request
       keyword from settings.tts_voice_trigger_keywords (e.g. "رسالة صوتية").

    Args:
        decision  — The routing decision carrying session state and tier info.
        ai_text   — The generated AI response text (reserved for future
                    length-based heuristics, e.g. skip TTS for very short text).
    """
    session = decision.session_state or {}
    event = decision.event

    # Condition 1: VIP customer
    if session.get("is_vip"):
        return True

    # Condition 2: Premium L3 tier
    if decision.target_tier == RoutingTier.L3_MASTER:
        return True

    # Condition 3: Customer explicitly asked for a voice note
    customer_text = (event.text_content or "").lower()
    voice_keywords = [
        kw.strip().lower()
        for kw in settings.tts_voice_trigger_keywords.split(",")
        if kw.strip()
    ]
    if any(kw in customer_text for kw in voice_keywords):
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# Outbound Message Model — published to messages.outgoing.v1
# ══════════════════════════════════════════════════════════════════════════════

# Re-exported for existing worker imports.
from src.shared.events.outbound import OutboundMessage


# ══════════════════════════════════════════════════════════════════════════════
# LLMInvokerWorker
# ══════════════════════════════════════════════════════════════════════════════

class LLMInvokerWorker(BaseKafkaConsumer):
    """
    Consumes RoutingDecision from llm.routing.v1 and invokes Gemini.

    Inherits from BaseKafkaConsumer:
      - Manual offset commits
      - Retry with exponential backoff
      - DLQ routing on persistent failure
      - Redis idempotency guard

    Concurrency: runs as a single asyncio coroutine per process.
    Scale horizontally by deploying multiple replicas (K8s HPA).
    All replicas share the same consumer group → Kafka partitions distribute load.
    """

    def __init__(self) -> None:
        super().__init__(
            topics=[settings.kafka_topic_llm_routing],
            group_id=settings.kafka_consumer_group_ai_workers,
            max_retries=3,
            retry_backoff_ms=1000,  # LLM errors may be transient (rate limits)
            batch_size=5,            # Small batch — LLM calls are slow
        )
        self._outbound_producer = KafkaProducerManager()
        self._analytics_producer = KafkaProducerManager()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_startup(self) -> None:
        await redis_mgr.start()
        await self._outbound_producer.start()
        await self._analytics_producer.start()
        gemini_client.configure()
        # RAG pipeline (embedder + Qdrant). Always enabled — a real embedding
        # provider is always available: OpenAI when a real key is configured,
        # otherwise the local fastembed model (no API key, no network
        # dependency beyond a one-time model download). See embedder.py.
        self._rag_enabled = True
        rag_retriever.configure()
        logger.info("rag_enabled", embedding_provider=settings.embedding_provider)
        from src.shared.qdrant_client.client import qdrant_mgr
        await qdrant_mgr.start()
        logger.info(
            "llm_invoker_worker_ready",
            input_topic=settings.kafka_topic_llm_routing,
            output_topic=settings.kafka_topic_messages_outgoing,
        )

    async def on_shutdown(self) -> None:
        await gemini_client.close()
        await self._outbound_producer.stop()
        await self._analytics_producer.stop()
        from src.shared.qdrant_client.client import qdrant_mgr
        await qdrant_mgr.stop()
        await redis_mgr.stop()
        logger.info("llm_invoker_worker_shutdown")

    # ── Core processing ───────────────────────────────────────────────────────

    async def process_message(self, record: ConsumerRecord) -> None:
        """
        Process a single RoutingDecision from Kafka.

        Steps:
          1. Deserialize RoutingDecision
          2. Handle L0 / HUMAN / VAULT tiers without LLM
          3. Fetch conversation history from Redis
          4. Build system prompt (tenant persona)
          5. Inject RAG context if L2 (Sprint 7: Qdrant)
          6. Call Gemini with retry
          7. Publish OutboundMessage to messages.outgoing.v1
          8. Append AI response to Redis history
          9. Publish analytics event
        """
        start_ns = asyncio.get_event_loop().time()

        # ── 1. Deserialize RoutingDecision ────────────────────────────────────
        raw = record.value
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        try:
            decision = RoutingDecision.model_validate_json(raw)
        except Exception as exc:
            raise ValueError(f"Cannot deserialize RoutingDecision: {exc}") from exc

        event = decision.event
        tenant_id = decision.tenant_id
        customer_phone = event.customer_phone or event.platform_user_id
        tier = decision.target_tier
        if decision.conversation_id:
            state = await load_conversation_state(tenant_id, decision.conversation_id)
            if state["is_human_active"] or state["is_processing_restricted"]:
                return

        log = logger.bind(
            event_id=str(event.event_id),
            tenant_id=str(tenant_id),
            tier=tier,
        )

        # ── 2. Non-LLM fast paths ─────────────────────────────────────────────
        if tier == RoutingTier.HUMAN_ESCALATION or (decision.skip_llm and tier != RoutingTier.L0_SEMANTIC_CACHE):
            log.info("llm_invoker_skipped_human_route", reason=decision.route_reason)
            # Human routing — do NOT call LLM, do NOT publish to outgoing.
            # The Human Takeover service handles this (Sprint 8).
            return

        if tier == RoutingTier.L0_SEMANTIC_CACHE:
            # Deterministic response — no LLM, instant reply
            response_text = self._get_l0_response(decision)
            await self._publish_outbound(
                decision=decision,
                text=response_text,
                llm_response=None,
                latency_ms=0,
            )
            log.info("llm_invoker_l0_response_sent", chars=len(response_text))
            return

        # ── 2b. Item 14: explicit v1 decision — no STT/vision pipeline exists ──
        # Whisper/vision workers were never built (multimodal/__init__.py is a
        # stub; nothing consumes multimodal.audio.v1 / multimodal.vision.v1).
        # Without this guard, a captionless audio/image/video message would
        # fall through to _build_current_message()'s "[audio message — media:
        # ...]" placeholder and the LLM would generate a confident-sounding
        # reply to content it never actually saw. Fail visibly to the
        # customer instead, gated by the same feature flags a future real
        # implementation would flip on.
        if not event.text_content and event.message_type in (
            MessageType.AUDIO, MessageType.IMAGE, MessageType.VIDEO,
        ):
            flag_enabled = (
                settings.feature_multimodal_voice
                if event.message_type == MessageType.AUDIO
                else settings.feature_multimodal_vision
            )
            if not flag_enabled:
                await self._publish_outbound(
                    decision=decision,
                    text=_L0_RESPONSES["multimodal_unsupported"],
                    llm_response=None,
                    latency_ms=0,
                )
                log.info("llm_invoker_multimodal_unsupported_fallback", message_type=str(event.message_type))
                return

        # ── 3. Fetch conversation history from Redis ──────────────────────────
        conversation_id = decision.conversation_id
        history: list[dict[str, Any]] = []
        if conversation_id:
            history = await redis_mgr.get_conversation_history(
                tenant_id, conversation_id, last_n=20
            )

        # Ensure current customer message is the last in history for Gemini
        current_message = self._build_current_message(event)
        gemini_messages = self._history_to_gemini_format(history) + [current_message]

        # ── 4. Build system prompt ────────────────────────────────────────────
        tenant_context = await self._get_tenant_context(tenant_id, decision.session_state)
        system_prompt = self._build_persona(tenant_context)

        # ── 5. RAG context injection (L2) ────────────────────────────────────
        rag_context: str | None = None
        if tier == RoutingTier.L2_RAG and getattr(self, "_rag_enabled", False):
            user_text = event.text_content or ""
            if event.location_latitude:
                user_text = (
                    f"{user_text} الموقع: {event.location_latitude}, "
                    f"{event.location_longitude}"
                ).strip()

            # Derive intent hint from route_reason for knowledge topic filter
            intent_hint: str | None = None
            route_reason = decision.route_reason.lower()
            if "deed" in route_reason or "صك" in route_reason:
                intent_hint = "rega_regulations"
            elif "location" in route_reason or "neighborhood" in route_reason:
                intent_hint = "neighborhoods"

            if user_text:
                rag_context = await rag_retriever.get_rag_context(
                    tenant_id=str(tenant_id),
                    user_message=user_text,
                    include_shared_knowledge=True,
                    intent_hint=intent_hint,
                )

            log.info(
                "rag_context_injected" if rag_context else "rag_context_empty",
                chars=len(rag_context) if rag_context else 0,
                tier=tier,
            )

        # ── 6. Call Gemini, with one immediate retry before giving up ──────────
        # Previously: a timeout silently escalated to human with no message to
        # the customer, and any other exception was re-raised for Kafka's
        # redelivery/DLQ machinery — also customer-silent, and (per a real
        # live-test finding) indistinguishable from the bot just not working.
        # One retry absorbs a transient blip; if that also fails, the customer
        # gets a real natural-language reply instead of nothing, and the
        # conversation is still escalated so a human follows up.
        llm_response = None
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                llm_response = await gemini_client.generate_response(
                    messages=gemini_messages,
                    tier=tier,
                    system_prompt=system_prompt,
                    tenant_context=tenant_context,
                    rag_context=rag_context,
                )
                break
            except Exception as exc:
                last_exc = exc
                log.warning("llm_invoker_call_failed", attempt=attempt + 1, error=str(exc)[:200])

        if llm_response is None:
            log.error("llm_invoker_fallback_after_retry", tier=tier, error=str(last_exc)[:200] if last_exc else None)
            await self._publish_outbound(
                decision=decision,
                text=_L0_RESPONSES["llm_unavailable_fallback"],
                llm_response=None,
                latency_ms=int((asyncio.get_event_loop().time() - start_ns) * 1000),
            )
            await self._publish_escalation_event(decision)
            return

        # Handle safety block
        if llm_response.was_blocked:
            log.warning("llm_invoker_response_blocked_by_safety")
            llm_response.text = (
                "أعتذر، لا يمكنني الإجابة على هذا الاستفسار بشكل مباشر. "
                "يرجى التواصل مع فريقنا للمساعدة. 🤝"
            )

        total_latency_ms = int((asyncio.get_event_loop().time() - start_ns) * 1000)

        # ── 7. Publish OutboundMessage ────────────────────────────────────────
        await self._publish_outbound(
            decision=decision,
            text=llm_response.text,
            llm_response=llm_response,
            latency_ms=total_latency_ms,
        )

        # ── 8. Append AI response to Redis conversation history ───────────────
        if conversation_id:
            await redis_mgr.append_message_to_history(
                tenant_id,
                conversation_id,
                {
                    "role": "model",
                    "content": llm_response.text,
                    "sender_type": "ai_bot",
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    "message_type": "text",
                    "tokens": llm_response.output_tokens,
                    "model": llm_response.model_used,
                },
            )

        # ── 9. Analytics event (fire-and-forget) ─────────────────────────────
        asyncio.create_task(
            self._publish_analytics(decision, llm_response, total_latency_ms)
        )

        # ── 9b. Generic explicit-profile extraction (fire-and-forget) ─────────
        # Runs for every tenant/vertical, not just real estate — see
        # shared/services/customer_profile_extractor.py. Feeds lead_scoring's
        # explicit-profile-data component. Never blocks the reply.
        if decision.customer_id and event.text_content:
            asyncio.create_task(
                self._extract_customer_profile_background(decision)
            )

        log.info(
            "llm_invoker_response_published",
            model=llm_response.model_used,
            input_tokens=llm_response.input_tokens,
            output_tokens=llm_response.output_tokens,
            latency_ms=total_latency_ms,
            truncated=llm_response.was_truncated,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _publish_outbound(
        self,
        *,
        decision: RoutingDecision,
        text: str,
        llm_response: LLMResponse | None,
        latency_ms: int,
    ) -> None:
        """Publish the AI response as an OutboundMessage to Kafka.

        Sprint 14: If voice-note conditions are met, call TTSClient to
        synthesise audio and attach the resulting URL to the message.
        The text transcript is always preserved for Smart Inbox display.
        """
        event = decision.event

        # ── Sprint 14: TTS / Voice Note synthesis ─────────────────────────────
        final_message_type = "text"
        final_media_url: str | None = None

        if settings.feature_voice_notes_enabled and llm_response is not None:
            if _should_use_voice_note(decision, text):
                try:
                    audio_url = await asyncio.wait_for(
                        tts_client.synthesize(
                            text,
                            tenant_id=str(decision.tenant_id),
                        ),
                        timeout=10.0,   # Hard cap — never block Kafka pipeline > 10s
                    )
                    final_message_type = "audio"
                    final_media_url = audio_url
                    logger.info(
                        "tts_voice_note_generated",
                        tenant_id=str(decision.tenant_id),
                        tier=decision.target_tier,
                        mock=tts_client.is_mock,
                        audio_url=audio_url,
                    )
                except (asyncio.TimeoutError, TTSGenerationError, Exception) as exc:
                    # Graceful fallback: send as plain text — never drop the message
                    logger.warning(
                        "tts_synthesis_failed_falling_back_to_text",
                        error=str(exc),
                        exc_type=type(exc).__name__,
                        tenant_id=str(decision.tenant_id),
                    )

        outbound = OutboundMessage(
            tenant_id=decision.tenant_id,
            conversation_id=decision.conversation_id,
            customer_phone=event.customer_phone or event.platform_user_id,
            platform_conversation_id=event.platform_conversation_id,
            text=text,
            message_type=final_message_type,
            media_url=final_media_url,
            source_event_id=event.event_id,
            routing_tier_used=decision.target_tier,
            model_used=llm_response.model_used if llm_response else "L0_deterministic",
            input_tokens=llm_response.input_tokens if llm_response else 0,
            output_tokens=llm_response.output_tokens if llm_response else 0,
            latency_ms=latency_ms,
        )

        await self._outbound_producer.publish(
            topic=settings.kafka_topic_messages_outgoing,
            event=outbound,
            key=OutboundMessage.kafka_key(decision.tenant_id, outbound.customer_phone),
            headers={
                "tenant_id": str(decision.tenant_id),
                "channel": str(event.channel),
                "tier": decision.target_tier,
                "message_type": final_message_type,   # Sprint 14: propagate to dispatcher
            },
        )

    async def _publish_escalation_event(self, decision: RoutingDecision) -> None:
        """Persist human escalation when LLM times out, then notify the inbox."""
        if not decision.conversation_id:
            raise ValueError("Cannot escalate without a persisted conversation")
        async with get_tenant_session(decision.tenant_id) as session:
            result = await session.execute(
                update(Conversation).where(
                    Conversation.conversation_id == decision.conversation_id,
                    Conversation.status == ConversationStatus.AI_ACTIVE,
                ).values(status=ConversationStatus.ESCALATED)
            )
            changed = result.rowcount
        # A human takeover or closure while the model ran must be preserved.
        if not changed:
            return
        await redis_mgr.publish_sse(
            tenant_id=decision.tenant_id,
            event_type="conversation_update",
            data={
                "id": str(decision.conversation_id),
                "status": ConversationStatus.ESCALATED.value,
                "is_ai_active": False,
            },
        )

    async def _publish_analytics(
        self,
        decision: RoutingDecision,
        llm_response: LLMResponse,
        latency_ms: int,
    ) -> None:
        """Publish an analytics event for ClickHouse ingestion."""
        from pydantic import BaseModel as _BM

        class _AnalyticsEvent(_BM):
            event_type: str = "llm_invocation"
            tenant_id: str
            tier: str
            model: str
            input_tokens: int
            output_tokens: int
            latency_ms: int
            channel: str
            ts: str

        evt = _AnalyticsEvent(
            tenant_id=str(decision.tenant_id),
            tier=decision.target_tier,
            model=llm_response.model_used,
            input_tokens=llm_response.input_tokens,
            output_tokens=llm_response.output_tokens,
            latency_ms=latency_ms,
            channel=str(decision.event.channel),
            ts=datetime.now(tz=timezone.utc).isoformat(),
        )
        try:
            await self._analytics_producer.publish(
                topic=settings.kafka_topic_analytics_events,
                event=evt,
            )
        except Exception as exc:
            logger.warning("analytics_publish_failed", error=str(exc))

    async def _extract_customer_profile_background(
        self, decision: RoutingDecision
    ) -> None:
        """Fire-and-forget: extract generic buying signals onto Customer.extracted_profile.

        Pulls the customer's own recent messages from real Postgres rather
        than the Redis conversation-history cache — that cache is only ever
        written with the AI's own replies (see append_message_to_history's
        one call site), never the customer's inbound text, so it would
        always be empty here.

        Never raises into the caller — a failure here must not affect the
        real reply that already went out.
        """
        from sqlalchemy import select
        from src.shared.db.models import Message
        from src.shared.services.customer_profile_extractor import extract_and_persist_customer_profile

        try:
            async with get_tenant_session(decision.tenant_id) as session:
                customer_texts: list[str] = []
                if decision.conversation_id:
                    rows = (
                        await session.execute(
                            select(Message.text_content)
                            .where(
                                Message.conversation_id == decision.conversation_id,
                                Message.sender_type == "customer",
                                Message.text_content.is_not(None),
                            )
                            .order_by(Message.created_at.desc())
                            .limit(8)
                        )
                    ).scalars().all()
                    customer_texts = list(reversed(rows))
                if not customer_texts and decision.event.text_content:
                    customer_texts = [decision.event.text_content]

                merged = await extract_and_persist_customer_profile(
                    session=session,
                    customer_id=decision.customer_id,
                    customer_texts=customer_texts,
                )
            logger.info(
                "customer_profile_extraction_updated",
                tenant_id=str(decision.tenant_id),
                customer_id=str(decision.customer_id),
                has_budget=merged.get("budget_min") is not None or merged.get("budget_max") is not None,
                has_location=merged.get("location") is not None,
                has_need=merged.get("stated_need") is not None,
                urgency=merged.get("urgency"),
            )
        except Exception as exc:
            logger.warning("customer_profile_extraction_background_failed", error=str(exc))

    @staticmethod
    def _get_l0_response(decision: RoutingDecision) -> str:
        """Return the pre-configured L0 deterministic response."""
        intent = str(getattr(decision.event, "interactive_payload", {}) or {})
        if "vcard" in decision.route_reason.lower():
            return _L0_RESPONSES["vcard_confirmation"]
        return _L0_RESPONSES["greeting"]

    @staticmethod
    def _build_current_message(event: Any) -> dict[str, Any]:
        """Build the Gemini-format dict for the current customer message."""
        if event.text_content:
            content = event.text_content
        elif event.media_url:
            content = f"[{event.message_type} message — media: {event.media_url}]"
        elif event.location_latitude:
            content = f"[Location: {event.location_latitude}, {event.location_longitude}]"
        elif event.interactive_payload:
            payload = event.interactive_payload
            content = payload.get("title") or payload.get("body") or "[Interactive]"
        else:
            content = "[رسالة غير نصية]"

        return {"role": "user", "parts": [content]}

    @staticmethod
    def _history_to_gemini_format(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Redis history entries to Gemini message format."""
        result = []
        for msg in history:
            role = "model" if msg.get("sender_type") in ("ai_bot", "model") else "user"
            content = msg.get("content", "")
            if content:
                result.append({"role": role, "parts": [content]})
        return result

    async def _get_tenant_context(
        self,
        tenant_id: uuid.UUID,
        session_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Build the tenant context dict injected into the system prompt.

        Fast path: session_state already contains tenant metadata.
        Cold path: query DB (Sprint 7 — for now return minimal context).
        """
        if session_state and session_state.get("business_name"):
            return {
                "business_name": session_state.get("business_name"),
                "city": session_state.get("city"),
                "fal_license_number": session_state.get("fal_license_number"),
                "agent_name": session_state.get("agent_name"),
            }
        # Fallback: minimal context (will be enriched in Sprint 7 with DB lookup)
        return {"tenant_id": str(tenant_id)}

    @staticmethod
    def _build_persona(tenant_context: dict[str, Any]) -> str:
        """
        Build the Ahmad Al-Sayegh system prompt from tenant context.

        Sprint 15: Delegates fully to persona.get_system_prompt() which
        assembles the 6-section structured prompt:
            Identity → Tenant slot → Tone → Capabilities →
            Guardrails → Escape patterns → Format contract

        The resulting string is injected as Gemini's system_instruction
        via GenerateContentConfig — the highest-priority instruction channel
        in the google-genai SDK.

        Falls back to DEFAULT_SYSTEM_PROMPT (pre-built at import time)
        when tenant_context contains no usable business data, ensuring
        zero latency overhead on the fast path.
        """
        # Fast path: no business metadata — use pre-built default
        if not tenant_context or not any(
            tenant_context.get(k)
            for k in ("business_name", "city", "fal_license_number")
        ):
            return DEFAULT_SYSTEM_PROMPT

        # Slow path: assemble with tenant-specific slot
        return get_system_prompt(tenant_context)


# ══════════════════════════════════════════════════════════════════════════════
# Worker entrypoint
# ══════════════════════════════════════════════════════════════════════════════

async def _main() -> None:
    worker = LLMInvokerWorker()
    await worker.run()


def run() -> None:
    """
    Synchronous entrypoint for process managers.

    Usage:
        python -m src.ai_workers.llm_invoker.worker
        python scripts/run_worker.py llm_invoker
    """
    asyncio.run(_main())


if __name__ == "__main__":
    run()
