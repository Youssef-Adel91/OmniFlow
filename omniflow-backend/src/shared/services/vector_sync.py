"""
shared/services/vector_sync.py — Property Listing → Qdrant Vector Sync

Embeds a PropertyListing's text representation using the OpenAI
text-embedding-3-small model and upserts the vector into the tenant's
dedicated Qdrant collection.

Design principles:
  - Fire-and-forget via asyncio.create_task() — never blocks HTTP responses.
  - Mock fallback: if OPENAI_API_KEY is absent/empty/"mock", a deterministic
    dummy 1536-dim vector [0.1] * 1536 is used. Qdrant still receives the
    point so the rest of the pipeline is exercisable without OpenAI billing.
  - Errors are logged but NEVER propagated — the HTTP endpoint must always
    succeed even if Qdrant is temporarily unreachable.

Fix (Sprint 16 E2E):
  The SQLAlchemy get_tenant_session() context manager uses NullPool (PgBouncer
  transaction-mode). When called from inside asyncio.create_task(), the session
  is closed by the time the background coroutine executes the UPDATE, causing
  the qdrant_point_id back-write to fail silently.

  Resolution: bypass SQLAlchemy entirely for this single-row UPDATE.
  Use asyncpg.connect() directly to localhost:6432 (PgBouncer). This is safe
  because:
    1. We only do a point-style UPDATE — no RLS queries needed (we have
       the exact listing_id + tenant_id from the calling context).
    2. asyncpg is already a project dependency (used by the test harness).
    3. The connection is acquired, used, and released in < 5ms.

Usage in FastAPI endpoint:
    asyncio.create_task(sync_listing_to_qdrant(listing, tenant_id))

References: SRS §3.3 — Vector Store; Sprint 14 spec; Sprint 16 E2E fix
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from src.shared.core.config import get_settings
from src.shared.db.models import PropertyListing
from src.shared.qdrant_client.client import qdrant_mgr

logger = structlog.get_logger(__name__)
_settings = get_settings()

# Qdrant embedding dimension (text-embedding-3-small)
_EMBEDDING_DIM = 1536

# Dummy vector used when OpenAI is unavailable / mocked
_MOCK_VECTOR: list[float] = [0.1] * _EMBEDDING_DIM


# ══════════════════════════════════════════════════════════════════════════════
# Embedding Helper
# ══════════════════════════════════════════════════════════════════════════════

def _build_listing_text(listing: PropertyListing) -> str:
    """
    Build the Arabic text representation that will be embedded.

    Format mirrors what the RAG retriever expects in the prompt context.
    Example:
        "شقة في الرياض حي النرجس - 4 غرف - 180م² - 850000 ريال
         وصف: شقة فاخرة بإطلالة على الحديقة ..."
    """
    type_map: dict[str, str] = {
        "apartment":    "شقة",
        "villa":        "فيلا",
        "land":         "أرض",
        "commercial":   "عقار تجاري",
        "daily_rental": "إيجار يومي",
        "office":       "مكتب",
        "warehouse":    "مستودع",
    }
    prop_type_ar = type_map.get(str(listing.property_type), str(listing.property_type))

    parts: list[str] = [prop_type_ar]

    if listing.city:
        loc = f"في {listing.city}"
        if listing.district:
            loc += f" حي {listing.district}"
        parts.append(loc)

    if listing.bedrooms is not None:
        parts.append(f"{listing.bedrooms} غرف نوم")
    if listing.bathrooms is not None:
        parts.append(f"{listing.bathrooms} حمام")
    if listing.area_sqm is not None:
        parts.append(f"مساحة {listing.area_sqm:.0f}م²")
    if listing.price is not None:
        parts.append(f"السعر {listing.price:,.0f} ريال سعودي")

    header = " - ".join(parts)

    if listing.description_ar:
        return f"{header}\nوصف: {listing.description_ar}"
    if listing.description_en:
        return f"{header}\nDescription: {listing.description_en}"
    return header


async def _embed_text(text: str) -> list[float]:
    """
    Embed text using OpenAI text-embedding-3-small.

    MOCK FALLBACK:
        If OPENAI_API_KEY is missing, empty, or the literal string "mock",
        returns _MOCK_VECTOR ([0.1] * 1536) and logs a warning. This allows
        the full Qdrant pipeline to be exercised in dev without OpenAI billing.

    Returns:
        List of 1536 floats (cosine-normalised by OpenAI).
    """
    api_key = getattr(_settings, "openai_api_key", None) or ""

    if not api_key or api_key.lower() in ("mock", "none", ""):
        logger.warning(
            "vector_sync_mock_embedding",
            reason="OPENAI_API_KEY not configured — using dummy vector",
            text_preview=text[:80],
        )
        return _MOCK_VECTOR

    try:
        # Late import — openai package may not be installed in all workers
        import openai  # type: ignore[import]

        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
            encoding_format="float",
        )
        vector: list[float] = response.data[0].embedding
        logger.debug(
            "vector_sync_embedded",
            text_length=len(text),
            vector_dim=len(vector),
        )
        return vector

    except Exception as exc:
        logger.error(
            "vector_sync_embedding_failed",
            error=str(exc),
            exc_type=type(exc).__name__,
            reason="Falling back to mock vector",
        )
        return _MOCK_VECTOR


# ══════════════════════════════════════════════════════════════════════════════
# DB Back-Write Helper — asyncpg direct connection (bypasses NullPool issue)
# ══════════════════════════════════════════════════════════════════════════════

async def _backfill_qdrant_point_id(listing_id: str) -> None:
    """
    Update property_listings.qdrant_point_id via a direct asyncpg connection.

    WHY NOT get_tenant_session()?
    ─────────────────────────────
    This function is always called from inside asyncio.create_task(). By the
    time the background task runs, the SQLAlchemy NullPool session created
    inside the HTTP request handler has already been closed and returned to
    PgBouncer. Attempting to reuse the session (or even create a new one
    through the shared engine) can fail with "connection closed" errors in
    some asyncio event-loop scheduling scenarios.

    Using asyncpg.connect() directly to PgBouncer (port 6432) is:
      - Reliable: a fresh connection is opened and closed per call.
      - Safe: we hold the listing_id so no RLS filter is needed; the
        WHERE clause is an exact primary-key lookup.
      - Fast: the UPDATE touches exactly one row and completes in < 3ms.

    The connection is always closed in the finally block — no leaks.
    """
    import asyncpg  # type: ignore[import]

    conn: asyncpg.Connection | None = None
    try:
        conn = await asyncpg.connect(
            host=_settings.pgbouncer_host,
            port=_settings.pgbouncer_port,
            database=_settings.postgres_db,
            user=_settings.postgres_user,
            password=_settings.postgres_password,
            ssl="disable",
            timeout=10,
        )
        await conn.execute(
            "UPDATE property_listings SET qdrant_point_id = $1 WHERE listing_id = $2",
            listing_id,
            uuid.UUID(listing_id),
        )
        logger.debug(
            "vector_sync_qdrant_point_id_backfilled",
            listing_id=listing_id,
        )
    except Exception as db_exc:
        # Non-fatal — the vector is already indexed in Qdrant.
        # The qdrant_point_id column is a convenience back-pointer, not
        # the source of truth. The Qdrant collection is.
        logger.warning(
            "vector_sync_db_backfill_failed",
            listing_id=listing_id,
            error=str(db_exc),
            exc_type=type(db_exc).__name__,
        )
    finally:
        if conn is not None:
            await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Main Sync Entry Point
# ══════════════════════════════════════════════════════════════════════════════

async def sync_listing_to_qdrant(
    listing: PropertyListing,
    tenant_id: uuid.UUID,
) -> None:
    """
    Embed and upsert a property listing into the tenant's Qdrant collection.

    This function is designed to be called via:
        asyncio.create_task(sync_listing_to_qdrant(listing, tenant_id))

    It NEVER raises — all errors are caught and logged so that the parent
    HTTP request is never affected.

    Flow:
        1. Build Arabic text representation from listing fields.
        2. Embed via OpenAI (or mock fallback).
        3. Build Qdrant payload dict (mirrors _PAYLOAD_FIELDS from client.py).
        4. Upsert into tenant's per-collection (auto-created if absent).
        5. Update listing.qdrant_point_id in Postgres via direct asyncpg
           connection to PgBouncer — bypassing the NullPool session issue.

    Args:
        listing   — The PropertyListing ORM object (already committed to DB).
        tenant_id — The owning tenant UUID (used for collection routing + RLS).
    """
    listing_id_str = str(listing.listing_id)

    try:
        # ── Step 1: Build text ──────────────────────────────────────────────
        text = _build_listing_text(listing)

        # ── Step 2: Embed ───────────────────────────────────────────────────
        vector = await _embed_text(text)

        # ── Step 3: Build Qdrant payload ────────────────────────────────────
        payload: dict[str, Any] = {
            "tenant_id":      str(tenant_id),
            "listing_id":     listing_id_str,
            "rega_ad_number": listing.rega_ad_number,
            "property_type":  str(listing.property_type),
            "price_sar":      float(listing.price) if listing.price is not None else None,
            "area_sqm":       float(listing.area_sqm) if listing.area_sqm is not None else None,
            "bedrooms":       listing.bedrooms,
            "bathrooms":      listing.bathrooms,
            "city":           listing.city,
            "district":       listing.district,
            "latitude":       listing.latitude,
            "longitude":      listing.longitude,
            "status":         str(listing.status),
            "summary":        text[:500],  # First 500 chars — injected into LLM prompts
        }

        # ── Step 4: Upsert to Qdrant ────────────────────────────────────────
        # QdrantManager.start() must already be called at app startup.
        # It calls _ensure_collection_exists() so the first upsert is safe.
        await qdrant_mgr.upsert_property_listing(
            tenant_id=tenant_id,
            listing_id=listing_id_str,
            vector=vector,
            payload=payload,
        )

        # ── Step 5: Persist qdrant_point_id back to Postgres ────────────────
        # Use direct asyncpg connection to bypass NullPool session lifecycle
        # issue that occurs when this runs inside asyncio.create_task().
        await _backfill_qdrant_point_id(listing_id_str)

        logger.info(
            "vector_sync_complete",
            listing_id=listing_id_str,
            tenant_id=str(tenant_id),
            mock=vector == _MOCK_VECTOR,
        )

    except Exception as exc:
        # Top-level catch — NEVER propagate so the background task stays clean
        logger.error(
            "vector_sync_failed",
            listing_id=listing_id_str,
            tenant_id=str(tenant_id),
            error=str(exc),
            exc_type=type(exc).__name__,
        )


async def delete_listing_from_qdrant(
    listing_id: str,
    tenant_id: uuid.UUID,
) -> None:
    """
    Remove a listing vector from Qdrant on DELETE.

    Also fire-and-forget safe — errors are logged, not propagated.
    """
    try:
        await qdrant_mgr.delete_property_listing(
            tenant_id=tenant_id,
            listing_id=listing_id,
        )
        logger.info(
            "vector_sync_deleted",
            listing_id=listing_id,
            tenant_id=str(tenant_id),
        )
    except Exception as exc:
        logger.error(
            "vector_sync_delete_failed",
            listing_id=listing_id,
            tenant_id=str(tenant_id),
            error=str(exc),
        )
