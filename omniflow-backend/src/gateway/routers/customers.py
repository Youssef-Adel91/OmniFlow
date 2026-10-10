"""
gateway/routers/customers.py — Customers REST API

Endpoints (prefix /api/v1/customers):
    GET    /                    → paginated list  {items, total, page, page_size}  (filters: see below)
    POST   /                    → add a customer manually (admin/agent)
    GET    /export.csv          → CSV of the filtered list (formula-injection safe)
    GET    /{customer_id}       → single customer
    GET    /{customer_id}/detail→ customer + conversations + appointments + reports + activity
    PATCH  /{customer_id}       → edit profile / consent / archive (admin/agent)
    POST   /{customer_id}/merge → fold another identity of the same person into this customer
    DELETE /{customer_id}       → permanently delete (admin only; PDPL erasure)
Bulk import lives in routers/customer_import.py (/api/v1/customers/import/...).

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
import csv
import io

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.dependencies import AdminUser, AuthTenantSession, CurrentUser, CustomerWriteUser
from src.shared.db.models import Appointment, AuditLog, Conversation, Customer, CustomerReport, Message
from src.shared.services import customers as svc
from src.shared.services.bulk_import.customer_schema import MAX_TAGS, MAX_TAG_LEN
from src.shared.services.bulk_import.phone import normalize_phone
from src.shared.services.bulk_import.text import ParseError
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
    # Identity & CRM
    phone_e164: Optional[str] = None
    source: str = "whatsapp"
    channel_user_ids: dict = Field(default_factory=dict)
    email: Optional[str] = None
    city: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    # PDPL marketing consent
    marketing_opt_in: bool = False
    opt_in_source: Optional[str] = None
    opt_in_at: Optional[datetime] = None
    opt_out_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
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


def _clean_tags(value: Optional[list[str]]) -> Optional[list[str]]:
    if value is None:
        return None
    tags = list(dict.fromkeys(t.strip() for t in value if t and t.strip()))
    if len(tags) > MAX_TAGS or any(len(t) > MAX_TAG_LEN for t in tags):
        raise ValueError(f"الوسوم: حتى {MAX_TAGS} وسمًا وكل وسم حتى {MAX_TAG_LEN} حرفًا")
    return tags


class CustomerCreate(BaseModel):
    """Manual add. The phone is validated/normalised to E.164 (Saudi 05xxxxxxxx -> +9665xxxxxxxx)."""
    phone: str = Field(..., min_length=1, max_length=30)
    display_name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[str] = Field(default=None, max_length=254, pattern=r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
    city: Optional[str] = Field(default=None, max_length=100)
    tags: list[str] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=2000)
    is_vip: bool = False
    marketing_opt_in: bool = Field(default=False, description="Only tick when the customer actually consented (PDPL).")

    _tags = field_validator("tags")(_clean_tags)


class CustomerPatch(BaseModel):
    """Partial update — only the supplied fields are written."""
    display_name: Optional[str] = Field(default=None, max_length=200)
    is_vip: Optional[bool] = None
    is_processing_restricted: Optional[bool] = None
    email: Optional[str] = Field(default=None, max_length=254, pattern=r"^([^@\s]{1,64}@[^@\s]+\.[^@\s]{2,})?$")
    city: Optional[str] = Field(default=None, max_length=100)
    tags: Optional[list[str]] = None
    notes: Optional[str] = Field(default=None, max_length=2000)
    phone: Optional[str] = Field(default=None, max_length=30, description="Attach/replace the real phone number")
    marketing_opt_in: Optional[bool] = None
    archived: Optional[bool] = Field(default=None, description="true = archive, false = restore")

    _tags = field_validator("tags")(_clean_tags)


class MergeBody(BaseModel):
    source_customer_id: uuid.UUID = Field(..., description="The duplicate identity to fold into this customer")


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


def _like(term: str) -> str:
    return "%" + term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _filtered(status_filter, search, source, tag, city, opt_in, archived):
    """Shared by the list and the CSV export so both always agree on what "the filtered customers" are."""
    stmt = select(Customer)
    stmt = stmt.where(Customer.archived_at.is_not(None) if archived else Customer.archived_at.is_(None))
    if status_filter:
        normalised = status_filter.strip()
        if normalised.lower() == "vip":
            stmt = stmt.where(Customer.is_vip.is_(True))
        elif normalised.lower() == "restricted":
            stmt = stmt.where(Customer.is_processing_restricted.is_(True))
        else:
            stmt = stmt.where(Customer.vcard_state == normalised)
    if source:
        stmt = stmt.where(Customer.source == source.strip().lower())
    if tag and tag.strip():
        stmt = stmt.where(Customer.tags.contains([tag.strip()]))
    if city and city.strip():
        stmt = stmt.where(Customer.city.ilike(_like(city), escape="\\"))
    if opt_in is not None:
        stmt = stmt.where(Customer.marketing_opt_in.is_(opt_in))
    if search and search.strip():
        needle = _like(search)
        stmt = stmt.where(or_(*(c.ilike(needle, escape="\\") for c in (
            Customer.unified_phone, Customer.phone_e164, Customer.display_name, Customer.whatsapp_profile_name,
            Customer.email, Customer.city))))
    return stmt


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "NOT_FOUND", "message": "Customer not found."})


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
    source: Annotated[str | None, Query(max_length=20, description="whatsapp | instagram | messenger | manual | import")] = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    city: Annotated[str | None, Query(max_length=100)] = None,
    opt_in: Annotated[bool | None, Query(description="marketing consent on/off")] = None,
    archived: Annotated[bool, Query(description="true = only archived customers")] = False,
) -> CustomerPage:
    stmt = _filtered(status_filter, search, source, tag, city, opt_in, archived)

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
# GET /api/v1/customers/export.csv   (registered before /{customer_id})
# ══════════════════════════════════════════════════════════════════════════════

EXPORT_MAX_ROWS = 20_000


@router.get("/export.csv", summary="Export the filtered customers as CSV",
            description=f"Same filters as the list (no pagination), capped at {EXPORT_MAX_ROWS} rows. "
                        "Cells starting with = + - @ are prefixed with ' (spreadsheet formula injection).")
async def export_customers(
    user: CurrentUser,
    session: AuthTenantSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    source: Annotated[str | None, Query(max_length=20)] = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    city: Annotated[str | None, Query(max_length=100)] = None,
    opt_in: Annotated[bool | None, Query()] = None,
    archived: Annotated[bool, Query()] = False,
) -> Response:
    from src.shared.services.property_import import csv_safe

    rows = (await session.execute(
        _filtered(status_filter, search, source, tag, city, opt_in, archived)
        .order_by(Customer.created_at.desc(), Customer.customer_id).limit(EXPORT_MAX_ROWS))).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["الاسم", "الجوال", "البريد", "المدينة", "المصدر", "الوسوم", "VIP", "موافقة تسويقية", "تاريخ الموافقة", "ملاحظات", "تاريخ الإضافة"])
    for c in rows:
        w.writerow([csv_safe(v) for v in (
            c.display_name or c.whatsapp_profile_name or "", c.phone_e164 or "", c.email or "", c.city or "", c.source,
            "، ".join(c.tags or []), "نعم" if c.is_vip else "لا", "نعم" if c.marketing_opt_in else "لا",
            c.opt_in_at.isoformat() if c.opt_in_at else "", c.notes or "", c.created_at.isoformat() if c.created_at else "")])
    return Response(b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="omniflow-customers.csv"'})


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /api/v1/customers/{customer_id}
# ══════════════════════════════════════════════════════════════════════════════

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
# POST /api/v1/customers
# ══════════════════════════════════════════════════════════════════════════════

def _dup_http(exc: svc.DuplicateCustomerError) -> HTTPException:
    return HTTPException(status_code=409, detail={
        "code": "DUPLICATE_PHONE", "message": str(exc), "existing_customer_id": str(exc.existing_id)})


@router.post("", response_model=CustomerItem, status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("/", response_model=CustomerItem, status_code=status.HTTP_201_CREATED, summary="Add a customer manually")
async def create_customer(user: CustomerWriteUser, session: AuthTenantSession, body: CustomerCreate) -> CustomerItem:
    try:
        customer = await svc.create_customer(
            session, tenant_id=user.tenant_id, actor_id=getattr(user, "user_id", None), phone=body.phone, source="manual",
            marketing_opt_in=body.marketing_opt_in, opt_in_source="manual", display_name=body.display_name,
            email=body.email, city=body.city, tags=body.tags, notes=body.notes, is_vip=body.is_vip)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_PHONE", "message": str(exc)}) from exc
    except svc.DuplicateCustomerError as exc:
        raise _dup_http(exc) from exc
    await session.refresh(customer)
    return CustomerItem.model_validate(customer)


@router.get("/{customer_id}/detail", summary="Customer profile with conversations, appointments, reports and activity")
async def customer_detail(user: CurrentUser, session: AuthTenantSession,
                          customer_id: Annotated[uuid.UUID, Path(description="Customer UUID")]) -> dict:
    customer = await session.scalar(select(Customer).where(Customer.customer_id == customer_id))
    if customer is None:
        raise _not_found()
    scores = await _load_lead_scores(session, [customer_id])
    convs = (await session.execute(
        select(Conversation).where(Conversation.customer_id == customer_id)
        .order_by(Conversation.last_message_at.desc().nulls_last()).limit(50))).scalars().all()
    appts = (await session.execute(
        select(Appointment).where(Appointment.customer_id == customer_id).order_by(Appointment.scheduled_at.desc()).limit(50))).scalars().all()
    reports = (await session.execute(
        select(CustomerReport).where(CustomerReport.customer_id == customer_id).order_by(CustomerReport.created_at.desc()).limit(50))).scalars().all()
    audit = (await session.execute(
        select(AuditLog).where(AuditLog.entity_type == "customer", AuditLog.entity_id == str(customer_id))
        .order_by(AuditLog.created_at.desc()).limit(50))).scalars().all()
    return {
        "customer": _customer_item_with_score(customer, scores.get(customer_id)).model_dump(mode="json"),
        "conversations": [{"conversation_id": str(c.conversation_id), "channel": str(c.channel), "status": str(c.status),
                           "message_count": c.message_count, "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
                           "created_at": c.created_at.isoformat() if c.created_at else None} for c in convs],
        "appointments": [{"appointment_id": str(a.appointment_id), "scheduled_at": a.scheduled_at.isoformat(),
                          "status": str(a.status), "location_note": a.location_note} for a in appts],
        "reports": [{"report_id": str(r.report_id), "report_type": str(r.report_type), "price_sar": float(r.price_sar) if r.price_sar is not None else None,
                     "is_delivered": r.is_delivered, "created_at": r.created_at.isoformat() if r.created_at else None} for r in reports],
        "activity": [{"action": a.action, "details": a.details, "at": a.created_at.isoformat() if a.created_at else None,
                      "actor_user_id": str(a.actor_user_id) if a.actor_user_id else None} for a in audit],
    }


@router.patch(
    "/{customer_id}",
    response_model=CustomerItem,
    status_code=status.HTTP_200_OK,
    summary="Update a customer",
    description=(
        "Partial update by admin/agent: profile (name, email, city, tags, notes, VIP), the real `phone` "
        "(a number already owned by another customer returns 409 with `existing_customer_id` so the two can be merged), "
        "PDPL `marketing_opt_in` (withdrawal always applies; consent for someone who opted out is refused) and `archived`."
    ),
)
async def update_customer(
    user: CustomerWriteUser,
    session: AuthTenantSession,
    customer_id: Annotated[uuid.UUID, Path(description="Customer UUID")],
    body: CustomerPatch,
) -> CustomerItem:
    customer = await session.scalar(select(Customer).where(Customer.customer_id == customer_id))
    if customer is None:
        raise _not_found()
    actor = getattr(user, "user_id", None)
    updates = body.model_dump(exclude_unset=True)

    if "phone" in updates:
        phone = updates.pop("phone")
        if phone:
            try:
                await svc.set_phone(session, customer, phone)
            except ParseError as exc:
                raise HTTPException(status_code=422, detail={"code": "INVALID_PHONE", "message": str(exc)}) from exc
            except svc.DuplicateCustomerError as exc:
                raise _dup_http(exc) from exc
    if "marketing_opt_in" in updates:
        wanted = updates.pop("marketing_opt_in")
        if wanted is not None:
            if wanted and customer.opt_out_at is not None:
                raise HTTPException(status_code=409, detail={
                    "code": "OPTED_OUT", "message": "العميل ألغى الاشتراك سابقًا؛ لا يمكن إعادة تفعيل الموافقة إلا بموافقة جديدة منه."})
            if svc.apply_marketing_consent(customer, wanted, "manual"):
                await svc.audit(session, tenant_id=user.tenant_id, actor_id=actor, customer_id=customer_id,
                                action="customer.opt_in_granted" if wanted else "customer.opt_out", details={"source": "dashboard"})
    if "archived" in updates:
        archived = updates.pop("archived")
        if archived is not None:
            customer.archived_at = datetime.now(tz=customer.created_at.tzinfo) if archived else None
            await svc.audit(session, tenant_id=user.tenant_id, actor_id=actor, customer_id=customer_id,
                            action="customer.archived" if archived else "customer.restored")
    for field, value in updates.items():
        if field in ("tags", "is_vip", "is_processing_restricted") and value is None:
            continue                                         # NOT NULL columns: null means "leave as is"
        if field in ("display_name", "email", "city", "notes") and isinstance(value, str):
            value = value.strip() or None                    # an emptied field is stored as NULL, not ''
        setattr(customer, field, value)

    await session.flush()
    await session.refresh(customer)
    logger.info("customer_updated", customer_id=str(customer_id), tenant_id=str(user.tenant_id), fields=sorted(body.model_dump(exclude_unset=True)))
    return CustomerItem.model_validate(customer)


@router.post("/{customer_id}/merge", response_model=CustomerItem, summary="Merge a duplicate identity into this customer")
async def merge_customer(user: CustomerWriteUser, session: AuthTenantSession,
                         customer_id: Annotated[uuid.UUID, Path()], body: MergeBody) -> CustomerItem:
    try:
        keep = await svc.merge_customers(session, tenant_id=user.tenant_id, actor_id=getattr(user, "user_id", None),
                                         keep_id=customer_id, drop_id=body.source_customer_id)
    except LookupError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_MERGE", "message": str(exc)}) from exc
    await session.refresh(keep)
    return CustomerItem.model_validate(keep)


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Permanently delete a customer and their conversations (admin only)")
async def delete_customer(user: AdminUser, session: AuthTenantSession, customer_id: Annotated[uuid.UUID, Path()]) -> Response:
    customer = await session.scalar(select(Customer).where(Customer.customer_id == customer_id))
    if customer is None:
        raise _not_found()
    await svc.audit(session, tenant_id=user.tenant_id, actor_id=getattr(user, "user_id", None), customer_id=customer_id,
                    action="customer.deleted", details={"source": customer.source, "had_phone": bool(customer.phone_e164)})
    await session.delete(customer)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
