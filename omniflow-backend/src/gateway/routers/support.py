"""
gateway/routers/support.py — Support Tickets REST API

Endpoints (prefix /api/v1/support):
    GET   /tickets        → list this tenant's tickets
    POST  /tickets        → create a ticket (subject, message)
    PATCH /tickets/{id}   → update the ticket status

Table `support_tickets` is created by alembic migration
0007_support_tickets_and_broadcast_campaigns and is RLS-protected like every
other tenant-scoped table.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.enums import SupportTicketStatus
from src.shared.db.models import SupportTicket

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/support", tags=["Support"])


# ══════════════════════════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════════════════════════

class TicketItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket_id: uuid.UUID
    tenant_id: uuid.UUID
    created_by_user_id: Optional[uuid.UUID] = None
    subject: str
    message: str
    status: str
    created_at: datetime
    updated_at: datetime


class TicketPage(BaseModel):
    items: list[TicketItem]
    total: int
    page: int
    page_size: int


class TicketCreate(BaseModel):
    subject: str = Field(..., min_length=3, max_length=255)
    message: str = Field(..., min_length=1, max_length=10_000)


class TicketPatch(BaseModel):
    status: SupportTicketStatus = Field(
        ..., description="open | in_progress | closed"
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/support/tickets
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/tickets",
    response_model=TicketPage,
    status_code=status.HTTP_200_OK,
    summary="List support tickets",
)
async def list_tickets(
    user: CurrentUser,
    session: AuthTenantSession,
    status_filter: Annotated[
        SupportTicketStatus | None,
        Query(alias="status", description="Filter by ticket status"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TicketPage:
    stmt = select(SupportTicket)
    if status_filter is not None:
        stmt = stmt.where(SupportTicket.status == status_filter.value)

    total = await session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    rows = (
        await session.execute(
            stmt.order_by(SupportTicket.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return TicketPage(
        items=[TicketItem.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/support/tickets
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/tickets",
    response_model=TicketItem,
    status_code=status.HTTP_201_CREATED,
    summary="Create a support ticket",
)
async def create_ticket(
    user: CurrentUser,
    session: AuthTenantSession,
    body: TicketCreate,
) -> TicketItem:
    # tenant_id comes from the verified token, never from the request body.
    ticket = SupportTicket(
        tenant_id=user.tenant_id,
        created_by_user_id=user.user_id,
        subject=body.subject.strip(),
        message=body.message.strip(),
        status=SupportTicketStatus.OPEN,
    )
    session.add(ticket)
    await session.flush()
    await session.refresh(ticket)

    logger.info(
        "support_ticket_created",
        ticket_id=str(ticket.ticket_id),
        tenant_id=str(user.tenant_id),
    )
    return TicketItem.model_validate(ticket)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /api/v1/support/tickets/{ticket_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/tickets/{ticket_id}",
    response_model=TicketItem,
    status_code=status.HTTP_200_OK,
    summary="Update a ticket's status",
)
async def update_ticket(
    user: CurrentUser,
    session: AuthTenantSession,
    ticket_id: Annotated[uuid.UUID, Path(description="Ticket UUID")],
    body: TicketPatch,
) -> TicketItem:
    ticket = await session.scalar(
        select(SupportTicket).where(SupportTicket.ticket_id == ticket_id)
    )
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Ticket not found."},
        )

    ticket.status = body.status
    await session.flush()
    await session.refresh(ticket)

    logger.info(
        "support_ticket_status_changed",
        ticket_id=str(ticket_id),
        new_status=body.status.value,
    )
    return TicketItem.model_validate(ticket)
