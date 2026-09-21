"""
gateway/routers/dashboard.py — Dashboard Summary API

Endpoint (prefix /api/v1/dashboard):
    GET /summary → headline counters for the authenticated tenant

Every number is a live COUNT against PostgreSQL inside the tenant's RLS
session — nothing is mocked or cached.

    total_conversations   COUNT(conversations)
    active_conversations  COUNT(conversations WHERE status IN
                          (ai_active, human_active, escalated))
    total_customers       COUNT(customers)
    hot_leads_count       COUNT(customers WHERE engagement_score > 70 OR is_vip)
    reports_sold_count    COUNT(customer_reports WHERE is_delivered = true)
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, or_, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.enums import ConversationStatus
from src.shared.db.models import Conversation, Customer, CustomerReport

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"])

# Threshold above which a lead is considered "hot" by the AI scorer.
_HOT_LEAD_SCORE = 70

_ACTIVE_CONVERSATION_STATES = (
    ConversationStatus.AI_ACTIVE.value,
    ConversationStatus.HUMAN_ACTIVE.value,
    ConversationStatus.ESCALATED.value,
)


class DashboardSummary(BaseModel):
    total_conversations: int = Field(..., description="All conversations, any status")
    active_conversations: int = Field(
        ..., description="Status in ai_active | human_active | escalated"
    )
    total_customers: int = Field(..., description="All customers for this tenant")
    hot_leads_count: int = Field(
        ..., description="engagement_score > 70 OR is_vip = true"
    )
    reports_sold_count: int = Field(
        ..., description="customer_reports with is_delivered = true"
    )


async def _count(session, stmt: Select) -> int:
    """Run `SELECT count(*) FROM (<stmt>)` and normalise NULL to 0."""
    return await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0


@router.get(
    "/summary",
    response_model=DashboardSummary,
    status_code=status.HTTP_200_OK,
    summary="Dashboard headline counters",
)
async def get_summary(
    user: CurrentUser,
    session: AuthTenantSession,
) -> DashboardSummary:
    total_conversations = await _count(session, select(Conversation.conversation_id))

    active_conversations = await _count(
        session,
        select(Conversation.conversation_id).where(
            Conversation.status.in_(_ACTIVE_CONVERSATION_STATES)
        ),
    )

    total_customers = await _count(session, select(Customer.customer_id))

    hot_leads_count = await _count(
        session,
        select(Customer.customer_id).where(
            or_(
                Customer.engagement_score > _HOT_LEAD_SCORE,
                Customer.is_vip.is_(True),
            )
        ),
    )

    reports_sold_count = await _count(
        session,
        select(CustomerReport.report_id).where(
            CustomerReport.is_delivered.is_(True)
        ),
    )

    logger.info("dashboard_summary_served", tenant_id=str(user.tenant_id))

    return DashboardSummary(
        total_conversations=total_conversations,
        active_conversations=active_conversations,
        total_customers=total_customers,
        hot_leads_count=hot_leads_count,
        reports_sold_count=reports_sold_count,
    )
