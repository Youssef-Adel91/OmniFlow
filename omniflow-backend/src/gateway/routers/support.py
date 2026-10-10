"""
gateway/routers/support.py — Support tickets, threads, attachments, help center (prefix /api/v1/support)

    GET   /meta                          categories / priorities / SLA targets / real contact channels (only if configured)
    GET   /kb/search?q=                  help-center articles (answered before a ticket is opened)
    GET   /status                        platform dependency status (database / redis / kafka) — no secrets
    GET   /tickets                       list this tenant's tickets (status/category/priority filters)
    POST  /tickets                       create (reference OF-XXXXXXXX, first thread message)
    GET   /tickets/{id}                  ticket + full thread + attachments
    POST  /tickets/{id}/messages         customer reply (re-opens a closed ticket)
    POST  /tickets/{id}/attachments      one file (image/pdf/docx/xlsx/csv, ≤5 MB, ≤5 per ticket)
    GET   /attachments/{id}              download (tenant-scoped, private bucket, served only through here)
    PATCH /tickets/{id}                  customer closes / re-opens
    POST  /admin/tickets/{id}/reply      support-staff reply; needs header X-Support-Key (disabled when not configured)

Every tenant endpoint runs on the RLS tenant session: another tenant's ticket/attachment is simply "not found".
"""
from __future__ import annotations

import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, File, Header, HTTPException, Path, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.config import get_settings
from src.shared.core.enums import SupportTicketStatus
from src.shared.db.models import SupportAttachment, SupportTicket, SupportTicketMessage
from src.shared.db.session import get_system_session
from src.shared.services import support_kb
from src.shared.services.bulk_import.files import ImportFileError, detect_kind
from src.shared.services.property_import import ImportStorage

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/support", tags=["Support"])

CATEGORIES = {
    "technical": "مشكلة تقنية", "billing": "الفوترة والاشتراك", "ai": "المساعد الذكي",
    "channels": "القنوات (واتساب / إنستجرام / فيسبوك)", "integrations": "التكاملات",
    "import": "استيراد البيانات", "feature": "اقتراح ميزة", "other": "أخرى",
}
# Targets we aim for, not a contract: shown to the user as "الاستجابة المستهدفة".
PRIORITIES = {"urgent": ("عاجل", 4), "high": ("مرتفع", 8), "normal": ("عادي", 24), "low": ("منخفض", 48)}

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS_PER_TICKET = 5
ALLOWED_KINDS = {"image": "image/*", "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv"}
_IMAGE_TYPES = ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"RIFF", "image/webp"))
_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # pragma: allowlist secret (reference alphabet)


class SupportStorage(ImportStorage):
    """Private bucket for ticket attachments (replaceable in tests)."""

    @property
    def bucket(self) -> str:
        return get_settings().s3_support_bucket


storage = SupportStorage()


def new_reference() -> str:
    return "OF-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))


def safe_filename(name: str) -> str:
    name = (name or "file").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f\"<>|:*?]", "", name).strip() or "file"
    return name[-120:]


def content_type_for(kind: str, data: bytes) -> str:
    if kind == "image":
        return next((ct for magic, ct in _IMAGE_TYPES if data.startswith(magic)), "image/png")
    return ALLOWED_KINDS[kind]


# ══════════════════════════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════════════════════════

class TicketItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket_id: uuid.UUID
    tenant_id: uuid.UUID
    created_by_user_id: Optional[uuid.UUID] = None
    reference: Optional[str] = None
    subject: str
    message: str
    status: str
    category: str = "other"
    priority: str = "normal"
    first_response_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class TicketPage(BaseModel):
    items: list[TicketItem]
    total: int
    page: int
    page_size: int


class AttachmentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attachment_id: uuid.UUID
    message_id: Optional[uuid.UUID] = None
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class MessageItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: uuid.UUID
    author_type: str
    kind: str
    body: str
    created_at: datetime


class TicketDetail(BaseModel):
    ticket: TicketItem
    messages: list[MessageItem]
    attachments: list[AttachmentItem]


class TicketCreate(BaseModel):
    subject: str = Field(..., min_length=3, max_length=255)
    message: str = Field(..., min_length=1, max_length=10_000)
    category: str = "other"
    priority: str = "normal"


class MessageCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=10_000)


class TicketPatch(BaseModel):
    status: SupportTicketStatus = Field(..., description="open | closed (customer side); in_progress is set by support staff")


def _bad(code: str, message: str, http: int = status.HTTP_422_UNPROCESSABLE_ENTITY) -> HTTPException:
    return HTTPException(status_code=http, detail={"code": code, "message": message})


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "التذكرة غير موجودة."})


async def _ticket(session, ticket_id: uuid.UUID) -> SupportTicket:
    t = await session.scalar(select(SupportTicket).where(SupportTicket.ticket_id == ticket_id))
    if t is None:
        raise _not_found()
    return t


async def _add_message(session, ticket: SupportTicket, *, author_type: str, user_id: uuid.UUID | None, body: str,
                       kind: str = "message") -> SupportTicketMessage:
    m = SupportTicketMessage(ticket_id=ticket.ticket_id, tenant_id=ticket.tenant_id, author_type=author_type,
                             author_user_id=user_id, kind=kind, body=body,
                             created_at=datetime.now(timezone.utc))      # now() is the transaction start: would tie rows of one request
    session.add(m)
    await session.flush()
    await session.refresh(m)
    return m


# ══════════════════════════════════════════════════════════════════════════════
# Static / informational
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/meta", summary="Ticket form options and the support channels that are really configured")
async def support_meta(user: CurrentUser) -> dict:
    cfg = get_settings()
    wa = re.sub(r"\D", "", cfg.support_whatsapp_number or "")
    return {
        "categories": [{"id": k, "label": v} for k, v in CATEGORIES.items()],
        "priorities": [{"id": k, "label": v[0], "target_hours": v[1]} for k, v in PRIORITIES.items()],
        # Only channels the operator really configured are returned — the UI hides the rest (no placeholders).
        "contacts": {
            "whatsapp": f"https://wa.me/{wa}" if len(wa) >= 8 else None,
            "zoom": cfg.support_zoom_url or None,
            "docs": cfg.support_docs_url or None,
        },
        "attachments": {"max_bytes": MAX_ATTACHMENT_BYTES, "max_per_ticket": MAX_ATTACHMENTS_PER_TICKET,
                        "kinds": sorted(ALLOWED_KINDS)},
    }


@router.get("/kb/search", summary="Search the help center")
async def kb_search(user: CurrentUser, q: Annotated[str, Query(max_length=200)] = "") -> dict:
    hits = support_kb.search(q) if q.strip() else []
    return {"query": q, "items": [_article(a) for a in hits]}


@router.get("/kb", summary="All help-center articles")
async def kb_all(user: CurrentUser) -> dict:
    return {"items": [_article(a) for a in support_kb.all_articles()]}


def _article(a: dict) -> dict:
    return {k: a[k] for k in ("id", "title", "answer", "category", "link", "link_label")}


@router.get("/status", summary="Platform status (no secrets)")
async def platform_status(user: CurrentUser) -> dict:
    from src.gateway.routers.health import dependency_status

    checks = await dependency_status()
    return {"checks": checks, "all_ok": all(v == "ok" for v in checks.values()),
            "checked_at": datetime.now(timezone.utc).isoformat()}


# ══════════════════════════════════════════════════════════════════════════════
# Tickets
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/tickets", response_model=TicketPage, summary="List support tickets")
async def list_tickets(
    user: CurrentUser,
    session: AuthTenantSession,
    status_filter: Annotated[SupportTicketStatus | None, Query(alias="status")] = None,
    category: Annotated[str | None, Query()] = None,
    priority: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TicketPage:
    stmt = select(SupportTicket)
    if status_filter is not None:
        stmt = stmt.where(SupportTicket.status == status_filter.value)
    if category:
        stmt = stmt.where(SupportTicket.category == category)
    if priority:
        stmt = stmt.where(SupportTicket.priority == priority)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await session.execute(stmt.order_by(SupportTicket.created_at.desc())
                                  .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return TicketPage(items=[TicketItem.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


@router.post("/tickets", response_model=TicketItem, status_code=status.HTTP_201_CREATED, summary="Create a support ticket")
async def create_ticket(user: CurrentUser, session: AuthTenantSession, body: TicketCreate) -> TicketItem:
    if body.category not in CATEGORIES:
        raise _bad("INVALID_CATEGORY", "تصنيف التذكرة غير صالح.")
    if body.priority not in PRIORITIES:
        raise _bad("INVALID_PRIORITY", "أولوية التذكرة غير صالحة.")
    subject, message = body.subject.strip(), body.message.strip()
    if len(subject) < 3 or not message:
        raise _bad("EMPTY", "العنوان والرسالة مطلوبان.")
    cap = get_settings().support_tickets_per_hour
    recent = await session.scalar(select(func.count()).select_from(SupportTicket).where(
        SupportTicket.created_at > datetime.now(timezone.utc) - timedelta(hours=1)))
    if cap and (recent or 0) >= cap:
        raise HTTPException(status_code=429, headers={"Retry-After": "600"},
                            detail={"code": "TICKET_RATE_LIMIT", "message": f"أنشأت {recent} تذاكر خلال الساعة الأخيرة. للمتابعة أضف رسالة إلى تذكرة قائمة أو حاول لاحقًا."})
    # tenant_id comes from the verified token, never from the request body.
    ticket = SupportTicket(tenant_id=user.tenant_id, created_by_user_id=user.user_id, subject=subject, message=message,
                           status=SupportTicketStatus.OPEN, reference=new_reference(),
                           category=body.category, priority=body.priority)
    session.add(ticket)
    await session.flush()
    await session.refresh(ticket)
    await _add_message(session, ticket, author_type="customer", user_id=user.user_id, body=message)
    logger.info("support_ticket_created", ticket_id=str(ticket.ticket_id), tenant_id=str(user.tenant_id),
                category=body.category, priority=body.priority)
    return TicketItem.model_validate(ticket)


@router.get("/tickets/{ticket_id}", response_model=TicketDetail, summary="Ticket with its full thread")
async def ticket_detail(user: CurrentUser, session: AuthTenantSession,
                        ticket_id: Annotated[uuid.UUID, Path()]) -> TicketDetail:
    t = await _ticket(session, ticket_id)
    msgs = (await session.execute(select(SupportTicketMessage).where(SupportTicketMessage.ticket_id == ticket_id)
                                  .order_by(SupportTicketMessage.created_at, SupportTicketMessage.message_id))).scalars().all()
    atts = (await session.execute(select(SupportAttachment).where(SupportAttachment.ticket_id == ticket_id)
                                  .order_by(SupportAttachment.created_at))).scalars().all()
    return TicketDetail(ticket=TicketItem.model_validate(t), messages=[MessageItem.model_validate(m) for m in msgs],
                        attachments=[AttachmentItem.model_validate(a) for a in atts])


@router.post("/tickets/{ticket_id}/messages", response_model=MessageItem, status_code=status.HTTP_201_CREATED,
             summary="Add a customer message to the thread")
async def add_message(user: CurrentUser, session: AuthTenantSession, ticket_id: Annotated[uuid.UUID, Path()],
                      body: MessageCreate) -> MessageItem:
    t = await _ticket(session, ticket_id)
    text = body.body.strip()
    if not text:
        raise _bad("EMPTY", "الرسالة فارغة.")
    m = await _add_message(session, t, author_type="customer", user_id=user.user_id, body=text)
    if t.status == SupportTicketStatus.CLOSED:                     # a reply on a closed ticket re-opens it
        t.status = SupportTicketStatus.OPEN
        t.closed_at = None
        await _add_message(session, t, author_type="system", user_id=None, kind="status", body="أُعيد فتح التذكرة")
    await session.flush()
    return MessageItem.model_validate(m)


@router.patch("/tickets/{ticket_id}", response_model=TicketItem, summary="Close or re-open a ticket")
async def update_ticket(user: CurrentUser, session: AuthTenantSession, ticket_id: Annotated[uuid.UUID, Path()],
                        body: TicketPatch) -> TicketItem:
    if body.status == SupportTicketStatus.IN_PROGRESS:
        raise _bad("FORBIDDEN_STATUS", "حالة «قيد المعالجة» يحدّدها فريق الدعم فقط.", status.HTTP_403_FORBIDDEN)
    t = await _ticket(session, ticket_id)
    if t.status != body.status.value:
        t.status = body.status
        t.closed_at = datetime.now(timezone.utc) if body.status == SupportTicketStatus.CLOSED else None
        await _add_message(session, t, author_type="system", user_id=user.user_id, kind="status",
                           body="أُغلقت التذكرة" if body.status == SupportTicketStatus.CLOSED else "أُعيد فتح التذكرة")
        await session.flush()
        await session.refresh(t)
    logger.info("support_ticket_status_changed", ticket_id=str(ticket_id), new_status=body.status.value)
    return TicketItem.model_validate(t)


# ══════════════════════════════════════════════════════════════════════════════
# Attachments
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/tickets/{ticket_id}/attachments", response_model=AttachmentItem, status_code=status.HTTP_201_CREATED,
             summary="Attach a file to a ticket")
async def add_attachment(user: CurrentUser, session: AuthTenantSession, ticket_id: Annotated[uuid.UUID, Path()],
                         file: Annotated[UploadFile, File()]) -> AttachmentItem:
    t = await _ticket(session, ticket_id)
    data = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise _bad("FILE_TOO_LARGE", "حجم الملف يتجاوز 5 ميجابايت.", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    name = safe_filename(file.filename or "file")
    try:
        kind = detect_kind(data, name)                      # content sniffing, not the client's content-type
    except ImportFileError as exc:
        raise _bad(exc.code.upper(), exc.message) from exc
    if kind not in ALLOWED_KINDS:
        raise _bad("UNSUPPORTED_TYPE", "نوع الملف غير مسموح. المسموح: صور، PDF، Word، Excel، CSV.")
    count = await session.scalar(select(func.count()).select_from(SupportAttachment)
                                 .where(SupportAttachment.ticket_id == ticket_id)) or 0
    if count >= MAX_ATTACHMENTS_PER_TICKET:
        raise _bad("TOO_MANY_FILES", f"الحد الأقصى {MAX_ATTACHMENTS_PER_TICKET} ملفات لكل تذكرة.")

    att_id = uuid.uuid4()
    key = f"support/{t.tenant_id}/{t.ticket_id}/{att_id}"
    ctype = content_type_for(kind, data)
    await storage.put(key, data, ctype)
    att = SupportAttachment(attachment_id=att_id, ticket_id=t.ticket_id, tenant_id=t.tenant_id, filename=name,
                            content_type=ctype, size_bytes=len(data), storage_key=key)
    session.add(att)
    await session.flush()
    await session.refresh(att)
    return AttachmentItem.model_validate(att)


@router.get("/attachments/{attachment_id}", summary="Download an attachment")
async def download_attachment(user: CurrentUser, session: AuthTenantSession,
                              attachment_id: Annotated[uuid.UUID, Path()]) -> Response:
    att = await session.scalar(select(SupportAttachment).where(SupportAttachment.attachment_id == attachment_id))
    if att is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "الملف غير موجود."})
    data = await storage.get(att.storage_key)
    disposition = "inline" if att.content_type.startswith("image/") or att.content_type == "application/pdf" else "attachment"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", att.filename) or "file"
    return Response(content=data, media_type=att.content_type, headers={
        "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


# ══════════════════════════════════════════════════════════════════════════════
# Staff reply (cross-tenant by design → guarded by a server-side key, disabled unless configured)
# ══════════════════════════════════════════════════════════════════════════════

class StaffReply(BaseModel):
    body: str = Field(..., min_length=1, max_length=10_000)
    status: Optional[SupportTicketStatus] = None


async def _staff_reply_core(session, t: SupportTicket, text: str, status_: Optional[SupportTicketStatus]) -> SupportTicketMessage:
    m = await _add_message(session, t, author_type="support", user_id=None, body=text)
    if t.first_response_at is None:
        t.first_response_at = datetime.now(timezone.utc)
    new_status = status_ or (SupportTicketStatus.IN_PROGRESS if t.status == SupportTicketStatus.OPEN else None)
    if new_status is not None and t.status != new_status.value:
        t.status = new_status
        t.closed_at = datetime.now(timezone.utc) if new_status == SupportTicketStatus.CLOSED else None
        labels = {"in_progress": "التذكرة قيد المعالجة", "closed": "أُغلقت التذكرة", "open": "أُعيد فتح التذكرة"}
        await _add_message(session, t, author_type="system", user_id=None, kind="status", body=labels[new_status.value])
    await session.flush()
    from src.shared.services.notifications import notify_safe

    await notify_safe(session, t.tenant_id, "support_reply", f"رد جديد على تذكرتك {t.reference or ''}".strip(), text[:300], "/support",
                      user_ids=[t.created_by_user_id] if t.created_by_user_id else None)
    return m


@router.post("/admin/tickets/{ticket_id}/reply", response_model=MessageItem, status_code=status.HTTP_201_CREATED,
             summary="Support-staff reply (X-Support-Key)")
async def staff_reply(ticket_id: Annotated[uuid.UUID, Path()], body: StaffReply,
                      x_support_key: Annotated[str | None, Header()] = None) -> MessageItem:
    expected = get_settings().support_admin_api_key
    if not expected:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Not found."})   # endpoint is off
    if not x_support_key or not hmac.compare_digest(x_support_key.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Invalid support key."})
    text = body.body.strip()
    if not text:
        raise _bad("EMPTY", "الرد فارغ.")
    async with get_system_session() as session:
        t = await session.scalar(select(SupportTicket).where(SupportTicket.ticket_id == ticket_id))
        if t is None:
            raise _not_found()
        return MessageItem.model_validate(await _staff_reply_core(session, t, text, body.status))


# ══════════════════════════════════════════════════════════════════════════════
# Staff console (signed-in OmniFlow staff; allow-listed by email in SUPPORT_STAFF_EMAILS). Hidden (404) for everyone else.
# ══════════════════════════════════════════════════════════════════════════════

def _staff_emails() -> set[str]:
    return {e.strip().lower() for e in (get_settings().support_staff_emails or "").split(",") if e.strip()}


def is_staff(user) -> bool:
    return bool(getattr(user, "email", None)) and user.email.strip().lower() in _staff_emails() and bool(getattr(user, "is_active", True))


async def require_staff(user: CurrentUser):
    if not is_staff(user):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Not found."})
    return user


StaffUser = Annotated[object, Depends(require_staff)]


@router.get("/staff/me", summary="Is the caller OmniFlow support staff?")
async def staff_me(user: CurrentUser) -> dict:
    return {"is_staff": is_staff(user)}


@router.get("/staff/tickets", summary="Staff: tickets across all tenants")
async def staff_tickets(staff: StaffUser, status_filter: Annotated[SupportTicketStatus | None, Query(alias="status")] = None,
                        priority: Optional[str] = None, page: Annotated[int, Query(ge=1)] = 1,
                        page_size: Annotated[int, Query(ge=1, le=100)] = 30) -> dict:
    from src.shared.db.models import Tenant

    async with get_system_session() as session:
        stmt = select(SupportTicket, Tenant.business_name).join(Tenant, Tenant.tenant_id == SupportTicket.tenant_id)
        if status_filter is not None:
            stmt = stmt.where(SupportTicket.status == status_filter.value)
        if priority:
            stmt = stmt.where(SupportTicket.priority == priority)
        total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        urgency = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        rows = (await session.execute(stmt.order_by(SupportTicket.created_at.asc()).offset((page - 1) * page_size).limit(page_size))).all()
        items = [{**TicketItem.model_validate(t).model_dump(mode="json"), "tenant_name": name, "awaiting_reply": t.first_response_at is None} for t, name in rows]
        items.sort(key=lambda i: (i["status"] == "closed", urgency.get(i["priority"], 2)))
        return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/staff/tickets/{ticket_id}", summary="Staff: a ticket with its thread")
async def staff_ticket(staff: StaffUser, ticket_id: Annotated[uuid.UUID, Path()]) -> dict:
    async with get_system_session() as session:
        t = await _ticket(session, ticket_id)
        msgs = (await session.execute(select(SupportTicketMessage).where(SupportTicketMessage.ticket_id == ticket_id)
                                      .order_by(SupportTicketMessage.created_at, SupportTicketMessage.message_id))).scalars().all()
        return {"ticket": TicketItem.model_validate(t).model_dump(mode="json"), "messages": [MessageItem.model_validate(m).model_dump(mode="json") for m in msgs]}


@router.post("/staff/tickets/{ticket_id}/reply", response_model=MessageItem, status_code=status.HTTP_201_CREATED, summary="Staff: reply / set status")
async def staff_console_reply(staff: StaffUser, ticket_id: Annotated[uuid.UUID, Path()], body: StaffReply) -> MessageItem:
    text = body.body.strip()
    if not text:
        raise _bad("EMPTY", "الرد فارغ.")
    async with get_system_session() as session:
        t = await _ticket(session, ticket_id)
        m = await _staff_reply_core(session, t, text, body.status)
        logger.info("support_staff_reply", ticket_id=str(ticket_id), tenant_id=str(t.tenant_id), staff_user=str(getattr(staff, "user_id", "")))
        return MessageItem.model_validate(m)
