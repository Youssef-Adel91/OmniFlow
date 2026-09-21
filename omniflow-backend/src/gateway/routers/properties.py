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
import math
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from src.gateway.dependencies import (
    CurrentUser,
    PropertyWriteUser,
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
from src.shared.services.vector_sync import (
    delete_listing_from_qdrant,
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

@router.get(
    "",
    response_model=PropertyListingPage,
    status_code=status.HTTP_200_OK,
    summary="List property listings",
    description=(
        "Return a paginated list of property listings for the authenticated tenant. "
        "Filter by `status` or `property_type` to narrow results."
    ),
)
async def list_properties(
    user: CurrentUser,
    repo: PropertyListingRepo,
    page: int = Query(default=1, ge=1, description="1-indexed page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by ListingStatus (e.g. VERIFIED_ACTIVE)",
    ),
    type_filter: str | None = Query(
        default=None,
        alias="property_type",
        description="Filter by PropertyType (e.g. apartment)",
    ),
) -> PropertyListingPage:
    offset = (page - 1) * limit

    # Build filters dict — only include non-None values
    filters: dict[str, str] = {}
    if status_filter:
        filters["status"] = status_filter
    if type_filter:
        filters["property_type"] = type_filter

    items, total = await repo.get_multi(
        offset=offset,
        limit=limit,
        filters=filters or None,
    )

    logger.debug(
        "properties_list",
        tenant_id=str(user.tenant_id),
        page=page,
        total=total,
    )

    return PropertyListingPage(
        items=[PropertyListingResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        limit=limit,
        pages=max(1, math.ceil(total / limit)),
    )


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
