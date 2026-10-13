# Verification report — Social & Closing checklist (28 items)

Date: 2026-10-13. Legend: ✅ proven by a test/run listed here · 🟡 built but partly unproven or fixed in a PR still waiting for merge · 🔴 missing.
"Mocks" = Meta/LLM/Moyasar replaced by fakes; **nothing below was exercised against real Meta, Moyasar, ZATCA or a real model.**

| # | Item | Status | Evidence / gap | PR |
|---|---|---|---|---|
| 1 | WhatsApp templates + 24 h window | ✅ | `tests/test_whatsapp_window.py`; dispatcher check before send; `send_template_message` | main |
| 2 | Instagram DM (per-tenant token, inbound image/audio) | 🟡 | DM sent with decrypted tenant token tested; inbound attachment mapping exists but no test | open |
| 3 | IG comments (public, Private Reply, ignore, hide, dedupe, loop guard) | ✅ | `test_comment_replies.py`, `test_comments_v2.py`; dedupe `comment:seen:{id}` 7 d; own-id loop guard. No client-side 7-day age check (Meta rejects) | main |
| 4 | Facebook Page (feed comments, Messenger DM, Private Reply) | 🟡→✅ | shared router; Messenger had been stored as `instagram` | #39 |
| 5 | Telegram | 🔴 | not implemented (only a label in `channel_health`) | — |
| 6 | `ChannelAdapter` interface + `docs/channels.md` | 🔴 | no common interface; docs exist per channel | — |
| 7 | Per-tenant comment settings (DB + API + UI) | ✅ | `tenant_preferences.social_comments`, `PUT /settings/preferences/social_comments`, CommentRepliesCard | main |
| 8 | No prices in public replies | ✅ | `violates_public_policy` tests | main |
| 9 | Channels page (status, missing perms, real test message, last 20 comments) | 🟡 | diagnostics + WhatsApp real test send + comment list ✅; no IG/FB test send (Meta restricts) | main |
| 10 | `scripts/meta_check_subscriptions.py` dry-run | ✅ | without token exits 2 with a clear message; tests pass; no real Meta call made | main |
| 11 | Error-190 handling | 🟡→✅ | detection + daily notify + banner existed; degradation and self-heal added | #40 |
| 12 | Embedded Signup + FB Login | 🟡 | OAuth/ES flows tested with mocks, flag off; Meta error text not localized | main |
| 13 | Qualification + score | ✅ | `customers.qualification`, `deals.lead_label` (tests in #34/#38) | main |
| 14 | RAG listing match with images/links, no invention | 🟡 | SQL matcher over VERIFIED_ACTIVE rows (not Qdrant); price only from inventory; no photo fields in `PropertyListing`, map link only | — |
| 15 | Viewing booking (hours, staff, confirm, 2 h reminder, reschedule, UI) | 🟡 | hook/booking/reminders were untested → #38 tests; no calendar UI page | #38 |
| 16 | Handoff | 🟡→✅ | summary + notification tested | #38 |
| 17 | Follow-ups | 🟡→✅ | tested; template-variable bug fixed | #38 |
| 18 | Pipeline / Kanban / lost reason / per-channel report | 🟡 | Kanban UI + report API ✅; no report UI; FB/IG conflation fixed | #39 |
| 19 | Negotiation guard | 🟡→✅ | fixed reply + handoff + stored offer tested | #38 |
| 20 | Opt-out across channels | 🟡→✅ | `opt_out_at` set in hook, follow-ups stopped (tested); broadcasts exclude opt-out | #38 |
| 21 | Golden tests ≥ 40 | 🟡→✅ | 47 scenarios / 52 lines (Saudi 30, Egyptian 6, MSA 4, English 7), rule-based, run in CI as unit tests; `eval_bot --live` never run (no model) | #41 |
| 22 | RLS + leak test | ✅ | `tests/test_tenant_isolation.py` scans every table with `tenant_id` | main |
| 23 | Load test, 50 concurrent, real result | 🔴 | `loadtest/locustfile.py` exists; no result ever recorded | — |
| 24 | Sentry + metrics + dashboard | 🟡 | Sentry SDK wired; `/health/lag`; no Prometheus metrics/dashboard | — |
| 25 | Billing names + upgrade / top-up sandbox | 🟡→✅ | real plan labels; `PAYMENT_PROVIDER=sandbox` | #42 |
| 26 | Non-exempt trial seed + 300 replies / 14 days | 🟡→✅ | `scripts/seed_demo_tenant.py`; caps in `billing/catalog.py` | #42 |
| 27 | Local Dev User cleanup | ✅ | `scripts/dev_cleanup_test_data.py` + `tests/test_dev_cleanup.py` (dev only) | main |
| 28 | Docs exist | 🟡 | all present except `night-3-report.md` (added in this PR) | docs/night-4 |

Statuses with an arrow become final once the listed PR is merged.

## Manual actions (short)
1. Meta: subscribe the app to `page` (feed, messages) and `instagram` (comments, messages); run `scripts/meta_check_subscriptions.py` with a real token.
2. Run `python -m scripts.eval_bot --live` with a real model key.
3. Moyasar sandbox keys → real checkout/refund test.
