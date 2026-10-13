# OmniFlow

Multi-tenant SaaS for Saudi real-estate agencies: one inbox for WhatsApp / Instagram / Messenger, an AI assistant grounded in the agency's own listings and company profile, customer management with PDPL consent, campaigns, billing in SAR, and a developer API.

| Folder | What |
|---|---|
| `omniflow-backend/` | FastAPI gateway, Kafka workers, Celery tasks, Alembic migrations, tests, ops scripts |
| `omniflow-frontend/` | Next.js 16 app (Arabic RTL + English): public site and dashboard |
| `omniflow-deploy/` | Production compose files, nginx, monitoring, backup/restore, `RUNBOOK.md` |
| `docs/` | Architecture, API, env reference, go-live checklist, per-feature notes, night-2 reports |

## Documentation map
- Start here: [`docs/architecture.md`](docs/architecture.md) → [`docs/env-reference.md`](docs/env-reference.md) → [`omniflow-deploy/RUNBOOK.md`](omniflow-deploy/RUNBOOK.md)
- Launching: [`docs/go-live-checklist.md`](docs/go-live-checklist.md), [`docs/launch-decisions-and-manual-tests.md`](docs/launch-decisions-and-manual-tests.md)
- Using the product (Arabic): [`docs/user-guide-ar.md`](docs/user-guide-ar.md)
- Integrations: [`docs/api.md`](docs/api.md), [`docs/comment-replies.md`](docs/comment-replies.md), [`docs/einvoice.md`](docs/einvoice.md)
- Data & security: [`docs/retention.md`](docs/retention.md), [`docs/security-audit.md`](docs/security-audit.md)
- Quality: [`docs/ui-quality.md`](docs/ui-quality.md), [`docs/morning-verification.md`](docs/morning-verification.md)
- Honest status of everything: [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md)

## Local development
```bash
# 1. dependencies (Postgres 16 + PgBouncer, Redis, Kafka/Redpanda, Qdrant, MinIO): your docker compose
# 2. backend
cd omniflow-backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # fill in; never commit .env
alembic upgrade head
python -m scripts.dev_up        # checks ports/dependencies/ngrok, then starts API + workers + celery + frontend
# 3. frontend alone
cd ../omniflow-frontend && npm ci && npm run dev
```
Windows shortcuts: `run-backend.bat`, `run-workers.bat`, `run-ngrok.bat`.

## Tests
```bash
cd omniflow-backend && python -m unittest discover -s tests -t .       # DB-backed tests need OMNIFLOW_TEST_DATABASE_URL / OMNIFLOW_TEST_RLS_URL (scratch DB)
cd omniflow-frontend && npx tsc --noEmit && npx eslint src && node --test tests/*.test.cjs && npx next build
cd omniflow-frontend && E2E_BASE_URL=http://localhost:3100 npx playwright test e2e/public.spec.ts
```
CI (`.github/workflows`) runs on a self-hosted runner for pull requests into `main`.
