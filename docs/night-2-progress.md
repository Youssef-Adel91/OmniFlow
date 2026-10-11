# Night-2 progress (checkpoint file — any later session resumes from here)

Branch chain (each branch is built on the previous one; migrations are linear 0028 → …). All PRs stay DRAFT; nothing is merged.

| # | Item | Branch | PR | Status |
|---|------|--------|----|--------|
| 0a | P10-A plans/limits enforcement | `feat/p10-pricing-einvoice` | (see below) | done (backend + billing UI) |
| 0b | P10-B/C top-ups, hard cap, AI cost | same | | done (backend + billing UI; platform margin UI = API only) |
| 0c | P10-D/E/F trial identity, refunds, staff emails | same | | done (backend); refund policy page comes with item 2 |
| 0d | P10-G e-invoice module | same | | done (flag off; unverified by ZATCA) |
| 0e | Team page tenant filter + dev cleanup script | same | | done |
| 1 | Security & tenant-isolation audit | `feat/n2-security-audit` (on P10) | #23 | done — found + fixed Facebook-callback 401 regression; see docs/security-audit.md |
| 2 | Public pages (Meta / launch) + Meta data-deletion callback | `feat/n2-public-pages` (on item 1) | #24 | done — tsc/eslint/64 node tests/next build OK; DB-backed deletion tests ran earlier (33 OK) but were skipped in the last full run (scratch DB offline); legal text = placeholder pending lawyer |
| 3 | Onboarding from zero (checklist + Embedded Signup, flag off) | `feat/n2-onboarding` (on item 2) | #25 | done — migration 0033 up/down/up verified on scratch PG; backend suite OK with DB (1 timing flake passes alone); tsc/eslint/68 node tests/next build OK; Embedded Signup tested only against a simulated Meta (httpx MockTransport), never real Meta |
| 4 | Help-centre content + user guide | `feat/n2-help-content` (on item 3) | #26 | done — 11 new KB articles (support_kb.py, shown before a ticket is opened) + docs/user-guide-ar.md; guard tests in test_help_content.py; also removed an unverified "30 days retention" claim from the public FAQ |
| 5 | Developers (API keys + outgoing webhooks) | `feat/n2-developers` (on item 4) | #27 | done — migration 0034; 22 tests (incl. real-Postgres/RLS: key hashed+shown once, tenant isolation, revoke, usage log, 429, outbox→delivery→retry, SSRF guard); receivers simulated, no real HTTP endpoint called; docs/api.md |
| 6 | Instagram/Facebook comment replies (flag off) | `feat/n2-social-comments` (on item 5) | #28 | done — migration 0035; 18 new tests + 4 updated; BEHAVIOUR CHANGE: comment replies were always on before, now off unless flag + tenant switch; private-DM mode unverified against live Meta; docs/comment-replies.md |
| 7 | Retention & erasure job | `feat/n2-retention` (on item 6) | (pending) | done — no migration; job OFF + dry-run by default; 12 tests (real Postgres/RLS, fake object store); anonymize now deletes files first; docs/retention.md; periods are placeholders pending legal review |
| 8 | Reliability & ops scripts | | | pending |
| 9 | UI quality pass | | | pending |
| 10 | Documentation | | | pending |

Local verification environment used overnight: PostgreSQL 16 (owner + non-superuser `app_user`), Redis with the test password, no Docker.
