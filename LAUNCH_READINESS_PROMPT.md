# OmniFlow — Launch Readiness Prompt for Claude Code

You are working in this repo: `omniflow-backend/`, `omniflow-frontend/`, `omniflow-deploy/` at the project root. Before touching anything, read these in full, in this order:

1. `IMPLEMENTATION_STATUS.md` (root) — the authoritative, current source of truth for what's actually done vs. not. Trust this over your own assumptions or over older docs.
2. `omniflow-deploy/RELEASE_CHECKLIST.md`
3. `تقرير_مراجعة_OmniFlow_مقابل_SRS.docx` and `تقرير_الإنجاز_إصلاحات_OmniFlow.docx` (root, Arabic) — a prior SRS-vs-code gap analysis (~6 weeks old, some items it flags as broken have since been fixed per IMPLEMENTATION_STATUS.md — use it for context, not as current truth).
4. `SRSTech.HTML` and `SRS_NONTECH.HTML` (root) — the full spec. Read the sections relevant to whatever module you're working on before changing it.

**Ground rule: do not mark anything "done" or "complete" in status docs, commit messages, or to the user unless you have end-to-end evidence (a real test against a real dependency, not a mock). IMPLEMENTATION_STATUS.md explicitly warns that passing unit tests / a clean build were previously overstated as production-readiness. Keep that document updated honestly as you go — it's the project's memory across sessions.**

## Immediate, do-first (security/urgent — do this before anything else)

1. **Rotate the exposed Gemini API key and Instagram access token.** They were removed from current code but still exist in Git history. Rotate both credentials at the provider immediately, purge them from Git history (`git filter-repo` or BFG), and force-push only after confirming with the user (this rewrites history — coordinate timing). Add a pre-commit secret scanner (e.g. `gitleaks` or `detect-secrets`) so this can't happen again.
2. Confirm the legacy local-JWT auth paths (flagged: `/tenants/onboarding` still issuing unused local JWTs) are fully removed or dead-coded now that Clerk is the auth system. Remove `DEMO_CREDENTIALS` from the onboarding page and confirm the `TestBotSimulator`/`localhost:8000` test artifact is actually gone from anything that ships to production.

## P0 — revenue & core-loop blockers (nothing can launch without these)

3. **Payments**: implement real Moyasar/Tap integration end-to-end — checkout, webhook signature verification, a real `transactions` table, subscription state transitions. Test with each provider's sandbox/test mode against a running webhook endpoint, not mocks.
4. **Real PDF reports**: replace the placeholder (currently plain UTF-8 text with a `.pdf` extension) with actual PDF generation sourced from real report data, gated behind the payment above.
5. **WhatsApp channel — real end-to-end delivery**: the adapter (HMAC verify, SLA) is reportedly solid, but no real customer message has ever been sent/received. Get a real WhatsApp Business test number wired up and prove a full send + receive + AI-reply round trip. Decide and document the launch status of TikTok/X/Snapchat (currently empty stubs) — either finish or explicitly mark out-of-scope for v1 in both code (feature-flag off) and the SRS-scope doc from item 22 below.
6. Validate the Kafka pending-message publisher against the real DB + Kafka (not just unit/validator tests) and confirm retry/DLQ behavior under an actual worker crash — IMPLEMENTATION_STATUS.md flags this as unverified.

## P1 — feature completeness the SRS promises as core UX

7. VCard sending: implement real delivery (current state-change does not prove delivery) and the reminders tied to it.
8. Campaigns/broadcasts: finish scheduling + delivery worker + delivery tracking; create the missing `vip_subscribers`/`broadcast_deliveries` tables (needs a quick product decision with the user on VIP tiering first — ask, don't assume). Add a pre-send check against Meta's WhatsApp template-approval rules so free-text broadcast sends can't risk a number ban.
9. RAG: wire real embeddings + retrieval (worker currently skips vector retrieval without a real embedding key) and verify per-tenant isolation explicitly, especially on the Instagram path.
10. Real-estate recommendations: replace the now-emptied inbox recommendation panel with real data from the recommendation engine.
11. Conversation quick-actions (reports / appointments / notes): currently disabled with a "not available" message — implement them.
12. Long chat history: implement pagination for long conversations, and a delivery-status UI (sent/delivered/read) via SSE.
13. Concurrency audit: conversation counters, takeover/ownership races, user-provisioning races, SSE-before-commit ordering — IMPLEMENTATION_STATUS.md lists this as open.
14. Voice/image: either finish both pipelines properly or explicitly, visibly disable them for v1 (current state: voice fails closed when unconfigured, image unclear — make both an explicit, tested decision, not a silent gap).
15. REGA license/ad-number verification: connect to the real REGA API rather than leaving it unverified — business review flags this as a legal risk for the client's brokers, higher priority than UI polish.

## P2 — production/ops readiness

16. File/storage: finish public storage addressing so file/image/download links point at the real domain (not `example.com`), confirm signed-URL coverage is complete across all buckets, and implement a file-retention/temp-cleanup policy.
17. Observability: actually wire Sentry/OpenTelemetry to a running collector (settings alone aren't instrumentation) and decide + deploy the monitoring stack (`docker-compose.monitoring.yml` is currently local-only).
18. Backups: get off-host backup storage configured (currently local-only) and run a real restore test for MinIO and Qdrant, not just Postgres.
19. Dependencies: pin backend dependency versions (currently unlocked), run a vulnerability scan (bandit/safety in CI, not just as optional dev deps), and shrink the backend image (~6.89 GB currently — check for unnecessary layers/dev deps in prod image).
20. CI/CD: add an automated test + build pipeline that runs on every push/PR (none found — RELEASE_CHECKLIST currently documents manual local commands only).
21. Production environment: get the real domain, a proper public TLS cert (Certbot, replace self-signed), and production provider keys from the user/client. Confirm `/docs` and `/openapi.json` are gated behind real IP allowlist/Basic Auth credentials once available.
22. Do a **refreshed** SRS-vs-code line-item review (reuse the structure of `تقرير_مراجعة_OmniFlow_مقابل_SRS.docx` but re-verify against current code) and get explicit sign-off from the user on what's in vs. explicitly deferred for v1 — the SRS describes a much larger system (Kubernetes/multi-region/full observability/ClickHouse analytics/CalDAV scheduling) than what's being launched; that's fine, but it needs to be a documented decision, not a silent gap.
23. Once 3–21 are done: staging environment test of every user journey and every role/permission; load/stress test and a service-outage/recovery drill; a documented release procedure with a rollback that has actually been executed once as a rehearsal (Alembic downgrade + image rollback), not just written down.
24. Privacy policy, terms of use, and a support channel need to exist before onboarding real clients — not found anywhere in the repo; draft and get user sign-off.

## Working style

- Work in the priority order above; don't jump to P2 polish while P0 revenue blockers are open.
- After each numbered item (or logical group), update `IMPLEMENTATION_STATUS.md` with what you actually verified and how (what you tested it against), and note anything still open or needing a decision from the user.
- When something needs a product/business decision (VIP tiers, v1 scope cuts, provider choice, domain), stop and ask rather than guessing.
- Prefer small, reviewable commits per item over one giant commit.
