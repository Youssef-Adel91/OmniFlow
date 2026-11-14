"""
Per-tenant channel health: "this channel's access token stopped working, reconnect it".

A revoked / expired Meta token (Graph error 190) or WhatsApp auth error used to surface only as FAILED messages and a DLQ entry;
the tenant was never told. `mark_auth_failed` now (1) remembers the problem for 30 days in Redis so Settings → Channels can show
a "reconnect" banner and (2) sends ONE in-app notification (plus e-mail if the admin enabled it) per channel per 24 hours.
`clear` is called after a successful reconnect. Everything here is best-effort: a Redis or notification problem is logged,
never raised, because it runs inside the outbound send path.

Key: `channel:auth_failed:{tenant_id}:{channel}` -> JSON {"at": iso, "reason": str}.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import structlog

from src.shared.redis_client import redis_mgr

logger = structlog.get_logger(__name__)

CHANNELS = ("whatsapp", "facebook", "instagram", "telegram")
FAILED_TTL_S = 30 * 24 * 3600
NOTIFY_TTL_S = 24 * 3600
CHANNEL_LABEL_AR = {"whatsapp": "واتساب", "facebook": "فيسبوك ماسنجر", "instagram": "إنستجرام", "telegram": "تيليجرام"}


def health_channel(channel: object) -> str:
    """Channel value of a conversation/message -> the key used for health alarms (Messenger and Facebook comments share the Facebook Page token)."""
    value = str(getattr(channel, "value", channel))
    return {"whatsapp": "whatsapp", "instagram": "instagram", "messenger": "facebook", "telegram": "telegram"}.get(value, "facebook")


def _key(tenant_id: uuid.UUID | str, channel: str) -> str:
    return f"channel:auth_failed:{tenant_id}:{channel}"


async def mark_auth_failed(tenant_id: uuid.UUID, channel: str, reason: str) -> None:
    """Record the failure (idempotent) and notify the tenant's admins at most once per 24 h per channel."""
    if channel not in CHANNELS:
        return
    try:
        await redis_mgr.set_raw(_key(tenant_id, channel), json.dumps({"at": datetime.now(timezone.utc).isoformat(), "reason": (reason or "")[:300]}), ttl=FAILED_TTL_S)
        first = await redis_mgr.set_if_absent(f"channel:auth_notified:{tenant_id}:{channel}", "1", ttl=NOTIFY_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("channel_health_mark_failed", channel=channel, error=type(exc).__name__)
        return
    logger.error("channel_auth_failed", tenant_id=str(tenant_id), channel=channel, reason=(reason or "")[:200])
    if not first:
        return
    try:
        from src.shared.db.session import get_tenant_session
        from src.shared.services import notifications

        label = CHANNEL_LABEL_AR.get(channel, channel)
        async with get_tenant_session(tenant_id) as session:
            await notifications.notify_safe(
                session, tenant_id, "channel_disconnected", f"قناة {label} تحتاج إعادة ربط",
                f"انتهت صلاحية ربط قناة {label} أو أُلغي، ولن تصل ردود المساعد للعملاء حتى تعيد الربط من الإعدادات ← القنوات.",
                "/settings", dedupe_key=f"channel_disconnected:{channel}:{datetime.now(timezone.utc):%Y%m%d}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("channel_health_notify_failed", channel=channel, error=type(exc).__name__)


async def get_failure(tenant_id: uuid.UUID | str, channel: str) -> dict | None:
    try:
        raw = await redis_mgr.get_raw(_key(tenant_id, channel))
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


async def clear(tenant_id: uuid.UUID | str, channel: str) -> None:
    try:
        await redis_mgr.delete_raw(_key(tenant_id, channel))
        await redis_mgr.delete_raw(f"channel:auth_notified:{tenant_id}:{channel}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("channel_health_clear_failed", channel=channel, error=type(exc).__name__)


async def note_success(tenant_id: uuid.UUID | str, channel: str) -> None:
    """A send/reply over this channel just worked: if an old "needs reconnect" alarm is still up it was stale (token fixed outside the app, or a one-off), so drop it.
    One cheap Redis GET on the normal path; never raises."""
    try:
        if await redis_mgr.get_raw(_key(tenant_id, channel)):
            await clear(tenant_id, channel)
            logger.info("channel_health_recovered", tenant_id=str(tenant_id), channel=channel)
    except Exception as exc:  # noqa: BLE001
        logger.warning("channel_health_success_note_failed", channel=channel, error=type(exc).__name__)
