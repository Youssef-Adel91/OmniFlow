"""
celery_app/tasks.py — Background tasks

On-demand tasks (dispatched by the API, not on a schedule):

    omniflow.ingest_knowledge_document  Knowledge Base ingestion (SRS §4.2):
                                        S3 → text → chunks → embeddings → Qdrant

Four periodic sweeps (registered in `src/celery_app/app.py::beat_schedule`):

    omniflow.sla_escalation_check       every minute
    omniflow.vcard_reminder_check       hourly
    omniflow.vip_followup_check         daily 03:00 Riyadh
    omniflow.rega_reverification_check  daily 02:00 Riyadh

DESIGN NOTES
------------
Cross-tenant scope
    These are platform-wide sweeps, so they run inside `get_system_session()`
    (the reserved nil-UUID sentinel + `system_bypass_policy` from migration
    0006). Never copy this pattern into a request handler.

Async inside Celery
    The whole data layer is async SQLAlchemy while Celery workers are
    synchronous, so each task body is an `async def` executed through
    `_run(...)` → `asyncio.run(...)`. The engine uses NullPool (PgBouncer owns
    pooling), so no connection outlives the per-task event loop.

Return values
    Every task returns a small JSON-serialisable dict. Those land in the Redis
    result backend and are the quickest way to inspect what a run actually did.

Alerting
    Findings are currently emitted as structured log events
    (`sla_breach_detected`, `vip_followup_due`, ...). Routing them to a real
    notification channel is the job of `src/notification_svc`, which is still
    an empty package — the hand-off points are marked `TODO(alerting)`.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, TypeVar

import structlog
from sqlalchemy import and_, exists, func, or_, select, update

from src.celery_app.app import app
from src.shared.core.config import get_settings
from src.shared.core.enums import ConversationStatus, ListingStatus
from src.shared.db.models import (
    Conversation,
    Customer,
    Message,
    PropertyListing,
)
from src.shared.db.session import get_system_session
from src.shared.tasks.vcard_tasks import advance_vcard_states

logger = structlog.get_logger(__name__)
settings = get_settings()

_T = TypeVar("_T")

# Safety cap so a single run can never hold a transaction open over a huge
# backlog. Anything left over is picked up by the next scheduled run.
_BATCH_LIMIT = 500

# A verified listing must be re-checked against REGA after this many days.
_REGA_REVERIFY_AFTER_DAYS = 30


def _run(coro_factory: Callable[[], Awaitable[_T]]) -> _T:
    """Execute an async task body from a synchronous Celery worker."""
    return asyncio.run(coro_factory())


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# 1. SLA escalation check — every minute
# ══════════════════════════════════════════════════════════════════════════════

@app.task(name="omniflow.sla_escalation_check", bind=True, max_retries=2)
def sla_escalation_check(self) -> dict[str, Any]:  # noqa: ANN001
    """
    Find escalated conversations that no human has answered in time.

    A conversation breaches SLA when ALL of the following hold:
      * status = 'escalated'
      * it has been in that state longer than ESCALATION_SLA_MINUTES
        (measured from `last_message_at`, falling back to `updated_at`)
      * no message with sender_type = 'human_agent' exists after the threshold

    Two severities are reported:
      warning  — past ESCALATION_SLA_MINUTES (default 15)
      critical — past ESCALATION_MANAGER_ALERT_MINUTES (default 20)
    """
    return _run(_sla_escalation_check_async)


async def _sla_escalation_check_async() -> dict[str, Any]:
    now = _now()
    sla_cutoff = now - timedelta(minutes=settings.escalation_sla_minutes)
    manager_cutoff = now - timedelta(
        minutes=settings.escalation_manager_alert_minutes
    )

    async with get_system_session() as session:
        # No human reply after the SLA cutoff.
        human_replied = exists(
            select(Message.message_id).where(
                and_(
                    Message.conversation_id == Conversation.conversation_id,
                    Message.sender_type == "human_agent",
                    Message.created_at >= sla_cutoff,
                )
            )
        )

        stale_at = func.coalesce(Conversation.last_message_at, Conversation.updated_at)

        rows = (
            await session.execute(
                select(
                    Conversation.conversation_id,
                    Conversation.tenant_id,
                    Conversation.customer_id,
                    Conversation.assigned_agent_id,
                    stale_at.label("stale_at"),
                )
                .where(Conversation.status == ConversationStatus.ESCALATED.value)
                .where(stale_at < sla_cutoff)
                .where(~human_replied)
                .order_by(stale_at.asc())
                .limit(_BATCH_LIMIT)
            )
        ).all()

        breaches: list[dict[str, Any]] = []
        for row in rows:
            waiting_minutes = int((now - row.stale_at).total_seconds() // 60)
            severity = "critical" if row.stale_at < manager_cutoff else "warning"
            breaches.append(
                {
                    "conversation_id": str(row.conversation_id),
                    "tenant_id": str(row.tenant_id),
                    "waiting_minutes": waiting_minutes,
                    "severity": severity,
                    "assigned": row.assigned_agent_id is not None,
                }
            )
            logger.warning(
                "sla_breach_detected",
                conversation_id=str(row.conversation_id),
                tenant_id=str(row.tenant_id),
                customer_id=str(row.customer_id),
                waiting_minutes=waiting_minutes,
                severity=severity,
                sla_minutes=settings.escalation_sla_minutes,
            )

    critical = sum(1 for b in breaches if b["severity"] == "critical")

    # TODO(alerting): push `breaches` to notification_svc so managers get a
    # push/WhatsApp alert rather than only a log line.
    logger.info(
        "sla_escalation_check_completed",
        breaches=len(breaches),
        critical=critical,
    )
    return {
        "checked_at": now.isoformat(),
        "breaches": len(breaches),
        "critical": critical,
        "details": breaches[:50],  # keep the result payload bounded
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. VCard reminder check — hourly
# ══════════════════════════════════════════════════════════════════════════════

@app.task(name="omniflow.vcard_reminder_check", bind=True, max_retries=2)
def vcard_reminder_check(self) -> dict[str, Any]:  # noqa: ANN001
    """
    Advance the VCard drip sequence for customers stuck awaiting confirmation.

    Delegates to `src.shared.tasks.vcard_tasks.advance_vcard_states`, which is
    the single implementation of the state machine shared with the VCard
    gatekeeper Kafka worker.
    """
    return _run(_vcard_reminder_check_async)


async def _vcard_reminder_check_async() -> dict[str, Any]:
    if not settings.feature_vcard_gatekeeper:
        logger.info("vcard_reminder_check_skipped", reason="feature disabled")
        return {"skipped": True, "reason": "FEATURE_VCARD_GATEKEEPER is off"}

    from src.shared.kafka.producer import KafkaProducerManager

    producer = KafkaProducerManager()
    await producer.start()
    try:
        async with get_system_session() as session:
            result = await advance_vcard_states(session, batch_limit=_BATCH_LIMIT, producer=producer)
    finally:
        await producer.stop()

    payload = result.as_dict()
    payload["checked_at"] = _now().isoformat()
    logger.info("vcard_reminder_check_completed", **{
        k: v for k, v in payload.items() if k != "customer_ids"
    })
    return payload


# ══════════════════════════════════════════════════════════════════════════════
# 3. VIP follow-up check — daily
# ══════════════════════════════════════════════════════════════════════════════

@app.task(name="omniflow.vip_followup_check", bind=True, max_retries=2)
def vip_followup_check(self) -> dict[str, Any]:  # noqa: ANN001
    """
    List VIP customers who have gone quiet and are due a follow-up.

    "Due" = flagged `is_vip` (or scoring above VIP_ENGAGEMENT_SCORE_THRESHOLD)
    with no conversation activity for 7 days. Read-only: it reports, it does
    not message anyone — outbound VIP sends are rate-limited by
    VIP_MAX_MESSAGES_PER_WEEK and belong to the broadcast pipeline.
    """
    return _run(_vip_followup_check_async)


async def _vip_followup_check_async() -> dict[str, Any]:
    if not settings.feature_vip_broadcast:
        logger.info("vip_followup_check_skipped", reason="feature disabled")
        return {"skipped": True, "reason": "FEATURE_VIP_BROADCAST is off"}

    now = _now()
    quiet_since = now - timedelta(days=7)

    async with get_system_session() as session:
        # Latest activity per customer, NULL when they never had a conversation.
        last_activity = (
            select(func.max(Conversation.last_message_at))
            .where(Conversation.customer_id == Customer.customer_id)
            .correlate(Customer)
            .scalar_subquery()
        )

        rows = (
            await session.execute(
                select(
                    Customer.customer_id,
                    Customer.tenant_id,
                    Customer.unified_phone,
                    Customer.display_name,
                    Customer.engagement_score,
                    last_activity.label("last_activity"),
                )
                .where(
                    or_(
                        Customer.is_vip.is_(True),
                        Customer.engagement_score
                        >= settings.vip_engagement_score_threshold,
                    )
                )
                .where(Customer.is_processing_restricted.is_(False))
                .where(
                    or_(
                        last_activity.is_(None),
                        last_activity < quiet_since,
                    )
                )
                .order_by(Customer.engagement_score.desc().nullslast())
                .limit(_BATCH_LIMIT)
            )
        ).all()

        due = [
            {
                "customer_id": str(r.customer_id),
                "tenant_id": str(r.tenant_id),
                "display_name": r.display_name,
                "engagement_score": r.engagement_score,
                "last_activity": (
                    r.last_activity.isoformat() if r.last_activity else None
                ),
            }
            for r in rows
        ]

    for entry in due:
        logger.info("vip_followup_due", **entry)

    # TODO(alerting): hand `due` to the broadcast pipeline so a re-engagement
    # template is queued, respecting VIP_MAX_MESSAGES_PER_WEEK.
    logger.info("vip_followup_check_completed", due=len(due))
    return {
        "checked_at": now.isoformat(),
        "due": len(due),
        "details": due[:50],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. REGA re-verification check — daily
# ══════════════════════════════════════════════════════════════════════════════

@app.task(name="omniflow.rega_reverification_check", bind=True, max_retries=2)
def rega_reverification_check(self) -> dict[str, Any]:  # noqa: ANN001
    """
    Flag listings whose REGA verification has gone stale.

    A listing verified more than `_REGA_REVERIFY_AFTER_DAYS` ago is reset to
    `is_verified = False` / `status = PENDING_VERIFICATION`, which puts it back
    in the queue for the REGA verification worker. This keeps the platform
    compliant: a listing must never be advertised as REGA-verified on the
    strength of a month-old check.
    """
    return _run(_rega_reverification_check_async)


async def _rega_reverification_check_async() -> dict[str, Any]:
    if not settings.feature_rega_verification:
        logger.info("rega_reverification_skipped", reason="feature disabled")
        return {"skipped": True, "reason": "FEATURE_REGA_VERIFICATION is off"}

    now = _now()
    stale_before = now - timedelta(days=_REGA_REVERIFY_AFTER_DAYS)

    async with get_system_session() as session:
        rows = (
            await session.execute(
                select(
                    PropertyListing.listing_id,
                    PropertyListing.tenant_id,
                    PropertyListing.rega_ad_number,
                    PropertyListing.updated_at,
                )
                .where(PropertyListing.is_verified.is_(True))
                .where(PropertyListing.updated_at < stale_before)
                .order_by(PropertyListing.updated_at.asc())
                .limit(_BATCH_LIMIT)
            )
        ).all()

        listing_ids = [r.listing_id for r in rows]
        if listing_ids:
            await session.execute(
                update(PropertyListing)
                .where(PropertyListing.listing_id.in_(listing_ids))
                .values(
                    is_verified=False,
                    status=ListingStatus.PENDING_VERIFICATION.value,
                )
            )

        flagged = [
            {
                "listing_id": str(r.listing_id),
                "tenant_id": str(r.tenant_id),
                "rega_ad_number": r.rega_ad_number,
                "verified_age_days": (now - r.updated_at).days,
            }
            for r in rows
        ]

    for entry in flagged:
        logger.info("rega_reverification_flagged", **entry)

    logger.info("rega_reverification_check_completed", flagged=len(flagged))
    return {
        "checked_at": now.isoformat(),
        "flagged": len(flagged),
        "reverify_after_days": _REGA_REVERIFY_AFTER_DAYS,
        "details": flagged[:50],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. Knowledge Base document ingestion — on demand (SRS §4.2)
# ══════════════════════════════════════════════════════════════════════════════

# Hard ceiling on chunks per document. A 20 MB text-heavy PDF could otherwise
# produce thousands of embeddings in one task; this bounds both cost and the
# task's wall-clock against `task_time_limit` (300s).
_MAX_CHUNKS_PER_DOCUMENT = 1500

# Chunks per OpenAI embeddings call. The API allows up to 2048 inputs, but
# smaller batches keep each HTTP request well under the payload limit and make
# a rate-limit retry cheap.
_EMBED_BATCH_SIZE = 64

# Stable namespace so re-indexing a document overwrites its old vectors instead
# of duplicating them (point id = uuid5(namespace, "document_id:chunk_index")).
_CHUNK_ID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


@app.task(
    name="omniflow.ingest_knowledge_document",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def ingest_knowledge_document(self, document_id: str) -> dict[str, Any]:  # noqa: ANN001
    """
    Parse → chunk → embed → index one uploaded Knowledge Base document.

    Triggered by `POST /api/v1/knowledge/documents` (and `.../reindex`) right
    after the file lands in S3/MinIO. The endpoint returns immediately with
    status=processing; this task drives the row to `indexed` or `failed`.

    Failure policy: NEVER fail silently. Any exception is caught, written to
    `knowledge_documents.error_message`, and the row is set to `failed` so the
    dashboard can show it and offer a re-index. The task itself then returns a
    dict describing the failure rather than raising — a Celery retry would just
    repeat a deterministic parse error. Genuinely transient problems (S3 or
    OpenAI outages) are already retried inside their own clients.
    """
    return _run(lambda: _ingest_knowledge_document_async(document_id))


async def _ingest_knowledge_document_async(document_id: str) -> dict[str, Any]:
    # Local imports: these pull in pypdf / qdrant / openai, which the periodic
    # sweeps above have no need for.
    from qdrant_client.http import models as qmodels  # noqa: PLC0415

    from src.ai_workers.rag_engine.embedder import embedder  # noqa: PLC0415
    from src.ai_workers.rag_engine.ingestion import (  # noqa: PLC0415
        ExtractionError,
        chunk_text,
        extract_text,
    )
    from src.shared.core.enums import KnowledgeDocumentStatus  # noqa: PLC0415
    from src.shared.db.models import KnowledgeDocument  # noqa: PLC0415
    from src.shared.qdrant_client.client import qdrant_mgr  # noqa: PLC0415
    from src.shared.storage.s3 import s3_mgr  # noqa: PLC0415

    doc_uuid = uuid.UUID(str(document_id))
    log = logger.bind(document_id=str(doc_uuid))

    # ── 1. Load the row and claim it (status → processing) ───────────────────
    async with get_system_session() as session:
        document = await session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.document_id == doc_uuid
            )
        )
        if document is None:
            log.warning("ingest_document_not_found")
            return {"document_id": str(doc_uuid), "status": "not_found"}

        tenant_id = document.tenant_id
        s3_key = document.s3_key
        file_type = document.file_type
        title = document.title
        original_filename = document.original_filename

        document.status = KnowledgeDocumentStatus.PROCESSING.value
        document.error_message = None

    log = log.bind(tenant_id=str(tenant_id))
    log.info("ingest_document_started", file_type=file_type, s3_key=s3_key)

    # ── 2. Fetch → extract → chunk → embed → upsert ──────────────────────────
    try:
        bucket = settings.s3_knowledge_bucket
        file_bytes = await s3_mgr.download_file(bucket=bucket, key=s3_key)

        text = extract_text(file_bytes, file_type, filename=original_filename)
        chunks = chunk_text(text)

        if not chunks:
            raise ExtractionError("Document produced no text chunks.")

        truncated = len(chunks) > _MAX_CHUNKS_PER_DOCUMENT
        if truncated:
            log.warning(
                "ingest_document_truncated",
                produced=len(chunks),
                kept=_MAX_CHUNKS_PER_DOCUMENT,
            )
            chunks = chunks[:_MAX_CHUNKS_PER_DOCUMENT]

        embedder.configure()
        await qdrant_mgr.start()

        # Replace any previous vectors for this document (re-index safety).
        await qdrant_mgr.delete_document_points(tenant_id, doc_uuid)

        indexed = 0
        for offset in range(0, len(chunks), _EMBED_BATCH_SIZE):
            batch = chunks[offset : offset + _EMBED_BATCH_SIZE]
            vectors = await embedder.embed_batch(batch)

            points = [
                qmodels.PointStruct(
                    id=str(
                        uuid.uuid5(
                            _CHUNK_ID_NAMESPACE, f"{doc_uuid}:{offset + i}"
                        )
                    ),
                    vector=vector,
                    payload={
                        "tenant_id": str(tenant_id),
                        "document_id": str(doc_uuid),
                        "chunk_index": offset + i,
                        "source_title": title,
                        "text": chunk,
                    },
                )
                for i, (chunk, vector) in enumerate(zip(batch, vectors))
            ]
            await qdrant_mgr.upsert_document_chunks(tenant_id, points)
            indexed += len(points)

    except Exception as exc:  # noqa: BLE001 — recorded on the row, see docstring
        message = f"{type(exc).__name__}: {exc}"[:1000]
        log.error("ingest_document_failed", error=message)
        async with get_system_session() as session:
            await session.execute(
                update(KnowledgeDocument)
                .where(KnowledgeDocument.document_id == doc_uuid)
                .values(
                    status=KnowledgeDocumentStatus.FAILED.value,
                    error_message=message,
                )
            )
        return {
            "document_id": str(doc_uuid),
            "status": KnowledgeDocumentStatus.FAILED.value,
            "error": message,
        }

    # ── 3. Mark indexed ──────────────────────────────────────────────────────
    async with get_system_session() as session:
        await session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.document_id == doc_uuid)
            .values(
                status=KnowledgeDocumentStatus.INDEXED.value,
                chunk_count=indexed,
                error_message=None,
                indexed_at=_now(),
            )
        )

    log.info("ingest_document_completed", chunks=indexed, chars=len(text))
    return {
        "document_id": str(doc_uuid),
        "tenant_id": str(tenant_id),
        "status": KnowledgeDocumentStatus.INDEXED.value,
        "chunk_count": indexed,
        "characters": len(text),
        "truncated": truncated,
    }


__all__ = [
    "sla_escalation_check",
    "vcard_reminder_check",
    "vip_followup_check",
    "rega_reverification_check",
    "ingest_knowledge_document",
]
