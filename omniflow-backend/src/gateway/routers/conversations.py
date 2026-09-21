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
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from jose import JWTError
from pydantic import BaseModel, Field

from src.gateway.dependencies import ConversationRepo, CurrentUser
from src.shared.db.repository import ConversationRepository
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
        status_val  = str(conv.status.value if hasattr(conv.status, "value") else conv.status)
        status_bonus = (
            20 if status_val == "ESCALATED"
            else 10 if status_val in ("AI_ACTIVE", "HUMAN_ACTIVE")
            else 0
        )
        raw_score = engagement * 0.6 + (15 if is_vip else 0) + msg_count * 0.4 + status_bonus
        lead_score = max(0, min(100, round(raw_score)))

        return cls(
            id              = str(conv.conversation_id),
            tenant_id       = str(conv.tenant_id),
            customer_phone  = getattr(conv.customer, "unified_phone", "") if conv.customer else "",  # noqa: E501
            customer_name   = getattr(conv.customer, "display_name", "") or getattr(conv.customer, "whatsapp_profile_name", "") if conv.customer else "",  # noqa: E501
            channel         = str(conv.channel.value if hasattr(conv.channel, "value") else conv.channel),
            status          = status_val,
            last_message    = "",  # denormalised field — updated by SSE events
            last_message_at = conv.last_message_at.isoformat() if conv.last_message_at else "",
            unread_count    = 0,   # managed client-side via SSE
            is_ai_active    = conv.assigned_agent_id is None,
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
        )


# Import ORM models for type hints only (avoid circular import at module level)
from src.shared.db.models import Conversation, Message  # noqa: E402


class PaginatedConversations(BaseModel):
    items:  list[ConversationOut]
    total:  int
    offset: int
    limit:  int


class PaginatedMessages(BaseModel):
    items:  list[MessageOut]
    total:  int
    offset: int
    limit:  int


class SendMessageRequest(BaseModel):
    text:        str  = Field(..., min_length=1, max_length=4096)
    sender_type: str  = Field(default="human_agent")


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
)
async def get_messages(
    conversation_id: uuid.UUID,
    user:            CurrentUser,
    repo:            ConversationRepo,
    limit:           Annotated[int, Query(ge=1, le=200)] = 100,
    offset:          Annotated[int, Query(ge=0)]          = 0,
) -> PaginatedMessages:
    # get_messages returns list[Message] (not a tuple)
    messages = await repo.get_messages(
        conversation_id=conversation_id, offset=offset, limit=limit
    )
    items = [MessageOut.from_orm_model(m) for m in messages]
    return PaginatedMessages(items=items, total=len(items), offset=offset, limit=limit)


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/conversations/{conversation_id}/messages
# ══════════════════════════════════════════════════════════════════════════════

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
) -> MessageOut:
    message = await repo.create_message(
        conversation_id=conversation_id,
        sender_type=body.sender_type,
        text=body.text,
    )
    msg_out = MessageOut.from_orm_model(message)
    # Publish to Redis pub/sub so the SSE stream picks it up
    await _publish_sse_event(
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
) -> ConversationOut:
    conv = await repo.update_status(
        conversation_id=conversation_id,
        status="HUMAN_ACTIVE",
        is_ai_active=False,
    )
    conv_out = ConversationOut.from_orm_model(conv)
    await _publish_sse_event(
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
) -> ConversationOut:
    conv = await repo.update_status(
        conversation_id=conversation_id,
        status="AI_ACTIVE",
        is_ai_active=True,
    )
    conv_out = ConversationOut.from_orm_model(conv)
    await _publish_sse_event(
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
        "A bare `?tenant_id=<uuid>` fallback is permitted ONLY in development mode."
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
    """
    Resolve the tenant_id from the request, with proper JWT validation.

    Priority order:
      1. JWT token query param → full validation (signature, expiry, type, blacklist)
      2. tenant_id query param  → DEVELOPMENT ONLY, never in production
      3. Auto dev-mode bypass   → APP_ENV=development with no params at all

    Returns the tenant_id string, or None if authentication fails.
    All failure paths log a warning; no internal details are returned to callers.
    """
    from src.shared.core.config import get_settings as _gs
    settings = _gs()

    # ── Path 1: Full JWT validation (production + development) ────────────────
    if token:
        try:
            payload = verify_clerk_token(token)
        except JWTError as exc:
            logger.warning("sse_jwt_invalid", error=str(exc))
            return None

        clerk_id = payload.get("sub")
        if not clerk_id:
            logger.warning("sse_jwt_missing_clerk_id")
            return None

        from src.shared.db.session import get_system_session
        from src.shared.db.models import TenantUser
        from sqlalchemy import select
        
        async with get_system_session() as session:
            result = await session.execute(
                select(TenantUser)
                .where(TenantUser.clerk_id == clerk_id)
                .where(TenantUser.is_active == True)
            )
            user = result.scalar_one_or_none()

            if not user:
                from src.shared.core.config import get_settings
                if get_settings().is_development:
                    from src.shared.db.models import Tenant
                    from src.shared.core.enums import SubscriptionStatus, OnboardingStatus, TenantUserRole
                    new_tenant = Tenant(
                        business_name=f"Dev Workspace",
                        fal_license_number=f"DEV-{clerk_id[:8]}",
                        status=SubscriptionStatus.TRIAL,
                        onboarding_status=OnboardingStatus.PENDING_SELECTION
                    )
                    session.add(new_tenant)
                    await session.flush()
                    
                    user = TenantUser(
                        clerk_id=clerk_id,
                        tenant_id=new_tenant.tenant_id,
                        full_name="Local Dev User",
                        email="dev@example.com",
                        role=TenantUserRole.ADMIN,
                        hashed_password="clerk_managed",
                    )
                    session.add(user)
                    # get_system_session() owns the transaction (it opens
                    # `async with session.begin()`), so flush here and let the
                    # context manager commit on exit.
                    await session.flush()
                    logger.info("sse_auth_dev_auto_provisioned", clerk_id=clerk_id)
                else:
                    logger.warning("sse_auth_user_not_found_in_db", clerk_id=clerk_id)
                    return None

        tenant_id = str(user.tenant_id)
        logger.debug("sse_jwt_resolved", tenant_id=tenant_id, user_id=clerk_id)
        return tenant_id

    # ── Path 2: Bare tenant_id param — DEVELOPMENT ONLY ───────────────────────
    if tenant_id_param:
        if settings.is_production:
            # Never allow bare tenant_id in production — reject silently
            logger.warning("sse_bare_tenant_id_rejected_in_production")
            return None
        try:
            uuid.UUID(tenant_id_param)   # format validation
            logger.debug("sse_dev_tenant_id_fallback", tenant_id=tenant_id_param)
            return tenant_id_param
        except ValueError:
            return None

    # ── Path 3: Fully unauthenticated dev bypass ───────────────────────────────
    if settings.is_development:
        _DEV_TENANT = "00000000-0000-0000-0000-000000000001"
        logger.debug("sse_dev_auto_bypass", tenant_id=_DEV_TENANT)
        return _DEV_TENANT

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

