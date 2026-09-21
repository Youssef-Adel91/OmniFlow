# Celery — Worker & Beat Runbook

Two **separate** long-running processes are required. Do not merge them: beat
only schedules, workers only execute.

```bash
# Task executor (scale horizontally — safe to run many replicas)
celery -A src.celery_app worker --loglevel=INFO --concurrency=4

# Scheduler (emits the periodic jobs)
celery -A src.celery_app beat --loglevel=INFO
```

Both must run from the repository root with the same `.env` as the API, since
they read `src/shared/core/config.py::Settings` for the Redis broker and the
PostgreSQL connection.

## Docker Compose services

```yaml
  celery-worker:
    build: .
    command: celery -A src.celery_app worker --loglevel=INFO --concurrency=4
    env_file: .env
    depends_on: [redis, pgbouncer]
    restart: unless-stopped

  celery-beat:
    build: .
    command: celery -A src.celery_app beat --loglevel=INFO
    env_file: .env
    depends_on: [redis, pgbouncer]
    restart: unless-stopped
    deploy:
      replicas: 1   # see note below
```

`celery-redbeat` is a declared dependency, so the schedule lives in Redis and
takes a distributed lock (`redbeat_lock_timeout = 90`). That makes more than
one beat replica survivable, but **one replica is still the recommendation** —
the lock protects against duplicates, it is not a scaling mechanism.

If `redbeat` is not installed the app silently falls back to Celery's
file-based `PersistentScheduler`, which writes `celerybeat-schedule` to the
working directory. In that mode you **must** run exactly one beat replica, and
the container needs a writable volume for that file.

## Schedule

| Task name | Cadence | What it does |
|---|---|---|
| `omniflow.sla_escalation_check` | every 60s | Flags `escalated` conversations with no `human_agent` reply past `ESCALATION_SLA_MINUTES`; `critical` past `ESCALATION_MANAGER_ALERT_MINUTES`. |
| `omniflow.vcard_reminder_check` | hourly | Advances the VCard state machine: `VCARD_SENT`/`AWAITING_VALIDATION` → `REMINDER_1` → `REMINDER_2` → `DORMANT`. |
| `omniflow.vip_followup_check` | 03:00 Asia/Riyadh | Lists VIP / high-engagement customers with no activity for 7 days. |
| `omniflow.rega_reverification_check` | 02:00 Asia/Riyadh | Resets listings verified more than 30 days ago to `PENDING_VERIFICATION`. |

Every task returns a JSON summary that is stored in the Redis result backend
(`REDIS_DB_CELERY_RESULTS`, TTL 1h) — useful for verifying a run without
grepping logs.

## Manual invocation

```bash
celery -A src.celery_app call omniflow.sla_escalation_check
celery -A src.celery_app inspect scheduled     # queued ETA tasks
celery -A src.celery_app inspect registered    # confirm task names
```

## Environment

Uses the standard Redis settings plus the two dedicated logical databases:

```
REDIS_HOST / REDIS_PORT / REDIS_PASSWORD / REDIS_SSL
REDIS_DB_CELERY_BROKER=3
REDIS_DB_CELERY_RESULTS=4
APP_TIMEZONE=Asia/Riyadh        # cron expressions resolve in this zone
```

## Known gaps

- Tasks report findings as structured log events. Routing them to a real alert
  channel is blocked on `src/notification_svc`, which is still an empty
  package. Search for `TODO(alerting)` in `src/celery_app/tasks.py`.
- The VCard sweep advances persisted state but does not yet push the reminder
  message to WhatsApp; see `TODO(outbound)` in
  `src/shared/tasks/vcard_tasks.py`.
- SRS also lists media cleanup and semantic-cache pruning as scheduled jobs.
  Neither is implemented.
