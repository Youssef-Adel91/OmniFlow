"""
Automatic follow-up when a lead goes quiet, and the viewing reminder. Both are conservative by construction:

  * A follow-up is only ever SCHEDULED after the assistant itself replied (so the customer started the conversation), and it is CANCELLED the moment the customer
    writes, a human takes over, the customer opts out (any channel), is restricted under PDPL, or the deal is won/lost.
  * It is re-checked at send time (flags, tenant switch, customer, conversation status, open deal) — a task can never outlive the condition that justified it.
  * 24-hour window: inside it the nudge is plain text. Outside it WhatsApp needs a pre-approved template (`sales.whatsapp_followup_template`); Instagram/Messenger
    have no equivalent for automated messages, so the nudge is SKIPPED (status "skipped", reason recorded) rather than sent against Meta's policy.
  * Max 3 steps per cycle, 2 cycles per customer.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.shared.core.config import get_settings
from src.shared.core.enums import ConversationStatus
from src.shared.db.models import Appointment, Conversation, Customer, Deal, FollowupTask, Message
from src.shared.events.outbound import OutboundMessage

logger = structlog.get_logger(__name__)
WINDOW = timedelta(hours=24)
MAX_CYCLES = 2
OPEN = ("new", "qualified", "viewing", "negotiation")


def first_name(customer: Customer | None) -> str:
    name = (getattr(customer, "display_name", None) or getattr(customer, "whatsapp_profile_name", None) or "").strip()
    return name.split(" ")[0][:30] if name else ""


def render(template: str, name: str) -> str:
    import re

    out = template.replace("{name}", name)
    out = re.sub(r"\s{2,}", " ", out).replace(" ،", "،").replace(" ,", ",")
    return out.strip()


async def last_customer_message_at(session, conversation_id: uuid.UUID) -> datetime | None:
    return await session.scalar(select(func.max(Message.created_at)).where(Message.conversation_id == conversation_id, Message.sender_type == "customer"))


def in_window(last_inbound: datetime | None, now: datetime) -> bool:
    return last_inbound is not None and now - last_inbound < WINDOW


def enabled(prefs_sales: dict[str, Any]) -> bool:
    return bool(get_settings().feature_sales_closing and prefs_sales.get("enabled") and prefs_sales.get("followup_enabled"))


async def schedule(session, tenant_id: uuid.UUID, customer: Customer, conversation_id: uuid.UUID, channel: str, prefs_sales: dict[str, Any],
                   *, anchor: datetime | None = None) -> int:
    """(Re)arm the steps after the assistant's reply. Returns how many steps are pending."""
    delays = list(prefs_sales.get("followup_delays_hours") or [])
    if not enabled(prefs_sales) or not delays:
        return 0
    if customer.opt_out_at or customer.is_processing_restricted or customer.erased_at:
        return 0
    q = dict(customer.qualification or {})
    if int(q.get("followup_cycles", 0)) >= MAX_CYCLES and not await _has_pending(session, tenant_id, conversation_id):
        return 0
    anchor = anchor or datetime.now(timezone.utc)
    fresh_cycle = not await _has_pending(session, tenant_id, conversation_id)
    for i, hours in enumerate(delays, start=1):
        stmt = pg_insert(FollowupTask).values(task_id=uuid.uuid4(), tenant_id=tenant_id, customer_id=customer.customer_id, conversation_id=conversation_id,
                                              channel=channel, step=i, due_at=anchor + timedelta(hours=hours), status="pending")
        stmt = stmt.on_conflict_do_update(constraint="uq_followup_conversation_step",
                                          set_={"due_at": stmt.excluded.due_at, "status": "pending", "reason": None, "sent_at": None, "channel": channel})
        await session.execute(stmt)
    if fresh_cycle:
        customer.qualification = {**q, "followup_cycles": int(q.get("followup_cycles", 0)) + 1}
    return len(delays)


async def _has_pending(session, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> bool:
    return bool(await session.scalar(select(func.count()).select_from(FollowupTask).where(
        FollowupTask.tenant_id == tenant_id, FollowupTask.conversation_id == conversation_id, FollowupTask.status == "pending")))


@dataclass
class SweepResult:
    sent: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    failed: int = 0

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


async def _decide(session, task: FollowupTask, now: datetime):
    """(customer, conversation, prefs_sales, skip_reason). Re-validates everything at send time."""
    from src.shared.services import tenant_prefs

    cfg = get_settings()
    if not cfg.feature_sales_closing:
        return None, None, {}, "platform_flag_off"
    customer = await session.scalar(select(Customer).where(Customer.tenant_id == task.tenant_id, Customer.customer_id == task.customer_id))
    conv = await session.scalar(select(Conversation).where(Conversation.tenant_id == task.tenant_id, Conversation.conversation_id == task.conversation_id))
    if customer is None or conv is None:
        return customer, conv, {}, "missing_records"
    if customer.opt_out_at or customer.is_processing_restricted or customer.erased_at or customer.archived_at:
        return customer, conv, {}, "customer_opted_out_or_restricted"
    prefs = (await tenant_prefs.get_prefs(task.tenant_id)).get("sales") or {}
    prefs = tenant_prefs.with_defaults("sales", prefs)
    if not (prefs["enabled"] and prefs["followup_enabled"]):
        return customer, conv, prefs, "tenant_disabled"
    if conv.status != ConversationStatus.AI_ACTIVE.value and str(conv.status) != "ai_active":
        return customer, conv, prefs, "human_involved"
    deal = await session.scalar(select(Deal).where(Deal.tenant_id == task.tenant_id, Deal.customer_id == task.customer_id, Deal.stage.in_(OPEN)))
    if deal is None:
        return customer, conv, prefs, "no_open_deal"
    return customer, conv, prefs, None


async def process_due(session, producer, *, now: datetime | None = None, batch: int = 200) -> SweepResult:
    """Send (or skip, with a reason) every due task. The caller owns the (system) session and a started Kafka producer."""
    now = now or datetime.now(timezone.utc)
    res = SweepResult()
    tasks = (await session.execute(select(FollowupTask).where(FollowupTask.status == "pending", FollowupTask.due_at <= now)
                                   .order_by(FollowupTask.due_at).limit(batch).with_for_update(skip_locked=True))).scalars().all()
    for t in tasks:
        customer, conv, prefs, why = await _decide(session, t, now)
        if why:
            t.status, t.reason = "skipped", why
            res.skip(why)
            continue
        messages = prefs["followup_messages"]
        text = render(messages[min(t.step, len(messages)) - 1], first_name(customer))
        last_in = await last_customer_message_at(session, t.conversation_id)
        template = None
        if not in_window(last_in, now):
            if t.channel == "whatsapp" and prefs.get("whatsapp_followup_template"):
                template = prefs["whatsapp_followup_template"]
            else:
                t.status, t.reason = "skipped", "outside_24h_window_no_template" if t.channel == "whatsapp" else "outside_window"
                res.skip(t.reason)
                continue
        try:
            recipient = customer.contact_key_for(t.channel)
            await producer.publish(
                topic=get_settings().kafka_topic_messages_outgoing,
                event=OutboundMessage(tenant_id=t.tenant_id, conversation_id=t.conversation_id, customer_phone=recipient,
                                      platform_conversation_id=conv.platform_conversation_id or recipient, channel=t.channel, text=text, message_type="text",
                                      template_name=template, template_language=prefs.get("whatsapp_followup_template_lang") or "ar",
                                      template_params=[first_name(customer) or "عميلنا"] if template else [],
                                      source_event_id=uuid.uuid4(), routing_tier_used="FOLLOWUP", model_used="deterministic"),
                key=OutboundMessage.kafka_key(t.tenant_id, recipient))
            t.status, t.sent_at = "sent", now
            res.sent += 1
        except Exception as exc:  # noqa: BLE001 -- one bad task must not stop the sweep; retried next run (stays pending)
            res.failed += 1
            logger.warning("followup_publish_failed", task_id=str(t.task_id), error=type(exc).__name__)
    return res


async def remind_viewings(session, producer, *, now: datetime | None = None, batch: int = 200) -> dict[str, int]:
    """Remind customers `reminder_hours_before` hours before a booked viewing. Inside the 24-hour window: plain text. Outside it: the approved REMINDER template (`whatsapp_reminder_template`, two variables: name + time) if the tenant has one;
    otherwise the STAFF member is notified instead (so the reminder is never silently lost). Marks `reminder_sent_at` either way."""
    from src.sales import booking
    from src.shared.services import tenant_prefs
    from src.shared.services.notifications import notify_safe

    now = now or datetime.now(timezone.utc)
    out = {"customer": 0, "staff_only": 0}
    horizon = now + timedelta(hours=48)
    rows = (await session.execute(select(Appointment).where(Appointment.status == "scheduled", Appointment.reminder_sent_at.is_(None),
                                                            Appointment.scheduled_at > now, Appointment.scheduled_at <= horizon)
                                  .order_by(Appointment.scheduled_at).limit(batch).with_for_update(skip_locked=True))).scalars().all()
    for ap in rows:
        prefs = tenant_prefs.with_defaults("sales", (await tenant_prefs.get_prefs(ap.tenant_id)).get("sales"))
        if ap.scheduled_at - now > timedelta(hours=prefs["reminder_hours_before"]):
            continue
        customer = await session.scalar(select(Customer).where(Customer.tenant_id == ap.tenant_id, Customer.customer_id == ap.customer_id))
        conv = await session.scalar(select(Conversation).where(Conversation.tenant_id == ap.tenant_id, Conversation.conversation_id == ap.conversation_id))
        if customer is None or conv is None:
            ap.reminder_sent_at = now
            continue
        tz = prefs["viewing_hours"].get("timezone", "Asia/Riyadh")
        when = booking.format_slot(ap.scheduled_at, tz)
        text = f"تذكير بموعد المعاينة: {when}." + (f"\nالموقع: {ap.location_note}" if ap.location_note else "") + "\nإذا احتجت تغيير الموعد قل لي بس."
        last_in = await last_customer_message_at(session, ap.conversation_id)
        channel = str(conv.channel)
        template = (prefs.get("whatsapp_reminder_template") or None) if channel == "whatsapp" and not in_window(last_in, now) else None
        can_send = (not customer.is_processing_restricted and not customer.erased_at and not customer.opt_out_at
                    and (in_window(last_in, now) or bool(template)))
        if can_send:
            recipient = customer.contact_key_for(channel)
            try:
                await producer.publish(
                    topic=get_settings().kafka_topic_messages_outgoing,
                    event=OutboundMessage(tenant_id=ap.tenant_id, conversation_id=ap.conversation_id, customer_phone=recipient,
                                          platform_conversation_id=conv.platform_conversation_id or recipient, channel=channel, text=text, message_type="text",
                                          template_name=template, template_language=prefs.get("whatsapp_followup_template_lang") or "ar",
                                          template_params=[first_name(customer) or "عميلنا", when] if template else [],
                                          source_event_id=uuid.uuid4(), routing_tier_used="REMINDER", model_used="deterministic"),
                    key=OutboundMessage.kafka_key(ap.tenant_id, recipient))
                out["customer"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("viewing_reminder_publish_failed", appointment_id=str(ap.appointment_id), error=type(exc).__name__)
                continue
        else:
            out["staff_only"] += 1
        await notify_safe(session, ap.tenant_id, "viewing_reminder", "موعد معاينة قريب",
                          f"{first_name(customer) or 'العميل'} — {when}" + ("" if can_send else " (تعذّر إرسال تذكير تلقائي للعميل: اتصل به أو راسله يدويًا)"),
                          f"/inbox?conversation={ap.conversation_id}", user_ids=[ap.staff_user_id or ap.created_by_user_id] if (ap.staff_user_id or ap.created_by_user_id) else None,
                          dedupe_key=f"viewing:{ap.appointment_id}")
        ap.reminder_sent_at = now
    return out
