"""
gateway/routers/broadcasts.py — Broadcast Campaigns REST API (WhatsApp, template based, consent aware)

Endpoints (prefix /api/v1/broadcasts):
    GET  /                  → list campaigns
    POST /                  → create a draft (audience + approved template + variables + send rate)
    GET  /readiness         → is WhatsApp connected? can approved templates be listed?
    GET  /templates         → approved Meta templates of the tenant's WhatsApp Business Account
    GET  /preview-audience  → reach for a segment/tags/city/source + WHY customers are excluded
    GET  /{id}              → one campaign
    GET  /{id}/stats        → sent / delivered / read / failed / replies / unsubscribes
    POST /{id}/schedule     → schedule (or send now). Requires `confirmed_recipients` = the current reach
    POST /{id}/cancel

Who is messaged (see shared/services/broadcast_audience.py): only customers who opted in to marketing (PDPL) and
never opted out, with a real phone number, not archived and without a processing objection.

Status machine:  draft ──schedule──► scheduled ──(Beat + worker)──► sending ─► completed | failed
                 draft | scheduled ──cancel──► cancelled
Sending is dispatched by `omniflow.broadcast_dispatch_check` (Celery Beat, every minute) → Kafka → BroadcastWorker.
Nothing is sent unless WhatsApp is connected AND an APPROVED template is chosen (re-checked at dispatch and send).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional

import structlog
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser, MarketingWriteUser
from src.shared.core.config import get_settings
from src.shared.security.crypto import reveal_secret
from src.shared.core.enums import BroadcastCampaignStatus
from src.shared.db.models import AuditLog, BroadcastCampaign, BroadcastDelivery, Conversation, Customer, Message, Tenant
from src.shared.services import broadcast_audience as aud
from src.shared.services.billing import quotas
from src.shared.services import meta_templates

logger = structlog.get_logger(__name__)
_settings = get_settings()

router = APIRouter(prefix="/api/v1/broadcasts", tags=["Broadcasts"])

_MUTABLE_STATES = {BroadcastCampaignStatus.DRAFT.value, BroadcastCampaignStatus.SCHEDULED.value}
VALID_SEGMENTS = aud.SEGMENTS
MIN_LEAD_SECONDS = 45          # "send now" is scheduled this far ahead so the Beat sweep (every minute) picks it up
MAX_RATE = 80                  # WhatsApp Cloud API default throughput per number
REPLY_WINDOW_HOURS = 72


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
    template_language: str = "ar"
    template_params: list[str] = Field(default_factory=list)
    send_rate_per_second: int = 20
    status: str
    recipients_count: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("target_audience", mode="before")
    @classmethod
    def _legacy_audience(cls, v: Any) -> Any:
        """Rows written by the old composer hold a bare string ("all_vip"); show them instead of failing the whole list."""
        return {"segment": v} if isinstance(v, str) else v


class AudiencePreview(BaseModel):
    target_audience: str = Field(..., description="The segment the estimate was computed for.")
    recipients_count: int = Field(..., description="Customers who WOULD receive it now (consented, with a phone).")
    total_customers: int = Field(..., description="All non-archived customers, for context.")
    matching_segment: int = 0
    excluded_no_consent: int = 0
    excluded_opted_out: int = 0
    excluded_restricted: int = 0
    excluded_no_phone: int = 0
    valid_segments: list[str]


class CampaignPage(BaseModel):
    items: list[CampaignItem]
    total: int
    page: int
    page_size: int


class CampaignCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    message_template: str = Field(default="", max_length=20_000, description="Preview/body text of the chosen template (display only)")
    target_audience: Optional[dict[str, Any] | str] = Field(
        default=None, description='{"segment": "vip|engaged|new|inactive|hot|all", "tags": [...], "city": "...", "sources": [...]}')
    campaign_type: Optional[str] = Field(default=None, max_length=40)
    meta_template_id: Optional[str] = Field(default=None, max_length=120, description="Name of an APPROVED Meta template (see GET /templates)")
    template_language: str = Field(default="ar", max_length=10)
    template_params: list[str] = Field(default_factory=list, max_length=10,
                                       description="Body variables in order; may contain {name} and {city} filled per recipient")
    send_rate_per_second: int = Field(default=20, ge=1, le=MAX_RATE)


class CampaignSchedule(BaseModel):
    scheduled_at: Optional[datetime] = Field(default=None, description="ISO 8601 with offset (UTC stored). Omit = send now.")
    confirmed_recipients: Optional[int] = Field(
        default=None, ge=0, description="The reach the user confirmed. If the audience changed since, the call fails with 409 so nobody is surprised.")


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _err(code: int, error: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": error, "message": message, **extra})


def _parse(raw: Any) -> aud.Audience:
    try:
        return aud.parse_audience(raw)
    except aud.InvalidAudience as exc:
        raise _err(422, "INVALID_AUDIENCE", str(exc)) from exc


def _normalise_segment(raw: Any) -> str:
    """Kept for callers/tests of the old API: the segment name of an audience (unknown → InvalidAudience)."""
    return aud.parse_audience(raw).segment


async def _count_audience(session, segment_or_audience) -> int:
    a = segment_or_audience if isinstance(segment_or_audience, aud.Audience) else aud.parse_audience(segment_or_audience)
    return (await aud.count_audience(session, a, engagement_threshold=_settings.vip_engagement_score_threshold))["recipients_count"]


async def _get_campaign(session, campaign_id: uuid.UUID) -> BroadcastCampaign:
    campaign = await session.scalar(select(BroadcastCampaign).where(BroadcastCampaign.campaign_id == campaign_id))
    if campaign is None:
        raise _err(404, "NOT_FOUND", "Campaign not found.")
    return campaign


def _current_status(campaign: BroadcastCampaign) -> str:
    raw = campaign.status
    return str(raw.value if hasattr(raw, "value") else raw)


def _require_mutable(campaign: BroadcastCampaign, action: str) -> None:
    current = _current_status(campaign)
    if current not in _MUTABLE_STATES:
        raise _err(409, "INVALID_STATE_TRANSITION",
                   f"Cannot {action} a campaign in state '{current}'. Allowed states: {sorted(_MUTABLE_STATES)}.")


async def _tenant(session, tenant_id: uuid.UUID) -> Tenant | None:
    return await session.scalar(select(Tenant).where(Tenant.tenant_id == tenant_id))


def _wa_connected(t: Tenant | None) -> bool:
    return bool(t and t.whatsapp_phone_number_id and t.meta_access_token)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/broadcasts   POST /api/v1/broadcasts
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=CampaignPage, include_in_schema=False)
@router.get("/", response_model=CampaignPage, status_code=status.HTTP_200_OK, summary="List broadcast campaigns")
async def list_campaigns(
    user: CurrentUser,
    session: AuthTenantSession,
    status_filter: Annotated[BroadcastCampaignStatus | None, Query(alias="status", description="Filter by campaign status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CampaignPage:
    stmt = select(BroadcastCampaign)
    if status_filter is not None:
        stmt = stmt.where(BroadcastCampaign.status == status_filter.value)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await session.execute(
        stmt.order_by(BroadcastCampaign.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return CampaignPage(items=[CampaignItem.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


@router.post("", response_model=CampaignItem, status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("/", response_model=CampaignItem, status_code=status.HTTP_201_CREATED, summary="Create a draft campaign")
async def create_campaign(user: MarketingWriteUser, session: AuthTenantSession, body: CampaignCreate) -> CampaignItem:
    await quotas.guard_feature(session, user.tenant_id, "campaigns")
    audience = _parse(body.target_audience)
    reach = await _count_audience(session, audience)
    campaign = BroadcastCampaign(
        tenant_id=user.tenant_id,          # from the verified token only
        title=body.title.strip(),
        message_template=body.message_template or body.title.strip(),
        target_audience=audience.as_dict(),
        campaign_type=body.campaign_type,
        meta_template_id=(body.meta_template_id or None),
        template_language=body.template_language,
        template_params=[p[:1024] for p in body.template_params],
        send_rate_per_second=body.send_rate_per_second,
        status=BroadcastCampaignStatus.DRAFT,
        recipients_count=reach,
        created_by=getattr(user, "user_id", None),
    )
    session.add(campaign)
    await session.flush()
    await session.refresh(campaign)
    logger.info("broadcast_campaign_created", campaign_id=str(campaign.campaign_id), tenant_id=str(user.tenant_id),
                segment=audience.segment, recipients_count=reach)
    return CampaignItem.model_validate(campaign)


# ══════════════════════════════════════════════════════════════════════════════
# readiness / templates / preview  (registered before /{campaign_id})
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/readiness", summary="Can this tenant send broadcasts right now?")
async def readiness(user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    t = await _tenant(session, user.tenant_id)
    connected = _wa_connected(t)
    reasons: list[str] = []
    if not connected:
        reasons.append("اربط واتساب أولًا (رقم الهاتف ورمز الوصول) من الإعدادات.")
    can_list = bool(connected and t and t.whatsapp_waba_id)
    if connected and not can_list:
        reasons.append("معرّف حساب واتساب للأعمال (WABA ID) غير مُدخل: لا يمكن عرض القوالب المعتمدة.")
    return {"whatsapp_connected": connected, "can_list_templates": can_list, "can_send": can_list, "reasons": reasons}


@router.get("/templates", summary="Approved WhatsApp message templates (live from Meta)")
async def list_templates(user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    t = await _tenant(session, user.tenant_id)
    if not _wa_connected(t):
        raise _err(409, "WHATSAPP_NOT_CONNECTED", "اربط واتساب أولًا لعرض القوالب المعتمدة.")
    if not t.whatsapp_waba_id:
        raise _err(409, "WABA_ID_MISSING", "معرّف حساب واتساب للأعمال (WABA ID) غير مُدخل في الإعدادات.")
    try:
        items = await meta_templates.list_approved_templates(t.whatsapp_waba_id, reveal_secret(t.meta_access_token))
    except meta_templates.TemplateFetchError as exc:
        raise _err(502, "META_TEMPLATES_FAILED", str(exc)) from exc
    return {"items": items}


@router.get(
    "/preview-audience",
    response_model=AudiencePreview,
    status_code=status.HTTP_200_OK,
    summary="Who a segment reaches — and who is excluded and why",
    description=(
        "Live reach for `target_audience` (segment: all | vip | engaged | new | inactive | hot) narrowed by `tags` "
        "(comma separated, any), `city` and `sources` (comma separated). Only customers who opted in to marketing, "
        "never opted out, with a real phone, not archived and without a processing objection are counted; the "
        "`excluded_*` fields say how many matching customers are left out and why. An unknown segment is a 422 "
        "(never silently widened to everyone)."
    ),
)
async def preview_audience(
    user: CurrentUser,
    session: AuthTenantSession,
    target_audience: Annotated[str, Query(description=f"One of: {', '.join(aud.SEGMENTS)}")] = "all",
    tags: Annotated[str | None, Query(max_length=400, description="comma separated, any-of")] = None,
    city: Annotated[str | None, Query(max_length=100)] = None,
    sources: Annotated[str | None, Query(max_length=200, description="comma separated")] = None,
) -> AudiencePreview:
    a = _parse({"segment": target_audience, "tags": (tags or "").split(","), "city": city, "sources": (sources or "").split(",")})
    counts = await aud.count_audience(session, a, engagement_threshold=_settings.vip_engagement_score_threshold)
    total = await session.scalar(select(func.count(Customer.customer_id)).where(Customer.archived_at.is_(None))) or 0
    return AudiencePreview(target_audience=a.segment, total_customers=total, valid_segments=list(aud.SEGMENTS), **counts)


# ══════════════════════════════════════════════════════════════════════════════
# GET /{id}   GET /{id}/stats
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{campaign_id}", response_model=CampaignItem, summary="One campaign")
async def get_campaign(user: CurrentUser, session: AuthTenantSession, campaign_id: Annotated[uuid.UUID, Path()]) -> CampaignItem:
    return CampaignItem.model_validate(await _get_campaign(session, campaign_id))


@router.get("/{campaign_id}/stats", summary="Delivery, read, reply and unsubscribe numbers for a campaign")
async def campaign_stats(user: CurrentUser, session: AuthTenantSession, campaign_id: Annotated[uuid.UUID, Path()]) -> dict[str, Any]:
    campaign = await _get_campaign(session, campaign_id)
    rows = (await session.execute(
        select(BroadcastDelivery.delivery_status, func.count()).where(BroadcastDelivery.campaign_id == campaign_id)
        .group_by(BroadcastDelivery.delivery_status))).all()
    by = {str(k): int(v) for k, v in rows}
    sent = by.get("SENT", 0) + by.get("DELIVERED", 0) + by.get("READ", 0)       # reached WhatsApp's servers
    delivered = by.get("DELIVERED", 0) + by.get("READ", 0)
    read = by.get("READ", 0)
    failed = by.get("FAILED", 0)

    # Replies: a recipient wrote to us within REPLY_WINDOW_HOURS after the campaign message was sent.
    replies = await session.scalar(
        select(func.count(func.distinct(BroadcastDelivery.customer_id)))
        .select_from(BroadcastDelivery)
        .join(Conversation, Conversation.customer_id == BroadcastDelivery.customer_id)
        .join(Message, Message.conversation_id == Conversation.conversation_id)
        .where(BroadcastDelivery.campaign_id == campaign_id, BroadcastDelivery.sent_at.is_not(None),
               Message.sender_type == "customer", Message.created_at > BroadcastDelivery.sent_at,
               Message.created_at <= BroadcastDelivery.sent_at + timedelta(hours=REPLY_WINDOW_HOURS))) or 0
    # Unsubscribes: recipients who opted out after the campaign started.
    since = campaign.started_at or campaign.scheduled_at or campaign.created_at
    unsub = await session.scalar(
        select(func.count(func.distinct(BroadcastDelivery.customer_id)))
        .select_from(BroadcastDelivery).join(Customer, Customer.customer_id == BroadcastDelivery.customer_id)
        .where(BroadcastDelivery.campaign_id == campaign_id, Customer.opt_out_at.is_not(None), Customer.opt_out_at >= since)) or 0

    def pct(x: int, of: int) -> float:
        return round(100 * x / of, 1) if of else 0.0

    errors = (await session.execute(
        select(BroadcastDelivery.error_detail, func.count()).where(BroadcastDelivery.campaign_id == campaign_id, BroadcastDelivery.delivery_status == "FAILED")
        .group_by(BroadcastDelivery.error_detail).order_by(func.count().desc()).limit(5))).all()
    return {
        "campaign_id": str(campaign_id), "status": _current_status(campaign), "recipients": campaign.recipients_count or 0,
        "sent": sent, "delivered": delivered, "read": read, "failed": failed, "replies": int(replies), "unsubscribes": int(unsub),
        "rates": {"delivered_pct": pct(delivered, sent), "read_pct": pct(read, delivered), "reply_pct": pct(int(replies), sent), "unsubscribe_pct": pct(int(unsub), sent)},
        "top_errors": [{"error": (e or "")[:200], "count": int(n)} for e, n in errors],
        "reply_window_hours": REPLY_WINDOW_HOURS, "last_error": campaign.last_error,
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /{id}/schedule   POST /{id}/cancel
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{campaign_id}/schedule",
    response_model=CampaignItem,
    status_code=status.HTTP_200_OK,
    summary="Schedule a campaign (or send now)",
    description=(
        "Moves the campaign to `scheduled`. Refused unless: WhatsApp is connected, an approved template is set, the audience is "
        "not empty and — when `confirmed_recipients` is given — equals the current reach (409 `AUDIENCE_CHANGED` otherwise). "
        "Omit `scheduled_at` to send as soon as the next sweep runs."
    ),
)
async def schedule_campaign(
    user: MarketingWriteUser,
    session: AuthTenantSession,
    campaign_id: Annotated[uuid.UUID, Path(description="Campaign UUID")],
    body: Optional[CampaignSchedule] = None,
) -> CampaignItem:
    body = body or CampaignSchedule()
    await quotas.guard_feature(session, user.tenant_id, "campaigns")
    campaign = await _get_campaign(session, campaign_id)
    _require_mutable(campaign, "schedule")

    if not campaign.meta_template_id:
        raise _err(422, "TEMPLATE_REQUIRED",
                   "اختر قالب واتساب معتمدًا من Meta أولًا. الرسائل الحرة خارج نافذة الـ24 ساعة قد تعرّض الرقم للحظر.")
    t = await _tenant(session, user.tenant_id)
    if not _wa_connected(t):
        raise _err(422, "WHATSAPP_NOT_CONNECTED", "اربط واتساب أولًا — لا يمكن جدولة حملة بدون رقم واتساب مربوط.")

    audience = _parse(campaign.target_audience)
    reach = await _count_audience(session, audience)
    if reach == 0:
        raise _err(422, "NO_RECIPIENTS", "لا يوجد عملاء مؤهلون لاستلام هذه الحملة (لا موافقة تسويقية أو لا رقم جوال).")
    if body.confirmed_recipients is not None and body.confirmed_recipients != reach:
        raise _err(409, "AUDIENCE_CHANGED", f"تغيّر عدد المستلمين من {body.confirmed_recipients} إلى {reach}. راجع الحملة ثم أكّد من جديد.",
                   current_recipients=reach)

    now = datetime.now(timezone.utc)
    scheduled_at = body.scheduled_at
    if scheduled_at is None:
        scheduled_at = now + timedelta(seconds=MIN_LEAD_SECONDS)
    elif scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    if scheduled_at < now + timedelta(seconds=MIN_LEAD_SECONDS - 5):
        raise _err(422, "SCHEDULED_AT_IN_PAST", "scheduled_at must be in the future.")

    campaign.scheduled_at = scheduled_at
    campaign.recipients_count = reach
    campaign.status = BroadcastCampaignStatus.SCHEDULED
    campaign.last_error = None
    session.add(AuditLog(tenant_id=user.tenant_id, actor_user_id=getattr(user, "user_id", None), action="broadcast.scheduled",
                         entity_type="broadcast_campaign", entity_id=str(campaign_id),
                         details={"recipients": reach, "template": campaign.meta_template_id, "scheduled_at": scheduled_at.isoformat(),
                                  "audience": audience.as_dict(), "confirmed": body.confirmed_recipients is not None}))
    await session.flush()
    await session.refresh(campaign)
    logger.info("broadcast_campaign_scheduled", campaign_id=str(campaign_id), scheduled_at=scheduled_at.isoformat(), recipients=reach)
    return CampaignItem.model_validate(campaign)


@router.post("/{campaign_id}/cancel", response_model=CampaignItem, status_code=status.HTTP_200_OK, summary="Cancel a campaign",
             description="Only `draft` or `scheduled` campaigns can be cancelled.")
async def cancel_campaign(user: MarketingWriteUser, session: AuthTenantSession, campaign_id: Annotated[uuid.UUID, Path(description="Campaign UUID")]) -> CampaignItem:
    campaign = await _get_campaign(session, campaign_id)
    _require_mutable(campaign, "cancel")
    campaign.status = BroadcastCampaignStatus.CANCELLED
    session.add(AuditLog(tenant_id=user.tenant_id, actor_user_id=getattr(user, "user_id", None), action="broadcast.cancelled",
                         entity_type="broadcast_campaign", entity_id=str(campaign_id), details=None))
    await session.flush()
    await session.refresh(campaign)
    logger.info("broadcast_campaign_cancelled", campaign_id=str(campaign_id))
    return CampaignItem.model_validate(campaign)
