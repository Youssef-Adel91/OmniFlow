"""
gateway/routers/public_api.py — the read-only public API (`/api/public/v1`), authenticated with `Authorization: Bearer ofk_…`.

Read-only by construction: this router defines GET handlers only. Each key is rate-limited (120 requests / minute, fail-open if Redis is down)
and every request is written to `api_usage_log`. Tenant isolation comes from the tenant session (RLS) opened for the key's tenant.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, AsyncIterator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update

from src.shared.db.models import ApiKey, ApiUsageLog, Conversation, Customer, PropertyListing
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.redis_client import redis_mgr
from src.shared.services.developers import api_keys

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/public/v1", tags=["Public API (read-only)"])
_bearer = HTTPBearer(auto_error=False)
RATE_LIMIT_PER_MINUTE = 120
MAX_LIMIT = 100


class Principal:
    def __init__(self, key: ApiKey):
        self.key_id, self.tenant_id, self.name = key.key_id, key.tenant_id, key.name


def _err(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message}, headers=headers)


async def _log_usage(principal: Principal, method: str, path: str, status: int) -> None:
    try:
        async with get_system_session() as s:
            s.add(ApiUsageLog(tenant_id=principal.tenant_id, key_id=principal.key_id, method=method[:8], path=path[:200], status=status))
    except Exception as exc:  # noqa: BLE001 — logging must never break a request
        logger.warning("api_usage_log_failed", error=type(exc).__name__)


async def api_principal(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)] = None) -> AsyncIterator[Principal]:
    if not credentials or not credentials.credentials:
        raise _err(401, "UNAUTHORIZED", "Missing API key. Send `Authorization: Bearer ofk_…`.")
    async with get_system_session() as s:
        row = await api_keys.authenticate(s, credentials.credentials.strip())
        if row is not None:
            await s.execute(update(ApiKey).where(ApiKey.key_id == row.key_id).values(last_used_at=datetime.now(timezone.utc)))
            principal = Principal(row)
    if row is None:
        raise _err(401, "UNAUTHORIZED", "Invalid or revoked API key.")
    minute = int(datetime.now(timezone.utc).timestamp() // 60)
    count = await redis_mgr.incr_counter(f"apikey:rl:{principal.key_id}:{minute}", ttl=70)
    if count is not None and count > RATE_LIMIT_PER_MINUTE:
        await _log_usage(principal, request.method, request.url.path, 429)
        raise _err(429, "RATE_LIMITED", f"Rate limit is {RATE_LIMIT_PER_MINUTE} requests per minute per key.", {"Retry-After": "30"})
    status = 200
    try:
        yield principal
    except HTTPException as exc:
        status = exc.status_code
        raise
    except Exception:
        status = 500
        raise
    finally:
        await _log_usage(principal, request.method, request.url.path, status)


async def tenant_session(principal: Annotated[Principal, Depends(api_principal)]) -> AsyncIterator[Any]:
    async with get_tenant_session(principal.tenant_id) as s:
        yield s


Session = Annotated[Any, Depends(tenant_session)]
Auth = Annotated[Principal, Depends(api_principal)]


def _page(rows: list[Any], limit: int, offset: int, fmt) -> dict[str, Any]:
    more = len(rows) > limit
    return {"data": [fmt(r) for r in rows[:limit]], "limit": limit, "offset": offset, "has_more": more, "next_offset": offset + limit if more else None}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def customer_json(c: Customer) -> dict[str, Any]:
    return {"id": str(c.customer_id), "name": c.display_name or c.whatsapp_profile_name, "phone": c.phone_e164, "email": c.email, "city": c.city,
            "tags": c.tags or [], "source": c.source, "marketing_opt_in": bool(c.marketing_opt_in), "is_vip": bool(c.is_vip),
            "engagement_score": c.engagement_score, "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at)}


def conversation_json(c: Conversation) -> dict[str, Any]:
    return {"id": str(c.conversation_id), "customer_id": str(c.customer_id), "channel": c.channel, "status": getattr(c.status, "value", c.status),
            "assigned_agent_id": str(c.assigned_agent_id) if c.assigned_agent_id else None, "message_count": c.message_count,
            "last_message_at": _iso(c.last_message_at), "created_at": _iso(c.created_at)}


def property_json(p: PropertyListing) -> dict[str, Any]:
    return {"id": str(p.listing_id), "type": getattr(p.property_type, "value", p.property_type), "status": getattr(p.status, "value", p.status),
            "is_verified": bool(p.is_verified), "city": p.city, "district": p.district, "price": float(p.price) if p.price is not None else None,
            "area_sqm": float(p.area_sqm) if p.area_sqm is not None else None, "bedrooms": p.bedrooms, "bathrooms": p.bathrooms,
            "description_ar": p.description_ar, "description_en": p.description_en, "created_at": _iso(p.created_at), "updated_at": _iso(p.updated_at)}


@router.get("/customers", summary="List customers (newest first)")
async def list_customers(_: Auth, session: Session, limit: int = Query(25, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0, le=100_000),
                         phone: str | None = Query(None, max_length=20), updated_since: datetime | None = None) -> dict[str, Any]:
    q = select(Customer).where(Customer.archived_at.is_(None), Customer.erased_at.is_(None))
    if phone:
        q = q.where(Customer.phone_e164 == phone)
    if updated_since:
        q = q.where(Customer.updated_at >= updated_since)
    rows = (await session.scalars(q.order_by(Customer.created_at.desc(), Customer.customer_id).limit(limit + 1).offset(offset))).all()
    return _page(list(rows), limit, offset, customer_json)


@router.get("/customers/{customer_id}", summary="One customer")
async def get_customer(customer_id: uuid.UUID, _: Auth, session: Session) -> dict[str, Any]:
    c = await session.scalar(select(Customer).where(Customer.customer_id == customer_id, Customer.archived_at.is_(None), Customer.erased_at.is_(None)))
    if c is None:
        raise _err(404, "NOT_FOUND", "Customer not found.")
    return customer_json(c)


@router.get("/conversations", summary="List conversations (most recent activity first)")
async def list_conversations(_: Auth, session: Session, limit: int = Query(25, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0, le=100_000),
                             status: str | None = Query(None, max_length=20), channel: str | None = Query(None, max_length=20),
                             customer_id: uuid.UUID | None = None) -> dict[str, Any]:
    q = select(Conversation)
    if status:
        q = q.where(Conversation.status == status)
    if channel:
        q = q.where(Conversation.channel == channel)
    if customer_id:
        q = q.where(Conversation.customer_id == customer_id)
    rows = (await session.scalars(q.order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.conversation_id).limit(limit + 1).offset(offset))).all()
    return _page(list(rows), limit, offset, conversation_json)


@router.get("/conversations/{conversation_id}", summary="One conversation")
async def get_conversation(conversation_id: uuid.UUID, _: Auth, session: Session) -> dict[str, Any]:
    c = await session.scalar(select(Conversation).where(Conversation.conversation_id == conversation_id))
    if c is None:
        raise _err(404, "NOT_FOUND", "Conversation not found.")
    return conversation_json(c)


@router.get("/properties", summary="List properties (newest first)")
async def list_properties(_: Auth, session: Session, limit: int = Query(25, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0, le=100_000),
                          city: str | None = Query(None, max_length=60), status: str | None = Query(None, max_length=20)) -> dict[str, Any]:
    q = select(PropertyListing)
    if city:
        q = q.where(PropertyListing.city == city)
    if status:
        q = q.where(PropertyListing.status == status)
    rows = (await session.scalars(q.order_by(PropertyListing.created_at.desc(), PropertyListing.listing_id).limit(limit + 1).offset(offset))).all()
    return _page(list(rows), limit, offset, property_json)


@router.get("/properties/{listing_id}", summary="One property")
async def get_property(listing_id: uuid.UUID, _: Auth, session: Session) -> dict[str, Any]:
    p = await session.scalar(select(PropertyListing).where(PropertyListing.listing_id == listing_id))
    if p is None:
        raise _err(404, "NOT_FOUND", "Property not found.")
    return property_json(p)
