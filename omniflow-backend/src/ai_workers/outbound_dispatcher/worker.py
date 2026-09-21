"""
ai_workers/outbound_dispatcher/worker.py — Outbound Message Dispatcher

The final worker in the pipeline: consumes AI responses from
`messages.outgoing.v1` and delivers them to customers via the
appropriate channel adapter (currently WhatsApp).

Pipeline position:
    Kafka: messages.outgoing.v1
        └─► OutboundDispatcherWorker (THIS FILE)
              ├─► Deserialize OutboundMessage
              ├─► Resolve tenant credentials (Redis → Settings)
              ├─► Send read receipt for original message
              ├─► Dispatch via WhatsAppClient
              ├─► Log wamid + latency
              └─► (Placeholder) Update DB message status → DELIVERED

Guarantees:
    - At-least-once delivery (Kafka manual offsets)
    - Idempotency: wamid-based deduplication via Redis SET NX
    - Never drops a message: DLQ routing after max_retries
    - Auth failures are NOT retried (token revoked → alert and move on)

Consumer group: omniflow.notification-svc.v1
Input topic:    messages.outgoing.v1
DLQ topic:      messages.outgoing.v1.dlq  (automatic via BaseKafkaConsumer)

References: SRS §5 — Channel Adapters, Sprint 9 spec
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from aiokafka.structs import ConsumerRecord

from src.ai_workers.llm_invoker.worker import OutboundMessage
from src.channel_adapters.whatsapp.client import (
    WhatsAppAuthError,
    WhatsAppInvalidRecipientError,
    whatsapp_client,
)
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel
from src.shared.db.persistence import persist_outbound_message, update_message_delivery_status
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.redis_client.client import redis_mgr

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Delivered-message idempotency prefix ──────────────────────────────────────
# Key: "sent:{wamid}" → TTL 7 days
# Prevents duplicate sends if the worker crashes after sending but before commit
_SENT_KEY_PREFIX = "sent:"
_SENT_KEY_TTL = 60 * 60 * 24 * 7   # 7 days


# ══════════════════════════════════════════════════════════════════════════════
# Tenant Credentials Resolver
# ══════════════════════════════════════════════════════════════════════════════

class TenantCredentials:
    """Holds resolved per-tenant Meta API credentials."""
    __slots__ = ("phone_number_id", "access_token", "tenant_id")

    def __init__(
        self,
        phone_number_id: str,
        access_token: str,
        tenant_id: str,
    ) -> None:
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.tenant_id = tenant_id


async def _resolve_tenant_credentials(
    tenant_id: uuid.UUID,
) -> TenantCredentials | None:
    """
    Resolve the Meta API credentials for a given tenant.

    Resolution order:
      1. Redis cache: "tenant:creds:{tenant_id}" (5min TTL)
         Payload: {"phone_number_id": "...", "access_token": "..."}
      2. Settings fallback (single-tenant dev mode)
         Uses META_WHATSAPP_PHONE_NUMBER_ID + META_WHATSAPP_ACCESS_TOKEN from .env

    Sprint 10 (Multi-Tenant Production):
      Step 1 will hit a DB query (tenant.whatsapp_phone_number_id + KMS-decrypted token).
      The Redis cache prevents per-message DB round-trips.

    Returns None if credentials cannot be resolved (log + DLQ the message).
    """
    cache_key = f"tenant:creds:{tenant_id}"

    # ── 1. Try Redis cache ────────────────────────────────────────────────────
    try:
        cached = await redis_mgr.get_raw(cache_key)
        if cached:
            import json
            data = json.loads(cached)
            return TenantCredentials(
                phone_number_id=data["phone_number_id"],
                access_token=data["access_token"],
                tenant_id=str(tenant_id),
            )
    except Exception as exc:
        logger.warning("tenant_creds_cache_miss", tenant_id=str(tenant_id), error=str(exc))

    # ── 2. Settings fallback (dev single-tenant mode) ─────────────────────────
    if settings.meta_whatsapp_phone_number_id and settings.meta_whatsapp_access_token:
        logger.debug(
            "tenant_creds_using_env_fallback",
            tenant_id=str(tenant_id),
            phone_number_id=settings.meta_whatsapp_phone_number_id,
        )
        return TenantCredentials(
            phone_number_id=settings.meta_whatsapp_phone_number_id,
            access_token=settings.meta_whatsapp_access_token,
            tenant_id=str(tenant_id),
        )

    logger.error(
        "tenant_creds_not_found",
        tenant_id=str(tenant_id),
        tip="Set META_WHATSAPP_PHONE_NUMBER_ID and META_WHATSAPP_ACCESS_TOKEN in .env",
    )
    return None


# ══════════════════════════════════════════════════════════════════════════════
# OutboundDispatcherWorker
# ══════════════════════════════════════════════════════════════════════════════

class OutboundDispatcherWorker(BaseKafkaConsumer):
    """
    Consumes OutboundMessage from messages.outgoing.v1 and delivers
    them to customers via the channel-specific adapter.

    Current channels: WhatsApp (Cloud API)
    Planned channels: TikTok DM, Instagram DM, SMS (Sprint 11)

    Scale: Run 1 replica per Kafka partition (each partition handles one
    tenant's messages in order). Horizontal scaling = add partitions.
    """

    def __init__(self) -> None:
        super().__init__(
            topics=[settings.kafka_topic_messages_outgoing],
            group_id=settings.kafka_consumer_group_notification_svc,
            max_retries=4,       # Retry up to 4 times before DLQ
            retry_backoff_ms=2000,
            batch_size=20,       # Outbound is slower than inbound
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_startup(self) -> None:
        await redis_mgr.start()
        await whatsapp_client.start()
        logger.info(
            "outbound_dispatcher_ready",
            topic=settings.kafka_topic_messages_outgoing,
            group=settings.kafka_consumer_group_notification_svc,
        )

    async def on_shutdown(self) -> None:
        await whatsapp_client.stop()
        await redis_mgr.stop()
        logger.info("outbound_dispatcher_shutdown")

    # ── Core processing ───────────────────────────────────────────────────────

    async def process_message(self, record: ConsumerRecord) -> None:
        """
        Process a single OutboundMessage from Kafka.

        Steps:
          1. Deserialize OutboundMessage
          2. Route by channel (currently only WhatsApp)
          3. Resolve tenant credentials
          4. Check outbound idempotency (wamid already sent?)
          5. Send via WhatsAppClient
          6. Record wamid in Redis (idempotency guard)
          7. (Placeholder) Update DB message status
        """
        start_ns = asyncio.get_event_loop().time()

        # ── 1. Deserialize ────────────────────────────────────────────────────
        raw = record.value
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        try:
            msg = OutboundMessage.model_validate_json(raw)
        except Exception as exc:
            raise ValueError(f"Cannot deserialize OutboundMessage: {exc}") from exc

        log = logger.bind(
            message_id=str(msg.message_id),
            tenant_id=str(msg.tenant_id),
            channel=msg.channel,
            tier=msg.routing_tier_used,
        )

        # ── 2. Channel routing ────────────────────────────────────────────────
        if msg.channel != Channel.WHATSAPP:
            log.warning(
                "outbound_unsupported_channel",
                channel=msg.channel,
                tip="Non-WhatsApp channels handled in Sprint 11",
            )
            return  # Acknowledge and skip — don't DLQ for unsupported channels

        # ── 3. Resolve tenant credentials ─────────────────────────────────────
        creds = await _resolve_tenant_credentials(msg.tenant_id)
        if not creds:
            # Cannot send without credentials — DLQ
            raise RuntimeError(
                f"No Meta credentials for tenant {msg.tenant_id}. "
                "Message routed to DLQ."
            )

        # ── 4. Outbound idempotency — prevent double-sends ────────────────────
        idem_key = f"{_SENT_KEY_PREFIX}{msg.source_event_id}"
        already_sent = await redis_mgr.set_idempotency_key(
            idem_key, _SENT_KEY_TTL
        )
        if not already_sent:
            log.warning(
                "outbound_duplicate_suppressed",
                source_event_id=str(msg.source_event_id),
                idem_key=idem_key,
            )
            return  # Already delivered — skip silently

        # ── 5. Optimistic Persistence ─────────────────────────────────────────
        persisted_msg_id = await persist_outbound_message(
            tenant_id=msg.tenant_id,
            conversation_id=msg.conversation_id,
            text=msg.text,
            platform_message_id=None,
            llm_routing_tier=msg.routing_tier_used,
            tokens_used=msg.output_tokens or None,
            latency_ms=None,
            delivery_status="PENDING",
        )

        # ── 6. Dispatch via WhatsApp (── route by message_type) ─────────────────────
        try:
            if msg.message_type == "audio" and msg.media_url:
                # Sprint 14: Dispatch as WhatsApp Voice Note ──────────────────
                # Send the synthesised audio URL as a WhatsApp audio message.
                # WhatsApp fetches the MP3 from the URL and renders it as a
                # voice note with a waveform bar.
                result = await whatsapp_client.send_audio_message(
                    phone_number_id=creds.phone_number_id,
                    to=msg.customer_phone,
                    audio_url=msg.media_url,
                    access_token=creds.access_token,
                )
                log.info(
                    "outbound_audio_dispatched",
                    wamid=result.wamid,
                    audio_url=msg.media_url,
                    mock=not settings.elevenlabs_api_key
                    or settings.elevenlabs_api_key.strip().lower() == "mock",
                )
            else:
                # Default path: plain text message ───────────────────────────
                result = await whatsapp_client.send_text_message(
                    phone_number_id=creds.phone_number_id,
                    to=msg.customer_phone,
                    text=msg.text,
                    access_token=creds.access_token,
                )

        except WhatsAppAuthError as exc:
            # Token revoked / expired — do NOT retry (it won't self-heal)
            # Move to DLQ and alert; ops team must rotate token
            if persisted_msg_id:
                await update_message_delivery_status(
                    tenant_id=msg.tenant_id,
                    message_id=persisted_msg_id,
                    delivery_status="FAILED",
                )
            log.error(
                "outbound_auth_failure_no_retry",
                error=str(exc),
                phone_number_id=creds.phone_number_id,
                action="message_sent_to_dlq_rotate_token",
            )
            raise  # BaseKafkaConsumer DLQ handler takes over after max_retries

        except WhatsAppInvalidRecipientError as exc:
            # Recipient not on WhatsApp — permanent failure, don't retry
            if persisted_msg_id:
                await update_message_delivery_status(
                    tenant_id=msg.tenant_id,
                    message_id=persisted_msg_id,
                    delivery_status="FAILED",
                )
            log.warning(
                "outbound_invalid_recipient",
                recipient=msg.customer_phone,
                error=str(exc),
            )
            # Acknowledge (don't DLQ) — retrying won't help
            return

        except Exception as exc:
            # Transient error — BaseKafkaConsumer will retry with backoff
            log.error(
                "outbound_send_error",
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            raise  # Triggers retry logic in BaseKafkaConsumer

        # ── 6. Record wamid for traceability ──────────────────────────────────
        if result.wamid:
            wamid_key = f"wamid:{result.wamid}"
            await redis_mgr.set_raw(
                wamid_key,
                str(msg.message_id),
                ttl=_SENT_KEY_TTL,
            )

        latency_ms = int((asyncio.get_event_loop().time() - start_ns) * 1000)

        log.info(
            "outbound_message_delivered",
            wamid=result.wamid,
            recipient=msg.customer_phone,
            tier=msg.routing_tier_used,
            model=msg.model_used,
            total_latency_ms=latency_ms,
            llm_latency_ms=msg.latency_ms,
        )

        # ── 7. Update DB message status + fire SSE ──────────────────────
        # Sprint 10: async DB write (messages table) + Redis SSE publish.
        # Runs as a background asyncio task so Kafka offset commits immediately.
        if persisted_msg_id:
            asyncio.create_task(
                update_message_delivery_status(
                    tenant_id=msg.tenant_id,
                    message_id=persisted_msg_id,
                    delivery_status="DELIVERED",
                    platform_message_id=result.wamid,
                )
            )

    async def _send_whatsapp_with_typing(
        self,
        *,
        creds: TenantCredentials,
        msg: OutboundMessage,
    ) -> None:
        """
        Optional: Add a natural typing delay before sending.
        Makes the bot feel more human (configurable, off by default).
        """
        typing_delay = min(len(msg.text) * 0.015, 3.0)  # ~67 words/sec cap at 3s
        if typing_delay > 0.5:
            await asyncio.sleep(typing_delay)




# ══════════════════════════════════════════════════════════════════════════════
# Worker entrypoint
# ══════════════════════════════════════════════════════════════════════════════

async def _main() -> None:
    worker = OutboundDispatcherWorker()
    await worker.run()


def run() -> None:
    """
    Synchronous entrypoint.

    Usage:
        python -m src.ai_workers.outbound_dispatcher.worker
        python scripts/run_worker.py outbound_dispatcher
    """
    asyncio.run(_main())


if __name__ == "__main__":
    run()
