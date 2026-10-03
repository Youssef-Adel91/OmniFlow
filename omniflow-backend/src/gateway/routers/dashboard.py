"""
gateway/routers/dashboard.py — Dashboard Summary API

Endpoint (prefix /api/v1/dashboard):
    GET /summary   → headline counters for the authenticated tenant
    GET /analytics → operational analytics (KPIs + period-over-period change,
                     daily series, funnel, busy-hours heatmap, distributions,
                     inventory, topics, attention lists). Definitions live in
                     shared/services/dashboard_analytics.py. Cached in Redis for
                     60 s per (tenant, params); Redis being down only skips the cache.

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

import hashlib
import json
from datetime import date
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, or_, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.enums import ConversationStatus
from src.shared.db.models import Conversation, Customer, CustomerReport
from src.shared.redis_client.client import redis_mgr
from src.shared.services.dashboard_analytics import (
    CHANNELS,
    AnalyticsRangeError,
    build_analytics,
    resolve_window,
)

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


_ANALYTICS_TTL_SECONDS = 60


@router.get(
    "/analytics",
    status_code=status.HTTP_200_OK,
    summary="Operational analytics for the dashboard",
)
async def get_analytics(
    user: CurrentUser,
    session: AuthTenantSession,
    range_: Annotated[Literal["7d", "30d", "90d", "custom"], Query(alias="range")] = "7d",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    channel: Annotated[str | None, Query(description="whatsapp | instagram | messenger | ...")] = None,
    sla_minutes: Annotated[int, Query(ge=1, le=1440)] = 15,
    refresh: Annotated[bool, Query(description="Bypass the 60 s cache (live auto-refresh)")] = False,
) -> dict[str, Any]:
    if channel is not None:
        channel = channel.lower()
        if channel in ("", "all"):
            channel = None
        elif channel not in CHANNELS:
            raise HTTPException(status_code=422, detail=f"unknown channel '{channel}'")
    try:
        window = resolve_window(range_, date_from, date_to)
    except AnalyticsRangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    key_src = f"{window.first_day}|{window.last_day}|{channel}|{sla_minutes}"
    cache_key = f"dash:analytics:{user.tenant_id}:{hashlib.sha256(key_src.encode()).hexdigest()[:16]}"
    try:
        cached = None if refresh else await redis_mgr.get_raw(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:  # noqa: BLE001 -- cache is best-effort
        logger.warning("dashboard_analytics_cache_read_failed", exc_info=True)

    data = await build_analytics(session, user.tenant_id, window, channel, sla_minutes)

    try:
        await redis_mgr.set_raw(cache_key, json.dumps(data, default=str), ttl=_ANALYTICS_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("dashboard_analytics_cache_write_failed", exc_info=True)

    logger.info("dashboard_analytics_served", tenant_id=str(user.tenant_id), range=range_, channel=channel)
    return data
