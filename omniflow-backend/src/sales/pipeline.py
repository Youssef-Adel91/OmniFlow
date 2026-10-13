"""
The deals pipeline: one OPEN deal per customer (enforced by a partial unique index), moved through
    new → qualified → viewing → negotiation → won | lost (+ reason)
by the assistant (qualification, booking, negotiation detection) or by staff (drag on the board). Every function takes the caller's tenant-scoped session
(RLS is the tenant guard); none of them commits — the caller owns the transaction.

Closing a deal cancels its pending follow-ups. Re-opening a closed deal is refused while the customer already has another open one.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from src.shared.db.models import DEAL_STAGES, LOST_REASONS, OPEN_STAGES, Customer, Deal, FollowupTask

STAGE_LABEL_AR = {"new": "جديد", "qualified": "مؤهَّل", "viewing": "معاينة", "negotiation": "تفاوض", "won": "مغلق ناجح", "lost": "خسارة"}
LOST_REASON_AR = {"price": "السعر", "no_response": "لا رد", "competitor": "منافس", "financing": "التمويل", "not_interested": "غير مهتم", "other": "أخرى"}
_RANK = {s: i for i, s in enumerate(DEAL_STAGES)}


def _set(deal: Deal, stage: str, now: datetime) -> None:
    """Every stage change goes through here so `reached_stage` (the highest open stage ever reached) stays right."""
    deal.stage, deal.stage_changed_at, deal.updated_at = stage, now, now
    if stage in OPEN_STAGES and _RANK[stage] > _RANK.get(deal.reached_stage or "new", 0):
        deal.reached_stage = stage


class PipelineError(ValueError):
    """Message is user-facing Arabic."""


async def open_deal(session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Deal | None:
    return await session.scalar(select(Deal).where(Deal.tenant_id == tenant_id, Deal.customer_id == customer_id, Deal.stage.in_(OPEN_STAGES)))


async def ensure_deal(session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, conversation_id: uuid.UUID | None, channel: str | None,
                      label: str | None = None, score: int = 0, qualified: bool = False) -> Deal:
    """The customer's open deal (created if none). Never moves a deal BACKWARD; `qualified=True` lifts a brand-new deal to "qualified"."""
    deal = await open_deal(session, tenant_id, customer_id)
    if deal is None:
        deal = Deal(tenant_id=tenant_id, customer_id=customer_id, conversation_id=conversation_id, source_channel=(channel or None), stage="new")
        session.add(deal)
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:                         # a concurrent message created it first: use that one
            session.expunge(deal)
            deal = await open_deal(session, tenant_id, customer_id)
            if deal is None:
                raise
    deal.lead_label, deal.score = label or deal.lead_label, int(score or deal.score or 0)
    deal.conversation_id = deal.conversation_id or conversation_id
    if qualified and deal.stage == "new":
        _set(deal, "qualified", datetime.now(timezone.utc))
    deal.updated_at = datetime.now(timezone.utc)
    return deal


async def advance(session, deal: Deal, stage: str) -> bool:
    """Move FORWARD only (assistant-driven). Returns True when the stage changed."""
    if deal.stage in ("won", "lost") or stage not in _RANK or _RANK[stage] <= _RANK[deal.stage] or stage in ("won", "lost"):
        return False
    _set(deal, stage, datetime.now(timezone.utc))
    return True


async def record_offer(session, deal: Deal, amount: float | None) -> None:
    _set(deal, "negotiation", datetime.now(timezone.utc))
    if amount:
        deal.offer_sar = amount


async def set_stage(session, tenant_id: uuid.UUID, deal_id: uuid.UUID, stage: str, *, lost_reason: str | None = None, lost_note: str | None = None,
                    value_sar: float | None = None, assigned_user_id: uuid.UUID | None = None) -> Deal:
    """Staff-driven move (any stage). won/lost close the deal; lost needs a reason; leaving won/lost re-opens it (refused if another open deal exists)."""
    if stage not in DEAL_STAGES:
        raise PipelineError("مرحلة غير معروفة")
    deal = await session.scalar(select(Deal).where(Deal.tenant_id == tenant_id, Deal.deal_id == deal_id))
    if deal is None:
        raise LookupError("الصفقة غير موجودة")
    if stage == "lost" and lost_reason not in LOST_REASONS:
        raise PipelineError("اختر سبب الخسارة")
    now = datetime.now(timezone.utc)
    was_closed = deal.stage in ("won", "lost")
    if was_closed and stage in OPEN_STAGES and await open_deal(session, tenant_id, deal.customer_id):
        raise PipelineError("لدى العميل صفقة مفتوحة أخرى")
    _set(deal, stage, now)
    deal.closed_at = now if stage in ("won", "lost") else None
    deal.lost_reason, deal.lost_note = (lost_reason, (lost_note or "")[:300] or None) if stage == "lost" else (None, None)
    if value_sar is not None:
        deal.value_sar = value_sar
    if assigned_user_id is not None:
        deal.assigned_user_id = assigned_user_id
    if stage in ("won", "lost"):
        await cancel_followups(session, tenant_id, customer_id=deal.customer_id, reason=f"deal_{stage}")
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise PipelineError("لدى العميل صفقة مفتوحة أخرى") from exc
    return deal


async def cancel_followups(session, tenant_id: uuid.UUID, *, customer_id: uuid.UUID | None = None, conversation_id: uuid.UUID | None = None, reason: str) -> int:
    stmt = update(FollowupTask).where(FollowupTask.tenant_id == tenant_id, FollowupTask.status == "pending")
    if customer_id is not None:
        stmt = stmt.where(FollowupTask.customer_id == customer_id)
    if conversation_id is not None:
        stmt = stmt.where(FollowupTask.conversation_id == conversation_id)
    res = await session.execute(stmt.values(status="cancelled", reason=reason[:60]))
    return res.rowcount or 0


def _card(d: Deal, c: Customer | None) -> dict[str, Any]:
    q = (c.qualification or {}) if c else {}
    return {"deal_id": str(d.deal_id), "stage": d.stage, "lead_label": d.lead_label, "score": d.score, "customer_id": str(d.customer_id),
            "customer_name": (c.display_name or c.whatsapp_profile_name or c.phone_e164 or "عميل") if c else "عميل", "conversation_id": str(d.conversation_id) if d.conversation_id else None,
            "channel": d.source_channel, "value_sar": float(d.value_sar) if d.value_sar is not None else None, "offer_sar": float(d.offer_sar) if d.offer_sar is not None else None,
            "assigned_user_id": str(d.assigned_user_id) if d.assigned_user_id else None, "lost_reason": d.lost_reason, "lost_note": d.lost_note,
            "budget_max": q.get("budget_max"), "district": q.get("district"), "property_type": q.get("property_type"),
            "updated_at": d.updated_at.isoformat() if d.updated_at else None, "stage_changed_at": d.stage_changed_at.isoformat() if d.stage_changed_at else None}


async def board(session, tenant_id: uuid.UUID, *, per_column: int = 100, closed_days: int = 30, assigned_user_id: uuid.UUID | None = None,
                now: datetime | None = None) -> dict[str, Any]:
    """Kanban data: open stages in full (newest activity first), won/lost only for the last `closed_days` days."""
    now = now or datetime.now(timezone.utc)
    cols: dict[str, Any] = {}
    for stage in DEAL_STAGES:
        stmt = select(Deal, Customer).join(Customer, Customer.customer_id == Deal.customer_id).where(Deal.tenant_id == tenant_id, Deal.stage == stage)
        if stage in ("won", "lost"):
            stmt = stmt.where(Deal.closed_at >= now - timedelta(days=closed_days))
        if assigned_user_id is not None:
            stmt = stmt.where(Deal.assigned_user_id == assigned_user_id)
        total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (await session.execute(stmt.order_by(Deal.updated_at.desc()).limit(per_column))).all()
        cols[stage] = {"label": STAGE_LABEL_AR[stage], "total": int(total or 0), "cards": [_card(d, c) for d, c in rows]}
    return {"stages": list(DEAL_STAGES), "columns": cols}


async def conversion_report(session, tenant_id: uuid.UUID, *, since: datetime, until: datetime | None = None) -> dict[str, Any]:
    """Per channel: deals created in the window, how many reached each open stage (from `reached_stage`, so a deal that was lost at negotiation still counts as having
    been qualified and viewed), won, lost, win rate, average days to win and the lost reasons. A deal closed as 'won' counts as having reached every open stage."""
    until = until or datetime.now(timezone.utc)
    rows = (await session.execute(select(Deal.source_channel, Deal.stage, Deal.reached_stage, Deal.lost_reason, Deal.created_at, Deal.closed_at).where(
        Deal.tenant_id == tenant_id, Deal.created_at >= since, Deal.created_at < until))).all()
    out: dict[str, dict[str, Any]] = {}

    def bucket(ch: str | None) -> dict[str, Any]:
        return out.setdefault(ch or "unknown", {"deals": 0, "qualified": 0, "viewing": 0, "negotiation": 0, "won": 0, "lost": 0, "_days": [], "lost_reasons": {}})

    for ch, stage, reached, reason, created, closed in rows:
        b = bucket(ch)
        b["deals"] += 1
        rank = _RANK.get(reached or "new", 0)
        for st in ("qualified", "viewing", "negotiation"):
            if rank >= _RANK[st] or stage == "won":
                b[st] += 1
        if stage == "won":
            b["won"] += 1
            if closed and created:
                b["_days"].append((closed - created).total_seconds() / 86400)
        elif stage == "lost":
            b["lost"] += 1
            b["lost_reasons"][reason or "other"] = b["lost_reasons"].get(reason or "other", 0) + 1
    total = {"deals": 0, "qualified": 0, "viewing": 0, "negotiation": 0, "won": 0, "lost": 0}
    for b in out.values():
        days = b.pop("_days")
        b["avg_days_to_win"] = round(sum(days) / len(days), 1) if days else None
        b["win_rate"] = round(b["won"] / b["deals"], 3) if b["deals"] else 0.0
        for k in total:
            total[k] += b[k]
    total["win_rate"] = round(total["won"] / total["deals"], 3) if total["deals"] else 0.0
    return {"since": since.isoformat(), "until": until.isoformat(), "by_channel": out, "total": total}
