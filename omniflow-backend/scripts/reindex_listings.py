"""Re-embed the VERIFIED_ACTIVE property listings from PostgreSQL (the source of truth) into Qdrant. NEVER deletes anything.

Use it after changing the embedding model (EMBEDDING_LOCAL_MODEL): old vectors live in a different vector space (and often a different size), so they must be rebuilt.

    python -m scripts.reindex_listings --dry-run                  # what would be done, per tenant
    python -m scripts.reindex_listings --tenant <uuid>            # one tenant
    python -m scripts.reindex_listings --all [--batch 50]         # every tenant

Safe path when the vector SIZE changes (e.g. 384 -> 1024): set QDRANT_COLLECTION_SUFFIX=_ml (any suffix) for this run AND for the app, run the script — it fills the NEW collection
`t_<tenant>_listings_ml` — then deploy the app with the same suffix. The old collection is untouched until you drop it yourself. Writing into an existing collection whose vector size
differs from the model's is REFUSED (Qdrant would reject it anyway). Upserts are idempotent: running twice is harmless.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from src.shared.core.config import get_settings
from src.shared.core.enums import ListingStatus
from src.shared.db.models import PropertyListing, Tenant
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.qdrant_client.client import _tenant_collection, qdrant_mgr
from src.shared.services.vector_sync import sync_listings_batch


async def _collection_dim(tenant_id: uuid.UUID) -> int | None:
    try:
        info = await qdrant_mgr._c.get_collection(_tenant_collection(tenant_id))
    except Exception:  # noqa: BLE001 — a missing collection is fine: it is created with the right size on first upsert
        return None
    vectors = info.config.params.vectors
    return getattr(vectors, "size", None)


async def reindex_tenant(tenant_id: uuid.UUID, *, batch: int, dry_run: bool) -> tuple[int, int, str]:
    """(listings, indexed, note)."""
    settings = get_settings()
    async with get_tenant_session(tenant_id) as session:
        listings = list((await session.execute(select(PropertyListing).where(
            PropertyListing.tenant_id == tenant_id, PropertyListing.status == ListingStatus.VERIFIED_ACTIVE.value))).scalars().all())
    if not listings:
        return 0, 0, "no active listings"
    await qdrant_mgr.ensure_started()
    existing = await _collection_dim(tenant_id)
    if existing is not None and existing != settings.embedding_dim:
        return len(listings), 0, (f"REFUSED: collection {_tenant_collection(tenant_id)} has vector size {existing} but the model produces {settings.embedding_dim}. "
                                  f"Set QDRANT_COLLECTION_SUFFIX (e.g. _ml) to fill a new collection; the old one is left untouched.")
    if dry_run:
        return len(listings), 0, f"dry-run: would upsert {len(listings)} listings into {_tenant_collection(tenant_id)} (dim {settings.embedding_dim})"
    done = 0
    for i in range(0, len(listings), batch):
        res = await sync_listings_batch(listings[i:i + batch], tenant_id)
        if res.error:
            return len(listings), done, f"FAILED after {done}: {res.error}"
        done += len(res.ids)
    return len(listings), done, "ok"


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenant", help="one tenant id")
    group.add_argument("--all", action="store_true", help="every tenant")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.all:
        async with get_system_session() as session:
            tenants = list((await session.execute(select(Tenant.tenant_id))).scalars().all())
    else:
        tenants = [uuid.UUID(args.tenant)]
    settings = get_settings()
    print(f"model={settings.embedding_local_model if settings.embedding_provider == 'fastembed' else settings.embedding_model} dim={settings.embedding_dim} suffix={settings.qdrant_collection_suffix!r}")
    bad = 0
    for tid in tenants:
        total, done, note = await reindex_tenant(tid, batch=args.batch, dry_run=args.dry_run)
        print(f"{tid}: listings={total} indexed={done} {note}")
        bad += note.startswith(("REFUSED", "FAILED"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
