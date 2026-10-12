"""
gateway/routers/developers.py — Settings → Developers (Clerk-authenticated; admin writes, others read).

    GET    /api/v1/developers/overview                    keys + webhooks + 24h/7d usage + the event catalogue
    POST   /api/v1/developers/api-keys                    { name }  → the key, ONCE
    DELETE /api/v1/developers/api-keys/{id}               revoke
    GET    /api/v1/developers/api-keys/{id}/usage         recent requests
    POST   /api/v1/developers/webhooks                    { url, events[], description? } → signing secret, ONCE
    PATCH  /api/v1/developers/webhooks/{id}               { active?, events?, url?, description? }
    DELETE /api/v1/developers/webhooks/{id}
    POST   /api/v1/developers/webhooks/{id}/rotate-secret → new secret, ONCE
    POST   /api/v1/developers/webhooks/{id}/test          sends a signed `webhook.test` right now
    GET    /api/v1/developers/webhooks/{id}/deliveries    delivery log
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import ApiKey, ApiUsageLog, AuditLog, WebhookDelivery, WebhookEndpoint
from src.shared.security.crypto import encrypt_secret
from src.shared.services.developers import api_keys, webhooks_out

router = APIRouter(prefix="/api/v1/developers", tags=["Developers"])


def _admin(user: CurrentUser) -> None:
    if user.role != TenantUserRole.ADMIN:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "هذا الإجراء للمدير فقط."})


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": what})


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _key(k: ApiKey) -> dict[str, Any]:
    return {"key_id": str(k.key_id), "name": k.name, "prefix": k.prefix, "created_at": _iso(k.created_at), "last_used_at": _iso(k.last_used_at),
            "revoked_at": _iso(k.revoked_at)}


def _endpoint(e: WebhookEndpoint) -> dict[str, Any]:
    return {"endpoint_id": str(e.endpoint_id), "url": e.url, "description": e.description, "events": e.events or [], "active": e.active,
            "consecutive_failures": e.consecutive_failures, "disabled_reason": e.disabled_reason, "created_at": _iso(e.created_at)}


def _delivery(d: WebhookDelivery) -> dict[str, Any]:
    return {"delivery_id": str(d.delivery_id), "event_id": str(d.event_id), "event_type": d.event_type, "status": d.status, "attempts": d.attempts,
            "last_status_code": d.last_status_code, "last_error": d.last_error, "next_attempt_at": _iso(d.next_attempt_at),
            "created_at": _iso(d.created_at), "delivered_at": _iso(d.delivered_at)}


def _audit(session, user, action: str, entity: str, entity_id: Any, details: dict | None = None) -> None:
    session.add(AuditLog(tenant_id=user.tenant_id, actor_user_id=user.user_id, action=action, entity_type=entity, entity_id=str(entity_id), details=details or {}))


async def _endpoint_of(session, user, endpoint_id: uuid.UUID) -> WebhookEndpoint:
    e = await session.scalar(select(WebhookEndpoint).where(WebhookEndpoint.endpoint_id == endpoint_id, WebhookEndpoint.tenant_id == user.tenant_id))
    if e is None:
        raise _not_found("نقطة الـ webhook غير موجودة.")
    return e


def _clean_events(events: list[str]) -> list[str]:
    bad = sorted(set(events) - set(webhooks_out.EVENTS))
    if bad or not events:
        raise HTTPException(status_code=422, detail={"code": "BAD_EVENTS", "message": f"أحداث غير معروفة أو فارغة: {', '.join(bad) or '—'}", "allowed": list(webhooks_out.EVENTS)})
    return sorted(set(events))


def _checked_url(url: str) -> str:
    try:
        return webhooks_out.validate_url(url)
    except webhooks_out.UnsafeUrl as exc:
        raise HTTPException(status_code=422, detail={"code": "UNSAFE_URL", "message": str(exc)}) from exc


@router.get("/overview", summary="API keys, webhooks, usage and the event catalogue")
async def overview(user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    keys = (await session.scalars(select(ApiKey).where(ApiKey.tenant_id == user.tenant_id).order_by(ApiKey.created_at.desc()))).all()
    eps = (await session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == user.tenant_id).order_by(WebhookEndpoint.created_at.desc()))).all()

    async def used(since: timedelta) -> int:
        return int(await session.scalar(select(func.count()).select_from(ApiUsageLog).where(ApiUsageLog.tenant_id == user.tenant_id, ApiUsageLog.created_at >= now - since)) or 0)

    return {"api_keys": [_key(k) for k in keys], "webhooks": [_endpoint(e) for e in eps], "events": list(webhooks_out.EVENTS),
            "usage": {"last_24h": await used(timedelta(hours=24)), "last_7d": await used(timedelta(days=7))},
            "limits": {"max_active_keys": api_keys.MAX_ACTIVE_KEYS, "max_webhooks": webhooks_out.MAX_ENDPOINTS, "rate_limit_per_minute": 120}}


class KeyBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


@router.post("/api-keys", status_code=201, summary="Create a read-only API key (shown once)")
async def create_api_key(body: KeyBody, user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    _admin(user)
    try:
        row, full = await api_keys.create_key(session, tenant_id=user.tenant_id, name=body.name, created_by=user.user_id)
    except api_keys.TooManyKeys as exc:
        raise HTTPException(status_code=409, detail={"code": "TOO_MANY_KEYS", "message": str(exc)}) from exc
    _audit(session, user, "developers.api_key_created", "api_key", row.key_id, {"name": row.name, "prefix": row.prefix})
    return {**_key(row), "key": full, "note": "انسخ المفتاح الآن؛ لن يُعرض مرة أخرى."}


@router.delete("/api-keys/{key_id}", summary="Revoke an API key")
async def revoke_api_key(key_id: uuid.UUID, user: CurrentUser, session: AuthTenantSession) -> dict[str, bool]:
    _admin(user)
    if not await api_keys.revoke_key(session, tenant_id=user.tenant_id, key_id=key_id):
        raise _not_found("المفتاح غير موجود أو مُلغى سابقًا.")
    _audit(session, user, "developers.api_key_revoked", "api_key", key_id)
    return {"revoked": True}


@router.get("/api-keys/{key_id}/usage", summary="Recent requests made with a key")
async def key_usage(key_id: uuid.UUID, user: CurrentUser, session: AuthTenantSession, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    if not await session.scalar(select(ApiKey.key_id).where(ApiKey.key_id == key_id, ApiKey.tenant_id == user.tenant_id)):
        raise _not_found("المفتاح غير موجود.")
    rows = (await session.scalars(select(ApiUsageLog).where(ApiUsageLog.key_id == key_id, ApiUsageLog.tenant_id == user.tenant_id)
                                  .order_by(ApiUsageLog.created_at.desc()).limit(limit))).all()
    return {"items": [{"method": r.method, "path": r.path, "status": r.status, "at": _iso(r.created_at)} for r in rows]}


class WebhookBody(BaseModel):
    url: str = Field(..., min_length=8, max_length=500)
    events: list[str] = Field(..., min_length=1)
    description: str | None = Field(default=None, max_length=120)


class WebhookPatch(BaseModel):
    url: str | None = Field(default=None, min_length=8, max_length=500)
    events: list[str] | None = None
    description: str | None = Field(default=None, max_length=120)
    active: bool | None = None


@router.post("/webhooks", status_code=201, summary="Add a webhook endpoint (signing secret shown once)")
async def create_webhook(body: WebhookBody, user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    _admin(user)
    count = int(await session.scalar(select(func.count()).select_from(WebhookEndpoint).where(WebhookEndpoint.tenant_id == user.tenant_id)) or 0)
    if count >= webhooks_out.MAX_ENDPOINTS:
        raise HTTPException(status_code=409, detail={"code": "TOO_MANY_WEBHOOKS", "message": f"الحد الأقصى {webhooks_out.MAX_ENDPOINTS} نقاط."})
    events = _clean_events(body.events)
    url = await asyncio.to_thread(_checked_url, body.url)
    ep, secret = webhooks_out.make_endpoint(tenant_id=user.tenant_id, url=url, events=events, description=body.description)
    session.add(ep)
    await session.flush()
    _audit(session, user, "developers.webhook_created", "webhook_endpoint", ep.endpoint_id, {"url": ep.url, "events": ep.events})
    return {**_endpoint(ep), "secret": secret, "note": "انسخ سر التوقيع الآن؛ لن يُعرض مرة أخرى."}


@router.patch("/webhooks/{endpoint_id}", summary="Update a webhook endpoint")
async def update_webhook(endpoint_id: uuid.UUID, body: WebhookPatch, user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    _admin(user)
    ep = await _endpoint_of(session, user, endpoint_id)
    if body.url is not None:
        ep.url = await asyncio.to_thread(_checked_url, body.url)
    if body.events is not None:
        ep.events = _clean_events(body.events)
    if body.description is not None:
        ep.description = body.description[:120] or None
    if body.active is not None:
        ep.active = body.active
        if body.active:
            ep.consecutive_failures, ep.disabled_reason = 0, None
    await session.flush()
    _audit(session, user, "developers.webhook_updated", "webhook_endpoint", ep.endpoint_id, body.model_dump(exclude_none=True))
    return _endpoint(ep)


@router.delete("/webhooks/{endpoint_id}", summary="Delete a webhook endpoint and its delivery log")
async def delete_webhook(endpoint_id: uuid.UUID, user: CurrentUser, session: AuthTenantSession) -> dict[str, bool]:
    _admin(user)
    ep = await _endpoint_of(session, user, endpoint_id)
    await session.delete(ep)
    _audit(session, user, "developers.webhook_deleted", "webhook_endpoint", endpoint_id)
    return {"deleted": True}


@router.post("/webhooks/{endpoint_id}/rotate-secret", summary="Issue a new signing secret (shown once)")
async def rotate_secret(endpoint_id: uuid.UUID, user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    _admin(user)
    ep = await _endpoint_of(session, user, endpoint_id)
    secret = webhooks_out.new_secret()
    ep.secret_enc = encrypt_secret(secret)
    _audit(session, user, "developers.webhook_secret_rotated", "webhook_endpoint", endpoint_id)
    return {"endpoint_id": str(endpoint_id), "secret": secret, "note": "السر القديم لم يعد صالحًا."}


@router.post("/webhooks/{endpoint_id}/test", summary="Send a signed webhook.test now and report the result")
async def test_webhook(endpoint_id: uuid.UUID, user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    _admin(user)
    ep = await _endpoint_of(session, user, endpoint_id)
    event_id = uuid.uuid4()
    d = WebhookDelivery(tenant_id=user.tenant_id, endpoint_id=ep.endpoint_id, event_id=event_id, event_type=webhooks_out.TEST_EVENT, status="pending",
                        payload=webhooks_out.build_payload(event_id, webhooks_out.TEST_EVENT, user.tenant_id, {"message": "OmniFlow test event"}))
    session.add(d)
    await session.flush()
    await webhooks_out.attempt(d, ep)
    if d.status == "pending":          # a test is a one-shot: do not leave a retry scheduled behind a button press
        d.status, d.next_attempt_at = "failed", None
    return _delivery(d)


@router.get("/webhooks/{endpoint_id}/deliveries", summary="Delivery log of an endpoint")
async def deliveries(endpoint_id: uuid.UUID, user: CurrentUser, session: AuthTenantSession, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    await _endpoint_of(session, user, endpoint_id)
    rows = (await session.scalars(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint_id, WebhookDelivery.tenant_id == user.tenant_id)
                                  .order_by(WebhookDelivery.created_at.desc()).limit(limit))).all()
    return {"items": [_delivery(r) for r in rows]}
