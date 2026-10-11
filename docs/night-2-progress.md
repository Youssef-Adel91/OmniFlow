# Night-2 progress (checkpoint file — any later session resumes from here)

Branch chain (each branch is built on the previous one; migrations are linear 0028 → …). All PRs stay DRAFT; nothing is merged.

| # | Item | Branch | PR | Status |
|---|------|--------|----|--------|
| 0a | P10-A plans/limits enforcement | `feat/p10-pricing-einvoice` | (see below) | done (backend), UI pending |
| 0b | P10-B/C top-ups, hard cap, AI cost | same | | done (backend), UI pending |
| 0c | P10-D/E/F trial identity, refunds, staff emails | same | | done (backend), UI + policy page pending |
| 0d | P10-G e-invoice module | same | | in progress |
| 0e | Team page tenant filter + dev cleanup script | same | | done |
| 1 | Security & tenant-isolation audit | | | pending |
| 2 | Public pages (Meta / launch) | | | pending |
| 3 | Onboarding from zero | | | pending |
| 4 | Help-centre content + user guide | | | pending |
| 5 | Developers (API keys + outgoing webhooks) | | | pending |
| 6 | Instagram/Facebook comment replies (flag off) | | | pending |
| 7 | Retention & erasure job | | | pending |
| 8 | Reliability & ops scripts | | | pending |
| 9 | UI quality pass | | | pending |
| 10 | Documentation | | | pending |

Local verification environment used overnight: PostgreSQL 16 (owner + non-superuser `app_user`), Redis with the test password, no Docker.
