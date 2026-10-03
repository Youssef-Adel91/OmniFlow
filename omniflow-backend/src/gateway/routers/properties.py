"""
gateway/routers/properties.py — Property Listings REST API

Sprint 14: Property Management CRUD + Vector Sync

Endpoints:
    GET    /api/v1/properties              → Paginated list (tenant-scoped)
    POST   /api/v1/properties              → Create listing + async vector upsert
    GET    /api/v1/properties/{listing_id} → Single listing detail
    PUT    /api/v1/properties/{listing_id} → Partial update + async vector upsert
    DELETE /api/v1/properties/{listing_id} → Hard delete + async vector delete

Security:
    All endpoints require Depends(get_current_user) — valid Bearer JWT.
    tenant_id is NEVER taken from the request body; it is ALWAYS extracted
    from the verified JWT claim. This prevents cross-tenant data injection.

Vector Sync:
    POST and PUT trigger asyncio.create_task(sync_listing_to_qdrant(...))
    immediately after the DB commit. The HTTP response is returned before
    embedding completes — P99 HTTP latency is unaffected by OpenAI calls.

REGA Ad Number:
    If the client omits rega_ad_number (or sends null), the router generates
    a DEV-REGA-{uuid_hex8} placeholder before writing to Postgres. This
    satisfies the UNIQUE(tenant_id, rega_ad_number) constraint without
    requiring the frontend to supply a real REGA number during development.

References: SRS §4 — API Design; Sprint 14 spec
"""
from __future__ import annotations

import asyncio
import csv
import io
import math
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update

from src.gateway.dependencies import (
    CurrentUser,
    PropertyWriteUser,
    can_activate_listings,
    PropertyListingRepo,
    TenantSession,
    get_property_listing_repo,
    get_current_user,
)
from src.shared.db.models import PropertyListing
from src.shared.schemas import (
    PropertyListingCreate,
    PropertyListingPage,
    PropertyListingResponse,
    PropertyListingUpdate,
)
from src.shared.core.enums import ListingStatus
from src.shared.services.vector_sync import (
    delete_listing_from_qdrant,
    delete_listings_from_qdrant,
    sync_listing_to_qdrant,
)

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/properties",
    tags=["Property Listings"],
)


# ══════════════════════════════════════════════════════════════════════════════
# Helper — auto-fill rega_ad_number if absent
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_rega_number(raw: str | None) -> str:
    """
    Return the supplied REGA number if non-empty, else generate a placeholder.

    Format: DEV-REGA-{8 random hex chars}
    This satisfies the UNIQUE(tenant_id, rega_ad_number) DB constraint without
    blocking development on strict REGA compliance workflows.
    """
    if raw and raw.strip():
        return raw.strip()
    return f"DEV-REGA-{uuid.uuid4().hex[:8].upper()}"


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/properties
# ══════════════════════════════════════════════════════════════════════════════

_SORTS = {
    "newest": lambda: PropertyListing.created_at.desc(),
    "oldest": lambda: PropertyListing.created_at.asc(),
    "price_asc": lambda: PropertyListing.price.asc().nulls_last(),
    "price_desc": lambda: PropertyListing.price.desc().nulls_last(),
    "area_desc": lambda: PropertyListing.area_sqm.desc().nulls_last(),
}
EXPORT_MAX_ROWS = 20_000
BULK_MAX_IDS = 500


def _like(term: str) -> str:
    """Escape LIKE wildcards so user input matches literally."""
    return "%" + term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _filtered(
    status_filter: str | None, type_filter: str | None, city: str | None, search: str | None,
    price_min: float | None, price_max: float | None, indexed: bool | None = None,
):
    stmt = select(PropertyListing)
    if indexed is True:
        stmt = stmt.where(PropertyListing.qdrant_point_id.is_not(None))
    elif indexed is False:
        stmt = stmt.where(PropertyListing.qdrant_point_id.is_(None))
    if status_filter:
        stmt = stmt.where(PropertyListing.status == status_filter)
    if type_filter:
        stmt = stmt.where(PropertyListing.property_type == type_filter)
    if city and city.strip():
        stmt = stmt.where(PropertyListing.city.ilike(_like(city), escape="\\"))
    if price_min is not None:
        stmt = stmt.where(PropertyListing.price >= price_min)
    if price_max is not None:
        stmt = stmt.where(PropertyListing.price <= price_max)
    if search and search.strip():
        needle = _like(search)
        stmt = stmt.where(or_(*(
            col.ilike(needle, escape="\\") for col in (
                PropertyListing.rega_ad_number, PropertyListing.city, PropertyListing.district,
                PropertyListing.description_ar, PropertyListing.description_en)
        )))
    return stmt


@router.get(
    "",
    response_model=PropertyListingPage,
    status_code=status.HTTP_200_OK,
    summary="List property listings",
    description=(
        "Paginated list for the authenticated tenant. Filters: `status`, `property_type`, `city`, "
        "`search` (REGA number, city, district, description), `price_min`/`price_max`; "
        "`sort`: newest | oldest | price_asc | price_desc | area_desc."
    ),
)
async def list_properties(
    user: CurrentUser,
    repo: PropertyListingRepo,
    page: int = Query(default=1, ge=1, description="1-indexed page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    status_filter: str | None = Query(default=None, alias="status", description="Filter by ListingStatus (e.g. VERIFIED_ACTIVE)"),
    type_filter: str | None = Query(default=None, alias="property_type", description="Filter by PropertyType (e.g. apartment)"),
    city: str | None = Query(default=None, max_length=100),
    search: str | None = Query(default=None, max_length=100),
    price_min: float | None = Query(default=None, ge=0),
    price_max: float | None = Query(default=None, ge=0),
    indexed: bool | None = Query(default=None, description="true = in the RAG index, false = not indexed"),
    sort: str = Query(default="newest", pattern="^(newest|oldest|price_asc|price_desc|area_desc)$"),
) -> PropertyListingPage:
    stmt = _filtered(status_filter, type_filter, city, search, price_min, price_max, indexed)
    total = await repo.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await repo.session.execute(
        stmt.order_by(_SORTS[sort](), PropertyListing.listing_id).offset((page - 1) * limit).limit(limit)
    )).scalars().all()

    logger.debug("properties_list", tenant_id=str(user.tenant_id), page=page, total=total)

    return PropertyListingPage(
        items=[PropertyListingResponse.model_validate(item) for item in rows],
        total=total,
        page=page,
        limit=limit,
        pages=max(1, math.ceil(total / limit)),
    )


# Registered before GET /{listing_id}: a single-segment literal path must come first.
@router.get(
    "/export.csv",
    summary="Export the filtered listings as CSV",
    description=f"Same filters as the list endpoint (no pagination); capped at {EXPORT_MAX_ROWS} rows. "
                "Cells starting with = + - @ are prefixed with ' to defuse spreadsheet formula injection.",
)
async def export_properties(
    user: CurrentUser,
    repo: PropertyListingRepo,
    status_filter: str | None = Query(default=None, alias="status"),
    type_filter: str | None = Query(default=None, alias="property_type"),
    city: str | None = Query(default=None, max_length=100),
    search: str | None = Query(default=None, max_length=100),
    price_min: float | None = Query(default=None, ge=0),
    price_max: float | None = Query(default=None, ge=0),
    indexed: bool | None = Query(default=None),
    sort: str = Query(default="newest", pattern="^(newest|oldest|price_asc|price_desc|area_desc)$"),
) -> Response:
    from src.shared.services.property_import import csv_safe

    stmt = _filtered(status_filter, type_filter, city, search, price_min, price_max, indexed)
    rows = (await repo.session.execute(
        stmt.order_by(_SORTS[sort](), PropertyListing.listing_id).limit(EXPORT_MAX_ROWS)
    )).scalars().all()
    cols = ("rega_ad_number", "property_type", "status", "city", "district", "price", "area_sqm", "bedrooms",
            "bathrooms", "latitude", "longitude", "description_ar", "description_en")
    head = ("رقم الإعلان", "النوع", "الحالة", "المدينة", "الحي", "السعر", "المساحة", "الغرف", "الحمامات",
            "خط العرض", "خط الطول", "الوصف", "الوصف (إنجليزي)")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    for r in rows:
        w.writerow([csv_safe(getattr(r, c) if getattr(r, c) is not None else "") for c in cols])
    return Response(
        b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="omniflow-properties.csv"'},
    )


class BulkIds(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=BULK_MAX_IDS)


@router.post("/bulk/delete", summary="Delete several listings", description=f"Up to {BULK_MAX_IDS} ids; vectors are removed asynchronously.")
async def bulk_delete(body: BulkIds, background: BackgroundTasks, user: PropertyWriteUser, repo: PropertyListingRepo) -> dict:
    ids = list(dict.fromkeys(body.ids))
    deleted = (await repo.session.execute(
        delete(PropertyListing).where(PropertyListing.tenant_id == user.tenant_id, PropertyListing.listing_id.in_(ids))
        .returning(PropertyListing.listing_id)
    )).scalars().all()
    logger.info("properties_bulk_deleted", tenant_id=str(user.tenant_id), requested=len(ids), deleted=len(deleted))
    if deleted:
        background.add_task(delete_listings_from_qdrant, [str(i) for i in deleted], user.tenant_id)
    return {"requested": len(ids), "deleted": len(deleted), "not_found": len(ids) - len(deleted)}


class ListingFilters(BaseModel):
    """The list endpoint's filters, used to act on "all N matching" instead of explicit ids."""
    status: str | None = None
    property_type: str | None = None
    city: str | None = Field(default=None, max_length=100)
    search: str | None = Field(default=None, max_length=100)
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    indexed: bool | None = None


class BulkScope(BaseModel):
    """Either explicit `ids` (<= 500) or `filters` (all matching, <= 20,000) -- never both."""
    ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=BULK_MAX_IDS)
    filters: ListingFilters | None = None


class BulkStatusScoped(BulkScope):
    status: ListingStatus


async def _scope_ids(session, tenant_id: uuid.UUID, scope: BulkScope) -> list[uuid.UUID]:
    if (scope.ids is None) == (scope.filters is None):
        raise HTTPException(status_code=422, detail={"code": "INVALID_SCOPE", "message": "حدّد ids أو filters (واحدًا منهما فقط)."})
    if scope.ids is not None:
        return list(dict.fromkeys(scope.ids))
    f = scope.filters
    stmt = _filtered(f.status, f.property_type, f.city, f.search, f.price_min, f.price_max, f.indexed)
    ids = (await session.execute(
        stmt.with_only_columns(PropertyListing.listing_id).where(PropertyListing.tenant_id == tenant_id)
        .order_by(PropertyListing.listing_id).limit(EXPORT_MAX_ROWS + 1)
    )).scalars().all()
    if len(ids) > EXPORT_MAX_ROWS:
        raise HTTPException(status_code=422, detail={
            "code": "TOO_MANY", "message": f"النتائج تتجاوز {EXPORT_MAX_ROWS} عقار. ضيّق الفلاتر."})
    return list(ids)


async def _queue_reindex(session, background: BackgroundTasks, user, ids: list[uuid.UUID]):
    """Create + start a reindex job. Returns the job, or None when another job is busy (caller falls back)."""
    from src.shared.services import property_import as svc

    try:
        job = await svc.create_reindex_job(session, tenant_id=user.tenant_id, user_id=getattr(user, "user_id", None), ids=ids)
    except svc.ConflictError:
        return None
    background.add_task(svc.start_reindex, user.tenant_id, job.import_id)
    return job


@router.post("/bulk/status", summary="Change the status of several listings",
             description=f"`ids` (<= {BULK_MAX_IDS}) or `filters` (all matching, <= {EXPORT_MAX_ROWS}). Re-indexing runs as a background job. "
                         "Setting VERIFIED_ACTIVE (bypasses REGA verification) is admin-only and audit-logged.")
async def bulk_status(body: BulkStatusScoped, background: BackgroundTasks, user: PropertyWriteUser, repo: PropertyListingRepo) -> dict:
    from src.shared.services.property_import import reindex, write_audit

    if body.status == ListingStatus.VERIFIED_ACTIVE and not can_activate_listings(user):
        raise HTTPException(status_code=403, detail={
            "code": "FORBIDDEN", "message": "اعتماد العقارات (نشط ومعتمد) دون تحقق الهيئة متاح للمدير (admin) فقط."})
    ids = await _scope_ids(repo.session, user.tenant_id, body)
    updated: list[uuid.UUID] = []
    for i in range(0, len(ids), 1000):
        updated += (await repo.session.execute(
            update(PropertyListing).where(PropertyListing.tenant_id == user.tenant_id, PropertyListing.listing_id.in_(ids[i:i + 1000]))
            .values(status=body.status.value).returning(PropertyListing.listing_id)
        )).scalars().all()
    logger.info("properties_bulk_status", tenant_id=str(user.tenant_id), status=body.status.value, updated=len(updated))
    if updated and body.status == ListingStatus.VERIFIED_ACTIVE:
        await write_audit(repo.session, tenant_id=user.tenant_id, actor_user_id=getattr(user, "user_id", None),
                          action="property.bulk_activate", entity_type="property_listing",
                          details={"listings": len(updated), "is_verified": False})
    job = await _queue_reindex(repo.session, background, user, updated) if updated else None
    if updated and job is None:                     # another job is busy: still re-embed, just without progress tracking
        background.add_task(reindex, user.tenant_id, list(updated))
    return {"requested": len(ids), "updated": len(updated), "not_found": len(ids) - len(updated),
            "reindex_job_id": str(job.import_id) if job else None}


@router.post("/reindex", status_code=status.HTTP_202_ACCEPTED, summary="Re-index listings into the RAG index (background job)",
             description=f"`ids` (<= {BULK_MAX_IDS}) or `filters` (all matching, <= {EXPORT_MAX_ROWS}); poll GET /api/v1/properties/import/{{job_id}}.")
async def reindex_properties(body: BulkScope, background: BackgroundTasks, user: PropertyWriteUser, repo: PropertyListingRepo) -> dict:
    from src.shared.services import property_import as svc

    ids = await _scope_ids(repo.session, user.tenant_id, body)
    if body.ids is not None:                       # explicit ids: keep only this tenant's listings
        ids = list((await repo.session.execute(select(PropertyListing.listing_id).where(
            PropertyListing.tenant_id == user.tenant_id, PropertyListing.listing_id.in_(ids)))).scalars().all())
    if not ids:
        return {"queued": 0, "job": None}
    try:
        job = await svc.create_reindex_job(repo.session, tenant_id=user.tenant_id, user_id=getattr(user, "user_id", None), ids=ids)
    except svc.ConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "IMPORT_IN_PROGRESS", "message": str(exc)}) from exc
    background.add_task(svc.start_reindex, user.tenant_id, job.import_id)
    return {"queued": len(ids), "job": svc.serialize_job(job)}


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/properties
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "",
    response_model=PropertyListingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a property listing",
    description=(
        "Create a new property listing for the authenticated tenant. "
        "If `rega_ad_number` is omitted, a DEV-REGA placeholder is generated. "
        "After the DB commit, the listing is asynchronously synced to Qdrant."
    ),
)
async def create_property(
    body: PropertyListingCreate,
    user: PropertyWriteUser,
    repo: PropertyListingRepo,
) -> PropertyListingResponse:
    # ── Inject tenant_id and resolve REGA number ──────────────────────────────
    # We build the ORM object directly (not via schema.model_dump()) so we can
    # inject fields that must come from the verified JWT, not the request body.
    rega_number = _resolve_rega_number(body.rega_ad_number)

    listing = PropertyListing(
        tenant_id=user.tenant_id,
        rega_ad_number=rega_number,
        property_type=body.property_type,
        status=body.status,
        city=body.city,
        district=body.district,
        latitude=body.latitude,
        longitude=body.longitude,
        price=body.price,
        area_sqm=body.area_sqm,
        bedrooms=body.bedrooms,
        bathrooms=body.bathrooms,
        description_ar=body.description_ar,
        description_en=body.description_en,
    )
    repo.session.add(listing)
    await repo.session.flush()
    await repo.session.refresh(listing)

    logger.info(
        "property_created",
        listing_id=str(listing.listing_id),
        tenant_id=str(user.tenant_id),
        rega=rega_number,
    )

    # ── Async vector sync — fire and forget ───────────────────────────────────
    # create_task() schedules the coroutine on the running event loop without
    # awaiting it. The HTTP response is returned immediately after this line.
    asyncio.create_task(
        sync_listing_to_qdrant(listing, user.tenant_id)
    )

    return PropertyListingResponse.model_validate(listing)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/properties/{listing_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{listing_id}",
    response_model=PropertyListingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a property listing",
)
async def get_property(
    listing_id: uuid.UUID,
    user: CurrentUser,
    repo: PropertyListingRepo,
) -> PropertyListingResponse:
    listing = await repo.get_or_404(listing_id)
    return PropertyListingResponse.model_validate(listing)


# ══════════════════════════════════════════════════════════════════════════════
# PUT /api/v1/properties/{listing_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.put(
    "/{listing_id}",
    response_model=PropertyListingResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a property listing",
    description=(
        "Partially update a property listing (PATCH semantics — only supplied fields "
        "are written). Triggers an async Qdrant re-index after the DB commit."
    ),
)
async def update_property(
    listing_id: uuid.UUID,
    body: PropertyListingUpdate,
    user: PropertyWriteUser,
    repo: PropertyListingRepo,
) -> PropertyListingResponse:
    listing = await repo.update(listing_id, body)

    logger.info(
        "property_updated",
        listing_id=str(listing_id),
        tenant_id=str(user.tenant_id),
    )

    # Re-index with updated content
    asyncio.create_task(
        sync_listing_to_qdrant(listing, user.tenant_id)
    )

    return PropertyListingResponse.model_validate(listing)


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/v1/properties/{listing_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.delete(
    "/{listing_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a property listing",
    description=(
        "Permanently delete a property listing from Postgres and asynchronously "
        "remove its vector from Qdrant."
    ),
)
async def delete_property(
    listing_id: uuid.UUID,
    user: PropertyWriteUser,
    repo: PropertyListingRepo,
) -> None:
    listing_id_str = str(listing_id)

    deleted = await repo.delete(listing_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": f"Property {listing_id_str} not found or already deleted.",
            },
        )

    logger.info(
        "property_deleted",
        listing_id=listing_id_str,
        tenant_id=str(user.tenant_id),
    )

    # Async Qdrant cleanup — fire and forget
    asyncio.create_task(
        delete_listing_from_qdrant(listing_id_str, user.tenant_id)
    )
