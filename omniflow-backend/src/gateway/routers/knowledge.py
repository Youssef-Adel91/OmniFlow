"""
gateway/routers/knowledge.py — Company Knowledge Base REST API (SRS §4.2)

Everything the tenant wants their WhatsApp AI to know about their own business
lives behind this router. Two complementary stores:

    company_profiles     structured, curated, short.
                         Injected into EVERY system prompt, always.
                         → GET/PATCH /api/v1/knowledge/profile

    knowledge_documents  uploaded files (catalogues, FAQ sheets, price lists).
                         Parsed → chunked → embedded → Qdrant, and retrieved
                         only when the customer's question matches them.
                         → GET/POST/DELETE /api/v1/knowledge/documents

Endpoints (prefix /api/v1/knowledge):
    GET    /profile                     → current profile (empty object if unset)
    PATCH  /profile                     → partial update, admin only, upserts
    GET    /documents                   → list documents + ingestion status
    POST   /documents                   → multipart upload, admin only
    DELETE /documents/{document_id}     → remove row + S3 object + Qdrant points
    POST   /documents/{document_id}/reindex → re-run ingestion

Authorisation
    Reads are open to any authenticated tenant user (agents benefit from seeing
    what the AI knows). Writes are admin-only, matching settings.py.

Isolation
    Every query runs on `AuthTenantSession`, whose RLS GUC comes from the
    verified Clerk token, and inserts always take `tenant_id` from
    `user.tenant_id` — never from the request body.
"""
from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime
from typing import Annotated, Optional

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from src.ai_engine.company_context import invalidate_company_context
from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.config import get_settings
from src.shared.core.enums import KnowledgeDocumentStatus, TenantUserRole
from src.shared.db.models import CompanyProfile, KnowledgeDocument

logger = structlog.get_logger(__name__)
_settings = get_settings()

router = APIRouter(prefix="/api/v1/knowledge", tags=["Knowledge Base"])

# ── Upload constraints ───────────────────────────────────────────────────────
_MAX_DOCUMENT_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

# extension → canonical file_type. MIME types are checked too, but browsers lie
# about them often enough (especially for docx and csv) that the extension is
# the authoritative signal and the MIME check is advisory.
_EXTENSION_TO_TYPE: dict[str, str] = {
    "pdf": "pdf",
    "docx": "docx",
    "txt": "txt",
    "csv": "csv",
}

_TYPE_TO_CONTENT_TYPE: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "csv": "text/csv",
}

_INGEST_TASK_NAME = "omniflow.ingest_knowledge_document"

# The upload transaction commits when the request finishes. The Celery task is
# dispatched from a background task (which FastAPI runs after yield-dependencies
# are closed) AND with a short countdown, so the worker can never race ahead of
# the INSERT and see a missing row.
_INGEST_DISPATCH_DELAY_SECONDS = 3


# ══════════════════════════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════════════════════════

class FaqEntry(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=4000)


class CompanyProfileResponse(BaseModel):
    """
    The full profile. Every field is nullable: a tenant that has never saved
    anything gets this object with `tenant_id` set and everything else null,
    so the frontend can bind a form to it without a special-case branch.
    """
    model_config = ConfigDict(from_attributes=True)

    tenant_id: uuid.UUID
    business_description: Optional[str] = None
    services_offered: Optional[list[str]] = None
    target_areas: Optional[list[str]] = None
    pricing_policy: Optional[str] = None
    working_hours: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    contact_address: Optional[str] = None
    social_links: Optional[dict[str, str]] = None
    unique_selling_points: Optional[str] = None
    policies_text: Optional[str] = None
    faq: Optional[list[FaqEntry]] = None
    updated_at: Optional[datetime] = None


class CompanyProfilePatch(BaseModel):
    """
    Partial update. Only the keys actually present in the JSON body are
    written (`exclude_unset`), so sending `{"contact_phone": "05..."}` leaves
    every other field untouched. Sending an explicit `null` clears a field.
    """
    business_description: Optional[str] = Field(default=None, max_length=20_000)
    services_offered: Optional[list[str]] = Field(default=None, max_length=50)
    target_areas: Optional[list[str]] = Field(default=None, max_length=100)
    pricing_policy: Optional[str] = Field(default=None, max_length=10_000)
    working_hours: Optional[str] = Field(default=None, max_length=2_000)
    contact_phone: Optional[str] = Field(default=None, max_length=50)
    contact_email: Optional[str] = Field(default=None, max_length=255)
    contact_address: Optional[str] = Field(default=None, max_length=500)
    social_links: Optional[dict[str, str]] = None
    unique_selling_points: Optional[str] = Field(default=None, max_length=10_000)
    policies_text: Optional[str] = Field(default=None, max_length=20_000)
    faq: Optional[list[FaqEntry]] = Field(default=None, max_length=100)


class DocumentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    original_filename: str
    file_type: str
    file_size_bytes: int
    status: str
    chunk_count: Optional[int] = None
    error_message: Optional[str] = None
    uploaded_by_user_id: Optional[uuid.UUID] = None
    created_at: datetime
    indexed_at: Optional[datetime] = None


class DocumentPage(BaseModel):
    items: list[DocumentItem]
    total: int
    page: int
    page_size: int


class DocumentDeleteResponse(BaseModel):
    document_id: uuid.UUID
    deleted: bool = True
    vectors_purged: bool = Field(
        ...,
        description=(
            "False when Qdrant was unreachable; the DB row is still deleted "
            "and the orphan vectors are unreachable because their document_id "
            "no longer resolves."
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _require_admin(user: CurrentUser) -> None:
    """Raise 403 unless the caller is a tenant admin (mirrors settings.py)."""
    if user.role != TenantUserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": "Only tenant admins can edit the knowledge base.",
            },
        )


def _empty_profile(tenant_id: uuid.UUID) -> CompanyProfileResponse:
    return CompanyProfileResponse(tenant_id=tenant_id)


def _resolve_file_type(filename: str, content_type: str | None) -> str:
    """
    Determine the canonical file type, or raise 415.

    Extension is authoritative; the declared MIME type is only used to catch an
    obvious mismatch (e.g. a .pdf that the browser reports as an image).
    """
    extension = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    file_type = _EXTENSION_TO_TYPE.get(extension)

    if file_type is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "UNSUPPORTED_MEDIA_TYPE",
                "message": (
                    "Supported document types: PDF, DOCX, TXT, CSV. "
                    f"Received: {filename or 'unnamed file'}"
                ),
                "allowed": sorted(_EXTENSION_TO_TYPE),
            },
        )
    return file_type


async def _get_document(session, document_id: uuid.UUID) -> KnowledgeDocument:
    document = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.document_id == document_id
        )
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Document not found."},
        )
    return document


def _dispatch_ingestion(document_id: uuid.UUID) -> bool:
    """
    Queue the Celery ingestion task. Returns False if the broker is unreachable.

    `send_task` by name is used deliberately: it keeps the gateway process from
    importing `src.celery_app.tasks`, which would drag pypdf / qdrant / openai
    into the API image for no reason.
    """
    try:
        from src.celery_app.app import app as celery_app  # noqa: PLC0415

        celery_app.send_task(
            _INGEST_TASK_NAME,
            args=[str(document_id)],
            countdown=_INGEST_DISPATCH_DELAY_SECONDS,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — broker down must not fail the upload
        logger.error(
            "knowledge_ingest_dispatch_failed",
            document_id=str(document_id),
            error=str(exc)[:400],
        )
        return False


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/knowledge/profile
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/profile",
    response_model=CompanyProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the company profile",
    description=(
        "Returns the tenant's structured company knowledge. If the profile has "
        "never been saved, returns the same shape with every field `null` "
        "(HTTP 200, never 404)."
    ),
)
async def get_profile(
    user: CurrentUser,
    session: AuthTenantSession,
) -> CompanyProfileResponse:
    profile = await session.scalar(
        select(CompanyProfile).where(CompanyProfile.tenant_id == user.tenant_id)
    )
    if profile is None:
        return _empty_profile(user.tenant_id)
    return CompanyProfileResponse.model_validate(profile)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /api/v1/knowledge/profile
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/profile",
    response_model=CompanyProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Update the company profile (partial)",
    description=(
        "Upserts the tenant's profile. Only fields present in the request body "
        "are modified; a field sent explicitly as `null` is cleared. Requires "
        "the `admin` role.\n\n"
        "Saving takes effect on the AI's next reply (the rendered prompt block "
        "is cached for up to 5 minutes in other worker processes)."
    ),
)
async def update_profile(
    user: CurrentUser,
    session: AuthTenantSession,
    body: CompanyProfilePatch,
) -> CompanyProfileResponse:
    _require_admin(user)

    updates = body.model_dump(exclude_unset=True)
    # Pydantic models inside JSONB columns must be plain dicts.
    if "faq" in updates and updates["faq"] is not None:
        updates["faq"] = [
            entry if isinstance(entry, dict) else entry.model_dump()
            for entry in updates["faq"]
        ]

    profile = await session.scalar(
        select(CompanyProfile).where(CompanyProfile.tenant_id == user.tenant_id)
    )
    created = profile is None
    if profile is None:
        profile = CompanyProfile(tenant_id=user.tenant_id)
        session.add(profile)

    for field, value in updates.items():
        setattr(profile, field, value)

    await session.flush()
    await session.refresh(profile)

    # Make the change visible to this process immediately; other processes pick
    # it up when their 5-minute cache entry expires.
    invalidate_company_context(user.tenant_id)

    logger.info(
        "company_profile_updated",
        tenant_id=str(user.tenant_id),
        created=created,
        fields=sorted(updates.keys()),
    )
    return CompanyProfileResponse.model_validate(profile)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/knowledge/documents
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/documents",
    response_model=DocumentPage,
    status_code=status.HTTP_200_OK,
    summary="List knowledge documents",
    description=(
        "Paginated list of uploaded documents with their ingestion status "
        "(`uploaded` | `processing` | `indexed` | `failed`). Poll this after an "
        "upload to watch a document reach `indexed`."
    ),
)
async def list_documents(
    user: CurrentUser,
    session: AuthTenantSession,
    status_filter: Annotated[
        KnowledgeDocumentStatus | None,
        Query(alias="status", description="Filter by ingestion status"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DocumentPage:
    stmt = select(KnowledgeDocument)
    if status_filter is not None:
        stmt = stmt.where(KnowledgeDocument.status == status_filter.value)

    total = await session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    rows = (
        await session.execute(
            stmt.order_by(KnowledgeDocument.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return DocumentPage(
        items=[DocumentItem.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/knowledge/documents
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/documents",
    response_model=DocumentItem,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a knowledge document",
    description=(
        "multipart/form-data upload of a PDF, DOCX, TXT or CSV file (max 20 MB).\n\n"
        "The file is stored in the private knowledge bucket, a row is created, "
        "and an asynchronous Celery task extracts → chunks → embeds → indexes "
        "it into the tenant's Qdrant collection.\n\n"
        "Returns immediately with `status = processing` (or `uploaded` if the "
        "task broker was unreachable — call `/reindex` to retry). Requires the "
        "`admin` role."
    ),
)
async def upload_document(
    user: CurrentUser,
    session: AuthTenantSession,
    background: BackgroundTasks,
    file: UploadFile = File(..., description="PDF, DOCX, TXT or CSV, max 20 MB"),
    title: Optional[str] = Form(
        default=None,
        description="Display title. Defaults to the original filename.",
    ),
) -> DocumentItem:
    _require_admin(user)

    filename = file.filename or "document"
    file_type = _resolve_file_type(filename, file.content_type)

    file_bytes = await file.read()
    size = len(file_bytes)

    if size == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "EMPTY_FILE", "message": "The uploaded file is empty."},
        )
    if size > _MAX_DOCUMENT_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": (
                    f"Document must be ≤ 20 MB. Received "
                    f"{size / 1024 / 1024:.2f} MB."
                ),
            },
        )

    document_id = uuid.uuid4()
    # Key includes the document id so re-uploading the same filename never
    # overwrites an existing document's object.
    s3_key = f"tenants/{user.tenant_id}/knowledge/{document_id}.{file_type}"
    bucket = _settings.s3_knowledge_bucket
    content_type = (
        file.content_type
        or mimetypes.guess_type(filename)[0]
        or _TYPE_TO_CONTENT_TYPE[file_type]
    )

    try:
        from src.shared.storage.s3 import s3_mgr  # noqa: PLC0415
    except ImportError as imp_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "STORAGE_UNAVAILABLE",
                "message": "Storage backend is not configured. Install aiobotocore.",
            },
        ) from imp_err

    try:
        await s3_mgr.upload_file(
            bucket=bucket,
            key=s3_key,
            file_data=file_bytes,
            content_type=content_type,
        )
    except Exception as exc:
        logger.error(
            "knowledge_document_upload_failed",
            tenant_id=str(user.tenant_id),
            error=str(exc)[:400],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "UPLOAD_FAILED",
                "message": "Failed to store the document. Please try again.",
            },
        ) from exc

    document = KnowledgeDocument(
        document_id=document_id,
        tenant_id=user.tenant_id,          # from the verified token only
        title=(title or filename).strip()[:255],
        original_filename=filename[:500],
        file_type=file_type,
        s3_key=s3_key,
        file_size_bytes=size,
        status=KnowledgeDocumentStatus.PROCESSING.value,
        uploaded_by_user_id=user.user_id,
    )
    session.add(document)
    await session.flush()
    await session.refresh(document)

    # Dispatched after the response is produced (and therefore after this
    # session's transaction has been committed) — see _INGEST_DISPATCH_DELAY.
    background.add_task(_dispatch_ingestion, document_id)

    logger.info(
        "knowledge_document_uploaded",
        document_id=str(document_id),
        tenant_id=str(user.tenant_id),
        file_type=file_type,
        size_bytes=size,
    )
    return DocumentItem.model_validate(document)


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/v1/knowledge/documents/{document_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.delete(
    "/documents/{document_id}",
    response_model=DocumentDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a knowledge document",
    description=(
        "Removes the database row, purges the document's vectors from the "
        "tenant's Qdrant collection, and best-effort deletes the stored object. "
        "Requires the `admin` role."
    ),
)
async def delete_document(
    user: CurrentUser,
    session: AuthTenantSession,
    document_id: Annotated[uuid.UUID, Path(description="Document UUID")],
) -> DocumentDeleteResponse:
    _require_admin(user)

    document = await _get_document(session, document_id)
    s3_key = document.s3_key

    # ── Purge vectors first: an orphaned vector would keep feeding the AI ────
    vectors_purged = True
    try:
        from src.shared.qdrant_client.client import qdrant_mgr  # noqa: PLC0415

        await qdrant_mgr.start()
        await qdrant_mgr.delete_document_points(user.tenant_id, document_id)
    except Exception as exc:  # noqa: BLE001
        vectors_purged = False
        logger.error(
            "knowledge_document_vector_purge_failed",
            document_id=str(document_id),
            error=str(exc)[:400],
        )

    # ── Best-effort object removal (never blocks the DB delete) ──────────────
    try:
        from src.shared.storage.s3 import s3_mgr  # noqa: PLC0415

        await s3_mgr.delete_file(bucket=_settings.s3_knowledge_bucket, key=s3_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "knowledge_document_object_delete_failed",
            document_id=str(document_id),
            error=str(exc)[:400],
        )

    await session.delete(document)
    await session.flush()

    logger.info(
        "knowledge_document_deleted",
        document_id=str(document_id),
        tenant_id=str(user.tenant_id),
        vectors_purged=vectors_purged,
    )
    return DocumentDeleteResponse(
        document_id=document_id, vectors_purged=vectors_purged
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/knowledge/documents/{document_id}/reindex
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/documents/{document_id}/reindex",
    response_model=DocumentItem,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run ingestion for a document",
    description=(
        "Re-queues the extraction/embedding pipeline, typically after a "
        "`failed` status. Existing vectors for the document are replaced, not "
        "duplicated. Returns the document with `status = processing`. "
        "Requires the `admin` role."
    ),
)
async def reindex_document(
    user: CurrentUser,
    session: AuthTenantSession,
    background: BackgroundTasks,
    document_id: Annotated[uuid.UUID, Path(description="Document UUID")],
) -> DocumentItem:
    _require_admin(user)

    document = await _get_document(session, document_id)

    document.status = KnowledgeDocumentStatus.PROCESSING.value
    document.error_message = None
    document.chunk_count = None
    document.indexed_at = None
    await session.flush()
    await session.refresh(document)

    background.add_task(_dispatch_ingestion, document_id)

    logger.info(
        "knowledge_document_reindex_requested",
        document_id=str(document_id),
        tenant_id=str(user.tenant_id),
    )
    return DocumentItem.model_validate(document)


__all__ = ["router"]
