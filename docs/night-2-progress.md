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
| 3 | Onboarding from zero | | | pending |
| 4 | Help-centre content + user guide | | | pending |
| 5 | Developers (API keys + outgoing webhooks) | | | pending |
| 6 | Instagram/Facebook comment replies (flag off) | | | pending |
| 7 | Retention & erasure job | | | pending |
| 8 | Reliability & ops scripts | | | pending |
| 9 | UI quality pass | | | pending |
| 10 | Documentation | | | pending |

Local verification environment used overnight: PostgreSQL 16 (owner + non-superuser `app_user`), Redis with the test password, no Docker.
