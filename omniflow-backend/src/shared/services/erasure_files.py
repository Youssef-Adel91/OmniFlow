"""Files that belong to one customer (today: the PDF reports in the vault bucket) and how to delete them when the customer is erased.

The database is the source of truth for what exists: `customer_reports.s3_url` rows and `customers.vault_s3_prefix`. Inbound media is NOT
copied into our storage (WhatsApp ids / Meta CDN links only), so there is nothing else to delete. Every key is checked to sit under
`tenant-<tenant_id>/` before it is touched, so a bad prefix in a row can never delete another workspace's files.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy import select, update

from src.shared.core.config import get_settings
from src.shared.db.models import Customer, CustomerReport

logger = structlog.get_logger(__name__)


class FileStore(Protocol):
    async def list_keys(self, bucket: str, prefix: str, limit: int = ...) -> list[str]: ...
    async def delete_file(self, bucket: str, key: str) -> None: ...


class FilesNotDeleted(Exception):
    def __init__(self, failed: list[str]):
        super().__init__(f"{len(failed)} file(s) could not be deleted")
        self.failed = failed


@dataclass(frozen=True)
class Targets:
    keys: tuple[str, ...]            # exact object keys in the vault bucket
    prefixes: tuple[str, ...]        # prefixes to expand (listing) before deleting
    report_ids: tuple[uuid.UUID, ...]

    @property
    def empty(self) -> bool:
        return not (self.keys or self.prefixes)


def tenant_root(tenant_id: uuid.UUID) -> str:
    return f"tenant-{tenant_id}/"


def key_from_url(url: str | None, bucket: str) -> str | None:
    """`<endpoint>/<bucket>/<key>` → key. None when the URL does not point into `bucket`."""
    if not url:
        return None
    marker = f"/{bucket}/"
    i = url.find(marker)
    return url[i + len(marker):] if i >= 0 and url[i + len(marker):] else None


async def collect(session, tenant_id: uuid.UUID, customer: Customer) -> Targets:
    bucket = get_settings().s3_vault_bucket
    root = tenant_root(tenant_id)
    reports = (await session.execute(select(CustomerReport).where(CustomerReport.customer_id == customer.customer_id,
                                                                  CustomerReport.tenant_id == tenant_id))).scalars().all()
    keys = [k for k in (key_from_url(r.s3_url, bucket) for r in reports) if k and k.startswith(root)]
    prefixes = [customer.vault_s3_prefix] if customer.vault_s3_prefix and customer.vault_s3_prefix.startswith(root) else []
    return Targets(tuple(dict.fromkeys(keys)), tuple(prefixes), tuple(r.report_id for r in reports if r.s3_url))


async def delete(targets: Targets, tenant_id: uuid.UUID, store: FileStore | None = None) -> int:
    """Delete everything in `targets`; returns the number of objects removed. Raises FilesNotDeleted if any deletion failed (others still attempted)."""
    if targets.empty:
        return 0
    if store is None:
        from src.shared.storage.s3 import s3_mgr
        store = s3_mgr
    bucket, root = get_settings().s3_vault_bucket, tenant_root(tenant_id)
    keys = list(targets.keys)
    failed: list[str] = []
    for prefix in targets.prefixes:
        try:
            keys += await store.list_keys(bucket, prefix)
        except Exception as exc:  # noqa: BLE001
            logger.warning("erasure_list_failed", error=type(exc).__name__)
            failed.append(prefix)
    removed = 0
    for key in dict.fromkeys(keys):
        if not key.startswith(root):
            continue
        try:
            await store.delete_file(bucket, key)
            removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("erasure_delete_failed", error=type(exc).__name__)
            failed.append(key)
    if failed:
        raise FilesNotDeleted(failed)
    return removed


async def forget_reports(session, tenant_id: uuid.UUID, report_ids: tuple[uuid.UUID, ...]) -> None:
    """The report rows stay (they are accounting records) but no longer point at a deleted file."""
    if report_ids:
        await session.execute(update(CustomerReport).where(CustomerReport.report_id.in_(report_ids), CustomerReport.tenant_id == tenant_id).values(s3_url=None))
