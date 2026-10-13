"""
gateway/routers/deals.py — the sales pipeline API (prefix /api/v1/deals)

    GET   /board                              → Kanban data: one column per stage (open stages in full; won/lost for the last 30 days)
    PATCH /{deal_id}                          → move a deal / close it (won | lost + reason) / set value / assign   (admin or agent)
    GET   /by-conversation/{conversation_id}  → the hand-off summary for the inbox side panel: qualification, label, deal, viewing, matching listings
    GET   /report                             → conversion by channel (deals → qualified → viewing → negotiation → won), lost reasons, avg days to win

Security: Clerk JWT; the DB session carries the verified tenant (RLS) — a deal id of another tenant is a 404. Auditors are read-only. The whole API answers 404 while
FEATURE_SALES_CLOSING is off, so a deployment that never enabled the feature exposes nothing new.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Optional

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.gateway.dependencies import AuthTenantSession, CurrentUser, CustomerWriteUser
from src.sales import matcher, pipeline, qualification
from src.shared.core.config import get_settings
from src.shared.db.models import Appointment, AuditLog, Conversation, Customer, Deal

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/deals", tags=["Sales pipeline"])


def _enabled() -> None:
    if not get_settings().feature_sales_closing:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "غير متاح."})


class DealPatch(BaseModel):
    stage: Optional[Literal["new", "qualified", "viewing", "negotiation", "won", "lost"]] = None
    lost_reason: Optional[Literal["price", "no_response", "competitor", "financing", "not_interested", "other"]] = None
    lost_note: Optional[str] = Field(default=None, max_length=300)
    value_sar: Optional[float] = Field(default=None, ge=0, le=10_000_000_000)
    assigned_user_id: Optional[uuid.UUID] = None


@router.get("/board", summary="Kanban board data")
async def get_board(user: CurrentUser, session: AuthTenantSession, mine: bool = Query(False, description="only deals assigned to me")) -> dict[str, Any]:
    _enabled()
    return await pipeline.board(session, user.tenant_id, assigned_user_id=user.user_id if mine else None)


@router.patch("/{deal_id}", summary="Move / close / assign a deal (admin or agent)")
async def patch_deal(deal_id: uuid.UUID, body: DealPatch, user: CustomerWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    _enabled()
    try:
        if body.stage is None:
            deal = await session.scalar(select(Deal).where(Deal.tenant_id == user.tenant_id, Deal.deal_id == deal_id))
            if deal is None:
                raise LookupError
            if body.value_sar is not None:
                deal.value_sar = body.value_sar
            if body.assigned_user_id is not None:
                deal.assigned_user_id = body.assigned_user_id
            deal.updated_at = datetime.now(timezone.utc)
            before = deal.stage
        else:
            before = await session.scalar(select(Deal.stage).where(Deal.tenant_id == user.tenant_id, Deal.deal_id == deal_id))
            deal = await pipeline.set_stage(session, user.tenant_id, deal_id, body.stage, lost_reason=body.lost_reason, lost_note=body.lost_note,
                                            value_sar=body.value_sar, assigned_user_id=body.assigned_user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "الصفقة غير موجودة."}) from exc
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_DEAL_CHANGE", "message": str(exc)}) from exc
    session.add(AuditLog(tenant_id=user.tenant_id, actor_user_id=getattr(user, "user_id", None), action="deal.updated", entity_type="deal", entity_id=str(deal_id),
                         details={"from": before, "to": deal.stage, "lost_reason": deal.lost_reason}))
    await session.flush()
    customer = await session.scalar(select(Customer).where(Customer.tenant_id == user.tenant_id, Customer.customer_id == deal.customer_id))
    return pipeline._card(deal, customer)


@router.get("/by-conversation/{conversation_id}", summary="Hand-off summary for the inbox panel")
async def by_conversation(conversation_id: uuid.UUID, user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    _enabled()
    conv = await session.scalar(select(Conversation).where(Conversation.tenant_id == user.tenant_id, Conversation.conversation_id == conversation_id))
    if conv is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "المحادثة غير موجودة."})
    customer = await session.scalar(select(Customer).where(Customer.tenant_id == user.tenant_id, Customer.customer_id == conv.customer_id))
    q = dict((customer.qualification or {}) if customer else {})
    deal = await pipeline.open_deal(session, user.tenant_id, conv.customer_id)
    if deal is None:        # show the last closed one rather than nothing
        deal = await session.scalar(select(Deal).where(Deal.tenant_id == user.tenant_id, Deal.customer_id == conv.customer_id).order_by(Deal.updated_at.desc()))
    ap = await session.scalar(select(Appointment).where(Appointment.tenant_id == user.tenant_id, Appointment.conversation_id == conversation_id,
                                                        Appointment.status == "scheduled").order_by(Appointment.scheduled_at))
    match = await matcher.match(session, user.tenant_id, q, limit=3)
    return {
        "label": q.get("label"), "score": q.get("score"), "summary": qualification.public_summary(q), "missing": q.get("missing", []),
        "qualification": {k: q.get(k) for k in ("budget_min", "budget_max", "district", "property_type", "rooms", "purpose", "financing", "urgency", "viewing_requested")},
        "deal": pipeline._card(deal, customer) if deal else None,
        "appointment": {"appointment_id": str(ap.appointment_id), "scheduled_at": ap.scheduled_at.isoformat(), "location_note": ap.location_note, "source": ap.source} if ap else None,
        "suggested_listings": match.listings, "match_note": match.note, "match_exact": match.exact,
    }


@router.get("/report", summary="Conversion report by channel")
async def report(user: CurrentUser, session: AuthTenantSession, days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, Any]:
    _enabled()
    return await pipeline.conversion_report(session, user.tenant_id, since=datetime.now(timezone.utc) - timedelta(days=days))
