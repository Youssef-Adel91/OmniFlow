"""In-app + email notifications with per-user preferences.

`notify_safe()` is what business code calls: it never raises and runs inside a SAVEPOINT, so a notification problem can
never roll back (or poison) the payment / handoff / support transaction that triggered it.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Iterable

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.shared.core.config import get_settings
from src.shared.db.models import Notification, NotificationPref, TenantUser
from src.shared.services import email_sender

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Kind:
    label: str
    description: str
    in_app: bool = True
    email: bool = False


KINDS: dict[str, Kind] = {
    "support_reply": Kind("رد فريق الدعم", "عندما يردّ فريق الدعم على تذكرتك.", True, True),
    "handoff": Kind("تحويل محادثة إلى موظف", "عندما تُحوَّل محادثة عميل إليك أو إلى الفريق.", True, False),
    "payment_failed": Kind("فشل الدفع", "عندما يتعذّر تحصيل الاشتراك.", True, True),
    "subscription_suspended": Kind("إيقاف الاشتراك", "عندما يُوقف الاشتراك لعدم السداد.", True, True),
    "usage_threshold": Kind("بلوغ حد الاستهلاك", "عند بلوغ 80% ثم 100% من رسائل الباقة.", True, True),
    "trial_ending": Kind("قرب انتهاء التجربة", "قبل انتهاء الفترة التجريبية.", True, True),
    "retention_notice": Kind("إشعار الاحتفاظ بالبيانات", "قبل إخفاء هوية عملاء لم يتفاعلوا لفترة طويلة.", True, True),
    "team_joined": Kind("انضمام عضو جديد", "عندما يقبل موظف دعوة الانضمام.", True, False),
}


async def effective_prefs(session, user_ids: Iterable[uuid.UUID], kind: str) -> dict[uuid.UUID, tuple[bool, bool]]:
    k = KINDS[kind]
    ids = list(user_ids)
    rows = (await session.execute(select(NotificationPref).where(NotificationPref.user_id.in_(ids), NotificationPref.kind == kind))).scalars().all() if ids else []
    saved = {r.user_id: (r.in_app, r.email) for r in rows}
    return {u: saved.get(u, (k.in_app, k.email)) for u in ids}


def _abs(link: str | None) -> str | None:
    if not link:
        return None
    return str(get_settings().frontend_url).rstrip("/") + "/ar" + (link if link.startswith("/") else "/" + link)


async def notify(session, tenant_id: uuid.UUID, kind: str, title: str, body: str = "", link: str | None = None, *,
                 user_ids: Iterable[uuid.UUID] | None = None, roles: tuple[str, ...] = ("admin",), dedupe_key: str | None = None) -> int:
    """Create the in-app rows and send the emails the recipients asked for. Returns the number of in-app notifications created."""
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind!r}")
    stmt = select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.is_active.is_(True))
    stmt = stmt.where(TenantUser.user_id.in_(list(user_ids))) if user_ids is not None else stmt.where(TenantUser.role.in_(roles))
    users = (await session.execute(stmt)).scalars().all()
    if not users:
        return 0
    prefs = await effective_prefs(session, [u.user_id for u in users], kind)
    created, emails = 0, []
    for u in users:
        in_app, want_email = prefs[u.user_id]
        fresh = True
        if in_app:
            res = await session.execute(pg_insert(Notification).values(
                notification_id=uuid.uuid4(), tenant_id=tenant_id, user_id=u.user_id, kind=kind, title=title[:200], body=body or None,
                link=link, dedupe_key=dedupe_key).on_conflict_do_nothing().returning(Notification.notification_id))
            fresh = res.scalar_one_or_none() is not None
            created += int(fresh)
        if want_email and fresh and u.email:
            emails.append(u.email)
    if emails and (email_sender.is_configured() or email_sender._override is not None):
        html, text = email_sender.render(title, body, link_label="فتح في OmniFlow", link=_abs(link))
        await asyncio.gather(*[email_sender.send(e, title, html, text) for e in emails], return_exceptions=True)
    return created


async def notify_safe(session, tenant_id: uuid.UUID, kind: str, title: str, body: str = "", link: str | None = None, **kw) -> int:
    try:
        async with session.begin_nested():
            return await notify(session, tenant_id, kind, title, body, link, **kw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("notification_failed", kind=kind, tenant_id=str(tenant_id), error=type(exc).__name__)
        return 0
