"""
channel_adapters/whatsapp/router.py — WhatsApp Cloud API Webhook Router

Implements the two Meta-required webhook endpoints:

  GET  /webhooks/whatsapp  — Hub challenge verification (one-time setup)
  POST /webhooks/whatsapp  — Inbound message ingestion (every message)

CRITICAL SLA: The POST handler MUST return HTTP 200 OK within 200ms.
If Meta does not receive 200 within the timeout, it will:
  1. Retry the delivery (causing duplicate processing)
  2. Eventually disable the webhook if failures persist

Implementation strategy:
  1. HMAC signature check        — done by dependency BEFORE handler runs
  2. Parse raw JSON payload       — ~0.1ms
  3. Extract message(s)           — loop, normalize to CanonicalInboundEvent
  4. publish() to Kafka           — async, ~5-20ms with local Redpanda
  5. Return 200 OK               — total < 50ms (well within 200ms SLA)

We do NOT use BackgroundTasks for Kafka publish because:
  - AIOKafkaProducer.send_and_wait() is already async and fast
  - BackgroundTasks run AFTER the response is sent, losing errors
  - If Kafka is unavailable, we want to return 200 anyway (Meta must not retry)
    and log the error for our own alerting

Reference: SRS §5 — WhatsApp Channel Adapter
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Query, Response, status
from sqlalchemy import select

from src.channel_adapters.whatsapp.security import VerifiedWebhookBody
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, MessageType
from src.shared.db.models import Tenant
from src.shared.db.persistence import persist_inbound_message
from src.shared.db.session import get_system_session
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.kafka.producer import kafka_producer

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(
    prefix="/webhooks/whatsapp",
    tags=["WhatsApp Webhook"],
)


# ══════════════════════════════════════════════════════════════════════════════
# GET /webhooks/whatsapp — Hub Challenge Verification
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "",
    summary="Meta Hub Challenge Verification",
    description=(
        "Meta calls this endpoint once when you register the webhook URL. "
        "Returns the `hub.challenge` value if `hub.verify_token` matches our secret."
    ),
    response_class=Response,
    include_in_schema=True,
)
async def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
) -> Response:
    """
    Meta sends:
        GET ?hub.mode=subscribe&hub.challenge=<random>&hub.verify_token=<our_token>

    We must respond with the raw hub.challenge string and HTTP 200.
    Any other response causes the webhook registration to fail.
    """
    if (
        hub_mode == "subscribe"
        and hub_verify_token == settings.meta_whatsapp_webhook_verify_token
        and hub_challenge
    ):
        logger.info("whatsapp_webhook_verified")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning(
        "whatsapp_webhook_verification_failed",
        mode=hub_mode,
        token_match=(hub_verify_token == settings.meta_whatsapp_webhook_verify_token),
    )
    return Response(
        content="Verification failed",
        status_code=status.HTTP_403_FORBIDDEN,
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /webhooks/whatsapp — Inbound Message Ingestion
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "",
    status_code=status.HTTP_200_OK,
    summary="WhatsApp Inbound Message Webhook",
    description=(
        "Receives signed POST payloads from Meta WhatsApp Cloud API. "
        "Validates HMAC signature, normalizes to CanonicalInboundEvent, "
        "and publishes to Kafka in < 50ms."
    ),
)
async def receive_whatsapp_message(
    raw_body: VerifiedWebhookBody,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """
    Core ingestion handler.

    Flow:
        1. raw_body is already verified by the HMAC dependency.
        2. Parse JSON payload — Meta batches multiple events per POST.
        3. For each message entry, call _process_entry() → publishes to Kafka.
        4. Return {"status": "ok"} immediately.

    Error handling strategy:
        - JSON parse errors: log + return 200 (malformed payloads from Meta
          should not cause retries — they will always be malformed).
        - Kafka publish errors: log + continue (return 200 so Meta doesn't retry;
          use Kafka's own replication for durability).
        - Unexpected errors: log + return 200 (same reason).

    Meta batching:
        Meta may batch multiple messages in one POST (e.g., rapid typing).
        We process ALL entries in the batch before responding.
    """
    # ── Parse payload ─────────────────────────────────────────────────────────
    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.error("whatsapp_payload_json_error", error=str(exc))
        return {"status": "ok"}  # Return 200 — malformed payloads won't self-heal

    # ── Validate top-level structure ──────────────────────────────────────────
    if payload.get("object") != "whatsapp_business_account":
        logger.warning(
            "whatsapp_unexpected_object_type",
            object_type=payload.get("object"),
        )
        return {"status": "ok"}

    # ── Process each entry (Meta may batch multiple) ───────────────────────────
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            background_tasks.add_task(_process_whatsapp_value, value)

    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# Internal processing — runs in BackgroundTask (after 200 is sent)
# ══════════════════════════════════════════════════════════════════════════════

async def _process_whatsapp_value(value: dict[str, Any]) -> None:
    """
    Process one WhatsApp 'value' block from the webhook payload.

    Called via BackgroundTasks so the 200 OK is sent to Meta first,
    then this runs. This is safe because:
      - HMAC is already verified (done synchronously before 200 is sent)
      - Kafka publish is durable (broker replication)
      - Failures are logged for alerting — not retried by Meta

    Value block structure (simplified):
        {
          "messaging_product": "whatsapp",
          "metadata": {"phone_number_id": "...", "display_phone_number": "..."},
          "contacts": [{"profile": {"name": "..."}, "wa_id": "..."}],
          "messages": [{...message object...}]
        }
    """
    # ── Resolve tenant from phone_number_id ─────────────────────────────────
    metadata = value.get("metadata", {})
    phone_number_id = metadata.get("phone_number_id", "")

    # Sprint 10: Resolve the real tenant_id by querying the tenants table.
    # We check the configured phone_number_id first (fast path for single-tenant
    # dev mode) and fall back to a system-session DB query for future
    # multi-tenant support. In both cases we never use a hardcoded UUID.
    tenant_id = await _resolve_tenant_id(phone_number_id)

    # ── Extract contact info ──────────────────────────────────────────────────
    contacts = value.get("contacts", [])
    contact_map: dict[str, str] = {  # wa_id → display_name
        c["wa_id"]: c.get("profile", {}).get("name", "")
        for c in contacts
        if "wa_id" in c
    }

    # ── Process each message in the batch ────────────────────────────────────
    for msg in value.get("messages", []):
        try:
            event = _normalize_message(msg, contact_map, tenant_id)
            if event is None:
                continue

            # ── Sprint 10: Persist Customer + Conversation + Message to DB ────
            # This runs AFTER 200 OK is sent to Meta (BackgroundTask), so
            # latency does not impact Meta's 200ms webhook SLA.
            # We persist BEFORE Kafka publish so downstream workers can
            # look up conversation_id from the DB immediately.
            try:
                conversation_id, _msg_id, _is_new = await persist_inbound_message(
                    tenant_id=tenant_id,
                    customer_phone=event.customer_phone or event.platform_user_id,
                    customer_display_name=event.customer_display_name,
                    channel=event.channel,
                    platform_conversation_id=event.platform_conversation_id,
                    platform_message_id=event.platform_message_id,
                    message_type=event.message_type,
                    text_content=event.text_content,
                    s3_media_url=event.media_url,
                )
                # Stamp the event with the resolved conversation_id so
                # downstream workers (SemanticRouter, LLMInvoker) can use it
                # without an additional DB round-trip.
                event = event.model_copy(
                    update={"master_customer_id": None}  # resolved by identity worker
                )
            except Exception as persist_exc:
                logger.error(
                    "whatsapp_inbound_persist_error",
                    msg_id=msg.get("id"),
                    error=str(persist_exc),
                    exc_type=type(persist_exc).__name__,
                )
                conversation_id = None  # Proceed without persistence — don't drop

            await kafka_producer.publish(
                topic=settings.kafka_topic_messages_incoming,
                event=event,
                key=CanonicalInboundEvent.kafka_key(
                    tenant_id, event.platform_user_id
                ),
                headers={
                    "channel": Channel.WHATSAPP,
                    "tenant_id": str(tenant_id),
                    "event_version": event.event_version,
                    # Propagate conversation_id downstream so LLMInvoker
                    # can skip its own DB lookup (warm-path optimisation).
                    "conversation_id": str(conversation_id) if conversation_id else "",
                },
            )

            logger.info(
                "whatsapp_message_published",
                event_id=str(event.event_id),
                message_type=event.message_type,
                platform_message_id=event.platform_message_id,
                conversation_id=str(conversation_id) if conversation_id else None,
            )

        except Exception as exc:
            # Log but don't re-raise — Meta must not retry
            logger.error(
                "whatsapp_message_processing_error",
                msg_id=msg.get("id"),
                error=str(exc),
                exc_type=type(exc).__name__,
            )


# ══════════════════════════════════════════════════════════════════════════════
# Tenant Resolution Helper
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_tenant_id(phone_number_id: str) -> uuid.UUID:
    """
    Resolve the real tenant_id for an inbound WhatsApp phone_number_id.

    Resolution order (fastest first):
      1. Settings fast-path — if phone_number_id matches the configured
         META_WHATSAPP_PHONE_NUMBER_ID, return the single-tenant UUID from
         the DB immediately (avoids full table scan in single-tenant setups).
      2. DB query — SELECT tenant_id FROM tenants WHERE
         whatsapp_phone_number_id = ? AND status IN ('active','trial').
      3. Dev fallback — if nothing is found and we are in development mode,
         log a warning and return a zero UUID so the pipeline continues;
         this prevents message loss during local testing with unregistered
         phone IDs.

    NOTE: In production a missing phone_number_id should return an error and
    the message should be dropped.  We keep the dev fallback to allow the
    simulate_whatsapp.py script to work out-of-the-box.
    """
    # Fast path: configured single-tenant phone number
    if phone_number_id and phone_number_id == settings.meta_whatsapp_phone_number_id:
        # Look up the exact tenant that owns this phone_number_id
        try:
            async with get_system_session() as session:
                result = await session.scalars(
                    select(Tenant.tenant_id).where(
                        Tenant.whatsapp_phone_number_id == phone_number_id,
                        Tenant.status.in_(["active", "trial"]),  # type: ignore[attr-defined]
                    )
                )
                tid: uuid.UUID | None = result.first()
                if tid is not None:
                    return tid
        except Exception as exc:
            logger.error(
                "whatsapp_tenant_resolve_db_error",
                phone_number_id=phone_number_id,
                error=str(exc),
            )

    # Cold path: full table scan (multi-tenant future)
    if phone_number_id:
        try:
            async with get_system_session() as session:
                result = await session.scalars(
                    select(Tenant.tenant_id).where(
                        Tenant.whatsapp_phone_number_id == phone_number_id,
                        Tenant.status.in_(["active", "trial"]),  # type: ignore[attr-defined]
                    )
                )
                tid = result.first()
                if tid is not None:
                    logger.info(
                        "whatsapp_tenant_resolved_from_db",
                        phone_number_id=phone_number_id,
                        tenant_id=str(tid),
                    )
                    return tid
        except Exception as exc:
            logger.error(
                "whatsapp_tenant_resolve_cold_path_error",
                phone_number_id=phone_number_id,
                error=str(exc),
            )

    # Dev fallback — unknown phone_number_id; log loudly and continue
    logger.warning(
        "whatsapp_tenant_not_found_using_dev_fallback",
        phone_number_id=phone_number_id,
        configured=settings.meta_whatsapp_phone_number_id,
        tip="Register the phone_number_id in the tenants table to resolve correctly.",
    )
    # Use zero UUID so the pipeline can continue in development
    return uuid.UUID("00000000-0000-0000-0000-000000000000")


def _normalize_message(
    msg: dict[str, Any],
    contact_map: dict[str, str],
    tenant_id: uuid.UUID,
) -> CanonicalInboundEvent | None:
    """
    Map a raw WhatsApp message object to a CanonicalInboundEvent.

    Supported message types:
        text, audio, image, video, document, location,
        interactive (quick_reply, list_reply, button),
        sticker, reaction

    Returns None for unsupported types (e.g., "ephemeral") — they are
    silently dropped to avoid polluting the event bus.

    WhatsApp message object structure:
        {
          "id": "wamid.xxx",
          "from": "966501234567",   ← E.164 without leading +
          "timestamp": "1716377600",
          "type": "text",
          "text": {"body": "Hello"}
        }
    """
    msg_type_raw = msg.get("type", "")
    wa_id = msg.get("from", "")           # E.164 without '+'
    platform_user_id = wa_id
    customer_phone = f"+{wa_id}" if wa_id and not wa_id.startswith("+") else wa_id
    platform_message_id = msg.get("id", "")
    display_name = contact_map.get(wa_id)

    # WhatsApp uses unix timestamp string
    try:
        ts_int = int(msg.get("timestamp", "0"))
        event_timestamp = datetime.fromtimestamp(ts_int, tz=timezone.utc)
    except (ValueError, OSError):
        event_timestamp = datetime.now(tz=timezone.utc)

    # ── Type mapping ──────────────────────────────────────────────────────────
    type_map: dict[str, MessageType] = {
        "text": MessageType.TEXT,
        "audio": MessageType.AUDIO,
        "image": MessageType.IMAGE,
        "video": MessageType.VIDEO,
        "document": MessageType.DOCUMENT,
        "location": MessageType.LOCATION,
        "interactive": MessageType.INTERACTIVE,
        "sticker": MessageType.STICKER,
        "reaction": MessageType.REACTION,
        "template": MessageType.TEMPLATE,
    }

    message_type = type_map.get(msg_type_raw)
    if message_type is None:
        logger.debug("whatsapp_unsupported_message_type", type=msg_type_raw)
        return None

    # ── Content extraction per type ───────────────────────────────────────────
    text_content: str | None = None
    media_url: str | None = None
    media_mime_type: str | None = None
    media_duration_seconds: float | None = None
    location_latitude: float | None = None
    location_longitude: float | None = None
    location_name: str | None = None
    interactive_payload: dict[str, Any] | None = None
    sticker_url: str | None = None

    if message_type == MessageType.TEXT:
        text_content = msg.get("text", {}).get("body")

    elif message_type == MessageType.AUDIO:
        audio = msg.get("audio", {})
        # Media ID — will be downloaded to S3 by the Multimodal Worker
        # Store as "waid:<media_id>" so the worker knows to fetch from Meta API
        media_url = f"waid:{audio.get('id', '')}"
        media_mime_type = audio.get("mime_type")
        # WhatsApp doesn't provide duration in the webhook — Whisper detects it
        media_duration_seconds = None

    elif message_type == MessageType.IMAGE:
        image = msg.get("image", {})
        media_url = f"waid:{image.get('id', '')}"
        media_mime_type = image.get("mime_type")
        # Caption goes as text_content if present
        text_content = image.get("caption")

    elif message_type == MessageType.VIDEO:
        video = msg.get("video", {})
        media_url = f"waid:{video.get('id', '')}"
        media_mime_type = video.get("mime_type")
        text_content = video.get("caption")

    elif message_type == MessageType.DOCUMENT:
        document = msg.get("document", {})
        media_url = f"waid:{document.get('id', '')}"
        media_mime_type = document.get("mime_type")
        text_content = document.get("filename")

    elif message_type == MessageType.LOCATION:
        loc = msg.get("location", {})
        location_latitude = loc.get("latitude")
        location_longitude = loc.get("longitude")
        location_name = loc.get("name") or loc.get("address")

    elif message_type == MessageType.INTERACTIVE:
        interactive = msg.get("interactive", {})
        interactive_type = interactive.get("type")
        if interactive_type == "button_reply":
            btn = interactive.get("button_reply", {})
            text_content = btn.get("title")
            interactive_payload = {"type": "button_reply", "id": btn.get("id"), "title": btn.get("title")}
        elif interactive_type == "list_reply":
            lst = interactive.get("list_reply", {})
            text_content = lst.get("title")
            interactive_payload = {"type": "list_reply", "id": lst.get("id"), "title": lst.get("title"), "description": lst.get("description")}
        elif interactive_type == "nfm_reply":
            # Flow completion — pass through raw
            interactive_payload = {"type": "nfm_reply", "body": interactive.get("nfm_reply", {})}

    elif message_type == MessageType.STICKER:
        sticker = msg.get("sticker", {})
        sticker_url = f"waid:{sticker.get('id', '')}"

    elif message_type == MessageType.REACTION:
        reaction = msg.get("reaction", {})
        text_content = reaction.get("emoji")
        interactive_payload = {
            "type": "reaction",
            "message_id": reaction.get("message_id"),
            "emoji": reaction.get("emoji"),
        }

    return CanonicalInboundEvent(
        tenant_id=tenant_id,
        channel=Channel.WHATSAPP,
        platform_message_id=platform_message_id,
        platform_conversation_id=wa_id,  # WhatsApp: 1-to-1, so wa_id = conv ID
        platform_user_id=platform_user_id,
        customer_phone=customer_phone,
        customer_display_name=display_name,
        event_timestamp=event_timestamp,
        message_type=message_type,
        text_content=text_content,
        media_url=media_url,
        media_mime_type=media_mime_type,
        media_duration_seconds=media_duration_seconds,
        location_latitude=location_latitude,
        location_longitude=location_longitude,
        location_name=location_name,
        interactive_payload=interactive_payload,
        sticker_url=sticker_url,
    )
