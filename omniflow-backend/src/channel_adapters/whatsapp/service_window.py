"""
WhatsApp customer-service window (Meta rule): free-form messages may only be sent within 24 hours of the customer's LAST inbound message. Outside the window Meta rejects
them (error 131047, shown to staff as a mysterious delivery failure) — only an approved template may be sent.

`is_open` is pure. `last_inbound_at` reads the newest customer message of a conversation. An UNKNOWN last-inbound time (no customer message found) is treated as "allowed"
so a flow we did not foresee is never silently blocked; Meta stays the final authority in that case.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

WINDOW = timedelta(hours=24)
FAILURE_REASON = "خارج نافذة الـ 24 ساعة: لا يمكن إرسال رسالة حرّة، استخدم قالبًا معتمدًا أو انتظر رد العميل."


def is_open(last_inbound: datetime | None, now: datetime | None = None) -> bool:
    if last_inbound is None:
        return True
    now = now or datetime.now(timezone.utc)
    if last_inbound.tzinfo is None:
        last_inbound = last_inbound.replace(tzinfo=timezone.utc)
    return now - last_inbound < WINDOW


async def last_inbound_at(session, conversation_id: uuid.UUID) -> datetime | None:
    from src.shared.db.models import Message

    return await session.scalar(select(func.max(Message.created_at)).where(Message.conversation_id == conversation_id, Message.sender_type == "customer"))
