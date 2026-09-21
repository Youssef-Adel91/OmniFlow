"""
gateway/routers/reports.py — Customer Reports REST API

Endpoints (prefix /api/v1/reports):
    GET /                   → paginated list  {items, total, page, page_size}
    GET /analytics/revenue  → revenue time series for the dashboard chart
    GET /{report_id}        → single report incl. s3_url

Backed by the existing `customer_reports` table. All access is scoped to the
authenticated tenant through the RLS session.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

import structlog
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.db.models import CustomerReport

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])


# ══════════════════════════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════════════════════════

class ReportItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    report_id: uuid.UUID
    customer_id: uuid.UUID
    report_type: str
    price_sar: Optional[float] = None
    payment_reference: Optional[str] = None
    is_delivered: bool
    created_at: datetime
    updated_at: datetime


class ReportDetail(ReportItem):
    """Detail view — adds the stored object URL."""
    s3_url: Optional[str] = None


class ReportPage(BaseModel):
    items: list[ReportItem]
    total: int
    page: int
    page_size: int


class RevenueBucket(BaseModel):
    """One point on the revenue chart."""

    month: str = Field(
        ...,
        description=(
            "Bucket label. `YYYY-MM` for monthly, `YYYY-MM-DD` (bucket start) "
            "for weekly and daily. Named `month` for frontend compatibility."
        ),
        examples=["2026-08"],
    )
    period_start: date = Field(..., description="First day of the bucket (UTC).")
    total_revenue: float = Field(
        ..., description="Sum of price_sar for reports created in the bucket."
    )
    count: int = Field(..., description="Number of reports created in the bucket.")


class RevenueAnalytics(BaseModel):
    period: str
    buckets: int
    currency: str = "SAR"
    total_revenue: float
    total_count: int
    series: list[RevenueBucket]


# Bucket sizes: period → (postgres date_trunc unit, default number of buckets)
_PERIOD_CONFIG: dict[str, tuple[str, int]] = {
    "daily": ("day", 30),
    "weekly": ("week", 12),
    "monthly": ("month", 6),
}


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/reports
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=ReportPage, include_in_schema=False)
@router.get(
    "/",
    response_model=ReportPage,
    status_code=status.HTTP_200_OK,
    summary="List customer reports",
    description=(
        "Paginated list of generated reports for the authenticated tenant. "
        "Optionally filter by `customer_id` and/or `report_type` "
        "(deed_check_29 | municipal_consulting_15 | premium_consultation)."
    ),
)
async def list_reports(
    user: CurrentUser,
    session: AuthTenantSession,
    customer_id: Annotated[
        uuid.UUID | None, Query(description="Filter by customer UUID")
    ] = None,
    report_type: Annotated[
        str | None, Query(description="Filter by ReportType value")
    ] = None,
    page: Annotated[int, Query(ge=1, description="1-indexed page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
) -> ReportPage:
    stmt = select(CustomerReport)

    if customer_id is not None:
        stmt = stmt.where(CustomerReport.customer_id == customer_id)
    if report_type:
        stmt = stmt.where(CustomerReport.report_type == report_type.strip())

    total = await session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    rows = (
        await session.execute(
            stmt.order_by(CustomerReport.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return ReportPage(
        items=[ReportItem.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/reports/analytics/revenue
#
# Declared before /{report_id} for clarity. (It would not actually collide —
# the path has two segments — but keeping analytics routes above the catch-all
# param route is the convention that stops the next endpoint from breaking.)
# ══════════════════════════════════════════════════════════════════════════════

def _bucket_starts(unit: str, count: int, today: date) -> list[date]:
    """
    Generate the first day of each bucket, oldest first, ending with the bucket
    containing `today`.

    Computed in Python rather than with generate_series so the response always
    contains a full, gap-free series — a month with zero sales must still show
    up on the chart as a zero, not vanish.
    """
    if unit == "day":
        return [today - timedelta(days=i) for i in range(count - 1, -1, -1)]

    if unit == "week":
        # ISO weeks start on Monday, matching Postgres date_trunc('week', …).
        this_week = today - timedelta(days=today.weekday())
        return [this_week - timedelta(weeks=i) for i in range(count - 1, -1, -1)]

    # month
    starts: list[date] = []
    year, month = today.year, today.month
    for _ in range(count):
        starts.append(date(year, month, 1))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(starts))


def _bucket_label(unit: str, start: date) -> str:
    return f"{start.year:04d}-{start.month:02d}" if unit == "month" else start.isoformat()


@router.get(
    "/analytics/revenue",
    response_model=RevenueAnalytics,
    status_code=status.HTTP_200_OK,
    summary="Revenue time series",
    description=(
        "Aggregates `customer_reports.price_sar` into a gap-free time series "
        "for the dashboard chart.\n\n"
        "`period=monthly` (default) returns the last 6 calendar months, "
        "`weekly` the last 12 ISO weeks, `daily` the last 30 days. Override the "
        "length with `buckets`.\n\n"
        "Buckets with no sales are returned with `total_revenue: 0` and "
        "`count: 0` so the frontend can plot the array directly. Revenue is "
        "attributed to `created_at`, i.e. when the report was generated."
    ),
)
async def revenue_analytics(
    user: CurrentUser,
    session: AuthTenantSession,
    period: Annotated[
        Literal["daily", "weekly", "monthly"],
        Query(description="Bucket size"),
    ] = "monthly",
    buckets: Annotated[
        int | None,
        Query(ge=1, le=36, description="How many buckets to return (period-specific default)"),
    ] = None,
    delivered_only: Annotated[
        bool,
        Query(description="Count only reports already delivered to the customer"),
    ] = False,
) -> RevenueAnalytics:
    unit, default_buckets = _PERIOD_CONFIG[period]
    bucket_count = buckets or default_buckets

    today = datetime.now(timezone.utc).date()
    starts = _bucket_starts(unit, bucket_count, today)
    window_start = starts[0]

    bucket_expr = func.date_trunc(unit, CustomerReport.created_at)

    stmt = (
        select(
            bucket_expr.label("bucket"),
            func.coalesce(func.sum(CustomerReport.price_sar), 0).label("total"),
            func.count(CustomerReport.report_id).label("count"),
        )
        .where(CustomerReport.created_at >= window_start)
        .group_by(bucket_expr)
    )
    if delivered_only:
        stmt = stmt.where(CustomerReport.is_delivered.is_(True))

    rows = (await session.execute(stmt)).all()

    # date_trunc returns a timestamptz; key the lookup by its date part.
    by_start: dict[date, tuple[float, int]] = {
        (row.bucket.date() if hasattr(row.bucket, "date") else row.bucket): (
            float(row.total or 0),
            int(row.count or 0),
        )
        for row in rows
    }

    series: list[RevenueBucket] = []
    for start in starts:
        total, count = by_start.get(start, (0.0, 0))
        series.append(
            RevenueBucket(
                month=_bucket_label(unit, start),
                period_start=start,
                total_revenue=round(total, 2),
                count=count,
            )
        )

    return RevenueAnalytics(
        period=period,
        buckets=bucket_count,
        total_revenue=round(sum(b.total_revenue for b in series), 2),
        total_count=sum(b.count for b in series),
        series=series,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/reports/{report_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{report_id}",
    response_model=ReportDetail,
    status_code=status.HTTP_200_OK,
    summary="Get a report, including its stored file URL",
    description=(
        "Returns the report row plus `s3_url`.\n\n"
        "NOTE: `s3_url` is the permanent object URL stored at generation time. "
        "Serving it directly requires the bucket to be reachable by the "
        "client; the intended production behaviour is to swap this for a "
        "short-lived pre-signed URL (see `src/shared/storage/s3.py`)."
    ),
)
async def get_report(
    user: CurrentUser,
    session: AuthTenantSession,
    report_id: Annotated[uuid.UUID, Path(description="Report UUID")],
) -> ReportDetail:
    report = await session.scalar(
        select(CustomerReport).where(CustomerReport.report_id == report_id)
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Report not found."},
        )
    return ReportDetail.model_validate(report)
