"""
gateway/routers/conversations.py — Conversations & Messages API

Sprint 12 — Real-Time Inbox Integration

Endpoints:
    GET  /api/v1/conversations                          → list active conversations
    GET  /api/v1/conversations/{conversation_id}/messages → paginated message history
    POST /api/v1/conversations/{conversation_id}/messages → human agent sends a message
    POST /api/v1/conversations/{conversation_id}/takeover       → human takes over
    POST /api/v1/conversations/{conversation_id}/return-to-ai   → return to AI

SSE:
    GET  /api/v1/stream/dashboard → Server-Sent Events for live inbox updates
        Supported query params (used instead of headers for native EventSource):
            ?token=<jwt>          → validated, exchanged for tenant context
            ?tenant_id=<uuid>     → used in dev / when token approach is not set

All tenant-scoped queries go through the ConversationRepository which relies
on PostgreSQL RLS (SET LOCAL app.current_tenant_id) for row isolation.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.gateway.dependencies import ConversationRepo, CurrentUser, get_current_user, get_tenant_id
from src.shared.db.repository import ConversationRepository
from src.shared.core.enums import Channel, ConversationStatus, TenantUserRole
from src.shared.redis_client.client import redis_mgr
from src.shared.security.jwt import is_token_blacklisted, verify_clerk_token

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Conversations"])


# ══════════════════════════════════════════════════════════════════════════════
# Pydantic Schemas
# ══════════════════════════════════════════════════════════════════════════════

class ConversationOut(BaseModel):
    """
    API-facing shape of a Conversation.

    The ORM Conversation model uses snake_case PK `conversation_id` and
    derives customer details from the joined Customer relationship.
    This schema normalises the output to match the frontend Zustand store's
    `Conversation` interface.
    """
    id:               str
    tenant_id:        str
    customer_phone:   str = ""
    customer_name:    str = ""
    channel:          str
    status:           str
    last_message:     str = ""
    last_message_at:  str = ""
    unread_count:     int = 0
    is_ai_active:     bool
    intent:           str | None   = None
    budget:           float | None = None
    looking_in:       str | None   = None
    property_type:    str | None   = None
    sentiment:        str | None   = None
    ai_confidence:    float | None = None
    # ── Sprint 13: AI Lead Scoring ──────────────────────────────────
    lead_score:       int = 0       # 0–100; computed server-side

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_model(cls, conv: "Conversation") -> "ConversationOut":
        """Convert an ORM Conversation to API shape."""
        # ── Compute lead score (mirrors SQL formula in repository.get_multi) ──
        customer = conv.customer if conv.customer else None
        engagement  = getattr(customer, "engagement_score", None) or 0
        is_vip      = getattr(customer, "is_vip", False)
        msg_count   = min(getattr(conv, "message_count", 0) or 0, 50)
        status_val  = str(conv.status.value if hasattr(conv.status, "value") else conv.status).lower()
        status_bonus = (
            20 if status_val == "escalated"
            else 10 if status_val in ("ai_active", "human_active")
            else 0
        )
        raw_score = engagement * 0.6 + (15 if is_vip else 0) + msg_count * 0.4 + status_bonus
        lead_score = max(0, min(100, round(raw_score)))
        # Legacy databases store this field as naive UTC. Always return an
        # explicit offset so browsers do not interpret it as their local time.
        last_message_at = conv.last_message_at
        if last_message_at is not None and last_message_at.tzinfo is None:
            last_message_at = last_message_at.replace(tzinfo=timezone.utc)

        return cls(
            id              = str(conv.conversation_id),
            tenant_id       = str(conv.tenant_id),
            customer_phone  = getattr(conv.customer, "unified_phone", "") if conv.customer else "",  # noqa: E501
            customer_name   = getattr(conv.customer, "display_name", "") or getattr(conv.customer, "whatsapp_profile_name", "") if conv.customer else "",  # noqa: E501
            channel         = str(conv.channel.value if hasattr(conv.channel, "value") else conv.channel),
            status          = status_val,
            last_message    = "",  # denormalised field — updated by SSE events
            last_message_at = last_message_at.isoformat() if last_message_at else "",
            unread_count    = 0,   # managed client-side via SSE
            is_ai_active    = status_val == ConversationStatus.AI_ACTIVE,
            lead_score      = lead_score,
        )


class MessageOut(BaseModel):
    """
    API-facing shape of a Message.

    ORM field mapping:
        message_id      → id
        text_content    → text
        conversation_id → conversation_id (str)
        llm_routing_tier→ tier
        created_at      → created_at (ISO string)
    """
    id:              str
    conversation_id: str
    sender_type:     str
    text:            str = ""
    message_type:    str  = "text"
    created_at:      str
    is_read:         bool = False
    tier:            str | None   = None
    model:           str | None   = None
    latency_ms:      int | None   = None
    s3_media_url:    str | None   = None
    delivery_status: str | None  = None

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_model(cls, msg: "Message") -> "MessageOut":
        """Convert an ORM Message to API shape."""
        return cls(
            id              = str(msg.message_id),
            conversation_id = str(msg.conversation_id),
            sender_type     = str(msg.sender_type),
            text            = msg.text_content or "",
            message_type    = str(msg.message_type.value if hasattr(msg.message_type, "value") else msg.message_type),
            created_at      = msg.created_at.isoformat() if msg.created_at else "",
            is_read         = False,
            tier            = msg.llm_routing_tier,
            latency_ms      = msg.latency_ms,
            s3_media_url     = msg.s3_media_url,
            delivery_status = msg.delivery_status,
        )


# Import ORM models for type hints only (avoid circular import at module level)
from src.shared.db.models import Conversation, Message  # noqa: E402


class PaginatedConversations(BaseModel):
    items:  list[ConversationOut]
    total:  int
    offset: int
    limit:  int


class PaginatedMessages(BaseModel):
    items:     list[MessageOut]
    total:     int
    offset:    int
    limit:     int
    has_more:  bool = False  # True if there may be older messages before this page


class PropertyRecommendation(BaseModel):
    """One Qdrant-retrieved property suggestion for the inbox context panel.

    `score` is the raw cosine similarity from Qdrant (0-1) — not a bespoke
    weighted match-rule score. There is no separate preference-extraction
    step yet (see get_conversation_recommendations' docstring); this is the
    simplest reasonable default, not a documented SRS requirement.
    """
    title:    str
    price:    float
    area:     float
    district: str
    score:    float


class ConversationNoteCreate(BaseModel):
    body:     str = Field(..., min_length=1, max_length=4000)
    severity: Literal["info", "warning"] = "info"


class ConversationNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    note_id:         uuid.UUID
    conversation_id: uuid.UUID
    author_user_id:  uuid.UUID | None
    body:            str
    severity:        str
    created_at:      datetime


class AppointmentCreate(BaseModel):
    scheduled_at:  datetime = Field(..., description="ISO 8601, UTC")
    location_note: str | None = Field(default=None, max_length=500)


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    appointment_id:  uuid.UUID
    conversation_id: uuid.UUID
    customer_id:     uuid.UUID
    scheduled_at:    datetime
    location_note:   str | None
    status:          str
    created_at:      datetime


class SendMessageRequest(BaseModel):
    text:        str  = Field(..., min_length=1, max_length=4096)
    sender_type: Literal["human_agent"] = "human_agent"


def _require_agent(user: CurrentUser) -> None:
    """Auditors can read conversations, but cannot take over or send messages."""
    if user.role not in (TenantUserRole.ADMIN, TenantUserRole.AGENT):
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Agent role required."})


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/conversations
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/conversations",
    response_model=PaginatedConversations,
    summary="List active conversations",
    description=(
        "Returns all conversations visible to the authenticated tenant. "
        "Optional `channel` filter (whatsapp | instagram | tiktok | x | web). "
        "Optional `sort_by`: 'recent' (default) or 'hot_leads' (AI lead score DESC). "
        "PostgreSQL RLS ensures cross-tenant isolation automatically."
    ),
)
async def list_conversations(
    user:      CurrentUser,
    repo:      ConversationRepo,
    limit:     Annotated[int, Query(ge=1, le=100)] = 50,
    offset:    Annotated[int, Query(ge=0)]          = 0,
    channel:   Annotated[str | None, Query(
        description="Filter by channel: whatsapp | instagram | tiktok | x | web"
    )] = None,
    sort_by:   Annotated[str | None, Query(
        description="Sort order: 'recent' (default) or 'hot_leads'"
    )] = None,
) -> PaginatedConversations:
    conversations, total = await repo.get_multi(
        offset=offset,
        limit=limit,
        channel=channel,
        sort_by=sort_by,
    )
    items = [ConversationOut.from_orm_model(c) for c in conversations]
    return PaginatedConversations(items=items, total=total, offset=offset, limit=limit)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/conversations/{conversation_id}/messages
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=PaginatedMessages,
    summary="Get conversation message history",
    description=(
        "Returns the most recent `limit` messages, oldest-first. Pass "
        "`before` (ISO timestamp — typically the oldest currently-loaded "
        "message's created_at) to page further into the past."
    ),
)
async def get_messages(
    conversation_id: uuid.UUID,
    user:            CurrentUser,
    repo:            ConversationRepo,
    limit:           Annotated[int, Query(ge=1, le=200)] = 100,
    before:          Annotated[datetime | None, Query(description="ISO timestamp cursor for 'load older'")] = None,
) -> PaginatedMessages:
    messages = await repo.get_recent_messages(
        conversation_id=conversation_id, limit=limit, before=before,
    )
    items = [MessageOut.from_orm_model(m) for m in messages]
    # Heuristic, not an exact count: a full page means there MAY be older
    # messages before it. Cheaper than a separate COUNT(*) query, and a false
    # positive just means one harmless extra "load older" click that returns [].
    has_more = len(items) == limit
    return PaginatedMessages(items=items, total=len(items), offset=0, limit=limit, has_more=has_more)


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/conversations/{conversation_id}/messages
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/conversations/{conversation_id}/recommendations",
    response_model=list[PropertyRecommendation],
    summary="Real-estate property suggestions for this conversation",
    description=(
        "Semantic search over the tenant's Qdrant-indexed property listings, "
        "using the conversation's own recent customer messages as the query — "
        "no separate preference-extraction step exists yet, so this reuses "
        "whatever intent/budget/location signal is already present in what "
        "the customer actually wrote. Returns [] if the conversation has no "
        "customer messages yet or no listings score above the threshold."
    ),
)
async def get_conversation_recommendations(
    conversation_id: uuid.UUID,
    user:            CurrentUser,
    repo:            ConversationRepo,
    limit:           Annotated[int, Query(ge=1, le=10)] = 3,
) -> list[PropertyRecommendation]:
    conversation = await repo.get_or_404(conversation_id)

    # Oldest-first page is all get_messages offers; take the tail in Python
    # rather than adding a new reverse-order repository method for one caller.
    messages = await repo.get_messages(conversation_id=conversation_id, offset=0, limit=200)
    customer_texts = [m.text_content for m in messages if m.sender_type == "customer" and m.text_content]
    if not customer_texts:
        return []
    query_text = " ".join(customer_texts[-5:])

    from src.ai_workers.rag_engine.embedder import embedder
    from src.ai_workers.rag_engine.retriever import _map_property_type
    from src.shared.qdrant_client.client import qdrant_mgr

    if not embedder.is_configured:
        embedder.configure()
    if not qdrant_mgr._started:
        await qdrant_mgr.start()

    query_vector = await embedder.embed_query(query_text)
    results = await qdrant_mgr.search_properties(
        conversation.tenant_id, query_vector, limit=limit, score_threshold=0.0,
    )

    recommendations = []
    for point in results:
        p = point.payload or {}
        district = p.get("district") or ""
        city = p.get("city") or ""
        title = f"{_map_property_type(p.get('property_type', ''))} في {district or city or 'موقع غير محدد'}"
        recommendations.append(PropertyRecommendation(
            title=title,
            price=p.get("price_sar") or 0,
            area=p.get("area_sqm") or 0,
            district=district or city or "—",
            score=round(point.score, 3),
        ))
    return recommendations


# ══════════════════════════════════════════════════════════════════════════════
# Internal notes (inbox quick action: "إضافة ملاحظة تحذير")
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/conversations/{conversation_id}/notes",
    response_model=list[ConversationNoteOut],
    summary="List internal notes for a conversation",
)
async def list_conversation_notes(
    conversation_id: uuid.UUID,
    user:            CurrentUser,
    repo:            ConversationRepo,
) -> list[ConversationNoteOut]:
    from src.shared.db.models import ConversationNote

    await repo.get_or_404(conversation_id)  # 404s if not visible to this tenant
    rows = (
        await repo.session.execute(
            select(ConversationNote)
            .where(ConversationNote.conversation_id == conversation_id)
            .order_by(ConversationNote.created_at.desc())
        )
    ).scalars().all()
    return [ConversationNoteOut.model_validate(r) for r in rows]


@router.post(
    "/conversations/{conversation_id}/notes",
    response_model=ConversationNoteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an internal note (never shown to the customer)",
)
async def create_conversation_note(
    conversation_id: uuid.UUID,
    body:            ConversationNoteCreate,
    user:            CurrentUser,
    repo:            ConversationRepo,
) -> ConversationNoteOut:
    from src.shared.db.models import ConversationNote

    await repo.get_or_404(conversation_id)
    note = ConversationNote(
        tenant_id=user.tenant_id,
        conversation_id=conversation_id,
        author_user_id=user.user_id,
        body=body.body.strip(),
        severity=body.severity,
    )
    repo.session.add(note)
    await repo.session.flush()
    await repo.session.refresh(note)
    return ConversationNoteOut.model_validate(note)


# ══════════════════════════════════════════════════════════════════════════════
# Viewing appointments (inbox quick action: "جدولة موعد زيارة")
#
# Deliberately minimal — a structured date/time + location note, not the full
# SRS §5 appointment subsystem (distance-based dispatch routing, CalDAV/Google
# Calendar sync, conflict detection, automated reminders). See
# IMPLEMENTATION_STATUS.md for the scope-cut rationale.
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/conversations/{conversation_id}/appointments",
    response_model=list[AppointmentOut],
    summary="List scheduled viewing appointments for a conversation",
)
async def list_appointments(
    conversation_id: uuid.UUID,
    user:            CurrentUser,
    repo:            ConversationRepo,
) -> list[AppointmentOut]:
    from src.shared.db.models import Appointment

    await repo.get_or_404(conversation_id)
    rows = (
        await repo.session.execute(
            select(Appointment)
            .where(Appointment.conversation_id == conversation_id)
            .order_by(Appointment.scheduled_at.asc())
        )
    ).scalars().all()
    return [AppointmentOut.model_validate(r) for r in rows]


@router.post(
    "/conversations/{conversation_id}/appointments",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a viewing appointment",
)
async def create_appointment(
    conversation_id: uuid.UUID,
    body:            AppointmentCreate,
    user:            CurrentUser,
    repo:            ConversationRepo,
) -> AppointmentOut:
    from src.shared.db.models import Appointment

    conversation = await repo.get_or_404(conversation_id)

    scheduled_at = body.scheduled_at
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    if scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "SCHEDULED_AT_IN_PAST", "message": "scheduled_at must be in the future."},
        )

    appointment = Appointment(
        tenant_id=user.tenant_id,
        conversation_id=conversation_id,
        customer_id=conversation.customer_id,
        created_by_user_id=user.user_id,
        scheduled_at=scheduled_at,
        location_note=body.location_note,
    )
    repo.session.add(appointment)
    await repo.session.flush()
    await repo.session.refresh(appointment)
    return AppointmentOut.model_validate(appointment)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Send a message (human agent)",
)
async def send_message(
    conversation_id: uuid.UUID,
    body:            SendMessageRequest,
    user:            CurrentUser,
    repo:            ConversationRepo,
    background_tasks: BackgroundTasks,
) -> MessageOut:
    _require_agent(user)
    conv = await repo.get_or_404(conversation_id)
    if str(conv.channel) != Channel.WHATSAPP:
        raise HTTPException(status_code=422, detail={"code": "UNSUPPORTED_CHANNEL", "message": "Human delivery is available for WhatsApp."})
    if str(conv.status).lower() != ConversationStatus.HUMAN_ACTIVE or conv.assigned_agent_id != user.user_id:
        raise HTTPException(status_code=409, detail={"code": "TAKEOVER_REQUIRED", "message": "Take over this conversation before sending."})
    message = await repo.create_message(
        conversation_id=conversation_id,
        sender_type="human_agent",
        text=body.text,
        agent_id=user.user_id,
    )
    # PENDING is the durable outbox: Celery publishes after this transaction commits.
    msg_out = MessageOut.from_orm_model(message)
    # Deferred to a background task (runs after the response is sent, i.e.
    # after the tenant session dependency has committed) so SSE subscribers
    # never observe an event for a row that isn't visible yet on a fresh read.
    background_tasks.add_task(
        _publish_sse_event,
        tenant_id=str(user.tenant_id),
        event_type="new_message",
        data=msg_out.model_dump(),
    )
    return msg_out


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/conversations/{conversation_id}/takeover
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/conversations/{conversation_id}/takeover",
    response_model=ConversationOut,
    summary="Human agent takes over conversation",
)
async def takeover_conversation(
    conversation_id: uuid.UUID,
    user:            CurrentUser,
    repo:            ConversationRepo,
    background_tasks: BackgroundTasks,
) -> ConversationOut:
    _require_agent(user)
    conv = await repo.assign_agent(
        conversation_id=conversation_id,
        agent_id=user.user_id,
    )
    conv = await repo.get_with_messages(conversation_id)
    conv_out = ConversationOut.from_orm_model(conv)
    background_tasks.add_task(
        _publish_sse_event,
        tenant_id=str(user.tenant_id),
        event_type="conversation_update",
        data=conv_out.model_dump(),
    )
    return conv_out


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/conversations/{conversation_id}/return-to-ai
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/conversations/{conversation_id}/return-to-ai",
    response_model=ConversationOut,
    summary="Return conversation control to the AI bot",
)
async def return_to_ai(
    conversation_id: uuid.UUID,
    user:            CurrentUser,
    repo:            ConversationRepo,
    background_tasks: BackgroundTasks,
) -> ConversationOut:
    _require_agent(user)
    current = await repo.get_or_404(conversation_id)
    if current.assigned_agent_id not in (None, user.user_id) and user.role != TenantUserRole.ADMIN:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Conversation belongs to another agent."})
    conv = await repo.update_status(
        conversation_id=conversation_id,
        status=ConversationStatus.AI_ACTIVE,
        is_ai_active=True,
    )
    conv = await repo.get_with_messages(conversation_id)
    conv_out = ConversationOut.from_orm_model(conv)
    background_tasks.add_task(
        _publish_sse_event,
        tenant_id=str(user.tenant_id),
        event_type="conversation_update",
        data=conv_out.model_dump(),
    )
    return conv_out


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/stream/dashboard — Server-Sent Events
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/stream/dashboard",
    summary="Real-time inbox SSE stream",
    description=(
        "Server-Sent Events endpoint. "
        "The native EventSource API cannot send custom headers, so the JWT "
        "is passed via `?token=<jwt>`. The backend validates the full token "
        "(signature, expiry, type, blacklist) and extracts tenant_id from the claims. "
        "The tenant is always resolved from the authenticated user."
    ),
    response_class=StreamingResponse,
    include_in_schema=True,
)
async def dashboard_stream(
    request:   Request,
    token:     Annotated[str | None, Query(description="JWT access token (required in production)")] = None,
    tenant_id: Annotated[str | None, Query(description="Tenant UUID — dev-only fallback, ignored in production")] = None,
) -> StreamingResponse:
    """
    Long-lived SSE endpoint.

    Architecture:
      1. Validate auth (JWT token query param or dev tenant_id fallback).
      2. Subscribe to Redis Pub/Sub channel: `sse:{tenant_id}`.
      3. Stream events as SSE-formatted strings.
      4. On client disconnect, unsubscribe and clean up.

    Event format:
        event: new_message\\n
        data: {JSON}\\n\\n

    Events emitted:
        - new_message          → a new WhatsApp/AI/human message arrived
        - conversation_update  → status change (takeover, return-to-ai, closed)
        - typing               → AI typing indicator on/off
        - ping                 → keepalive every 15s to prevent proxy timeouts
    """
    # ── Auth: resolve tenant_id from token or query param ───────────────────
    resolved_tenant_id = await _resolve_tenant_from_request(
        token=token, tenant_id_param=tenant_id
    )
    if not resolved_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Valid token or tenant_id required."},
        )

    channel = f"sse:{resolved_tenant_id}"
    logger.info("sse_client_connected", tenant_id=resolved_tenant_id, channel=channel)

    return StreamingResponse(
        _sse_generator(request=request, channel=channel),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",   # disable Nginx buffering
            "Connection":        "keep-alive",
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _sse_generator(
    request: Request,
    channel: str,
) -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE-formatted strings.

    Uses Redis Pub/Sub via `redis_mgr`. Falls back to a simple polling loop
    if Redis is unavailable (dev without Docker).
    """
    PING_INTERVAL = 15  # seconds

    try:
        redis = await redis_mgr.get_client()
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)

        last_ping = asyncio.get_event_loop().time()

        while True:
            # Check for client disconnect
            if await request.is_disconnected():
                logger.info("sse_client_disconnected", channel=channel)
                break

            # Read a message with a short timeout to allow periodic pings
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

            if msg and msg["type"] == "message":
                raw: bytes = msg["data"]
                try:
                    payload = json.loads(raw)
                    event_type = payload.get("event", "message")
                    data       = json.dumps(payload.get("data", payload))
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("sse_bad_payload", error=str(e))

            # Send a ping every PING_INTERVAL seconds
            now = asyncio.get_event_loop().time()
            if now - last_ping >= PING_INTERVAL:
                yield f"event: ping\ndata: {{}}\n\n"
                last_ping = now

            await asyncio.sleep(0.05)  # yield control to the event loop

    except Exception as exc:
        logger.error("sse_generator_error", error=str(exc))
        yield f"event: error\ndata: {json.dumps({'message': 'Stream error'})}\n\n"
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
        except Exception:
            pass


async def _publish_sse_event(
    tenant_id:  str,
    event_type: str,
    data:       dict,
) -> None:
    """
    Publish an SSE event to Redis Pub/Sub.
    Called by mutation endpoints (send_message, takeover, etc.) so that all
    connected dashboard clients for this tenant receive the update instantly.
    """
    try:
        redis = await redis_mgr.get_client()
        channel = f"sse:{tenant_id}"
        payload = json.dumps({"event": event_type, "data": data})
        await redis.publish(channel, payload)
    except Exception as exc:
        # Non-fatal — the HTTP response is already formed; SSE is best-effort
        logger.warning("sse_publish_failed", event=event_type, error=str(exc))


async def _resolve_tenant_from_request(
    token:           str | None,
    tenant_id_param: str | None,
) -> str | None:
    """Use the same authentication and revocation checks as the REST API."""
    if not token:
        return None
    try:
        user = await get_current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
        return str(await get_tenant_id(user))
    except HTTPException:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/chat/simulate-mock (WIZARD OF OZ SIMULATOR)
# ══════════════════════════════════════════════════════════════════════════════

class MockChatRequest(BaseModel):
    message: str
    tenant_id: str

@router.post(
    "/chat/simulate-mock",
    summary="Wizard of Oz Mock AI Simulator",
)
async def simulate_mock_chat(body: MockChatRequest) -> dict:
    await asyncio.sleep(1.5)
    text = body.message
    
    if "الياسمين" in text or "فيلا" in text:
        response = "أهلاً بك! لدينا فيلا رائعة في حي الياسمين بتشطيب فاخر ومواصفات ذكية، السعر ٤ مليون ريال سعودي. متى يناسبك ترتيب زيارة للمعاينة؟"
    elif "تقرير" in text or "فحص" in text:
        response = "بالطبع، يمكنك الوصول إلى الخزنة الرقمية والتقرير الفني الشامل عبر الرابط التالي: https://pay.omniflow.ai/demo-vault-7a9b"
    else:
        response = "أهلاً بك في شركة النخبة العقارية، كيف يمكنني مساعدتك اليوم؟"
        
    return {"response": response}
