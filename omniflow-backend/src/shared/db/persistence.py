"""
shared/db/persistence.py — Sprint 10: Inbound & Outbound Message Persistence

Centralised persistence helpers invoked by:
  1. WhatsApp adapter (router.py)  → persist the raw customer message.
  2. OutboundDispatcherWorker      → persist the AI-generated reply.

Design principles
─────────────────
• Single Responsibility: one function per write concern.
• RLS-aware: every write goes through get_tenant_session(tenant_id) so
  PostgreSQL Row-Level Security filters rows correctly.
• Idempotent inbound: duplicate platform_message_id is silently ignored
  via the UNIQUE constraint on messages.platform_message_id.
• Non-blocking SSE: Redis publish is fire-and-forget (exceptions logged,
  never raised) so a Redis hiccup cannot block message persistence.
• Zero circular imports: this module only imports from shared.* layers,
  never from gateway.* or ai_workers.*.

References: SRS §6 — Data Models, Sprint 10 specification.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from src.shared.core.enums import Channel, ConversationStatus, MessageType
from src.shared.db.models import Conversation, Customer, Message
from src.shared.db.repository import ConversationRepository, CustomerRepository
from src.shared.db.session import get_tenant_session
from src.shared.redis_client.client import redis_mgr

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════


async def persist_inbound_message(
    *,
    tenant_id: uuid.UUID,
    customer_phone: str,
    customer_display_name: str | None,
    channel: Channel,
    platform_conversation_id: str,
    platform_message_id: str,
    message_type: MessageType | str,
    text_content: str | None,
    s3_media_url: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID, bool]:
    """
    Atomically upsert a Customer, get-or-create a Conversation, and
    append the inbound Message — all in one tenant-scoped transaction.

    Called by the WhatsApp channel adapter immediately after HMAC
    verification, before publishing to Kafka.

    Args:
        tenant_id               — Resolved tenant UUID.
        customer_phone          — E.164 phone ("+966555123456").
        customer_display_name   — WhatsApp profile name (nullable).
        channel                 — Channel enum value (Channel.WHATSAPP).
        platform_conversation_id— The wa_id used as the thread key.
        platform_message_id     — wamid.xxx — unique per platform message.
        message_type            — MessageType enum or its string value.
        text_content            — Plaintext body for text messages.
        s3_media_url            — Pre-signed/raw S3 URL for media payloads.

    Returns:
        (conversation_id, message_id, conversation_is_new)

    Raises:
        Does NOT raise on duplicate platform_message_id (idempotent).
        All other DB errors propagate to the caller.
    """
    msg_type_str = (
        message_type.value
        if hasattr(message_type, "value")
        else str(message_type)
    )

    async with get_tenant_session(tenant_id) as session:
        # ── 1. Customer: upsert by E.164 phone ────────────────────────────────
        customer_repo = CustomerRepository(session)
        customer, customer_created = await customer_repo.get_or_create_by_phone(
            unified_phone=customer_phone,
            tenant_id=tenant_id,
        )

        # Patch display name if we have a richer one from WhatsApp profile
        if customer_display_name and not customer.display_name:
            customer.whatsapp_profile_name = customer_display_name
            session.add(customer)

        # ── 2. Conversation: get-or-create by platform thread ID ──────────────
        conv_repo = ConversationRepository(session)
        conversation, conv_created = await conv_repo.get_or_create_for_platform(
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            channel=channel,
            platform_conversation_id=platform_conversation_id,
        )

        # ── 3. Message: insert (skip silently if already persisted) ──────────
        message = await _safe_add_message(
            conv_repo=conv_repo,
            conversation_id=conversation.conversation_id,
            sender_type="customer",
            message_type=msg_type_str,
            text_content=text_content,
            s3_media_url=s3_media_url,
            platform_message_id=platform_message_id,
        )

    # ── 4. SSE: fire-and-forget publish ──────────────────────────────────────
    if message is not None:
        await _publish_sse(
            tenant_id=tenant_id,
            event_type="new_message",
            data={
                "id": str(message.message_id),
                "conversation_id": str(conversation.conversation_id),
                "sender_type": "customer",
                "text": text_content or "",
                "message_type": msg_type_str,
                "created_at": message.created_at.isoformat()
                if message.created_at
                else datetime.now(tz=timezone.utc).isoformat(),
            },
        )
        if conv_created:
            await _publish_sse(
                tenant_id=tenant_id,
                event_type="conversation_update",
                data={
                    "id": str(conversation.conversation_id),
                    "tenant_id": str(tenant_id),
                    "customer_phone": customer_phone,
                    "customer_name": customer_display_name or customer_phone,
                    "channel": str(channel.value if hasattr(channel, "value") else channel),
                    "status": ConversationStatus.AI_ACTIVE.value,
                    "is_ai_active": True,
                    "last_message_at": datetime.now(tz=timezone.utc).isoformat(),
                },
            )

    log = logger.bind(
        tenant_id=str(tenant_id),
        conversation_id=str(conversation.conversation_id),
        customer_phone=customer_phone,
        conv_created=conv_created,
        customer_created=customer_created,
    )
    log.info(
        "inbound_message_persisted",
        message_id=str(message.message_id) if message else "duplicate",
        platform_message_id=platform_message_id,
    )

    return conversation.conversation_id, (
        message.message_id if message else uuid.UUID(int=0)
    ), conv_created


async def persist_outbound_message(
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    text: str,
    platform_message_id: str | None = None,
    llm_routing_tier: str | None = None,
    tokens_used: int | None = None,
    latency_ms: int | None = None,
    delivery_status: str | None = "PENDING",
    message_id: uuid.UUID | None = None,
    sender_type: str = "ai_bot",
    agent_id: uuid.UUID | None = None,
    message_type: str = "text",
    media_url: str | None = None,
) -> uuid.UUID | None:
    """
    Persist an AI-generated outbound reply to the messages table and
    update the parent Conversation's last_message_at counter.

    Called by OutboundDispatcherWorker after a successful WhatsApp send.

    Args:
        tenant_id           — Tenant UUID (for RLS session).
        conversation_id     — Parent conversation UUID. If None (identity
                              resolution not yet implemented) we log and return
                              None rather than crashing the pipeline.
        text                — AI-generated reply text.
        platform_message_id — wamid returned by the Meta Graph API.
        llm_routing_tier    — "L1" / "L2" / "L3" / "VAULT" / "L0".
        tokens_used         — Output token count from Gemini.
        latency_ms          — Full end-to-end latency in milliseconds.

    Returns:
        Persisted message_id, or None if skipped (no conversation_id).
    """
    if conversation_id is None:
        logger.warning(
            "outbound_persist_skipped_no_conversation",
            tenant_id=str(tenant_id),
            tip=(
                "conversation_id will be populated once Sprint 10 inbound "
                "persistence is wired — outbound message not persisted."
            ),
        )
        return None

    async with get_tenant_session(tenant_id) as session:
        conv_repo = ConversationRepository(session)

        if message_id is not None:
            from sqlalchemy import select
            existing = await session.scalar(
                select(Message).where(Message.message_id == message_id, Message.conversation_id == conversation_id)
            )
            if existing is not None:
                return existing.message_id

        # Guard: conversation must be visible to this tenant (RLS enforced)
        conversation = await conv_repo.get(conversation_id)
        if conversation is None:
            logger.error(
                "outbound_persist_conversation_not_found",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
            )
            return None

        message = await conv_repo.add_message(
            conversation_id=conversation_id,
            sender_type=sender_type,
            agent_id=agent_id,
            message_id=message_id,
            message_type=message_type,
            s3_media_url=media_url,
            text_content=text,
            platform_message_id=platform_message_id,
            llm_routing_tier=llm_routing_tier,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            delivery_status=delivery_status,
        )

    # ── SSE: fire-and-forget ──────────────────────────────────────────────────
    await _publish_sse(
        tenant_id=tenant_id,
        event_type="new_message",
        data={
            "id": str(message.message_id),
            "conversation_id": str(conversation_id),
            "sender_type": sender_type,
            "text": text,
            "message_type": message_type,
            "s3_media_url": media_url,
            "delivery_status": delivery_status,
            "tier": llm_routing_tier,
            "latency_ms": latency_ms,
            "created_at": message.created_at.isoformat()
            if message.created_at
            else datetime.now(tz=timezone.utc).isoformat(),
        },
    )

    logger.info(
        "outbound_message_persisted",
        tenant_id=str(tenant_id),
        conversation_id=str(conversation_id),
        message_id=str(message.message_id),
        tier=llm_routing_tier,
        tokens=tokens_used,
        latency_ms=latency_ms,
        delivery_status=delivery_status,
    )
    return message.message_id


async def update_message_delivery_status(
    *,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    delivery_status: str,
    platform_message_id: str | None = None,
) -> None:
    """
    Update the delivery status (and optionally the platform_message_id) 
    of an existing message.
    """
    from sqlalchemy import select
    from src.shared.db.models import Message

    conversation_id: uuid.UUID | None = None
    async with get_tenant_session(tenant_id) as session:
        stmt = select(Message).where(Message.message_id == message_id)
        result = await session.scalars(stmt)
        msg = result.first()
        if msg is not None:
            msg.delivery_status = delivery_status
            if platform_message_id:
                msg.platform_message_id = platform_message_id
            conversation_id = msg.conversation_id
            session.add(msg)
            # The context manager automatically commits

    if conversation_id is not None:
        # Previously this update was invisible to the frontend until a full
        # reload — a message's PENDING → QUEUED → SENT transition (or a
        # future DELIVERED/READ once the Meta status webhook exists) never
        # reached an already-open inbox.
        await _publish_sse(
            tenant_id=tenant_id,
            event_type="message_status_update",
            data={
                "id": str(message_id),
                "conversation_id": str(conversation_id),
                "delivery_status": delivery_status,
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# Private helpers
# ══════════════════════════════════════════════════════════════════════════════


async def _safe_add_message(
    *,
    conv_repo: ConversationRepository,
    conversation_id: uuid.UUID,
    sender_type: str,
    message_type: str,
    text_content: str | None,
    s3_media_url: str | None,
    platform_message_id: str | None,
) -> Message | None:
    """
    Append a message row, returning None on duplicate platform_message_id.

    The messages.platform_message_id column has a UNIQUE constraint.
    A savepoint rolls back only the attempted message and counter update.
    Other integrity failures still propagate to the caller.
    """
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy import select

    try:
        async with conv_repo.session.begin_nested():
            msg = await conv_repo.add_message(
                conversation_id=conversation_id,
                sender_type=sender_type,
                message_type=message_type,
                text_content=text_content,
                s3_media_url=s3_media_url,
                platform_message_id=platform_message_id,
            )
        return msg
    except IntegrityError:
        if not platform_message_id:
            raise
        existing = await conv_repo.session.scalar(
            select(Message.message_id).where(
                Message.platform_message_id == platform_message_id,
                Message.conversation_id == conversation_id,
            )
        )
        if existing is None:
            raise
        # Savepoint rollback may expire conversation metadata used by the caller.
        conversation = await conv_repo.get_or_404(conversation_id)
        await conv_repo.session.refresh(conversation)
        logger.info(
            "inbound_message_duplicate_skipped",
            platform_message_id=platform_message_id,
            conversation_id=str(conversation_id),
        )
        return None


async def _publish_sse(
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """
    Publish an SSE event to Redis Pub/Sub channel ``sse:{tenant_id}``.

    Delegates to the canonical publish_sse() helper on redis_mgr so that
    both the gateway router (conversations.py) and the persistence service
    use an identical pub/sub channel format.

    Failures are logged as warnings — they must not abort DB writes.
    """
    try:
        await redis_mgr.publish_sse(
            tenant_id=tenant_id,
            event_type=event_type,
            data=data,
        )
    except Exception as exc:
        logger.warning(
            "sse_publish_failed",
            event_type=event_type,
            tenant_id=str(tenant_id),
            error=str(exc),
        )
