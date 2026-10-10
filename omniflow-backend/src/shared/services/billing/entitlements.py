"""Runtime entitlement lookups for workers and request guards (cached 60 s so the hot path never hits Postgres per message).

Fail-open on infrastructure errors by design: a Redis/DB blip must never silence every tenant's bot. The gate can still be
switched off globally with BILLING_GATE_ENABLED=false.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select

from src.shared.core.config import get_settings
from src.shared.db.models import Plan
from src.shared.db.session import get_system_session
from src.shared.redis_client import redis_mgr
from src.shared.services.billing import service

logger = structlog.get_logger(__name__)
CACHE_TTL = 60
_KEY = "billing:ent:{tid}"


def _fallback() -> dict[str, Any]:
    return {"mode": "full", "status": "unknown", "reason": None, "banner": None, "days_left": None, "plan_code": None, "limits": {}}


async def snapshot(tenant_id: uuid.UUID, *, now: datetime | None = None, use_cache: bool = True) -> dict[str, Any]:
    cfg = get_settings()
    if not cfg.billing_gate_enabled:
        return {**_fallback(), "status": "gate_disabled"}
    key = _KEY.format(tid=tenant_id)
    if use_cache:
        raw = await redis_mgr.get_raw(key)
        if raw:
            try:
                return json.loads(raw)
            except ValueError:
                pass
    try:
        async with get_system_session() as session:
            sub = await service.ensure_subscription(session, tenant_id, now)
            acc = service.access_of(sub, now)
            plan = await session.scalar(select(Plan).where(Plan.code == sub.plan_code))
            snap = {"mode": acc.mode, "status": acc.status, "reason": acc.reason, "banner": acc.banner, "days_left": acc.days_left,
                    "plan_code": sub.plan_code, "limits": dict(plan.limits) if plan else {}}
    except Exception as exc:  # noqa: BLE001 - never take every bot down because billing storage hiccuped
        logger.error("billing_entitlement_lookup_failed", tenant_id=str(tenant_id), error=str(exc)[:200])
        return _fallback()
    await redis_mgr.set_raw(key, json.dumps(snap), ttl=CACHE_TTL)
    return snap


async def invalidate(tenant_id: uuid.UUID) -> None:
    await redis_mgr.delete_raw(_KEY.format(tid=tenant_id))


async def bot_allowed(tenant_id: uuid.UUID) -> tuple[bool, str | None]:
    snap = await snapshot(tenant_id)
    return snap["mode"] != "blocked", snap.get("reason")


async def writes_allowed(tenant_id: uuid.UUID) -> tuple[bool, str | None]:
    snap = await snapshot(tenant_id)
    return snap["mode"] != "blocked", snap.get("reason")


async def over_message_limit(tenant_id: uuid.UUID) -> bool:
    """True only when BILLING_ENFORCE_MESSAGE_LIMIT is on and this month's AI messages reached the plan limit (Redis counter, no DB)."""
    if not get_settings().billing_enforce_message_limit:
        return False
    from src.shared.services.billing import usage

    limit = (await snapshot(tenant_id)).get("limits", {}).get("messages_per_month")
    if not limit:
        return False
    raw = await redis_mgr.get_raw(usage._key(tenant_id, usage.period_of()))
    return bool(raw and raw.isdigit() and int(raw) >= int(limit))
