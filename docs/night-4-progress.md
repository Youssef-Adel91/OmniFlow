# Night 4 — progress (verification + gaps + customer simulation)

Status is **actual** (what was run), not intended. Updated as work proceeds.

## 0) Baseline (before tonight's PRs)
| Check | Result |
|---|---|
| `main` | `c038d42` (PRs #33–#37 merged with explicit authorization) |
| Last migration | `0036_sales_pipeline`, one alembic head |
| Full backend suite, PostgreSQL + RLS + Redis up | 597 tests OK (2 skipped) after PR #43 (before it: 24 false failures from the shared rate limiter) |

## 1) PRs
| PR | Item(s) | Content | State |
|---|---|---|---|
| #38 | 15–17, 19, 20 | tests that execute the sales hook / follow-ups / reminders / `/deals` API + 2 fixes | **merged** `a4c2c6d` (backend+frontend green) |
| #43 | test infra | rate limiter off by default in tests | **merged** `a1cf2d6` |
| #41 | 21 | `scripts/eval_bot.py` (offline + live) | **merged** `2e75d0a` |
| #42 | 25, 26 | sandbox payment provider, plan labels, demo-tenant seed | **merged** `e5a2624` |
| #40 | 11 | dead-token degradation + self-healing alarm | draft → ready; updated from main (`ff1b06c`); **CI queued, runner down since ~02:55 UTC** |
| #39 | 4, 18 | Messenger as its own channel | draft; to be updated from main after #40 |
| #45 | simulation | customer simulation + 7 defects found/fixed | draft; contains #39/#40-independent changes; update after #39 |
| #46 | 5, 6 | `ChannelAdapter` + Telegram adapter (not wired) | draft (scope stated in the PR) |
| #44 | docs | this report set | draft; last |

## 2) Merge rules in force
Merge only when `backend` and `frontend` are both green on the current head; one by one; update the next branch from `main` and wait for CI; stop on any unexplained failure, big conflict, or a migration that changes data. No deployment, no Meta/Clerk/Vercel changes, no force-push on main.

## 3) Log
- Runner is a single self-hosted Windows runner; runs queue serially. **It stopped picking up jobs after the #42 run (~02:55 UTC); #40's run has been queued since 03:16.** Re-checked repeatedly; CI is never skipped. If it is still down in the morning: start the runner service, then the queued runs proceed by themselves.
- Local full suite (PostgreSQL + RLS + Redis) on the simulation branch: 668 tests OK before the last two additions; simulation (`python -m scripts.run_simulation`): all roles pass.
- Defects found by the simulation and fixed (PR #45): no output guard on model answers; Redis outage disabled all safety rules; webhook redelivery answered twice (WhatsApp + IG/Messenger); no rule for investment-guarantee/advice questions; public comment replies could promise a discount; two customers booking the same slot at once both got it (per-tenant advisory lock).
