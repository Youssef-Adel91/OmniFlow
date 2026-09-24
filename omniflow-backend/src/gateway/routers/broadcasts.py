"""
gateway/routers/broadcasts.py — Broadcast Campaigns REST API

Endpoints (prefix /api/v1/broadcasts):
    GET  /                → list campaigns
    POST /                → create a campaign (always starts as `draft`)
    POST /{id}/schedule   → body {scheduled_at} → status becomes `scheduled`
    POST /{id}/cancel     → status becomes `cancelled`
    GET  /preview-audience → estimated recipient count for a target segment

Status machine enforced here:
    draft ──schedule──► scheduled ──(worker)──► sending ─► completed | failed
    draft | scheduled ──cancel──► cancelled

A campaign that is already `sending`, `completed`, `cancelled` or `failed`
cannot be re-scheduled or cancelled — that returns HTTP 409.

NOTE: this router only manages campaign *records*. Actually dispatching a
campaign is the job of `src/ai_workers/broadcast_worker/worker.py`, triggered
by the `omniflow.broadcast_dispatch_check` Celery Beat sweep (every minute),
which publishes to the `broadcast.marketing.v1` Kafka topic once
`scheduled_at` arrives and the campaign has an approved Meta template.

Template-approval gate: `schedule_campaign` refuses to schedule a campaign
without `meta_template_id` set (HTTP 422). WhatsApp's Cloud API only allows
free-text sends within a customer's 24h session window; a broadcast reaching
customers outside that window with anything other than a pre-approved
template risks Meta banning the sending number. The Beat sweep re-checks this
at dispatch time too, since template approval can be revoked after
scheduling.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional

import structlog
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.config import get_settings
from src.shared.core.enums import BroadcastCampaignStatus
from src.shared.db.models import BroadcastCampaign, Customer

logger = structlog.get_logger(__name__)
_settings = get_settings()

router = APIRouter(prefix="/api/v1/broadcasts", tags=["Broadcasts"])

# Only these states may still be scheduled or cancelled.
_MUTABLE_STATES = {
    BroadcastCampaignStatus.DRAFT.value,
    BroadcastCampaignStatus.SCHEDULED.value,
}

# ── Audience segments ────────────────────────────────────────────────────────
#
# A campaign's `target_audience` is a free-form JSONB object; the *segment* is
# the one key that decides who receives it. Everything else in that object is
# passed through untouched for the worker's own use.
#
# Every segment implicitly excludes `is_processing_restricted` customers —
# those have exercised a PDPL processing objection and must never be messaged.
_SEGMENT_ALL = "all"
_SEGMENT_VIP = "vip"
_SEGMENT_ENGAGED = "engaged"
_SEGMENT_NEW = "new"
_SEGMENT_INACTIVE = "inactive"

VALID_SEGMENTS: tuple[str, ...] = (
    _SEGMENT_ALL,
    _SEGMENT_VIP,
    _SEGMENT_ENGAGED,
    _SEGMENT_NEW,
    _SEGMENT_INACTIVE,
)

# "new" means: first seen within this many days.
_NEW_CUSTOMER_WINDOW_DAYS = 30


# ══════════════════════════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════════════════════════

class CampaignItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    campaign_id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    message_template: str
    target_audience: Optional[dict[str, Any]] = None
    campaign_type: Optional[str] = None
    meta_template_id: Optional[str] = None
    status: str
    recipients_count: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class AudiencePreview(BaseModel):
    """Estimated reach for a target segment, as of right now."""

    target_audience: str = Field(
        ..., description="The segment the estimate was computed for."
    )
    recipients_count: int = Field(
        ..., description="Number of customers currently matching the segment."
    )
    total_customers: int = Field(
        ..., description="Total messageable customers, for context."
    )
    valid_segments: list[str] = Field(
        ..., description="All segment names this endpoint understands."
    )


class CampaignPage(BaseModel):
    items: list[CampaignItem]
    total: int
    page: int
    page_size: int


class CampaignCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    message_template: str = Field(..., min_length=1, max_length=20_000)
    target_audience: Optional[dict[str, Any]] = Field(
        default=None,
        description='Audience selector, e.g. {"list_type": "daily_rentals"}',
    )
    campaign_type: Optional[str] = Field(default=None, max_length=40)
    meta_template_id: Optional[str] = Field(
        default=None,
        max_length=120,
        description=(
            "Approved Meta WhatsApp template ID/name. Not required to save a "
            "draft, but required before it can be scheduled — see the "
            "template-approval gate on POST /{id}/schedule."
        ),
    )


class CampaignSchedule(BaseModel):
    scheduled_at: datetime = Field(
        ..., description="When the campaign should start sending (ISO 8601, UTC)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _normalise_segment(raw: Any) -> str:
    """
    Coerce whatever the caller sent into a known segment name.

    Accepts the segment directly (`"vip"`), or a `target_audience` object using
    either the `segment` key or the legacy `list_type` key that the broadcast
    worker already writes. Anything unrecognised degrades to `all`, because a
    slightly-too-large estimate is a far better failure mode than a 500 on the
    campaign-creation path.
    """
    value: Any = raw
    if isinstance(raw, dict):
        value = raw.get("segment") or raw.get("list_type") or raw.get("audience")

    if not isinstance(value, str):
        return _SEGMENT_ALL

    candidate = value.strip().lower()
    return candidate if candidate in VALID_SEGMENTS else _SEGMENT_ALL


async def _count_audience(session, segment: str) -> int:
    """
    Count the customers a campaign would reach.

    Runs on the tenant-scoped RLS session, so no explicit tenant filter is
    needed (and adding one would be the only thing standing between a bug and
    a cross-tenant count).
    """
    threshold = _settings.vip_engagement_score_threshold
    stmt = select(func.count(Customer.customer_id)).where(
        Customer.is_processing_restricted.is_(False)
    )

    if segment == _SEGMENT_VIP:
        stmt = stmt.where(Customer.is_vip.is_(True))
    elif segment == _SEGMENT_ENGAGED:
        stmt = stmt.where(
            or_(
                Customer.is_vip.is_(True),
                Customer.engagement_score >= threshold,
            )
        )
    elif segment == _SEGMENT_NEW:
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=_NEW_CUSTOMER_WINDOW_DAYS
        )
        stmt = stmt.where(Customer.created_at >= cutoff)
    elif segment == _SEGMENT_INACTIVE:
        stmt = stmt.where(
            or_(
                Customer.engagement_score.is_(None),
                Customer.engagement_score < threshold,
            )
        ).where(Customer.is_vip.is_(False))
    # _SEGMENT_ALL → no extra predicate

    return await session.scalar(stmt) or 0


async def _get_campaign(session, campaign_id: uuid.UUID) -> BroadcastCampaign:
    campaign = await session.scalar(
        select(BroadcastCampaign).where(
            BroadcastCampaign.campaign_id == campaign_id
        )
    )
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Campaign not found."},
        )
    return campaign


def _current_status(campaign: BroadcastCampaign) -> str:
    raw = campaign.status
    return str(raw.value if hasattr(raw, "value") else raw)


def _require_mutable(campaign: BroadcastCampaign, action: str) -> None:
    current = _current_status(campaign)
    if current not in _MUTABLE_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATE_TRANSITION",
                "message": (
                    f"Cannot {action} a campaign in state '{current}'. "
                    f"Allowed states: {sorted(_MUTABLE_STATES)}."
                ),
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/broadcasts
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=CampaignPage, include_in_schema=False)
@router.get(
    "/",
    response_model=CampaignPage,
    status_code=status.HTTP_200_OK,
    summary="List broadcast campaigns",
)
async def list_campaigns(
    user: CurrentUser,
    session: AuthTenantSession,
    status_filter: Annotated[
        BroadcastCampaignStatus | None,
        Query(alias="status", description="Filter by campaign status"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CampaignPage:
    stmt = select(BroadcastCampaign)
    if status_filter is not None:
        stmt = stmt.where(BroadcastCampaign.status == status_filter.value)

    total = await session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    rows = (
        await session.execute(
            stmt.order_by(BroadcastCampaign.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return CampaignPage(
        items=[CampaignItem.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/broadcasts
# ══════════════════════════════════════════════════════════════════════════════

@router.post("", response_model=CampaignItem, status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post(
    "/",
    response_model=CampaignItem,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft campaign",
)
async def create_campaign(
    user: CurrentUser,
    session: AuthTenantSession,
    body: CampaignCreate,
) -> CampaignItem:
    # Snapshot the reach at creation time so the dashboard can display it
    # without a second round-trip. It is an estimate: the worker recomputes the
    # real recipient set at send time, which may differ if the audience moved.
    segment = _normalise_segment(body.target_audience)
    recipients_count = await _count_audience(session, segment)

    campaign = BroadcastCampaign(
        tenant_id=user.tenant_id,          # from the verified token only
        title=body.title.strip(),
        message_template=body.message_template,
        target_audience=body.target_audience,
        campaign_type=body.campaign_type,
        meta_template_id=body.meta_template_id,
        status=BroadcastCampaignStatus.DRAFT,
        recipients_count=recipients_count,
    )
    session.add(campaign)
    await session.flush()
    await session.refresh(campaign)

    logger.info(
        "broadcast_campaign_created",
        campaign_id=str(campaign.campaign_id),
        tenant_id=str(user.tenant_id),
        segment=segment,
        recipients_count=recipients_count,
    )
    return CampaignItem.model_validate(campaign)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/broadcasts/preview-audience
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/preview-audience",
    response_model=AudiencePreview,
    status_code=status.HTTP_200_OK,
    summary="Estimate how many customers a segment reaches",
    description=(
        "Live count of customers matching `target_audience`, for the "
        '"this will reach N customers" hint in the campaign composer.\n\n'
        "Segments:\n"
        "  * `all` — every messageable customer\n"
        "  * `vip` — flagged `is_vip`\n"
        "  * `engaged` — VIP or engagement_score ≥ the VIP threshold\n"
        "  * `new` — first seen in the last 30 days\n"
        "  * `inactive` — not VIP and engagement_score below the threshold "
        "(or never scored)\n\n"
        "Customers with a PDPL processing objection (`is_processing_restricted`) "
        "are excluded from every segment. An unknown value falls back to `all` "
        "rather than erroring — check the echoed `target_audience` field."
    ),
)
async def preview_audience(
    user: CurrentUser,
    session: AuthTenantSession,
    target_audience: Annotated[
        str,
        Query(description=f"One of: {', '.join(VALID_SEGMENTS)}"),
    ] = _SEGMENT_ALL,
) -> AudiencePreview:
    segment = _normalise_segment(target_audience)

    recipients_count = await _count_audience(session, segment)
    total_customers = (
        recipients_count
        if segment == _SEGMENT_ALL
        else await _count_audience(session, _SEGMENT_ALL)
    )

    return AudiencePreview(
        target_audience=segment,
        recipients_count=recipients_count,
        total_customers=total_customers,
        valid_segments=list(VALID_SEGMENTS),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/broadcasts/{campaign_id}/schedule
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{campaign_id}/schedule",
    response_model=CampaignItem,
    status_code=status.HTTP_200_OK,
    summary="Schedule a campaign",
    description="Sets `scheduled_at` and moves the campaign to `scheduled`.",
)
async def schedule_campaign(
    user: CurrentUser,
    session: AuthTenantSession,
    campaign_id: Annotated[uuid.UUID, Path(description="Campaign UUID")],
    body: CampaignSchedule,
) -> CampaignItem:
    campaign = await _get_campaign(session, campaign_id)
    _require_mutable(campaign, "schedule")

    if not campaign.meta_template_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "TEMPLATE_REQUIRED",
                "message": (
                    "Cannot schedule a broadcast without an approved Meta "
                    "WhatsApp template (meta_template_id). Free-text sends "
                    "outside a customer's 24h session window risk Meta "
                    "banning the number — set meta_template_id first."
                ),
            },
        )

    scheduled_at = body.scheduled_at
    # Treat a naive datetime as UTC so comparisons stay unambiguous.
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    if scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "SCHEDULED_AT_IN_PAST",
                "message": "scheduled_at must be in the future.",
            },
        )

    campaign.scheduled_at = scheduled_at
    campaign.status = BroadcastCampaignStatus.SCHEDULED
    await session.flush()
    await session.refresh(campaign)

    logger.info(
        "broadcast_campaign_scheduled",
        campaign_id=str(campaign_id),
        scheduled_at=scheduled_at.isoformat(),
    )
    return CampaignItem.model_validate(campaign)


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/broadcasts/{campaign_id}/cancel
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{campaign_id}/cancel",
    response_model=CampaignItem,
    status_code=status.HTTP_200_OK,
    summary="Cancel a campaign",
    description="Only `draft` or `scheduled` campaigns can be cancelled.",
)
async def cancel_campaign(
    user: CurrentUser,
    session: AuthTenantSession,
    campaign_id: Annotated[uuid.UUID, Path(description="Campaign UUID")],
) -> CampaignItem:
    campaign = await _get_campaign(session, campaign_id)
    _require_mutable(campaign, "cancel")

    campaign.status = BroadcastCampaignStatus.CANCELLED
    await session.flush()
    await session.refresh(campaign)

    logger.info("broadcast_campaign_cancelled", campaign_id=str(campaign_id))
    return CampaignItem.model_validate(campaign)
