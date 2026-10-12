"""
Data-retention job (per tenant). OFF and DRY-RUN by default (`RETENTION_ENABLED=false`, `RETENTION_DRY_RUN=true`); the periods are conservative
placeholders that a lawyer must confirm before real use.

What it does, per tenant, in this order:
  1. WARN  — customers who will be anonymised within `retention_warning_days` → one in-app/e-mail notice to the workspace admins per week.
  2. ERASE — customers with no conversation activity for `retention_inactive_customer_days`: the same irreversible anonymisation a data-subject
             request uses (`privacy.anonymize_customer`), which also deletes their vault files. At most `retention_batch_limit` per run.
  3. SWEEP — report files of already-erased customers that still point at a file (older erasures), then log tables past their age:
             api_usage_log, webhook_deliveries (delivered/failed only), audit_logs.
System level: data_deletion_requests receipts past `retention_deletion_request_days`.

Nothing is deleted in dry-run: the report says what WOULD happen. Every tenant runs in its own transaction; one tenant's failure never stops the rest.
Audit-log rows hold ids and action names only (never PII) and are the accountability trail, hence the long default (5 years).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import delete, func, select

from src.shared.core.config import Settings, get_settings
from src.shared.db.models import ApiUsageLog, AuditLog, Conversation, Customer, CustomerReport, DataDeletionRequest, Tenant, WebhookDelivery
from src.shared.services import erasure_files, privacy

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TenantReport:
    tenant_id: str
    dry_run: bool
    warned_customers: int = 0
    inactive_customers: int = 0
    anonymized: int = 0
    anonymize_failed: int = 0
    orphan_report_files: int = 0
    deleted: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _activity_cutoff(cfg: Settings, now: datetime) -> datetime | None:
    return now - timedelta(days=cfg.retention_inactive_customer_days) if cfg.retention_inactive_customer_days > 0 else None


async def inactive_customer_ids(session, tenant_id: uuid.UUID, cutoff: datetime, *, newer_than: datetime | None = None, limit: int = 1000) -> list[uuid.UUID]:
    """Customers (not yet erased) whose last activity is older than `cutoff` (and, if given, not older than `newer_than`).
    Last activity = newest conversation message, else the customer's own last update/creation."""
    # conversations.last_message_at is a naive UTC column: convert it so it compares with the timezone-aware customer columns
    last_msg = select(Conversation.customer_id.label("cid"), func.timezone("UTC", func.max(Conversation.last_message_at)).label("last_msg")).where(
        Conversation.tenant_id == tenant_id).group_by(Conversation.customer_id).subquery()
    activity = func.coalesce(last_msg.c.last_msg, Customer.updated_at, Customer.created_at)
    q = (select(Customer.customer_id).outerjoin(last_msg, last_msg.c.cid == Customer.customer_id)
         .where(Customer.tenant_id == tenant_id, Customer.erased_at.is_(None), activity < cutoff))
    if newer_than is not None:
        q = q.where(activity >= newer_than)
    return list((await session.scalars(q.order_by(activity).limit(limit))).all())


async def run_for_tenant(session, tenant_id: uuid.UUID, *, dry_run: bool | None = None, now: datetime | None = None, file_store=None,
                         cfg: Settings | None = None) -> TenantReport:
    cfg = cfg or get_settings()
    now = now or _now()
    dry = cfg.retention_dry_run if dry_run is None else dry_run
    rep = TenantReport(str(tenant_id), dry)

    cutoff = _activity_cutoff(cfg, now)
    if cutoff is not None:
        # 1) warning window: will be anonymised within retention_warning_days but not yet
        warn_from = cutoff + timedelta(days=cfg.retention_warning_days)
        soon = await inactive_customer_ids(session, tenant_id, warn_from, newer_than=cutoff)
        rep.warned_customers = len(soon)
        if soon and not dry:
            from src.shared.services.notifications import notify_safe

            week = f"{now.isocalendar().year}-W{now.isocalendar().week}"
            await notify_safe(session, tenant_id, "retention_notice", "عملاء سيُخفى تعريفهم قريبًا",
                              f"{len(soon)} عميلًا لم يتفاعلوا منذ فترة طويلة سيُمحى تعريفهم تلقائيًا خلال {cfg.retention_warning_days} يومًا وفق سياسة الاحتفاظ بالبيانات. "
                              "إن أردت الإبقاء عليهم فتواصل معهم أو افتح محادثة جديدة.", "/customers", dedupe_key=f"retention_warning:{week}")
        # 2) erase
        due = await inactive_customer_ids(session, tenant_id, cutoff, limit=cfg.retention_batch_limit)
        rep.inactive_customers = len(due)
        if not dry:
            for cid in due:
                try:
                    async with session.begin_nested():
                        await privacy.anonymize_customer(session, tenant_id, cid, None, "retention policy: inactive customer", file_store=file_store)
                    rep.anonymized += 1
                except Exception as exc:  # noqa: BLE001 — a customer whose files could not be deleted is retried on the next run
                    rep.anonymize_failed += 1
                    logger.warning("retention_anonymize_failed", tenant_id=str(tenant_id), customer_id=str(cid), error=type(exc).__name__)

    # 3a) files that survived an older erasure (report rows still pointing at a file)
    orphans = (await session.execute(select(CustomerReport).join(Customer, Customer.customer_id == CustomerReport.customer_id)
                                     .where(CustomerReport.tenant_id == tenant_id, Customer.erased_at.is_not(None), CustomerReport.s3_url.is_not(None)))).scalars().all()
    rep.orphan_report_files = len(orphans)
    if orphans and not dry:
        bucket = cfg.s3_vault_bucket
        keys = tuple(k for k in (erasure_files.key_from_url(r.s3_url, bucket) for r in orphans) if k and k.startswith(erasure_files.tenant_root(tenant_id)))
        try:
            await erasure_files.delete(erasure_files.Targets(keys, (), ()), tenant_id, file_store)
            await erasure_files.forget_reports(session, tenant_id, tuple(r.report_id for r in orphans))
        except erasure_files.FilesNotDeleted:
            logger.warning("retention_orphan_files_not_deleted", tenant_id=str(tenant_id))

    # 3b) log tables
    async def sweep(label: str, model, time_col, days: int, extra=()) -> None:
        if days <= 0:
            return
        limit = now - timedelta(days=days)
        where = (model.tenant_id == tenant_id, time_col < limit, *extra)
        n = int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)
        if n and not dry:
            await session.execute(delete(model).where(*where))
        rep.deleted[label] = n

    await sweep("api_usage_log", ApiUsageLog, ApiUsageLog.created_at, cfg.retention_api_usage_days)
    await sweep("webhook_deliveries", WebhookDelivery, WebhookDelivery.created_at, cfg.retention_webhook_delivery_days, (WebhookDelivery.status != "pending",))
    await sweep("audit_logs", AuditLog, AuditLog.created_at, cfg.retention_audit_log_days)

    if not dry and (rep.anonymized or sum(rep.deleted.values()) or rep.orphan_report_files):
        session.add(AuditLog(tenant_id=tenant_id, actor_user_id=None, action="retention.run", entity_type="tenant", entity_id=str(tenant_id),
                             details={"anonymized": rep.anonymized, "deleted": rep.deleted, "orphan_files": rep.orphan_report_files}))
    return rep


async def run_system(session, *, dry_run: bool | None = None, now: datetime | None = None, cfg: Settings | None = None) -> dict[str, int]:
    cfg = cfg or get_settings()
    now = now or _now()
    dry = cfg.retention_dry_run if dry_run is None else dry_run
    if cfg.retention_deletion_request_days <= 0:
        return {"data_deletion_requests": 0}
    where = DataDeletionRequest.created_at < now - timedelta(days=cfg.retention_deletion_request_days)
    n = int(await session.scalar(select(func.count()).select_from(DataDeletionRequest).where(where)) or 0)
    if n and not dry:
        await session.execute(delete(DataDeletionRequest).where(where))
    return {"data_deletion_requests": n}


async def run_all(*, dry_run: bool | None = None, only_tenant: uuid.UUID | None = None, now: datetime | None = None, file_store=None) -> dict[str, Any]:
    """Entry point for the Celery task / CLI. Each tenant gets its own transaction; errors are reported per tenant."""
    from src.shared.db.session import get_system_session, get_tenant_session

    cfg = get_settings()
    dry = cfg.retention_dry_run if dry_run is None else dry_run
    async with get_system_session() as s:
        ids = [only_tenant] if only_tenant else list((await s.scalars(select(Tenant.tenant_id).where(Tenant.status.in_(["active", "trial", "past_due", "suspended"])))).all())
    reports: list[dict[str, Any]] = []
    for tid in ids:
        try:
            async with get_tenant_session(tid) as session:
                reports.append((await run_for_tenant(session, tid, dry_run=dry, now=now, file_store=file_store, cfg=cfg)).as_dict())
        except Exception as exc:  # noqa: BLE001
            logger.error("retention_tenant_failed", tenant_id=str(tid), error=type(exc).__name__)
            reports.append(TenantReport(str(tid), dry, error=type(exc).__name__).as_dict())
    async with get_system_session() as s:
        system = await run_system(s, dry_run=dry, now=now, cfg=cfg)
    return {"dry_run": dry, "tenants": reports, "system": system}
