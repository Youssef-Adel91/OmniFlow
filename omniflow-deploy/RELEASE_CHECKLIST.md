# Production release checklist

The current working tree is a verified local development milestone, not an approved production release. See `../IMPLEMENTATION_STATUS.md` for evidence and unfinished product paths.

## Before staging

- Use a reviewed commit and retain its image tags/digests for rollback. Python dependencies and infrastructure images still need reproducible version pinning and vulnerability review.
- Complete `.env.prod` from `.env.prod.example` on the target host. Keep it out of Git. Production credentials must be distinct from local test credentials.
- Set `APP_POSTGRES_USER` to a dedicated application role, distinct from the migration owner in `POSTGRES_USER`. Do not grant SUPERUSER or BYPASSRLS to the application role. Migration and role provisioning must run before the application.
- Set the actual domain in Nginx, CORS, API/frontend URLs and Clerk configuration. Configure provider webhook URLs/signatures. Build the frontend with the correct public Clerk key and `NEXT_PUBLIC_API_URL`; these are build-time values.
- Keep unfinished paid reports and unconfigured voice integrations disabled. Their production paths now reject placeholders, but disabling them does not complete the associated product journeys.
- Complete public storage addressing for signed media/report links and logos, and verify private-bucket access. The current MinIO initialization/backup coverage is not yet a complete storage deployment.
- Implement and verify Sentry/OTel export. Configuration flags alone do not send telemetry. Define alert routing, queue lag/DLQ monitoring and an incident owner.

## Staging checks

Run from the deployment directory on a Linux host:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile maintenance config --quiet
./deploy.sh
```

The deployment script builds the migration image, starts the data plane, runs migrations with owner credentials, provisions application grants, then starts compute services. It does not roll back automatically.

- `/health` checks the API process; `/health/ready` checks database, Redis and Kafka. Production readiness also rejects an RLS-bypassing database role. Neither endpoint establishes worker delivery, storage access, LLM availability or product acceptance.
- Verify Clerk sign-in, tenant isolation, inbox history/SSE/reconnect, takeover, durable human replies, return to AI and settings against the deployed stack.
- Exercise the real worker pipeline with explicitly designated test recipients. Validate retry/DLQ recovery and provider delivery callbacks; local mocked sends are not proof of live delivery.
- Test RAG using real embeddings and tenant-owned documents. Test storage downloads through the public HTTPS endpoint, including expiry and denied cross-tenant access.
- Paid reports/payment and other incomplete SRS paths require implementation and acceptance before being sold.
- Run backup and restore drills. PostgreSQL restoration was tested locally in a disposable container; off-host copies and Qdrant/MinIO restoration remain unverified. Default MinIO coverage includes media, reports, temporary media, assets and knowledge buckets, with a separate timestamp/bucket destination. Set `MINIO_SOURCE_BUCKETS` if custom storage buckets are used. These mirrors copy current objects; they do not preserve all source object versions or replace off-host backups.
- Issue a valid public TLS certificate and verify HTTPS without `-k`. The deployment script permits self-signed bootstrap certificates for ACME setup; its readiness message is not TLS approval.
- Run representative concurrency/load tests and verify resource limits. The current backend image includes the full ML/document stack and is large.

## Rollback

Take a fresh backup, identify the exact previous image/source revision, and review schema compatibility before restarting old code. Do not blindly downgrade or restore over current data: both can discard changes made after the backup.

Use the `migrate` maintenance service for reviewed Alembic downgrade operations, because the application role intentionally lacks schema ownership. Retain release images; deployment no longer globally prunes unrelated or previous images.

## Local verification commands

```powershell
# From omniflow-backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -q
.\.venv\Scripts\python.exe scripts/validate_local_migrations.py --via-pgbouncer --inbox

# From omniflow-frontend
node --test --test-isolation=none tests/*.test.cjs
npm run lint
npm run build
```

`tests/__init__.py` isolates test settings from real credentials; keep `-t .` in backend discovery. The database validator refuses remote hosts and rolls back its temporary schema/data. The Linux-only `tests/backup_failure.sh` verifies backup failure handling with a fake Docker command and does not contact services.
