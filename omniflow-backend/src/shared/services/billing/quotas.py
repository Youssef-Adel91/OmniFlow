"""Plan-limit enforcement for everything a tenant can add: properties, contacts, team seats, channels, campaigns.

`ensure_can_add` raises `QuotaExceeded` (mapped to HTTP 402 PLAN_LIMIT_REACHED by `to_http`). `remaining` lets bulk imports trim a
batch instead of failing the whole file. Exempt (grandfathered / internal) tenants are never limited, and BILLING_ENFORCE_LIMITS=false
switches the whole thing off. AI replies are NOT handled here — that hard cap sits in the LLM invoker (credits.py).

Contacts created by an inbound customer message are never blocked (dropping a real customer's first message would be worse than going
over the contact quota): they are counted, shown on the billing page, and block *manual* adds / imports until the plan is raised.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select

from src.shared.core.config import get_settings
from src.shared.db.models import BroadcastCampaign, Customer, Plan, PropertyListing, Subscription, Tenant
from src.shared.db.session import get_system_session
from src.shared.services.billing import catalog


class QuotaExceeded(Exception):
    def __init__(self, resource: str, limit: int | None, used: int, *, unavailable: bool = False):
        self.resource, self.limit, self.used, self.unavailable = resource, limit, used, unavailable
        label = catalog.LABELS_AR.get(resource, resource)
        if unavailable:
            msg = f"«{label}» غير متاحة في باقتك الحالية. رقِّ الباقة من صفحة الفوترة."
        else:
            msg = f"وصلت إلى حد {label} في باقتك ({limit}). رقِّ الباقة أو أضف سعة من صفحة الفوترة."
        super().__init__(msg)


def to_http(exc: QuotaExceeded) -> HTTPException:
    return HTTPException(status_code=402, detail={"code": "PLAN_LIMIT_REACHED", "resource": exc.resource, "limit": exc.limit,
                                                  "used": exc.used, "message": str(exc)})


def enforcement_on() -> bool:
    cfg = get_settings()
    return bool(cfg.billing_gate_enabled and cfg.billing_enforce_limits)


async def limits_of(session, tenant_id: uuid.UUID) -> dict | None:
    """Effective limits for the tenant; None = unlimited."""
    if not enforcement_on():
        return None
    # 1) the caller's own session — sees a subscription row created earlier in the same (uncommitted) request, and never takes a lock.
    sub = await session.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    if sub is not None:
        plan = await session.scalar(select(Plan).where(Plan.code == sub.plan_code))
        return catalog.effective_limits(sub, plan)
    # 2) bare onboarding sessions carry no tenant GUC (RLS hides the row) and a brand-new tenant may not have its lazy trial row yet:
    #    `ensure_subscription` on a system session finds or creates it, so the trial caps apply from the very first action.
    from src.shared.services.billing import service

    async with get_system_session() as sysdb:
        sub = await service.ensure_subscription(sysdb, tenant_id)
        plan = await sysdb.scalar(select(Plan).where(Plan.code == sub.plan_code))
        return catalog.effective_limits(sub, plan)


async def count_of(session, tenant_id: uuid.UUID, resource: str) -> int:
    if resource == "properties":
        stmt = select(func.count()).select_from(PropertyListing).where(PropertyListing.tenant_id == tenant_id)
    elif resource == "contacts":
        stmt = select(func.count()).select_from(Customer).where(Customer.tenant_id == tenant_id, Customer.erased_at.is_(None))
    elif resource == "seats":
        from src.shared.services import team

        return (await team.seat_usage_raw(session, tenant_id))["used"]
    elif resource == "channels":
        return await channels_in_use(session, tenant_id)
    elif resource == "whatsapp_numbers":
        t = await session.scalar(select(Tenant).where(Tenant.tenant_id == tenant_id))
        return 1 if (t and t.whatsapp_phone_number_id) else 0
    elif resource == "campaigns":
        stmt = select(func.count()).select_from(BroadcastCampaign).where(BroadcastCampaign.tenant_id == tenant_id)
    else:
        raise ValueError(f"unknown resource {resource!r}")
    return int(await session.scalar(stmt) or 0)


async def channels_in_use(session, tenant_id: uuid.UUID) -> int:
    t = await session.scalar(select(Tenant).where(Tenant.tenant_id == tenant_id))
    if t is None:
        return 0
    return int(bool(t.whatsapp_phone_number_id)) + int(bool(t.instagram_page_id))


async def remaining(session, tenant_id: uuid.UUID, resource: str) -> int | None:
    """Free capacity for a countable resource; None = unlimited."""
    limits = await limits_of(session, tenant_id)
    if limits is None or not limits.get(resource):
        return None
    return max(0, int(limits[resource]) - await count_of(session, tenant_id, resource))


async def ensure_can_add(session, tenant_id: uuid.UUID, resource: str, adding: int = 1) -> None:
    limits = await limits_of(session, tenant_id)
    if limits is None:
        return
    limit = limits.get(resource)
    if not limit:
        return
    used = await count_of(session, tenant_id, resource)
    if used + adding > int(limit):
        raise QuotaExceeded(resource, int(limit), used)


async def ensure_feature(session, tenant_id: uuid.UUID, feature: str) -> None:
    """Boolean features (`campaigns`): an explicit False blocks; a missing key means included."""
    limits = await limits_of(session, tenant_id)
    if limits is not None and limits.get(feature) is False:
        raise QuotaExceeded(feature, None, 0, unavailable=True)


async def ensure_channel_slot(session, tenant_id: uuid.UUID, kind: str) -> None:
    """Connecting a channel that is not connected yet must fit the plan's `channels` limit (trial: 1). `kind` = whatsapp | meta_page."""
    limits = await limits_of(session, tenant_id)
    if limits is None or not limits.get("channels"):
        return
    t = await session.scalar(select(Tenant).where(Tenant.tenant_id == tenant_id))
    already = bool(t and (t.whatsapp_phone_number_id if kind == "whatsapp" else t.instagram_page_id))
    if already:
        return
    used = await channels_in_use(session, tenant_id)
    if used + 1 > int(limits["channels"]):
        raise QuotaExceeded("channels", int(limits["channels"]), used)


async def snapshot(session, tenant_id: uuid.UUID) -> dict[str, Any]:
    """For the billing page: every limited resource with used / limit."""
    limits = await limits_of(session, tenant_id)
    out: dict[str, Any] = {"unlimited": limits is None, "resources": {}}
    if limits is None:
        return out
    for key in ("properties", "contacts", "seats", "channels", "whatsapp_numbers"):
        if limits.get(key):
            used = await count_of(session, tenant_id, key)
            out["resources"][key] = {"used": used, "limit": int(limits[key]), "label_ar": catalog.LABELS_AR[key], "over": used > int(limits[key])}
    out["campaigns"] = limits.get("campaigns") is not False
    return out


# ── HTTP-flavoured wrappers for routers ───────────────────────────────────────

async def guard_add(session, tenant_id: uuid.UUID, resource: str, adding: int = 1) -> None:
    try:
        await ensure_can_add(session, tenant_id, resource, adding)
    except QuotaExceeded as exc:
        raise to_http(exc) from exc


async def guard_feature(session, tenant_id: uuid.UUID, feature: str) -> None:
    try:
        await ensure_feature(session, tenant_id, feature)
    except QuotaExceeded as exc:
        raise to_http(exc) from exc


async def guard_channel(session, tenant_id: uuid.UUID, kind: str) -> None:
    try:
        await ensure_channel_slot(session, tenant_id, kind)
    except QuotaExceeded as exc:
        raise to_http(exc) from exc
