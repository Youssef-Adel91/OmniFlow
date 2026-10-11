# Morning verification (for Antigravity / a machine with Docker)

Everything below was **written without Docker, Kafka, MinIO, Qdrant, ngrok, real Meta/Moyasar/ZATCA or a real LLM**. The backend suite and the frontend checks were run on a local PostgreSQL 16 + Redis (see `night-2-progress.md`); the items here are what that environment could NOT prove. Nothing below deletes data you care about: commands that create anything use a scratch database or test tenant, and say so.

Conventions: run from the repository root unless stated; `$BACKEND = omniflow-backend`. Never put real keys in these commands — read them from your `.env`.

## 0. Check out the branches in order (each is stacked on the previous)
| # | PR | Branch | Contents |
|---|----|--------|----------|
| 1 | #22 | `feat/p10-pricing-einvoice` | pricing/plans/hard AI cap/refunds/ZATCA module (flag off) — migrations 0028–0031 |
| 2 | #23 | `feat/n2-security-audit` | security & tenant-isolation audit + Facebook-callback fix |
| 3 | #24 | `feat/n2-public-pages` | public site + Meta data-deletion callback — 0032 |
| 4 | #25 | `feat/n2-onboarding` | setup checklist + WhatsApp Embedded Signup (flag off) — 0033 |
| 5 | #26 | `feat/n2-help-content` | help articles + Arabic user guide |
| 6 | #27 | `feat/n2-developers` | read-only API keys + signed webhooks — 0034 |
| 7 | #28 | `feat/n2-social-comments` | comment replies (flag off) — 0035 |
| 8 | #29 | `feat/n2-retention` | retention & erasure job (off, dry-run) |
| 9 | #30 | `feat/n2-ops-reliability` | ops scripts, `/health/lag`, load-test files |

```bash
git fetch origin && git checkout feat/n2-ops-reliability   # the tip contains everything above
cd omniflow-backend
alembic upgrade head          # must end at 0035_social_comments_pref
alembic downgrade 0031_einvoice && alembic upgrade head     # down/up round-trip on a DEV database only
```

## 1. Backend + frontend suites with the real stack up
```bash
docker compose up -d                                  # your usual dev stack
export OMNIFLOW_TEST_DATABASE_URL=postgresql+asyncpg://<owner>:<pw>@127.0.0.1:<port>/<scratch_db>   # a SCRATCH database
export OMNIFLOW_TEST_RLS_URL=postgresql+asyncpg://<non_superuser_app_role>:<pw>@127.0.0.1:<port>/<scratch_db>
python -m unittest discover -s tests -t .             # expect: OK (skipped only for services you do not run)
cd ../omniflow-frontend && npx tsc --noEmit && npx eslint src && node --test tests/*.test.cjs && npx next build
```
Note: `tests.test_property_import_flow...test_reindex_job...` is timing-sensitive under full-suite load (passed alone every time; failed once in a full run). Re-run it alone before treating it as a regression.

## 2. Kafka worker-crash + DLQ drill (exists since the first night; needs real Postgres + Kafka + Redis)
```bash
cd omniflow-backend && python scripts/validate_kafka_outbound_recovery.py
```
Expected: (1) a killed dispatcher's message is **not** sent twice on redelivery, (2) a permanently failing message lands on `messages.outgoing.v1.dlq` and later messages still flow, (3) a failing DLQ publish stops the consumer. While it runs: `curl -s http://127.0.0.1:8000/health/lag` should show the DLQ count rise by 1 and `status: warn`.

## 3. Health / lag
```bash
curl -s http://127.0.0.1:8000/health/ready ; curl -s http://127.0.0.1:8000/health/workers ; curl -s http://127.0.0.1:8000/health/lag
```
`/health/lag` = per-consumer-group lag, dead-letter counts, and PENDING outbound messages stuck > 5 min. Stop the outbound worker, send a few test messages, wait 6 minutes → `outbound.level` becomes `warn`. (Informational only; deliberately not part of `/health/ready`.)

## 4. PostgreSQL backup → restore
Docker-free drill (reads the source, restores into a throw-away DB named `*_restore_drill_<ts>`, compares tables/row counts/Alembic revision/RLS policies, drops the scratch DB):
```bash
cd omniflow-backend && python -m scripts.pg_restore_drill --source postgresql://<user>:<pw>@127.0.0.1:<port>/<staging_db>     # exit 0 = identical
```
It ran green on the local test database (46 tables, 78 RLS policies). Docker/production path (restores into a NEW database, refuses to overwrite):
```bash
cd omniflow-deploy && ./backup.sh                                   # or use the latest file in backups/postgres/
./restore.sh postgres backups/postgres/omniflow_<ts>.dump omniflow_restored
docker exec prod-postgres psql -U "$POSTGRES_USER" omniflow_restored -c "select count(*) from tenants"
```
The guard rails of `restore.sh postgres` (missing file, bad name, live-DB name) are tested; the docker calls themselves were NOT run.

## 5. Load test (staging/local only — creates customers and AI replies)
See `omniflow-backend/loadtest/README.md`. `pip install locust`; run the headless command; then `psql … -f loadtest/reply_latency.sql`. Write the real numbers back into the README goals table. Never against production.

## 6. Start-all script + ngrok URL check
```bash
cd omniflow-backend
python -m scripts.dev_up --check-only             # ports (10048 guard), dependencies, ngrok tunnel vs PUBLIC_BASE_URL
python -m scripts.dev_up --ngrok --check-meta     # also compares the tunnel with the callback URL stored in the Meta app (read-only Graph call)
python -m scripts.dev_up                          # starts API, 3 workers, celery worker+beat, frontend
```
Try it with something already on port 8000: it must refuse and name the PID, without killing anything. The Windows branch (`netstat`, `--pool=solo`) was not run.

## 7. Items specific to earlier branches
- **Retention (#29):** `python -m scripts.retention_run` (dry-run JSON for all tenants). Review with the lawyer; only then `RETENTION_ENABLED=true`, then `RETENTION_DRY_RUN=false`. Real S3/MinIO deletion of a customer's PDF report was only tested with a fake store: generate a report for a test customer, anonymise the customer, confirm the object is gone from the `omniflow-reports-vault` bucket.
- **Developers (#27):** create a key in Settings → Developers, `curl -H "Authorization: Bearer ofk_…" http://127.0.0.1:8000/api/public/v1/customers`; add a webhook pointing at a local receiver (dev mode accepts http://localhost) and press "إرسال اختبار"; confirm Celery beat delivers `omniflow.deliver_webhooks` (every 15 s) for a real new-customer event.
- **Comment replies (#28):** needs a Meta TEST Page + app in dev mode: set `FEATURE_SOCIAL_COMMENT_REPLIES=true`, enable it in Settings → Channels, comment on a test post, try both modes. The private-reply endpoint is unverified.
- **WhatsApp Embedded Signup (#25):** needs Meta Tech Provider approval + `META_ES_CONFIG_ID`; only a simulated Meta was tested.
- **Public pages (#24):** open `/ar`, `/en`, `/ar/pricing`, legal pages, `/ar/data-deletion/status?code=…`; `GET /sitemap.xml` and `/robots.txt`. Meta's "Data Deletion Request Callback" test button in the app dashboard against your ngrok URL `…/api/v1/meta/data-deletion`.
- **ZATCA / Moyasar (#22):** sandbox only; see `docs/einvoice.md` and `docs/launch-decisions-and-manual-tests.md`.

## 8. UI (branch `feat/n2-ui-quality`)
```bash
cd omniflow-frontend && npm ci && npx next build && npx next start -p 3100 &
E2E_BASE_URL=http://localhost:3100 npx playwright test e2e/public.spec.ts            # 38 tests, expect all green (ran green in the sandbox)
E2E_CLERK_EMAIL=… E2E_CLERK_PASSWORD=… E2E_BASE_URL=http://localhost:3000 npx playwright test e2e/flows.spec.ts   # NEVER RUN: expect to fix locators
```
Because Clerk now wraps only the signed-in/auth areas, test in a real browser with your Clerk keys: sign-in → dashboard → onboarding → sign-out, and open a public page while signed in. Also check `/robots.txt` and `/sitemap.xml` return 200 (set `PUBLIC_BASE_URL`/`NEXT_PUBLIC_BASE_URL`, otherwise sitemap URLs are relative).
