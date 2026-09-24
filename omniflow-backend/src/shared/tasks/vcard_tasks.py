"""
shared/tasks/vcard_tasks.py — VCard drip-sequence logic

The VCard gate requires a customer to save the agency's contact card before
the AI assistant is unlocked. Customers who never confirm are nudged on a
timer:

    STATE_VCARD_SENT / STATE_AWAITING_VALIDATION
        ── after VCARD_REMINDER_1 (default 1h)  ─► STATE_REMINDER_1
    STATE_REMINDER_1
        ── after VCARD_REMINDER_2 (default 24h) ─► STATE_REMINDER_2
    STATE_REMINDER_2
        ── after VCARD_DORMANT   (default 7d)   ─► STATE_DORMANT

This module holds the *reusable* transition logic so it can be driven from two
places without duplication:

  * `src/celery_app/tasks.py::vcard_reminder_check` — the hourly sweep, which
    is the authoritative driver.
  * `src/ai_workers/vcard_gatekeeper/worker.py` — calls
    `schedule_vcard_reminder()` inline when it first sends a card.

Timing thresholds come from Settings, so they are tunable per environment:
    VCARD_VALIDATION_TIMEOUT_HOURS, VCARD_REMINDER_2_DELAY_HOURS,
    VCARD_DORMANT_DELAY_DAYS
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.core.config import get_settings
from src.shared.core.enums import CustomerVCardState
from src.shared.db.models import Conversation, Customer
from src.shared.events.outbound import OutboundMessage

if TYPE_CHECKING:
    from src.shared.kafka.producer import KafkaProducerManager

logger = structlog.get_logger(__name__)
settings = get_settings()

_REMINDER_TEXT = {
    "reminder_1": "لإكمال طلبك، يرجى حفظ رقمنا في جهات الاتصال وإرسال كلمة 'تم'. 🤝",
    "reminder_2": (
        "تذكير أخير: لم نتلقَّ تأكيد حفظ رقمنا بعد. بدون ذلك لن تصلك تقاريرك "
        "العقارية وإشعاراتك الهامة. يرجى حفظ الرقم وإرسال كلمة 'تم' لتفعيل الخدمة كاملة. 🙏"
    ),
}


@dataclass
class VCardSweepResult:
    """Counters returned by `advance_vcard_states` (JSON-serialisable)."""
    reminder_1: int = 0
    reminder_2: int = 0
    dormant: int = 0
    customer_ids: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.reminder_1 + self.reminder_2 + self.dormant

    def as_dict(self) -> dict:
        return {
            "reminder_1": self.reminder_1,
            "reminder_2": self.reminder_2,
            "dormant": self.dormant,
            "total": self.total,
            "customer_ids": self.customer_ids,
        }


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _transition(
    session: AsyncSession,
    *,
    from_states: list[str],
    to_state: str,
    older_than: datetime,
    limit: int,
) -> list[str]:
    """
    Move every customer sitting in `from_states` since before `older_than`
    into `to_state`. Returns the affected customer ids as strings.

    `updated_at` is the clock: it is bumped by the DB on every UPDATE, so the
    time a customer has spent in the current state is exactly
    `now - updated_at`. This also makes each transition naturally idempotent —
    a row that was just moved cannot immediately qualify for the next step.
    """
    rows = (
        await session.execute(
            select(Customer.customer_id)
            .where(Customer.vcard_state.in_(from_states))
            .where(Customer.updated_at < older_than)
            .order_by(Customer.updated_at.asc())
            .limit(limit)
        )
    ).scalars().all()

    if not rows:
        return []

    await session.execute(
        update(Customer)
        .where(Customer.customer_id.in_(rows))
        .values(vcard_state=to_state)
    )
    return [str(r) for r in rows]


async def _publish_reminders(
    session: AsyncSession,
    producer: "KafkaProducerManager",
    customer_ids: list[str],
    reminder_key: str,
) -> None:
    """Publish a reminder OutboundMessage for each customer's most recent conversation.

    Customers with no conversation row at all (shouldn't happen — a customer
    only exists because of an inbound message — but not guaranteed by a DB
    constraint) are skipped with a warning rather than crashing the sweep.
    """
    if not customer_ids:
        return

    ids = [uuid.UUID(c) for c in customer_ids]
    rows = (
        await session.execute(
            select(Customer, Conversation)
            .join(Conversation, Conversation.customer_id == Customer.customer_id)
            .where(Customer.customer_id.in_(ids))
            .order_by(
                Customer.customer_id,
                Conversation.last_message_at.desc().nullslast(),
                Conversation.created_at.desc(),
            )
        )
    ).all()

    reminded: set[uuid.UUID] = set()
    text = _REMINDER_TEXT[reminder_key]
    for customer, conv in rows:
        if customer.customer_id in reminded:
            continue  # rows are ordered most-recent-conversation-first per customer
        reminded.add(customer.customer_id)

        event = OutboundMessage(
            tenant_id=customer.tenant_id,
            conversation_id=conv.conversation_id,
            customer_phone=customer.unified_phone,
            platform_conversation_id=conv.platform_conversation_id or customer.unified_phone,
            channel=str(conv.channel),
            text=text,
            message_type="text",
            source_event_id=uuid.uuid4(),
            routing_tier_used="VCARD",
            model_used="deterministic",
        )
        await producer.publish(
            topic=settings.kafka_topic_messages_outgoing,
            event=event,
            key=OutboundMessage.kafka_key(customer.tenant_id, customer.unified_phone),
        )

    missing = len(ids) - len(reminded)
    if missing:
        logger.warning("vcard_reminder_no_conversation", reminder=reminder_key, missing_count=missing)


async def advance_vcard_states(
    session: AsyncSession,
    *,
    batch_limit: int = 500,
    producer: "KafkaProducerManager | None" = None,
) -> VCardSweepResult:
    """
    Run one pass of the VCard drip sequence.

    The caller owns the session/transaction. Use a system session to sweep
    across every tenant, or a tenant session to sweep one tenant only.

    `producer` is optional and defaults to None for backward compatibility
    (existing tests call this without a producer and only check the state
    transitions) — pass a started `KafkaProducerManager` to also actually
    deliver the reminder_1/reminder_2 nudge to the customer. Without it, this
    only advances the persisted state machine, as before.
    """
    now = _now()
    result = VCardSweepResult()

    # ── Step 1: sent / awaiting → REMINDER_1 ────────────────────────────────
    ids = await _transition(
        session,
        from_states=[
            CustomerVCardState.VCARD_SENT.value,
            CustomerVCardState.AWAITING_VALIDATION.value,
        ],
        to_state=CustomerVCardState.REMINDER_1.value,
        older_than=now - timedelta(hours=settings.vcard_validation_timeout_hours),
        limit=batch_limit,
    )
    result.reminder_1 = len(ids)
    if ids:
        result.customer_ids["reminder_1"] = ids
        if producer:
            await _publish_reminders(session, producer, ids, "reminder_1")

    # ── Step 2: REMINDER_1 → REMINDER_2 ─────────────────────────────────────
    ids = await _transition(
        session,
        from_states=[CustomerVCardState.REMINDER_1.value],
        to_state=CustomerVCardState.REMINDER_2.value,
        older_than=now - timedelta(hours=settings.vcard_reminder_2_delay_hours),
        limit=batch_limit,
    )
    result.reminder_2 = len(ids)
    if ids:
        result.customer_ids["reminder_2"] = ids
        if producer:
            await _publish_reminders(session, producer, ids, "reminder_2")

    # ── Step 3: REMINDER_2 → DORMANT ────────────────────────────────────────
    ids = await _transition(
        session,
        from_states=[CustomerVCardState.REMINDER_2.value],
        to_state=CustomerVCardState.DORMANT.value,
        older_than=now - timedelta(days=settings.vcard_dormant_delay_days),
        limit=batch_limit,
    )
    result.dormant = len(ids)
    if ids:
        result.customer_ids["dormant"] = ids

    logger.info(
        "vcard_sweep_completed",
        reminder_1=result.reminder_1,
        reminder_2=result.reminder_2,
        dormant=result.dormant,
        batch_limit=batch_limit,
    )

    return result


def schedule_vcard_reminder(
    tenant_id: str,
    customer_phone: str,
    current_state: str,
    delay_seconds: int,
) -> str | None:
    """
    Ask Celery to re-check a single customer's VCard state after a delay.

    Called inline by the VCard gatekeeper Kafka worker right after it sends a
    card. This is an *optimisation* on top of the hourly
    `omniflow.vcard_reminder_check` sweep, which remains the safety net — if
    Celery is unavailable the customer is still picked up within the hour.

    Returns the Celery task id, or None when the task could not be queued.
    """
    log = logger.bind(
        task="vcard_reminder",
        tenant_id=tenant_id,
        customer_phone=customer_phone,
        current_state=str(current_state),
        delay_seconds=delay_seconds,
    )
    try:
        # Imported lazily: the Kafka workers must not pull in Celery (and a
        # Redis broker connection) just to import this module.
        from src.celery_app.tasks import vcard_reminder_check

        async_result = vcard_reminder_check.apply_async(countdown=delay_seconds)
        log.info("celery_task_scheduled", task_id=async_result.id)
        return async_result.id
    except Exception as exc:
        # Never break message processing because the scheduler is down.
        log.warning("celery_task_schedule_failed", error=str(exc)[:200])
        return None


__all__ = [
    "VCardSweepResult",
    "advance_vcard_states",
    "schedule_vcard_reminder",
]
