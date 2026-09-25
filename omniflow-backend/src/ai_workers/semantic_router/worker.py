"""
ai_workers/semantic_router/worker.py — Semantic Router Worker

The first and fastest AI worker in the pipeline. It consumes every
inbound message from `messages.incoming.v1`, performs lightweight
pre-processing, and routes the message to the appropriate LLM tier.

Position in the pipeline:
    Kafka: messages.incoming.v1
        └─► SemanticRouterWorker (THIS FILE)
              ├─► Resolve tenant_id          (TenantResolver)
              ├─► Load session state         (RedisClientManager)
              ├─► Idempotency guard          (BaseKafkaConsumer)
              ├─► PDPL check → Human route   (if is_processing_restricted)
              ├─► Human-active check         (if is_human_active → skip AI)
              ├─► SemanticClassifier         (L0/L1/L2/L3/VAULT tier decision)
              └─► Publish to llm.routing.v1  (with RoutingDecision attached)

Sprint 6: SemanticClassifier integrated. Full L0→L3 tier assignment active.

Consumer group: omniflow.ai-workers.v1
Input topic:    messages.incoming.v1     (settings.kafka_topic_messages_incoming)
Output topic:   llm.routing.v1          (settings.kafka_topic_llm_routing)

References: SRS §2.3 — AI Processing Pipeline, Sprint 5 spec
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from aiokafka.structs import ConsumerRecord

from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, ConversationStatus, MessageType, RoutingTier
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.kafka.producer import KafkaProducerManager
from src.shared.redis_client.client import redis_mgr
from src.ai_workers.semantic_router.tenant_resolver import TenantResolver
from src.ai_workers.semantic_router.classifier import SemanticClassifier
from src.shared.services.conversation_state import load_conversation_state

logger = structlog.get_logger(__name__)
settings = get_settings()


# ══════════════════════════════════════════════════════════════════════════════
# Routing Decision Model (Sprint 5 stub — expanded in Sprint 6)
# ══════════════════════════════════════════════════════════════════════════════

from pydantic import BaseModel, Field  # noqa: E402


class RoutingDecision(BaseModel):
    """
    Enriched event published to llm.routing.v1 after pre-processing.

    Contains the original CanonicalInboundEvent plus routing metadata
    determined by this worker.
    """
    # Original event (embedded, not referenced by ID, for consumer independence)
    event: CanonicalInboundEvent

    # Resolved identity
    tenant_id: uuid.UUID
    customer_id: uuid.UUID | None = None          # Resolved by IdentityResolver
    conversation_id: uuid.UUID | None = None      # Active conversation UUID

    # Routing decision
    target_tier: str = Field(
        default="L1",
        description="Target LLM tier: L0 | L1 | L2 | L3 | VAULT | HUMAN",
    )
    route_reason: str = Field(
        default="triage",
        description="Human-readable reason for this routing decision",
    )
    skip_llm: bool = Field(
        default=False,
        description="True if the message should bypass LLM entirely",
    )

    # Session state snapshot (to avoid repeated Redis reads downstream)
    session_state: dict[str, Any] | None = None

    # Timing
    router_latency_ms: int = 0
    routed_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_kafka_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def kafka_key(cls, tenant_id: uuid.UUID, platform_user_id: str) -> bytes:
        return f"{tenant_id!s}:{platform_user_id}".encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# SemanticRouterWorker
# ══════════════════════════════════════════════════════════════════════════════

class SemanticRouterWorker(BaseKafkaConsumer):
    """
    Consumes messages.incoming.v1 and routes them to llm.routing.v1.

    Inherits from BaseKafkaConsumer:
      - Manual offset commits
      - Retry with exponential backoff
      - DLQ routing on persistent failure
      - Redis idempotency guard (deduplication)
    """

    def __init__(self) -> None:
        super().__init__(
            topics=[settings.kafka_topic_messages_incoming],
            group_id=settings.kafka_consumer_group_ai_workers,
            max_retries=3,
            retry_backoff_ms=500,
            batch_size=50,
        )
        self._resolver = TenantResolver()
        self._classifier = SemanticClassifier(
            confidence_escalation_threshold=settings.ai_confidence_escalation_threshold
        )
        self._routing_producer = KafkaProducerManager()

    # ── Lifecycle hooks ───────────────────────────────────────────────────────

    async def on_startup(self) -> None:
        """Start Redis and the routing producer alongside the consumer."""
        await redis_mgr.start()
        await self._routing_producer.start()
        logger.info(
            "semantic_router_worker_ready",
            input_topic=settings.kafka_topic_messages_incoming,
            output_topic=settings.kafka_topic_llm_routing,
            group_id=self.group_id,
        )

    async def on_shutdown(self) -> None:
        """Gracefully stop Redis and the routing producer."""
        await self._routing_producer.stop()
        await redis_mgr.stop()
        logger.info("semantic_router_worker_shutdown")

    # ── Core processing ───────────────────────────────────────────────────────

    async def process_message(self, record: ConsumerRecord) -> None:
        """
        Process a single CanonicalInboundEvent from Kafka.

        Pipeline:
          1. Deserialize CanonicalInboundEvent
          2. Resolve tenant_id (Redis → DB)
          3. Load session state from Redis
          4. Determine routing decision (PDPL, human-active, LLM tier)
          5. Publish RoutingDecision to llm.routing.v1
          6. Update session state in Redis (last_seen, conversation_id)
        """
        start_ns = asyncio.get_event_loop().time()

        # ── 1. Deserialize ────────────────────────────────────────────────────
        raw_value = record.value
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")

        try:
            event = CanonicalInboundEvent.model_validate_json(raw_value)
        except Exception as exc:
            logger.error(
                "semantic_router_deserialize_error",
                error=str(exc),
                topic=record.topic,
                offset=record.offset,
            )
            # Don't raise — malformed events go to DLQ via BaseKafkaConsumer
            raise ValueError(f"Cannot deserialize CanonicalInboundEvent: {exc}") from exc

        log = logger.bind(
            event_id=str(event.event_id),
            channel=event.channel,
            message_type=event.message_type,
        )

        # ── 2. Resolve tenant_id ──────────────────────────────────────────────
        tenant_id = await self._resolver.resolve_from_event(event)

        if tenant_id is None:
            log.error(
                "semantic_router_tenant_not_found",
                platform_user_id=event.platform_user_id,
            )
            # Unresolvable → DLQ (raise to trigger BaseKafkaConsumer retry→DLQ)
            raise ValueError(
                f"No tenant found for event {event.event_id}. "
                f"phone_number_id={settings.meta_whatsapp_phone_number_id}"
            )

        # Stamp the event with the resolved tenant_id
        event = event.model_copy(update={"tenant_id": tenant_id})

        # ── 3. Load session state from Redis ──────────────────────────────────
        customer_phone = event.customer_phone or event.platform_user_id
        session = await redis_mgr.get_session_state(tenant_id, customer_phone)
        # Database state takes precedence over cached takeover / privacy flags.
        state = await load_conversation_state(
            tenant_id,
            platform_conversation_id=event.platform_conversation_id,
            channel=str(event.channel),
        )
        session = {**(session or {}), **state}
        event = event.model_copy(update={
            "master_customer_id": uuid.UUID(state["customer_id"]),
            "is_human_active": state["is_human_active"],
            "is_processing_restricted": state["is_processing_restricted"],
            "vcard_state": state["vcard_state"],
        })

        # ── 4. Determine routing decision ─────────────────────────────────────
        decision = await self._make_routing_decision(event, tenant_id, session)

        # ── 5. Publish RoutingDecision to appropriate topic ─────────────────────
        latency_ms = int((asyncio.get_event_loop().time() - start_ns) * 1000)
        decision.router_latency_ms = latency_ms

        target_topic = settings.kafka_topic_llm_routing
        if decision.target_tier == RoutingTier.VAULT_RETRIEVAL:
            target_topic = settings.kafka_topic_vault_retrieval
        elif decision.target_tier == RoutingTier.VCARD_GATEKEEPER:
            target_topic = settings.kafka_topic_vcard_gatekeeper

        await self._routing_producer.publish(
            topic=target_topic,
            event=decision,
            key=RoutingDecision.kafka_key(tenant_id, event.platform_user_id),
            headers={
                "channel": str(event.channel),
                "tenant_id": str(tenant_id),
                "target_tier": decision.target_tier,
                "event_version": event.event_version,
            },
        )

        # ── 6. Update session last_seen ───────────────────────────────────────
        await redis_mgr.patch_session_state(
            tenant_id,
            customer_phone,
            {
                "last_seen": event.event_timestamp.isoformat(),
                "last_channel": str(event.channel),
            },
        )

        log.info(
            "semantic_router_message_routed",
            tenant_id=str(tenant_id),
            target_tier=decision.target_tier,
            route_reason=decision.route_reason,
            skip_llm=decision.skip_llm,
            latency_ms=latency_ms,
        )

    async def _make_routing_decision(
        self,
        event: CanonicalInboundEvent,
        tenant_id: uuid.UUID,
        session: dict[str, Any] | None,
    ) -> RoutingDecision:
        """
        Determine the routing tier for this message.

        Priority order:
          1. PDPL restricted          → HUMAN (mandatory by law)
          2. Human agent active       → HUMAN (bypass AI)
          3. SemanticClassifier L0    → L0 (deterministic, no LLM)
          4. SemanticClassifier VAULT → VAULT (document retrieval)
          5. SemanticClassifier L2    → L2 (RAG)
          6. SemanticClassifier L3    → L3 (negotiation)
          7. SemanticClassifier L1    → L1 (fast triage)
          8. Confidence < threshold   → HUMAN escalation
        """
        base = RoutingDecision(
            event=event,
            tenant_id=tenant_id,
            session_state=session,
            conversation_id=uuid.UUID(session["conversation_id"]) if session and session.get("conversation_id") else None,
            customer_id=uuid.UUID(session["customer_id"]) if session and session.get("customer_id") else None,
            target_tier=RoutingTier.L1_TRIAGE,
            route_reason="default_triage",
        )

        # ── Rule 1: PDPL opt-out → mandatory human routing ────────────────────
        is_restricted = event.is_processing_restricted or (
            session and session.get("is_processing_restricted", False)
        )
        if is_restricted:
            base.target_tier = RoutingTier.HUMAN_ESCALATION
            base.route_reason = "pdpl_processing_restricted"
            base.skip_llm = True
            logger.info(
                "semantic_router_pdpl_route",
                event_id=str(event.event_id),
                tenant_id=str(tenant_id),
            )
            return base

        # ── Rule 2: Human agent already active → bypass AI ────────────────────
        is_human_active = event.is_human_active or (
            session and session.get("is_human_active", False)
        )
        if is_human_active:
            base.target_tier = RoutingTier.HUMAN_ESCALATION
            base.route_reason = "human_agent_active"
            base.skip_llm = True
            logger.debug("semantic_router_human_active", event_id=str(event.event_id))
            return base

        # ── Rules 3-7: SemanticClassifier ─────────────────────────────────────
        # Fetch recent history for context-aware classification
        history: list[dict] = []
        if session and session.get("conversation_id"):
            try:
                conv_id = uuid.UUID(session["conversation_id"])
                history = await redis_mgr.get_conversation_history(
                    tenant_id, conv_id, last_n=10
                )
            except (ValueError, KeyError):
                pass

        classification = await self._classifier.classify(event, history)

        # ── Rule 3: VCard Gatekeeper — one-time first-contact step only ────────
        # The SRS (§5.5.4) actually specifies gating EVERY message behind an
        # explicit "تم/حفظت" confirmation word (state stays AWAITING_VALIDATION,
        # every other message gets only a "limited reply" nudge, not a real
        # answer) — confirmed by reading that section directly, this was not
        # a misreading. A real live test on the Naeem tenant surfaced this as
        # a real product problem: a genuine customer question on their
        # *second* message got intercepted into another VCard/reminder
        # instead of a real AI reply. Per explicit client direction, this is
        # now a deliberate SRS deviation, not a bug fix pretending to match
        # spec: intercept ONLY a customer's genuinely first-ever message
        # (vcard_state == NEW). Every state after that — VCARD_SENT,
        # AWAITING_VALIDATION, REMINDER_1/2, DORMANT, CONTACT_SAVED_VERIFIED —
        # flows straight to the normal AI pipeline. The customer still gets
        # the VCard once; nothing about the AI's usefulness after that is
        # held hostage to them replying a specific keyword first.
        from src.shared.core.enums import CustomerVCardState
        vcard_state = event.vcard_state or (session and session.get("vcard_state")) or CustomerVCardState.NEW

        # WhatsApp-only: the gate is "save our WhatsApp business card as a
        # contact" — meaningless on Instagram/Messenger, where the customer
        # already has a persistent DM thread with the page. Found in the same
        # P0 audit that removed Instagram's ad-hoc auto-reply: without this
        # check, a non-WhatsApp customer's first-ever message was routed here
        # anyway, `vcard_gatekeeper` built a WhatsApp-format VCard, and
        # `outbound_dispatcher` rejected it outright (only sends WhatsApp) —
        # a guaranteed-to-DLQ dead end with `skip_llm=True`, so that customer
        # never got so much as an LLM reply attempt either.
        if (
            settings.feature_vcard_gatekeeper
            and event.channel == Channel.WHATSAPP
            and vcard_state == CustomerVCardState.NEW
        ):
            base.target_tier = RoutingTier.VCARD_GATEKEEPER
            base.skip_llm = True
            base.route_reason = "vcard_gatekeeper_interception"
            logger.info("semantic_router_vcard_intercept", tenant_id=str(tenant_id), vcard_state=vcard_state, reason=base.route_reason)
            return base

        base.target_tier = classification.tier
        base.route_reason = classification.reasoning[:200]  # cap for Kafka header
        base.skip_llm = classification.skip_llm

        # ── Rule 8: Escalate if confidence is below threshold ─────────────────
        if classification.escalate_to_human:
            base.target_tier = RoutingTier.HUMAN_ESCALATION
            base.route_reason = f"low_confidence_escalation ({classification.confidence:.2f})"
            base.skip_llm = True
            logger.warning(
                "semantic_router_confidence_escalation",
                confidence=classification.confidence,
                intent=classification.intent,
                event_id=str(event.event_id),
            )
            return base

        logger.info(
            "semantic_router_classified",
            tenant_id=str(tenant_id),
            tier=base.target_tier,
            intent=str(classification.intent),
            confidence=round(classification.confidence, 3),
            route_reason=base.route_reason[:80],
        )
        return base


# ══════════════════════════════════════════════════════════════════════════════
# Worker entrypoint
# ══════════════════════════════════════════════════════════════════════════════

async def _main() -> None:
    """Async entrypoint — run the SemanticRouterWorker until SIGTERM."""
    worker = SemanticRouterWorker()
    await worker.run()


def run() -> None:
    """
    Synchronous entrypoint for process managers (systemd, K8s, Docker).

    Usage:
        # Direct:
        python -m src.ai_workers.semantic_router.worker

        # Via scripts/run_worker.py:
        python scripts/run_worker.py semantic_router
    """
    asyncio.run(_main())


if __name__ == "__main__":
    run()
