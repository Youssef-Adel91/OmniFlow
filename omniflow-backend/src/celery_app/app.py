"""
celery_app/app.py — Celery application instance & beat schedule

Ref: SRS §2.2.1 — Distributed Task Scheduler (Celery + Redis Beat)

Broker/backend: Redis. Two dedicated logical databases are used
(`REDIS_DB_CELERY_BROKER` / `REDIS_DB_CELERY_RESULTS`) so Celery traffic never
collides with the conversation-state cache.

Scheduler: celery-redbeat when installed (it stores the schedule in Redis, so
multiple beat replicas can run safely behind a lock). Falls back to the stock
PersistentScheduler otherwise.

Running it (deployment team — add these as two separate services):

    celery -A src.celery_app worker --loglevel=INFO --concurrency=4
    celery -A src.celery_app beat   --loglevel=INFO

See src/celery_app/README.md for the full runbook.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from src.shared.core.config import get_settings

settings = get_settings()


def _results_backend_url() -> str:
    """Redis URL pointing at the dedicated Celery *results* database."""
    scheme = "rediss" if settings.redis_ssl else "redis"
    return (
        f"{scheme}://:{settings.redis_password}@{settings.redis_host}:"
        f"{settings.redis_port}/{settings.redis_db_celery_results}"
    )


app = Celery(
    "omniflow",
    broker=settings.celery_broker_url,
    backend=_results_backend_url(),
    include=["src.celery_app.tasks"],
)

# ── Core configuration ───────────────────────────────────────────────────────
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=settings.app_timezone,          # Asia/Riyadh
    enable_utc=True,
    # Redelivery safety: a task is acknowledged only after it completes, so a
    # worker crash re-queues the job instead of losing it.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Hard ceilings so a hung DB query can never wedge a worker slot forever.
    task_soft_time_limit=240,                # 4 min — raises SoftTimeLimitExceeded
    task_time_limit=300,                     # 5 min — worker is killed
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)

# ── Beat scheduler: prefer RedBeat (Redis-backed, HA-safe) ───────────────────
try:  # pragma: no cover — depends on the deployed extras
    import redbeat  # noqa: F401

    app.conf.redbeat_redis_url = settings.celery_broker_url
    app.conf.redbeat_lock_timeout = 90
    app.conf.beat_scheduler = "redbeat.RedBeatScheduler"
except ImportError:  # pragma: no cover
    # Stock file-based scheduler — single beat replica only.
    pass


# ══════════════════════════════════════════════════════════════════════════════
# Periodic task schedule
#
# Times are interpreted in `settings.app_timezone` (Asia/Riyadh).
# ══════════════════════════════════════════════════════════════════════════════
app.conf.beat_schedule = {
    # SLA breach detection for escalated conversations — must be tight.
    "sla-escalation-check": {
        "task": "omniflow.sla_escalation_check",
        "schedule": 60.0,  # every minute
        "options": {"expires": 55},  # drop if the previous run is still queued
    },
    # VCard drip sequence: REMINDER_1 (1h) → REMINDER_2 (24h) → DORMANT (7d).
    "vcard-reminder-check": {
        "task": "omniflow.vcard_reminder_check",
        "schedule": 3600.0,  # hourly
        "options": {"expires": 3500},
    },
    # VIP re-engagement sweep — 03:00 Riyadh, outside business hours.
    "vip-followup-check": {
        "task": "omniflow.vip_followup_check",
        "schedule": crontab(hour=3, minute=0),
    },
    # REGA re-verification — 02:00 Riyadh (SRS §2.2.1).
    "rega-reverification-check": {
        "task": "omniflow.rega_reverification_check",
        "schedule": crontab(hour=2, minute=0),
    },
}


__all__ = ["app"]
