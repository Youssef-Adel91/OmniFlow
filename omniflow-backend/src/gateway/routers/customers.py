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

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.db.models import Customer

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
    engagement_score: Optional[int] = None
    created_at: datetime
    updated_at: datetime


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
        "WhatsApp profile name."
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

    rows = (
        await session.execute(
            stmt.order_by(Customer.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return CustomerPage(
        items=[CustomerItem.model_validate(row) for row in rows],
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
    return CustomerItem.model_validate(customer)


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
