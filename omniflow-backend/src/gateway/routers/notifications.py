"""
gateway/routers/notifications.py — in-app notifications + per-user preferences (prefix /api/v1/notifications), every role.

    GET  /                    latest notifications (?unread=true, ?limit=)
    GET  /unread-count
    POST /{id}/read · POST /read-all
    GET  /prefs               the caller's preferences for every kind (+ whether email is available on this platform)
    PUT  /prefs               { "<kind>": {"in_app": bool, "email": bool}, ... }
Everything is scoped to the caller's own rows (user_id) inside the tenant's RLS session.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.db.models import Notification, NotificationPref
from src.shared.services import email_sender
from src.shared.services.notifications import KINDS, effective_prefs

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


def _json(n: Notification) -> dict:
    return {"notification_id": str(n.notification_id), "kind": n.kind, "title": n.title, "body": n.body, "link": n.link,
            "created_at": n.created_at.isoformat() if n.created_at else None, "read": n.read_at is not None}


@router.get("", summary="Latest notifications")
async def list_notifications(user: CurrentUser, session: AuthTenantSession, unread: bool = False,
                             limit: Annotated[int, Query(ge=1, le=100)] = 30) -> dict:
    stmt = select(Notification).where(Notification.user_id == user.user_id)
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = (await session.execute(stmt.order_by(Notification.created_at.desc()).limit(limit))).scalars().all()
    unread_count = await session.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.user_id, Notification.read_at.is_(None)))
    return {"items": [_json(n) for n in rows], "unread_count": unread_count or 0}


@router.get("/unread-count", summary="Unread badge")
async def unread_count(user: CurrentUser, session: AuthTenantSession) -> dict:
    n = await session.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.user_id, Notification.read_at.is_(None)))
    return {"unread_count": n or 0}


@router.post("/read-all", summary="Mark everything as read")
async def read_all(user: CurrentUser, session: AuthTenantSession) -> dict:
    res = await session.execute(update(Notification).where(Notification.user_id == user.user_id, Notification.read_at.is_(None))
                                .values(read_at=datetime.now(timezone.utc)))
    return {"updated": res.rowcount or 0}


@router.post("/{notification_id}/read", summary="Mark one as read")
async def read_one(user: CurrentUser, session: AuthTenantSession, notification_id: Annotated[uuid.UUID, Path()]) -> dict:
    res = await session.execute(update(Notification).where(Notification.notification_id == notification_id, Notification.user_id == user.user_id)
                                .values(read_at=func.coalesce(Notification.read_at, datetime.now(timezone.utc))))
    if not res.rowcount:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "الإشعار غير موجود."})
    return {"ok": True}


@router.get("/prefs", summary="Notification preferences")
async def get_prefs(user: CurrentUser, session: AuthTenantSession) -> dict:
    out = []
    for kind, k in KINDS.items():
        in_app, email = (await effective_prefs(session, [user.user_id], kind))[user.user_id]
        out.append({"kind": kind, "label": k.label, "description": k.description, "in_app": in_app, "email": email})
    return {"items": out, "email_available": email_sender.is_configured()}


class PrefBody(BaseModel):
    in_app: bool
    email: bool


@router.put("/prefs", summary="Save notification preferences")
async def put_prefs(user: CurrentUser, session: AuthTenantSession, body: dict[str, PrefBody]) -> dict:
    unknown = [k for k in body if k not in KINDS]
    if unknown:
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_KIND", "message": f"نوع إشعار غير معروف: {unknown[0]}"})
    for kind, p in body.items():
        stmt = pg_insert(NotificationPref).values(tenant_id=user.tenant_id, user_id=user.user_id, kind=kind, in_app=p.in_app, email=p.email)
        await session.execute(stmt.on_conflict_do_update(index_elements=[NotificationPref.user_id, NotificationPref.kind],
                                                         set_={"in_app": p.in_app, "email": p.email, "updated_at": func.now()}))
    return await get_prefs(user, session)
