# Night 2 report

Nothing was merged. Every PR is a **draft**, stacked on the previous one (each PR's base is the previous branch; migrations are linear 0028 → 0035). CI runs only on PRs into `main` on the self-hosted runner, which was offline: PR #22's `backend`/`frontend` jobs sat **queued** and the stacked PRs got no CI. Vercel previews built for the PRs; one preview of #24 failed once (commit `ece1c15`) and rebuilt green on the next commit — the cause was not investigated.

Local verification environment: PostgreSQL 16 (owner + non-superuser role for RLS), Redis, headless Chromium. **No Docker, Kafka, MinIO, Qdrant, ngrok, real Meta/Clerk/Moyasar/ZATCA/LLM.** Backend suite at the tip: **484 tests, OK** (33 skipped: services not available), frontend: tsc / eslint (0 errors) / 71 node tests / `next build` OK, Playwright 38 green.

## Per item
| # | PR | Done | Actually tested (how) | NOT tested / why | Risks |
|---|----|------|-----------------------|------------------|-------|
| 0 | #22 | Plans/prices/limits, hard AI cap (atomic Redis Lua + FIFO credit ledger), top-ups, per-reply AI cost, first-payment refund + credit notes, trial identity (CR/phone) duplicate detection, ZATCA module (flag off), team-page tenant filter, dev cleanup script (dry-run default) | Unit + real-Postgres/RLS + real-Redis tests; migrations up/down/up; API + UI checks | Moyasar only via fake provider; ZATCA never contacted/validated (self-signed dev cert); real-model cost; phone OTP not built; extra WhatsApp number add-on unavailable (one number per tenant) | **PRICING_STUDY.md was not available** – numbers came from the P10 brief. Message cap now on by default (exempt/grandfathered tenants unaffected) |
| 1 | #23 | Auth-on-every-route test, per-table and per-API-route tenant-isolation tests, pip-audit/npm audit, gitleaks CI step, `docs/security-audit.md` | The tests themselves (real RLS role) | gitleaks only works if installed on the runner; the CSP is still report-only | **Found:** Facebook OAuth callback returned 401 on main (write-gate resolved the user for a browser redirect) – fixed here, **needs a hotfix if #23 waits**. Dead `simulate-mock` endpoint removed. python-jose/ecdsa advisories have no fix → migrate to PyJWT |
| 2 | #24 | Honest landing (no invented stats), pricing from the catalogue (+15 % VAT, Meta fees paid to Meta), privacy/terms/refund/deletion pages ar+en (**placeholder legal text**), Meta data-deletion callback + status page, SEO | 33 backend tests incl. signed_request vectors signed locally; frontend build | Real Meta callback test; browser rendering at the time | Legal text is a draft. Original sitemap/robots were broken (fixed in #31) |
| 3 | #25 | Setup checklist (derived from real data), WhatsApp Embedded Signup behind OFF flag (code→token exchange, phone∈WABA check, register + subscribe, token encrypted, duplicate-number guard), `reveal_secret` | Backend tests with Meta simulated (`httpx.MockTransport`); migration 0033 | Real Meta/Tech Provider flow | Never run against Meta |
| 4 | #26 | 11 new help-centre articles + Arabic user guide; removed an unverified "30 days retention" claim | Content guard tests | Wording vs live UI | Refund text is draft |
| 5 | #27 | Read-only API keys (hashed, shown once, rate limit, usage log, revoke), signed outgoing webhooks (outbox, retries, SSRF guard, delivery log), UI, `docs/api.md` | 22 tests incl. real Postgres/RLS (found+fixed a logging bug in `emit`) | Real receiving endpoint, Celery beat | At-least-once delivery documented |
| 6 | #28 | Comment replies behind OFF flag: dedupe, rate limit, own-comment/thread-reply guard, public reply or private DM per tenant, post link in inbox | 18 new + 4 adapted tests with locally signed payloads | Live Meta (private-reply endpoint, permissions, 7-day limit) | **Behaviour change:** comment replies were previously always on |
| 7 | #29 | Retention/erasure job (OFF + dry-run), erasure now deletes vault files first, `scripts.retention_run`, `docs/retention.md` | 12 tests on real Postgres with a fake object store | Real S3/MinIO, Celery schedule | Periods are placeholders. Manual erasure now returns 502 if storage is down (by design: no orphaned files) |
| 8 | #30 | PG restore drill (**ran for real**), `restore.sh postgres`, `/health/lag`, `dev_up.py` (port 10048 guard, ngrok vs PUBLIC_BASE_URL / Meta stored URL), Locust files, runbook | 21 tests; drill executed (46 tables, 78 RLS policies identical) | Load test never executed (no results exist), Kafka lag/DLQ drill, docker restore path, Windows branch | Load goals are targets, not measurements |
| 9 | #31 | Error/404/loading pages, contrast fix, Clerk off public pages, lazy-loaded panels, Playwright+axe | **38 Playwright tests ran green in real Chromium**; bundle numbers from the build | Signed-in flows (`e2e/flows.spec.ts` never run), signed-in a11y/mobile | **Clerk provider placement changed – verify login in a browser**. Found: public headings were 1.18:1 contrast (invisible), robots/sitemap 404 |
| 10 | #32 | README, architecture (mermaid), env reference, go-live checklist, runbook additions, `.env.prod.example` updated, this report, morning verification | Mermaid not rendered here; env reference generated from `Settings` | — | — |

## قرارات اتخذتها (decisions I made on your behalf)
1. Comment replies, Embedded Signup, retention, ZATCA: all **OFF** by default; retention also **dry-run** by default.
2. Retention periods are conservative placeholders (3 y inactive customers, 5 y audit logs, 90/30/365 d for logs) – no legal numbers invented.
3. Legal pages = clearly labelled placeholder text "مسودة بانتظار مراجعة محامٍ"; removed the unverifiable "data kept 30 days" claim.
4. When a customer's files can't be deleted, the erasure **stops** (502 / retried next run) rather than leave orphan files.
5. Redis down ⇒ comment dedupe fails **closed** (no reply), API-key rate-limit fails **open**.
6. Webhooks: https only, public IPs only (re-checked per delivery), no redirects, 5 endpoints / 10 keys per workspace.
7. Public API is read-only, full-read keys (no scopes) in v1; erased/archived customers never returned.
8. `.env.prod.example` now enables `BILLING_ENFORCE_MESSAGE_LIMIT=true` (was `false`) to match the code default.
9. Clerk middleware/provider removed from public pages (performance + resilience); unknown URLs render the localized 404 with status 404.
10. Pricing numbers kept exactly as given in the P10 brief; PRICING_STUDY.md could not be consulted.
11. Fixed the robots/sitemap bug in #31 (not in #24) so it ships with the Playwright test that proves it.

## أسئلة لي (questions for you)
1. Please supply **PRICING_STUDY.md** (or confirm the P10 numbers are final).
2. Legal: who reviews the privacy/terms/refund text, and what retention periods are acceptable under PDPL for you?
3. Should the Facebook-callback 401 fix be cherry-picked to `main` now, ahead of the stack?
4. Do you want per-tenant retention overrides, or are global settings enough?
5. Phone OTP for new companies (trial abuse) – build it, or is CR/phone duplicate detection enough?
6. Do you want API-key scopes (read customers / conversations / properties separately) before external developers use it?
7. Is a second WhatsApp number per tenant a requirement (add-on is shown as unavailable)?

## Suggested merge order and expected conflicts
Merge **in this order**, retargeting each PR's base to `main` after the previous merge (or use a merge-commit per PR): **#22 → #23 → #24 → #25 → #26 → #27 → #28 → #29 → #30 → #31 → #32**. Because they are stacked, merging in order is conflict-free by construction. Out of order you will conflict on: `docs/night-2-progress.md` (every PR edits one row), `omniflow-backend/.env.example` (appended at the end by items 3, 6, 7), `src/shared/core/config.py` and `src/shared/db/models.py` (appended blocks), `src/gateway/main.py` (router includes), `src/celery_app/{app,tasks}.py`, `omniflow-frontend/src/lib/public/copy.ts`. If you only want some items: #23 (security) and #31 (UI/SEO fixes) are the most valuable standalone, but #31 depends on #24 (public pages) and #25–#27 touch shared settings files.
After merging: `alembic upgrade head` (ends `0035_social_comments_pref`), then follow `docs/morning-verification.md`.

## Findings worth your attention first
1. Facebook OAuth callback 401 on `main` (fixed in #23).
2. Public headings invisible (contrast 1.18:1) and robots/sitemap 404 (fixed in #31).
3. A silent bug caught by a test before merge: structlog `event=` keyword clash in the webhook emitter's failure path (fixed in #27).
4. `pip-audit`: python-jose/ecdsa advisories without a fix → plan PyJWT migration.
5. Deploy template had `BILLING_ENFORCE_MESSAGE_LIMIT=false` (fixed in the docs PR).
