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

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.core.config import get_settings
from src.shared.core.enums import CustomerVCardState
from src.shared.db.models import Customer

logger = structlog.get_logger(__name__)
settings = get_settings()


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


async def advance_vcard_states(
    session: AsyncSession,
    *,
    batch_limit: int = 500,
) -> VCardSweepResult:
    """
    Run one pass of the VCard drip sequence.

    The caller owns the session/transaction. Use a system session to sweep
    across every tenant, or a tenant session to sweep one tenant only.

    NOTE (deliberate limitation): this advances the persisted state machine
    and emits a structured log line per batch. It does NOT itself push the
    reminder message to WhatsApp — outbound delivery goes through the Kafka
    topic `messages.outgoing.v1` and needs a conversation/platform thread id
    that is not derivable from the `customers` row alone. The outbound hook is
    marked with `TODO(outbound)` below and is the one remaining piece of work
    to make reminders user-visible.
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

    # TODO(outbound): publish a reminder OutboundMessage on
    # settings.kafka_topic_messages_outgoing for each id in
    # result.customer_ids["reminder_1"|"reminder_2"]. Requires resolving the
    # customer's most recent Conversation to obtain conversation_id and
    # platform_conversation_id.

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
