# Data retention & erasure

**Status: the scheduled job exists but is OFF (`RETENTION_ENABLED=false`) and report-only (`RETENTION_DRY_RUN=true`).** The periods below are conservative placeholders — **a lawyer must confirm them** before the job is switched on (no legal periods were provided; none were invented as "correct").

| Rule | Default | `0` means |
|---|---|---|
| Anonymise customers with no conversation activity for N days (`RETENTION_INACTIVE_CUSTOMER_DAYS`) | 1095 (3 years) | never |
| Warn the workspace admins N days before (`RETENTION_WARNING_DAYS`) — in-app + e-mail, once per week | 14 | — |
| Delete audit-log rows older than N days (`RETENTION_AUDIT_LOG_DAYS`; they hold ids/action names only) | 1825 (5 years) | never |
| API usage log (`RETENTION_API_USAGE_DAYS`) | 90 | never |
| Outgoing-webhook delivery log, delivered/failed rows only (`RETENTION_WEBHOOK_DELIVERY_DAYS`) | 30 | never |
| Meta data-deletion receipts (`RETENTION_DELETION_REQUEST_DAYS`) | 365 | never |
| Customers anonymised per tenant per run (`RETENTION_BATCH_LIMIT`) | 200 | — |

"Last activity" = the newest conversation message, else the customer's own last update/creation. A recent conversation protects an old customer.

## What erasure does (same code for a data-subject request and the job)
`privacy.anonymize_customer`: deletes the customer's **files first** (PDF reports in the vault bucket: every `customer_reports.s3_url` and `customers.vault_s3_prefix`), then wipes names/phones/email/notes/tags/profile/message text, opts the customer out and locks processing. If a file cannot be deleted, the customer is **not** erased (HTTP 502 for a manual request; counted as `anonymize_failed` and retried next run by the job) so no file is ever left without a way to find it. Report rows (accounting) stay but lose their file pointer; audit rows stay. Every key is verified to start with `tenant-<tenant_id>/` before deletion. Inbound media is never copied into our storage (WhatsApp ids / Meta CDN links), so there is nothing else to delete.

## Running it
- Scheduled: Celery beat `retention-run` daily → `omniflow.retention_run` (no-op unless enabled; each tenant in its own transaction; one tenant's failure does not stop the others).
- By hand: `python -m scripts.retention_run [--tenant <uuid>]` prints a JSON report (dry-run). `--apply` really deletes and additionally requires `RETENTION_ENABLED=true`.
- Recommended first steps: run the dry-run report in production, read it, get the periods approved, then set `RETENTION_ENABLED=true` and finally `RETENTION_DRY_RUN=false`.

## Not covered
Per-tenant custom periods (the settings are global), retention of Kafka topics / Qdrant vectors / backups, and message-level deletion of old conversations of *active* customers.
