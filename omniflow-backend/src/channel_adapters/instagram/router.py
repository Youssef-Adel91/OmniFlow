"""
channel_adapters/instagram/router.py — Meta Facebook & Instagram Webhook Router

Implements the two Meta-required webhook endpoints:

  GET  /webhooks/meta  — Hub challenge verification (one-time setup)
  POST /webhooks/meta  — Inbound event ingestion (every message/comment)

Meta uses a different top-level `object` (and a different `entry[].id`) per
product, all delivered to this one endpoint:

  object="page"       entry.id = Facebook Page ID
    1. Messenger DMs         → entry[].messaging[]
    2. Page/feed comments    → entry[].changes[]  (field "feed", item "comment")
  object="instagram"  entry.id = Instagram professional ACCOUNT ID (not the Page ID)
    3. Instagram DMs         → entry[].messaging[]
    4. Instagram comments    → entry[].changes[]  (field "comments")

Only these two objects are accepted; anything else is logged and ignored.
Tenant lookup therefore matches entry.id against Tenant.instagram_page_id OR
Tenant.instagram_account_id (see _resolve_tenant_id).

CRITICAL SLA: The POST handler MUST return HTTP 200 OK within 200ms.
If Meta does not receive 200 within the timeout, it will:
  1. Retry the delivery (causing duplicate processing)
  2. Eventually disable the webhook if failures persist

Implementation strategy:
  1. Verify hub token on GET (simple compare)
  2. Verify X-Hub-Signature-256 on POST (HMAC-SHA256 over the raw body,
     using the same META_WEBHOOK_HMAC_SECRET as the WhatsApp adapter —
     Meta signs every webhook delivery for an App the same way regardless
     of product). A real gap found and fixed 2026-09-28: this endpoint had
     no signature check at all, unlike WhatsApp's, so anyone who found the
     URL could POST fake events and have them processed as real ones.
  3. Check object is "page" or "instagram" on POST
  4. Route each entry to _process_messaging_event() or a comment processor
  5. Normalize to CanonicalInboundEvent → publish to Kafka
  6. Return 200 OK immediately (BackgroundTasks handle processing after response)

Pydantic models:
  - MetaMessagingEvent   — entry.messaging[] for DMs
  - MetaFeedChange       — entry.changes[] for comments (Facebook "feed" and Instagram "comments")

Reference: Meta Messenger Platform API, SRS §6 — Instagram/Facebook Adapter
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from sqlalchemy import or_, select

from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, MessageType
from src.shared.db.models import Tenant
from src.shared.db.persistence import persist_inbound_message
from src.shared.db.session import get_system_session
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.kafka.producer import kafka_producer
from src.shared.security import verify_hmac_signature

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(
    prefix="/webhooks/meta",
    tags=["Meta (Messenger & Instagram) Webhook"],
)

_ACCEPTED_OBJECTS: frozenset[str] = frozenset({"page", "instagram"})


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
    The 'value' block inside entry[].changes[].

    Facebook Page comment (field="feed"):
        {
          "item":       "comment",
          "verb":       "add",
          "comment_id": "123456_789012",
          "message":    "Great listing!",
          "from":       {"id": "99999", "name": "John"}
        }

    Instagram comment (field="comments") -- different keys for the same idea:
        {
          "id":        "17858893269000001",
          "text":      "Great listing!",
          "parent_id": "17858893269000000",        # only on replies to a comment
          "from":      {"id": "99999", "username": "john"},
          "media":     {"id": "17900000000000001", "media_product_type": "FEED"}
        }
    """
    item: str = Field(default="")          # "comment", "post", "like", etc.
    verb: str = Field(default="")          # "add", "edited", "remove"
    comment_id: str | None = Field(default=None)
    post_id: str | None = Field(default=None)
    message: str | None = Field(default=None)
    # Instagram "comments" field keys
    id: str | None = Field(default=None)
    text: str | None = Field(default=None)
    parent_id: str | None = Field(default=None)
    media: dict[str, Any] | None = Field(default=None)
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

    object is "page" for Messenger / Facebook Page events and "instagram" for
    Instagram DMs and comments. Instagram events are NOT sent as "page", and
    their entry.id is the Instagram account ID, not the Page ID.
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
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> JSONResponse:
    """
    Core ingestion handler for all Meta page events.

    Flow:
        1. Read raw bytes, verify X-Hub-Signature-256, then log → parse JSON
           → validate with Pydantic.
        2. Validate top-level object is "page" or "instagram".
        3. For each entry, dispatch:
           a. messaging[] events → Messenger / Instagram DMs
           b. changes[] field="feed" → Facebook Page comments
           c. changes[] field="comments" → Instagram comments
        4. Dispatch processing to BackgroundTasks (after 200 OK is sent).
        5. Return {"status": "ok"} immediately — ALWAYS, even on errors.

    Error handling:
        - Invalid signature: HTTP 403 immediately, before any parsing or
          logging of the payload — this is the one case that does NOT
          return 200, since an unsigned request isn't confirmed to be from
          Meta at all (see module docstring: this check didn't exist before
          2026-09-28, unlike the WhatsApp adapter's equivalent).
        - Invalid JSON: log raw bytes + return 200 (avoid Meta retries).
        - Pydantic validation error: log + return 200.
        - Wrong object type: log + return 200.
        - Per-entry processing errors: caught in background tasks, never raised here.
    """
    # ── Step 1: Read raw body and verify the Meta signature BEFORE anything
    # else — no logging, parsing, or processing of a payload we haven't
    # confirmed came from Meta.
    raw_body: bytes = await request.body()
    if not verify_hmac_signature(raw_body, x_hub_signature_256 or "", settings.meta_webhook_hmac_secret):
        logger.warning(
            "meta_webhook_rejected_bad_signature",
            remote=request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INVALID_SIGNATURE",
                "message": (
                    "Webhook signature verification failed. "
                    "Ensure X-Hub-Signature-256 matches the payload HMAC."
                ),
            },
        )

    # ── Step 2: Log what arrived BEFORE parsing ───────────────────────────────
    # We want to see exactly what Meta sent even if JSON/Pydantic parsing
    # fails below. This is the line to correlate with ngrok's request
    # inspector (http://127.0.0.1:4040): every request that reaches this
    # handler with a valid signature produces one `meta_webhook_received`.
    raw_text: str = raw_body.decode("utf-8", errors="replace")
    logger.info(
        "meta_webhook_received",
        body_length=len(raw_body),
        body_preview=_safe_preview(raw_text, 500),
    )

    # ── Step 3: Parse JSON ────────────────────────────────────────────────────
    try:
        body_dict: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as json_exc:
        logger.error(
            "meta_webhook_invalid_json",
            error=str(json_exc),
            raw_preview=_safe_preview(raw_text, 200),
        )
        return JSONResponse(status_code=200, content={"status": "ok"})

    # ── Step 4: Validate with Pydantic ────────────────────────────────────────
    try:
        payload = MetaWebhookPayload.model_validate(body_dict)
    except ValidationError as val_exc:
        logger.error(
            "meta_webhook_validation_error",
            errors=val_exc.errors(),
            raw_preview=_safe_preview(raw_text, 200),
        )
        return JSONResponse(status_code=200, content={"status": "ok"})

    # ── Step 5: Validate top-level object type ────────────────────────────────
    if payload.object not in _ACCEPTED_OBJECTS:
        logger.warning(
            "meta_unexpected_object_type",
            object_type=payload.object,
            accepted=sorted(_ACCEPTED_OBJECTS),
        )
        return JSONResponse(status_code=200, content={"status": "ok"})

    logger.info("meta_webhook_parsed", object=payload.object, entries=len(payload.entry))

    # ── Step 6: Dispatch each entry ───────────────────────────────────────────
    for entry in payload.entry:
        logger.info(
            "meta_webhook_entry",
            object=payload.object,
            entry_id=entry.id,
            messaging=len(entry.messaging),
            changes=len(entry.changes),
        )

        # 1. Messenger / Instagram DMs (entry.messaging)
        for msg_event in entry.messaging:
            # Skip delivery confirmations and read receipts — not actionable
            if msg_event.delivery is not None or msg_event.read is not None:
                logger.info(
                    "meta_messaging_receipt_skipped",
                    object=payload.object,
                    entry_id=entry.id,
                    sender_id=msg_event.sender.get("id"),
                )
                continue

            logger.info(
                "meta_messaging_event_queued",
                object=payload.object,
                entry_id=entry.id,
                sender_id=msg_event.sender.get("id"),
                text_preview=_safe_preview(msg_event.message.text if msg_event.message else "", 80),
            )
            background_tasks.add_task(
                _process_messaging_event, entry.id, msg_event, payload.object
            )

        # 2. Comments (entry.changes)
        for change in entry.changes:
            if change.field == "feed":
                # Facebook Page feed comments
                if change.value.item == "comment" and change.value.verb == "add":
                    logger.info(
                        "meta_feed_comment_queued",
                        object=payload.object,
                        entry_id=entry.id,
                        comment_id=change.value.comment_id,
                    )
                    background_tasks.add_task(
                        _process_comment_event, entry.id, change.value
                    )
                else:
                    logger.info(
                        "meta_feed_change_ignored",
                        entry_id=entry.id,
                        item=change.value.item,
                        verb=change.value.verb,
                    )
            elif change.field == "comments":
                # Instagram post/reel comments
                logger.info(
                    "meta_ig_comment_queued",
                    object=payload.object,
                    entry_id=entry.id,
                    comment_id=change.value.id,
                )
                background_tasks.add_task(
                    _process_instagram_comment_event, entry.id, change.value
                )
            else:
                logger.info(
                    "meta_change_field_ignored",
                    object=payload.object,
                    entry_id=entry.id,
                    field=change.field,
                )

    return JSONResponse(status_code=200, content={"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
# Background processors — run after 200 OK is sent to Meta
# ══════════════════════════════════════════════════════════════════════════════

async def _process_messaging_event(
    entry_id: str,
    event: MetaMessagingEvent,
    source_object: str = "page",
) -> None:
    """
    Process one Messenger / Instagram DM messaging event.

    `entry_id` is the Page ID for object="page" and the Instagram account ID
    for object="instagram" -- _resolve_tenant_id accepts either.

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
            logger.warning("meta_messaging_event_missing_sender", object=source_object, entry_id=entry_id)
            return

        # Defense in depth next to is_echo: never treat our own account as a customer.
        if sender_id == entry_id:
            logger.info("meta_messaging_self_authored_skipped", object=source_object, entry_id=entry_id)
            return

        # ── Determine channel: Instagram IGSID starts with a digit just like
        #    Facebook PSID — we cannot tell them apart at this layer.
        #    For now we use INSTAGRAM for both since the Page may be IG-connected.
        #    The platform_conversation_id (sender_id) is unique per channel anyway.
        channel = Channel.INSTAGRAM

        # ── Resolve tenant ────────────────────────────────────────────────────────
        tenant_id = await _resolve_tenant_id(entry_id)

        # ── Normalize event ───────────────────────────────────────────────────────
        canonical = _normalize_messaging_event(
            event=event,
            sender_id=sender_id,
            channel=channel,
            tenant_id=tenant_id,
        )
        if canonical is None:
            logger.info(
                "meta_messaging_event_ignored",
                object=source_object,
                entry_id=entry_id,
                sender_id=sender_id,
                reason="echo_or_unsupported",
            )
            return

        # ── Persist + Publish ─────────────────────────────────────────────────────
        # The real Kafka pipeline (this publish) is the only reply path. An
        # earlier inline "test auto-reply" that posted straight to Graph API
        # with one global token was removed: it bypassed the VCard gate,
        # per-tenant persona/RAG and credential isolation. Delivery of the
        # reply is outbound_dispatcher's job (Instagram/Messenger DMs and
        # comment replies are wired there, using the tenant's own encrypted
        # Page access token).
        await _persist_and_publish(
            canonical=canonical,
            tenant_id=tenant_id,
            platform_msg_id=canonical.platform_message_id,
            log_label="meta_messaging",
        )
    except Exception as exc:
        safe_exc = repr(exc).encode("ascii", "backslashreplace").decode("ascii")
        logger.exception(
            "meta_messaging_event_unhandled_error",
            object=source_object,
            entry_id=entry_id,
            sender=str(event.sender),
            error=safe_exc,
        )


async def _process_comment_event(
    page_id: str,
    value: MetaFeedChangeValue,
) -> None:
    """
    Process one Facebook Page feed comment creation event.

    Comment events carry:
        - value.from_user["id"]   → commenter's Facebook user ID
        - value.message           → comment text body
        - value.comment_id        → Graph API comment object ID

    Normalized to CanonicalInboundEvent with channel=INSTAGRAM (the shared
    Meta channel) and reply_target_type="comment" (reply via
    POST /{comment_id}/comments). Instagram post comments go through
    _process_instagram_comment_event instead.
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

        # Our own reply to a comment comes back as a new feed event authored
        # by the Page itself -- replying to it would loop forever.
        if sender_id == page_id:
            logger.info("meta_comment_self_authored_skipped", page_id=page_id, comment_id=comment_id)
            return

        tenant_id = await _resolve_tenant_id(page_id)

        logger.info(
            "meta_comment_received",
            page_id=page_id,
            tenant_id=str(tenant_id),
            sender_id=sender_id,
            sender_name=sender_name,
            comment_id=comment_id,
            text_preview=_safe_preview(comment_text, 80),
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
        logger.exception(
            "meta_comment_event_unhandled_error",
            page_id=page_id,
            comment_id=str(value.comment_id),
            error=safe_exc,
        )


async def _process_instagram_comment_event(
    account_id: str,
    value: MetaFeedChangeValue,
) -> None:
    """
    Process one Instagram comment (webhook field "comments", object "instagram").

    `account_id` is the Instagram account ID (entry.id). Replies are posted
    with POST /{comment_id}/replies, so the event is tagged
    reply_target_type="instagram_comment".

    Skipped on purpose (each logs why):
        - authored by our own account: our reply comes back as a new webhook;
          answering it would loop forever.
        - has a parent_id: it is itself a reply in a thread, and Instagram only
          allows replying to top-level comments.
        - empty text.
    """
    try:
        comment_id = value.id or ""
        from_user = value.from_user or {}
        sender_id = str(from_user.get("id", ""))
        username = str(from_user.get("username", ""))
        media_id = (value.media or {}).get("id")
        text = (value.text or "").strip()

        log = logger.bind(
            account_id=account_id,
            comment_id=comment_id,
            sender_id=sender_id,
            media_id=media_id,
        )

        if not comment_id or not sender_id:
            log.warning("ig_comment_missing_ids")
            return
        if sender_id == account_id:
            log.info("ig_comment_self_authored_skipped")
            return
        if value.parent_id:
            log.info("ig_comment_reply_skipped", parent_id=value.parent_id)
            return
        if not text:
            log.info("ig_comment_empty_skipped")
            return

        tenant_id = await _resolve_tenant_id(account_id)
        log.info(
            "ig_comment_received",
            tenant_id=str(tenant_id),
            username=username,
            text_preview=_safe_preview(text, 80),
        )

        canonical = CanonicalInboundEvent(
            tenant_id=tenant_id,
            channel=Channel.INSTAGRAM,
            platform_message_id=comment_id,
            platform_conversation_id=comment_id,   # the comment is the thread
            platform_user_id=sender_id,
            customer_display_name=username or None,
            event_timestamp=datetime.now(tz=timezone.utc),
            message_type=MessageType.TEXT,
            text_content=text,
            reply_target_type="instagram_comment",
        )

        await _persist_and_publish(
            canonical=canonical,
            tenant_id=tenant_id,
            platform_msg_id=comment_id,
            log_label="meta_ig_comment",
        )
    except Exception as exc:
        safe_exc = repr(exc).encode("ascii", "backslashreplace").decode("ascii")
        logger.exception(
            "meta_ig_comment_event_unhandled_error",
            account_id=account_id,
            comment_id=str(value.id),
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


async def _resolve_tenant_id(entry_id: str) -> uuid.UUID:
    """
    Resolve the tenant_id for an inbound Meta webhook `entry.id`.

    That ID is a Facebook Page ID for object="page" events and an Instagram
    professional account ID for object="instagram" events, so both
    Tenant.instagram_page_id and Tenant.instagram_account_id are checked
    (each has a unique index, so a match is never ambiguous). Same status
    filter as the WhatsApp adapter's resolver
    (channel_adapters/whatsapp/router.py).

    Falls back to the dev placeholder only in development mode, so local
    testing with an unregistered ID does not lose messages, matching the
    WhatsApp adapter's own documented dev-fallback behavior. A tenant that
    connected its Page before instagram_account_id existed has no account ID
    stored and must run "Connect with Facebook" once more.
    """
    if entry_id:
        try:
            async with get_system_session() as session:
                row = (
                    await session.execute(
                        select(Tenant.tenant_id, Tenant.instagram_page_id).where(
                            or_(
                                Tenant.instagram_page_id == entry_id,
                                Tenant.instagram_account_id == entry_id,
                            ),
                            Tenant.status.in_(["active", "trial"]),
                        )
                    )
                ).first()
                if row is not None:
                    logger.info(
                        "meta_tenant_resolved_from_db",
                        entry_id=entry_id,
                        matched_on="page_id" if row.instagram_page_id == entry_id else "instagram_account_id",
                        tenant_id=str(row.tenant_id),
                    )
                    return row.tenant_id
        except Exception as exc:
            logger.error("meta_tenant_resolve_db_error", entry_id=entry_id, error=str(exc))

    if settings.is_development:
        logger.warning(
            "meta_tenant_not_found_using_dev_fallback",
            entry_id=entry_id,
            tip="Connect this Page/Instagram account via \"Connect with Facebook\" "
            "(sets Tenant.instagram_page_id and instagram_account_id).",
        )
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    raise ValueError(f"No tenant found for Meta webhook entry.id={entry_id!r}")


def _safe_preview(text: str | None, limit: int) -> str:
    """Truncate and make log-safe for consoles that cannot print Arabic/emoji."""
    return (text or "")[:limit].encode("ascii", "backslashreplace").decode("ascii")


def _ts_to_dt(timestamp_ms: int) -> datetime:
    """Convert Meta's millisecond epoch timestamp to a UTC datetime."""
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return datetime.now(tz=timezone.utc)
