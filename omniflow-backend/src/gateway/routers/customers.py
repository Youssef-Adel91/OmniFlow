"""
gateway/routers/customers.py — Customers REST API

Endpoints (prefix /api/v1/customers):
    GET   /                 → paginated list  {items, total, page, page_size}
    GET   /{customer_id}    → single customer
    PATCH /{customer_id}    → update display_name / is_vip / is_processing_restricted

Security:
    Every endpoint depends on `get_current_user` (Clerk JWT). The DB session
    is opened with the RLS context taken from the *verified token*, never from
    a client header, so cross-tenant access is impossible at the SQL layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.db.models import Conversation, Customer, Message
from src.shared.services.lead_scoring import CustomerSignals, LeadScore, compute_lead_score

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/customers", tags=["Customers"])


# ══════════════════════════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════════════════════════

class CustomerItem(BaseModel):
    """Customer as returned by the dashboard API."""
    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    unified_phone: str
    display_name: Optional[str] = None
    whatsapp_profile_name: Optional[str] = None
    is_vip: bool
    is_processing_restricted: bool
    vcard_state: str
    vcard_opened_at: Optional[datetime] = None
    engagement_score: Optional[int] = None
    extracted_profile: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    # ── Purchase-likelihood scoring ──────────────────────────────────────
    lead_score: int = 0
    lead_tier: str = "cold"


class CustomerPage(BaseModel):
    items: list[CustomerItem]
    total: int
    page: int
    page_size: int


class CustomerPatch(BaseModel):
    """Partial update — only the supplied fields are written."""
    display_name: Optional[str] = Field(default=None, max_length=200)
    is_vip: Optional[bool] = None
    is_processing_restricted: Optional[bool] = None


# ══════════════════════════════════════════════════════════════════════════════
# Lead scoring — real aggregates, no N+1
# ══════════════════════════════════════════════════════════════════════════════

async def _load_lead_scores(
    session: AsyncSession, customer_ids: list[uuid.UUID]
) -> dict[uuid.UUID, LeadScore]:
    """
    Compute a real LeadScore per customer_id from two aggregate queries
    (never one query per customer) — see shared/services/lead_scoring.py for
    the formula and exactly which real DB fields it uses.
    """
    if not customer_ids:
        return {}

    from sqlalchemy import case

    customers = (
        await session.execute(select(Customer).where(Customer.customer_id.in_(customer_ids)))
    ).scalars().all()
    by_id = {c.customer_id: c for c in customers}

    agg_rows = (
        await session.execute(
            select(
                Conversation.customer_id,
                func.count(func.distinct(Conversation.conversation_id)).label("conversation_count"),
                func.coalesce(func.sum(Conversation.message_count), 0).label("total_messages"),
                func.max(Conversation.last_message_at).label("last_message_at"),
                func.max(
                    case((Message.llm_routing_tier.in_(["L2", "L3", "VAULT"]), 1), else_=0)
                ).label("reached_deep_tier"),
            )
            .select_from(Conversation)
            .outerjoin(Message, Message.conversation_id == Conversation.conversation_id)
            .where(Conversation.customer_id.in_(customer_ids))
            .group_by(Conversation.customer_id)
        )
    ).all()
    agg_by_id = {row.customer_id: row for row in agg_rows}

    text_rows = (
        await session.execute(
            select(Conversation.customer_id, Message.text_content)
            .select_from(Message)
            .join(Conversation, Conversation.conversation_id == Message.conversation_id)
            .where(
                Conversation.customer_id.in_(customer_ids),
                Message.sender_type == "customer",
                Message.text_content.is_not(None),
            )
        )
    ).all()
    texts_by_id: dict[uuid.UUID, list[str]] = {}
    for cid, text in text_rows:
        texts_by_id.setdefault(cid, []).append(text)

    scores = {}
    for cid in customer_ids:
        customer = by_id.get(cid)
        if customer is None:
            continue
        agg = agg_by_id.get(cid)
        signals = CustomerSignals(
            is_vip=customer.is_vip,
            vcard_state=str(customer.vcard_state),
            vcard_opened_at=customer.vcard_opened_at,
            conversation_count=agg.conversation_count if agg else 0,
            total_messages=int(agg.total_messages) if agg else 0,
            last_message_at=agg.last_message_at if agg else None,
            reached_deep_tier=bool(agg.reached_deep_tier) if agg else False,
            customer_message_texts=texts_by_id.get(cid, []),
            extracted_profile=customer.extracted_profile,
        )
        scores[cid] = compute_lead_score(signals)
    return scores


def _customer_item_with_score(customer: Customer, score: LeadScore | None) -> CustomerItem:
    item = CustomerItem.model_validate(customer)
    if score is not None:
        item.lead_score = score.score
        item.lead_tier = str(score.tier)
    return item


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/customers
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=CustomerPage, include_in_schema=False)
@router.get(
    "/",
    response_model=CustomerPage,
    status_code=status.HTTP_200_OK,
    summary="List customers",
    description=(
        "Paginated customer list for the authenticated tenant.\n\n"
        "- `status`: filters on the VCard state machine value "
        "(e.g. `STATE_VCARD_SENT`), or the shortcuts `vip` / `restricted`.\n"
        "- `search`: case-insensitive partial match on phone, display name or "
        "WhatsApp profile name.\n"
        "- `sort=lead_score`: ranked purchase-likelihood view (highest first). "
        "Scores every matching customer via real conversation/message "
        "aggregates before paginating — see shared/services/lead_scoring.py."
    ),
)
async def list_customers(
    user: CurrentUser,
    session: AuthTenantSession,
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="vcard_state value, or 'vip' / 'restricted'"),
    ] = None,
    search: Annotated[
        str | None,
        Query(max_length=100, description="Partial match on phone or name"),
    ] = None,
    sort: Annotated[
        str, Query(description="'created_at' (default) or 'lead_score' for the ranked view")
    ] = "created_at",
    page: Annotated[int, Query(ge=1, description="1-indexed page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
) -> CustomerPage:
    stmt = select(Customer)

    if status_filter:
        normalised = status_filter.strip()
        if normalised.lower() == "vip":
            stmt = stmt.where(Customer.is_vip.is_(True))
        elif normalised.lower() == "restricted":
            stmt = stmt.where(Customer.is_processing_restricted.is_(True))
        else:
            stmt = stmt.where(Customer.vcard_state == normalised)

    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Customer.unified_phone.ilike(pattern),
                Customer.display_name.ilike(pattern),
                Customer.whatsapp_profile_name.ilike(pattern),
            )
        )

    total = await session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    if sort == "lead_score":
        # Ranking requires scoring every matching customer before paginating
        # (the score isn't an indexed column) -- fine at realistic tenant
        # sizes via the two-aggregate-query approach in _load_lead_scores;
        # revisit with a persisted/cached score if a tenant's customer count
        # ever makes this slow.
        all_rows = (await session.execute(stmt)).scalars().all()
        scores = await _load_lead_scores(session, [c.customer_id for c in all_rows])
        ranked = sorted(
            all_rows,
            key=lambda c: scores[c.customer_id].score if c.customer_id in scores else 0,
            reverse=True,
        )
        page_rows = ranked[(page - 1) * page_size : (page - 1) * page_size + page_size]
        items = [_customer_item_with_score(c, scores.get(c.customer_id)) for c in page_rows]
    else:
        rows = (
            await session.execute(
                stmt.order_by(Customer.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        scores = await _load_lead_scores(session, [c.customer_id for c in rows])
        items = [_customer_item_with_score(c, scores.get(c.customer_id)) for c in rows]

    return CustomerPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/customers/{customer_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{customer_id}",
    response_model=CustomerItem,
    status_code=status.HTTP_200_OK,
    summary="Get a single customer",
)
async def get_customer(
    user: CurrentUser,
    session: AuthTenantSession,
    customer_id: Annotated[uuid.UUID, Path(description="Customer UUID")],
) -> CustomerItem:
    customer = await session.scalar(
        select(Customer).where(Customer.customer_id == customer_id)
    )
    if customer is None:
        # RLS makes other tenants' rows invisible, so "not visible" and
        # "does not exist" are indistinguishable — both are a plain 404.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Customer not found."},
        )
    scores = await _load_lead_scores(session, [customer.customer_id])
    return _customer_item_with_score(customer, scores.get(customer.customer_id))


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /api/v1/customers/{customer_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/{customer_id}",
    response_model=CustomerItem,
    status_code=status.HTTP_200_OK,
    summary="Update a customer",
    description=(
        "Partial update. Only `display_name`, `is_vip` and "
        "`is_processing_restricted` may be changed from the dashboard; all "
        "other fields are owned by the ingestion pipeline."
    ),
)
async def update_customer(
    user: CurrentUser,
    session: AuthTenantSession,
    customer_id: Annotated[uuid.UUID, Path(description="Customer UUID")],
    body: CustomerPatch,
) -> CustomerItem:
    customer = await session.scalar(
        select(Customer).where(Customer.customer_id == customer_id)
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Customer not found."},
        )

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(customer, field, value)

    await session.flush()
    await session.refresh(customer)

    logger.info(
        "customer_updated",
        customer_id=str(customer_id),
        tenant_id=str(user.tenant_id),
        fields=sorted(updates.keys()),
    )
    return CustomerItem.model_validate(customer)
