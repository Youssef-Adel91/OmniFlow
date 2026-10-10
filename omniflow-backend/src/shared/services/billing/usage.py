"""Monthly AI-message counter per tenant: Redis INCR on the hot path, periodically flushed to `usage_counters`."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text

from src.shared.core.config import get_settings
from src.shared.db.models import UsageCounter
from src.shared.redis_client import redis_mgr

logger = structlog.get_logger(__name__)
_TTL = 45 * 86400
_KEY_RE = re.compile(r"^usage:([0-9a-f-]{36}):(\d{4}-\d{2})$")


def period_of(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo(get_settings().app_timezone)).strftime("%Y-%m")


def _key(tenant_id: uuid.UUID, period: str) -> str:
    return f"usage:{tenant_id}:{period}"


async def record_ai_message(tenant_id: uuid.UUID, now: datetime | None = None) -> int | None:
    return await redis_mgr.incr_counter(_key(tenant_id, period_of(now)), ttl=_TTL)


async def current_usage(session, tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    """max(DB, Redis): Redis is ahead between flushes, the DB survives a Redis flush."""
    period = period_of(now)
    row = await session.scalar(select(UsageCounter.ai_messages).where(UsageCounter.tenant_id == tenant_id, UsageCounter.period == period))
    raw = await redis_mgr.get_raw(_key(tenant_id, period))
    return max(int(row or 0), int(raw) if raw and raw.isdigit() else 0)


def level(used: int, limit: int | None) -> str:
    if not limit or limit <= 0:
        return "unlimited"
    if used >= limit:
        return "limit_reached"
    if used * 100 >= limit * 80:
        return "warning"
    return "ok"


async def flush(session, limits_by_tenant: dict[uuid.UUID, int | None], now: datetime | None = None) -> list[dict]:
    """System session. Persist Redis counters (GREATEST, so it is idempotent) and stamp the 80 % / 100 % alert times once."""
    now = now or datetime.now(timezone.utc)
    alerts: list[dict] = []
    for key, value in (await redis_mgr.scan_counters("usage:*")).items():
        m = _KEY_RE.match(key)
        if not m:
            continue
        tid, period = uuid.UUID(m.group(1)), m.group(2)
        await session.execute(text(
            "INSERT INTO usage_counters (tenant_id, period, ai_messages, updated_at) VALUES (:t, :p, :v, now()) "
            "ON CONFLICT (tenant_id, period) DO UPDATE SET ai_messages = GREATEST(usage_counters.ai_messages, EXCLUDED.ai_messages), updated_at = now()"),
            {"t": tid, "p": period, "v": value})
        row = await session.scalar(select(UsageCounter).where(UsageCounter.tenant_id == tid, UsageCounter.period == period))
        lvl = level(row.ai_messages, limits_by_tenant.get(tid))
        if lvl == "limit_reached" and row.alerted_100_at is None:
            row.alerted_100_at = now
            row.alerted_80_at = row.alerted_80_at or now
            alerts.append({"tenant_id": tid, "period": period, "level": 100, "used": row.ai_messages})
        elif lvl == "warning" and row.alerted_80_at is None:
            row.alerted_80_at = now
            alerts.append({"tenant_id": tid, "period": period, "level": 80, "used": row.ai_messages})
    await session.flush()
    return alerts
