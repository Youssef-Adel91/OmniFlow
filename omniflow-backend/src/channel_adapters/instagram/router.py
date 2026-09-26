"""
channel_adapters/instagram/router.py — Meta Facebook & Instagram Webhook Router

Implements the two Meta-required webhook endpoints for the unified "page" object:

  GET  /webhooks/meta  — Hub challenge verification (one-time setup)
  POST /webhooks/meta  — Inbound event ingestion (every message/comment)

Supported event types (all arrive on the same endpoint):
  1. Facebook Messenger DMs   → entry[].messaging[]  (type: "message")
  2. Instagram Direct Messages → entry[].messaging[] (type: "message", app=instagram)
  3. Page/Feed Comments        → entry[].changes[]   (field: "feed", item: "comment")

CRITICAL SLA: The POST handler MUST return HTTP 200 OK within 200ms.
If Meta does not receive 200 within the timeout, it will:
  1. Retry the delivery (causing duplicate processing)
  2. Eventually disable the webhook if failures persist

Implementation strategy:
  1. Verify hub token on GET (simple compare — no HMAC needed for this adapter)
  2. Check object == "page" on POST
  3. Route each entry to _process_messaging_event() or _process_feed_change()
  4. Normalize to CanonicalInboundEvent → publish to Kafka
  5. Return 200 OK immediately (BackgroundTasks handle processing after response)

Pydantic models:
  - MetaMessagingEntry   — entry.messaging[] for DMs
  - MetaFeedChange       — entry.changes[] for comments

Reference: Meta Messenger Platform API, SRS §6 — Instagram/Facebook Adapter
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from sqlalchemy import select

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
    prefix="/webhooks/meta",
    tags=["Meta (Messenger & Instagram) Webhook"],
)


# ══════════════════════════════════════════════════════════════════════════════
# Pydantic Models — Inbound Payload Shapes
# ══════════════════════════════════════════════════════════════════════════════

class MetaMessageContent(BaseModel):
    """The 'message' sub-object inside a messaging event."""
    mid: str = Field(default="", description="Platform message ID")
    text: str | None = Field(default=None)
    attachments: list[dict[str, Any]] | None = Field(default=None)
    # Meta sends is_echo=true for messages sent *by* the page itself.
    # We must filter these out to avoid processing our own outbound replies.
    is_echo: bool = Field(default=False)


class MetaMessagingEvent(BaseModel):
    """
    One element from entry[].messaging[] — covers both Messenger and
    Instagram DMs. Both arrive in identical shape.

    Example payload:
        {
          "sender":    {"id": "12345"},
          "recipient": {"id": "67890"},
          "timestamp": 1716377600000,
          "message":   {"mid": "mid.xxx", "text": "Hello"}
        }
    """
    sender: dict[str, str]
    recipient: dict[str, str]
    timestamp: int = Field(default=0)
    message: MetaMessageContent | None = Field(default=None)
    # postback / quick_reply passthrough
    postback: dict[str, Any] | None = Field(default=None)
    read: dict[str, Any] | None = Field(default=None)
    delivery: dict[str, Any] | None = Field(default=None)


class MetaFeedChangeValue(BaseModel):
    """
    The 'value' block inside entry[].changes[] for field="feed".

    Example for a new comment:
        {
          "item":       "comment",
          "verb":       "add",
          "comment_id": "123456_789012",
          "message":    "Great listing!",
          "from":       {"id": "99999", "name": "John"}
        }
    """
    item: str = Field(default="")          # "comment", "post", "like", etc.
    verb: str = Field(default="")          # "add", "edited", "remove"
    comment_id: str | None = Field(default=None)
    post_id: str | None = Field(default=None)
    message: str | None = Field(default=None)
    # "from" is a reserved Python keyword — use model_config alias
    from_user: dict[str, Any] | None = Field(default=None, alias="from")

    model_config = {"populate_by_name": True}


class MetaFeedChange(BaseModel):
    """One element from entry[].changes[] with field="feed"."""
    field: str = Field(default="")
    value: MetaFeedChangeValue


class MetaEntry(BaseModel):
    """
    One element from the top-level 'entry' array.
    Both DMs and comments arrive here — routing depends on which sub-array
    is populated.
    """
    id: str = Field(default="")           # Page ID
    time: int = Field(default=0)          # Unix timestamp
    messaging: list[MetaMessagingEvent] = Field(default_factory=list)
    changes: list[MetaFeedChange] = Field(default_factory=list)


class MetaWebhookPayload(BaseModel):
    """
    Top-level Meta webhook POST body.

    Meta always sends object="page" for both Messenger and Instagram
    (when using a Facebook Page connected to an Instagram Business account).
    """
    object: str = Field(default="")
    entry: list[MetaEntry] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# GET /webhooks/meta — Hub Challenge Verification
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "",
    summary="Meta Hub Challenge Verification",
    description=(
        "Meta calls this endpoint once when you register the webhook URL. "
        "Returns the `hub.challenge` value if `hub.verify_token` matches "
        "the META_INSTAGRAM_VERIFY_TOKEN environment variable."
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
        and hub_verify_token == settings.meta_instagram_verify_token
        and hub_challenge
    ):
        logger.info("meta_webhook_verified", mode=hub_mode)
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning(
        "meta_webhook_verification_failed",
        mode=hub_mode,
        token_match=(hub_verify_token == settings.meta_instagram_verify_token),
    )
    return Response(
        content="Verification failed",
        status_code=status.HTTP_403_FORBIDDEN,
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /webhooks/meta — Inbound Event Ingestion
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "",
    status_code=status.HTTP_200_OK,
    summary="Meta (Messenger & Instagram) Inbound Webhook",
    description=(
        "Receives POST payloads from Meta for Facebook Messenger DMs, "
        "Instagram Direct Messages, and Page/Feed Comments. "
        "Validates payload structure, normalizes to CanonicalInboundEvent, "
        "and publishes to Kafka via BackgroundTasks."
    ),
)
async def receive_meta_event(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """
    Core ingestion handler for all Meta page events.

    Flow:
        1. Read raw bytes → log → parse JSON → validate with Pydantic.
        2. Validate top-level object == "page".
        3. For each entry, dispatch:
           a. messaging[] events → Messenger / Instagram DMs
           b. changes[] with field="feed" → Page comments
        4. Dispatch processing to BackgroundTasks (after 200 OK is sent).
        5. Return {"status": "ok"} immediately — ALWAYS, even on errors.

    Error handling:
        - Invalid JSON: log raw bytes + return 200 (avoid Meta retries).
        - Pydantic validation error: log + return 200.
        - Wrong object type: log + return 200.
        - Per-entry processing errors: caught in background tasks, never raised here.
    """
    # ── Step 1: Read & log raw body BEFORE any parsing ────────────────────────
    # This is the most important debug step: we see exactly what Meta sent
    # even if Pydantic or JSON parsing fails.
    try:
        raw_body: bytes = await request.body()
        raw_text: str = raw_body.decode("utf-8", errors="replace")
        print(f"=== INCOMING WEBHOOK ===\n{raw_text}")
    except Exception as read_exc:
        print(f"[META WEBHOOK] [ERROR] Failed to read request body: {read_exc}")
        logger.error("meta_webhook_body_read_error", error=str(read_exc))
        return JSONResponse(status_code=200, content={"status": "ok"})

    print(f"[META WEBHOOK] [>>] Raw POST body received:\n{raw_text}")
    logger.info(
        "meta_webhook_raw_payload",
        body_length=len(raw_body),
        body_preview=raw_text[:500],
    )

    # ── Step 2: Parse JSON ────────────────────────────────────────────────────
    try:
        body_dict: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as json_exc:
        print(f"[META WEBHOOK] [ERROR] JSON decode error: {json_exc}")
        logger.error(
            "meta_webhook_invalid_json",
            error=str(json_exc),
            raw_preview=raw_text[:200],
        )
        return JSONResponse(status_code=200, content={"status": "ok"})

    # ── Step 3: Validate with Pydantic ────────────────────────────────────────
    try:
        payload = MetaWebhookPayload.model_validate(body_dict)
    except ValidationError as val_exc:
        print(f"[META WEBHOOK] [ERROR] Pydantic validation error: {val_exc}")
        logger.error(
            "meta_webhook_validation_error",
            errors=val_exc.errors(),
            raw_preview=raw_text[:200],
        )
        return JSONResponse(status_code=200, content={"status": "ok"})

    # ── Step 4: Validate top-level object type ────────────────────────────────
    print(f"[META WEBHOOK] [SUCCESS] object={payload.object!r}, entries={len(payload.entry)}")
    
    if payload.object != "page":
        logger.warning(
            "meta_unexpected_object_type",
            object_type=payload.object,
        )
        return JSONResponse(status_code=200, content={"status": "ok"})

    # ── Step 5: Dispatch each entry ───────────────────────────────────────────
    for entry in payload.entry:
        print(f"[META WEBHOOK]   entry.id={entry.id!r}  messaging={len(entry.messaging)}  changes={len(entry.changes)}")

        # 1. Messenger / Instagram DMs (entry.messaging)
        for msg_event in entry.messaging:
            # Skip delivery confirmations and read receipts — not actionable
            if msg_event.delivery is not None or msg_event.read is not None:
                print(f"[META WEBHOOK]   [SKIP] Skipping delivery/read receipt from sender={msg_event.sender}")
                continue
            
            safe_text = str(msg_event.message.text if msg_event.message else "").encode("ascii", "backslashreplace").decode("ascii")
            print(f"[META WEBHOOK]   [MSG] Queuing messaging event: sender={msg_event.sender.get('id')} text={safe_text!r}")
            background_tasks.add_task(
                _process_messaging_event, entry.id, msg_event
            )

        # 2. Page/Feed Comments (entry.changes with field="feed")
        for change in entry.changes:
            if change.field != "feed":
                print(f"[META WEBHOOK]   [SKIP] Ignoring non-feed change: field={change.field!r}")
                continue
            if change.value.item == "comment" and change.value.verb == "add":
                safe_msg = str(change.value.message)[:80].encode("ascii", "backslashreplace").decode("ascii")
                print(f"[META WEBHOOK]   [COMMENT] Queuing comment event: comment_id={change.value.comment_id!r} msg={safe_msg!r}")
                background_tasks.add_task(
                    _process_comment_event, entry.id, change.value
                )
            else:
                print(f"[META WEBHOOK]   [SKIP] Ignoring feed change item={change.value.item!r} verb={change.value.verb!r}")

    return JSONResponse(status_code=200, content={"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
# Background processors — run after 200 OK is sent to Meta
# ══════════════════════════════════════════════════════════════════════════════

async def _process_messaging_event(
    page_id: str,
    event: MetaMessagingEvent,
) -> None:
    """
    Process one Messenger / Instagram DM messaging event.

    Called via BackgroundTasks so the 200 OK is sent to Meta first.
    Normalizes to CanonicalInboundEvent → persists → publishes to Kafka.

    Supported sub-types:
        - message.text    → MessageType.TEXT
        - message.attachments (image/video/audio/file) → MessageType.IMAGE etc.
        - postback        → MessageType.INTERACTIVE (button click)
    """
    try:
        sender_id = event.sender.get("id", "")
        if not sender_id:
            logger.warning("meta_messaging_event_missing_sender")
            return

        # ── Determine channel: Instagram IGSID starts with a digit just like
        #    Facebook PSID — we cannot tell them apart at this layer.
        #    For now we use INSTAGRAM for both since the Page may be IG-connected.
        #    The platform_conversation_id (sender_id) is unique per channel anyway.
        channel = Channel.INSTAGRAM

        # ── Resolve tenant ────────────────────────────────────────────────────────
        tenant_id = await _resolve_tenant_id(page_id)

        # ── Normalize event ───────────────────────────────────────────────────────
        canonical = _normalize_messaging_event(
            event=event,
            sender_id=sender_id,
            channel=channel,
            tenant_id=tenant_id,
        )
        if canonical is None:
            return

        # ── Persist + Publish ─────────────────────────────────────────────────────
        # NOTE: this used to be followed by an inline "test auto-reply" that
        # called the LLM orchestrator directly and posted straight to Graph API
        # with a single global settings.meta_instagram_page_access_token —
        # removed as a real, live safety issue found during a P0 WhatsApp
        # incident audit: it bypassed the VCard gate, per-tenant persona/RAG,
        # tenant credential isolation (one hardcoded token for every tenant in
        # a multi-tenant product), and the LLM-failure fallback safety net,
        # and could fire a second, uncontrolled reply alongside whatever the
        # real Kafka pipeline below does with the same message. The real
        # pipeline (this publish) is the only reply path now — see
        # IMPLEMENTATION_STATUS.md for why it currently cannot actually
        # deliver a reply for this channel yet (outbound_dispatcher only
        # sends WhatsApp), which is a real, separate, documented gap rather
        # than something worth papering over with the removed shortcut.
        await _persist_and_publish(
            canonical=canonical,
            tenant_id=tenant_id,
            platform_msg_id=canonical.platform_message_id,
            log_label="meta_messaging",
        )
    except Exception as exc:
        safe_exc = repr(exc).encode("ascii", "backslashreplace").decode("ascii")
        print(f"[META WEBHOOK] [ERROR] _process_messaging_event unhandled error: {safe_exc}")
        logger.exception(
            "meta_messaging_event_unhandled_error",
            sender=str(event.sender),
            error=safe_exc,
        )


async def _process_comment_event(
    page_id: str,
    value: MetaFeedChangeValue,
) -> None:
    """
    Process one page/feed comment creation event.

    Comment events carry:
        - value.from_user["id"]   → commenter's Facebook user ID
        - value.message           → comment text body
        - value.comment_id        → Graph API comment object ID

    We normalize to CanonicalInboundEvent with channel=INSTAGRAM
    (comments come via the connected Facebook Page / Instagram profile).
    """
    try:
        from_user = value.from_user or {}
        sender_id = str(from_user.get("id", ""))
        sender_name = str(from_user.get("name", ""))
        comment_text = value.message or ""
        comment_id = value.comment_id or str(uuid.uuid4())

        if not sender_id:
            logger.warning("meta_comment_missing_from_id", value=value.model_dump())
            return

        tenant_id = await _resolve_tenant_id(page_id)

        safe_preview = comment_text[:80].encode("ascii", "backslashreplace").decode("ascii")
        logger.info(
            "meta_comment_received",
            sender_id=sender_id,
            sender_name=sender_name,
            comment_id=comment_id,
            text_preview=safe_preview,
        )

        event_ts = datetime.now(tz=timezone.utc)

        canonical = CanonicalInboundEvent(
            tenant_id=tenant_id,
            channel=Channel.INSTAGRAM,
            platform_message_id=comment_id,
            platform_conversation_id=comment_id,   # comments are standalone threads
            platform_user_id=sender_id,
            customer_display_name=sender_name or None,
            event_timestamp=event_ts,
            message_type=MessageType.TEXT,
            text_content=comment_text or None,
            reply_target_type="comment",
        )

        await _persist_and_publish(
            canonical=canonical,
            tenant_id=tenant_id,
            platform_msg_id=comment_id,
            log_label="meta_comment",
        )
    except Exception as exc:
        safe_exc = repr(exc).encode("ascii", "backslashreplace").decode("ascii")
        print(f"[META WEBHOOK] [ERROR] _process_comment_event unhandled error: {safe_exc}")
        logger.exception(
            "meta_comment_event_unhandled_error",
            comment_id=str(value.comment_id),
            error=safe_exc,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Normalization Helper
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_messaging_event(
    event: MetaMessagingEvent,
    sender_id: str,
    channel: Channel,
    tenant_id: uuid.UUID,
) -> CanonicalInboundEvent | None:
    """
    Map a raw Messenger / Instagram DM event to CanonicalInboundEvent.

    Returns None for echo messages, delivery receipts, and unsupported types.

    Messaging event payload shape:
        {
          "sender":    {"id": "12345"},
          "recipient": {"id": "67890"},
          "timestamp": 1716377600000,
          "message":   {
              "mid": "mid.xxx",
              "text": "Hello world",
              "is_echo": false
          }
        }
    """
    msg = event.message

    # ── Handle postback (button clicks) ──────────────────────────────────────
    if msg is None and event.postback is not None:
        postback = event.postback
        event_ts = _ts_to_dt(event.timestamp)
        return CanonicalInboundEvent(
            tenant_id=tenant_id,
            channel=channel,
            platform_message_id=f"postback:{sender_id}:{event.timestamp}",
            platform_conversation_id=sender_id,
            platform_user_id=sender_id,
            event_timestamp=event_ts,
            message_type=MessageType.INTERACTIVE,
            text_content=postback.get("title") or postback.get("payload"),
            interactive_payload={
                "type": "postback",
                "title": postback.get("title"),
                "payload": postback.get("payload"),
                "referral": postback.get("referral"),
            },
        )

    if msg is None:
        # Neither message nor postback — skip silently
        return None

    # ── Skip echo messages (sent by the page itself) ──────────────────────────
    # Meta sets is_echo=true on messages sent by the page — these must be
    # ignored to avoid processing our own outbound replies as inbound events.
    # NOTE: Checking mid.startswith("echo") is unreliable; is_echo is canonical.
    if msg.is_echo:
        return None

    event_ts = _ts_to_dt(event.timestamp)
    platform_message_id = msg.mid or f"meta:{sender_id}:{event.timestamp}"

    safe_preview = (msg.text or "")[:80].encode("ascii", "backslashreplace").decode("ascii")
    logger.info(
        "meta_dm_received",
        sender_id=sender_id,
        channel=channel,
        mid=platform_message_id,
        text_preview=safe_preview,
    )

    # ── Text message ──────────────────────────────────────────────────────────
    if msg.text:
        return CanonicalInboundEvent(
            tenant_id=tenant_id,
            channel=channel,
            platform_message_id=platform_message_id,
            platform_conversation_id=sender_id,   # 1-to-1 thread = sender
            platform_user_id=sender_id,
            event_timestamp=event_ts,
            message_type=MessageType.TEXT,
            text_content=msg.text,
        )

    # ── Attachment messages ───────────────────────────────────────────────────
    if msg.attachments:
        attachment = msg.attachments[0]  # take first attachment
        att_type = attachment.get("type", "")
        payload = attachment.get("payload", {})
        media_url = payload.get("url") or payload.get("sticker_id")

        type_map: dict[str, MessageType] = {
            "image": MessageType.IMAGE,
            "video": MessageType.VIDEO,
            "audio": MessageType.AUDIO,
            "file":  MessageType.DOCUMENT,
        }
        message_type = type_map.get(att_type, MessageType.IMAGE)

        return CanonicalInboundEvent(
            tenant_id=tenant_id,
            channel=channel,
            platform_message_id=platform_message_id,
            platform_conversation_id=sender_id,
            platform_user_id=sender_id,
            event_timestamp=event_ts,
            message_type=message_type,
            media_url=str(media_url) if media_url else None,
        )

    # ── Unknown / empty message ───────────────────────────────────────────────
    logger.debug(
        "meta_unsupported_message_format",
        mid=platform_message_id,
        has_text=bool(msg.text),
        has_attachments=bool(msg.attachments),
    )
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Shared Utilities
# ══════════════════════════════════════════════════════════════════════════════

async def _persist_and_publish(
    canonical: CanonicalInboundEvent,
    tenant_id: uuid.UUID,
    platform_msg_id: str,
    log_label: str,
) -> None:
    """
    Persist the event to the DB and publish to Kafka.
    Errors are logged but never re-raised — Meta must not retry.
    """
    conversation_id: uuid.UUID | None = None

    # ── Persist ───────────────────────────────────────────────────────────────
    try:
        conversation_id, _msg_id, _is_new = await persist_inbound_message(
            tenant_id=tenant_id,
            customer_phone=canonical.customer_phone or canonical.platform_user_id,
            customer_display_name=canonical.customer_display_name,
            channel=canonical.channel,
            platform_conversation_id=canonical.platform_conversation_id,
            platform_message_id=canonical.platform_message_id,
            message_type=canonical.message_type,
            text_content=canonical.text_content,
            s3_media_url=canonical.media_url,
        )
    except Exception as exc:
        logger.error(
            f"{log_label}_persist_error",
            platform_msg_id=platform_msg_id,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        # Intentional: continue to Kafka publish even if DB persist failed.
        # The AI worker will still process the event; conversation_id header
        # will be empty string ("") to signal missing DB record.

    # ── Publish to Kafka ──────────────────────────────────────────────────────
    try:
        await kafka_producer.publish(
            topic=settings.kafka_topic_messages_incoming,
            event=canonical,
            key=CanonicalInboundEvent.kafka_key(
                tenant_id, canonical.platform_user_id
            ),
            headers={
                "channel": canonical.channel,
                "tenant_id": str(tenant_id),
                "event_version": canonical.event_version,
                "conversation_id": str(conversation_id) if conversation_id else "",
            },
        )
        logger.info(
            f"{log_label}_published",
            event_id=str(canonical.event_id),
            message_type=canonical.message_type,
            platform_user_id=canonical.platform_user_id,
            conversation_id=str(conversation_id) if conversation_id else None,
        )
    except Exception as exc:
        logger.error(
            f"{log_label}_kafka_publish_error",
            event_id=str(canonical.event_id),
            error=str(exc),
            exc_type=type(exc).__name__,
        )


async def _resolve_tenant_id(page_id: str) -> uuid.UUID:
    """
    Resolve the tenant_id for an inbound Meta page_id.

    Real per-tenant lookup, mirroring the WhatsApp adapter's own resolver
    (channel_adapters/whatsapp/router.py): a real gap found during a P0
    channel audit was that this always returned a dev placeholder zero UUID
    keyed off a single global settings.meta_instagram_page_id -- meaning
    every tenant's Instagram-connected page resolved to the same tenant (or
    none). Now queries Tenant.instagram_page_id (migration
    0015_tenant_instagram_creds), same status filter as WhatsApp's resolver.

    Falls back to the dev placeholder only in development mode, so local
    testing with an unregistered page_id does not lose messages, matching
    the WhatsApp adapter's own documented dev-fallback behavior.
    """
    if page_id:
        try:
            async with get_system_session() as session:
                tid = await session.scalar(
                    select(Tenant.tenant_id).where(
                        Tenant.instagram_page_id == page_id,
                        Tenant.status.in_(["active", "trial"]),
                    )
                )
                if tid is not None:
                    logger.info("meta_tenant_resolved_from_db", page_id=page_id, tenant_id=str(tid))
                    return tid
        except Exception as exc:
            logger.error("meta_tenant_resolve_db_error", page_id=page_id, error=str(exc))

    if settings.is_development:
        logger.warning(
            "meta_tenant_not_found_using_dev_fallback",
            page_id=page_id,
            tip="Set Tenant.instagram_page_id for this page in the tenant's onboarding/settings.",
        )
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    raise ValueError(f"No tenant found for Instagram/Messenger page_id={page_id!r}")


def _ts_to_dt(timestamp_ms: int) -> datetime:
    """Convert Meta's millisecond epoch timestamp to a UTC datetime."""
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return datetime.now(tz=timezone.utc)
