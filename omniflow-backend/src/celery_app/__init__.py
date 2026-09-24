"""celery_app — Celery application & scheduled tasks

Ref: SRS §2.2.1 — Distributed Task Scheduler (Celery + Redis Beat)

Layout
------
    app.py    Celery instance, broker/backend wiring, beat schedule
    tasks.py  the periodic task implementations

`celery -A src.celery_app ...` resolves the `app` symbol re-exported here.

Implemented scheduled tasks
---------------------------
    omniflow.sla_escalation_check       every minute   escalated conversations
                                                       with no human reply
    omniflow.vcard_reminder_check       hourly         VCard drip sequence
                                                       (REMINDER_1/2 → DORMANT)
    omniflow.vip_followup_check         03:00 daily    quiet VIP customers
    omniflow.rega_reverification_check  02:00 daily    stale REGA verifications
    omniflow.media_cleanup_check        hourly         voice-note TTL sweep (item 16)

Still to build (listed in the SRS, deliberately not implemented here):
    - Image retention (nothing to sweep yet — no vision pipeline downloads
      images into our storage at all; see item 14/16 in IMPLEMENTATION_STATUS.md)
    - Semantic cache pruning in Qdrant

Running it
----------
    celery -A src.celery_app worker --loglevel=INFO --concurrency=4
    celery -A src.celery_app beat   --loglevel=INFO

See README.md in this package for the full deployment runbook.
"""
from src.celery_app.app import app

# Import for side effects: registers every @app.task with the Celery registry
# so `celery -A src.celery_app worker` and beat both see the same task names.
from src.celery_app import tasks  # noqa: F401,E402

__all__ = ["app", "tasks"]
