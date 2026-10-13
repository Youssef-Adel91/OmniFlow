# Night 4 — progress (verification + gaps + customer simulation)

Status is **actual** (what was run), not intended. Updated as work proceeds.

## 0) Baseline (before tonight's PRs)
| Check | Result |
|---|---|
| `main` | `c038d42` (PRs #33–#37 merged with explicit authorization) |
| Last migration | `0036_sales_pipeline`, one alembic head |
| Full backend suite, PostgreSQL + RLS + Redis up | 597 tests OK (2 skipped) after PR #43 (before it: 24 false failures from the shared rate limiter) |

## 1) PRs opened tonight (all drafts until CI is green on the current head)
| PR | Item(s) | Content | State |
|---|---|---|---|
| #43 | test infra | rate limiter off by default in tests | draft, CI queued |
| #38 | 15, 16, 17, 19, 20 | tests that execute the sales hook / follow-ups / reminders / `/deals` API; fixes: reminder template, «لا أدري» | draft, CI running |
| #39 | 4, 18 | Facebook Messenger as its own channel (was stored as Instagram) | draft, backend ✅ |
| #40 | 11 | dead-token degradation + self-healing alarm | draft, CI queued |
| #41 | 21 | `scripts/eval_bot.py` (offline + live) | draft, CI queued |
| #42 | 25, 26 | sandbox payment provider, plan labels, demo-tenant seed | draft, CI queued |

## 2) Merge rules in force
Merge only when `backend` and `frontend` are both green on the current head; one by one; update the next branch from `main` and wait for CI; stop on any unexplained failure, big conflict, or a migration that changes data. No deployment, no Meta/Clerk/Vercel changes, no force-push on main.

## 3) Log
- Runner is a single self-hosted Windows runner; runs queue serially (≈ 15–25 min each). When it is down: re-check every 15 minutes, never skip CI.
