# Customer simulation report

Run: `python -m scripts.run_simulation` (needs `OMNIFLOW_TEST_DATABASE_URL` + `OMNIFLOW_TEST_RLS_URL`; exits 3 instead of passing when they are missing).
What it IS: scripted roles driving the real sales hook, booking, follow-ups, hand-off, output guard, comment policy, deals API, RLS on PostgreSQL. What it is NOT: real Meta, a real model (the scripted model is naive), real browsers/UI, real Redis/Kafka outages beyond the cases listed.

## Roles, scenarios, results (round 1 → fixes → round 2)
| Role | Scenarios exercised | Result | Defects found → fix |
|---|---|---|---|
| Buyer (Saudi / Egyptian / English) | real listing quoted with its inventory price; full booking dialogue → appointment + deal `viewing` + staff notification; cancel → rebook (one active appointment); budget changed mid-chat (latest wins); fact from message 1 still known after 25 messages; discount ask → hand-off, assistant silent afterwards; staff takes over → assistant silent; same message twice → one deal / one hot alert; disappears → follow-ups armed; opt-out → none pending and none re-armed | pass | none new in the sales rules; (earlier, #38) reminder template / «لا أدري» |
| Investor | «عائد مضمون», price predictions, legal/financial advice (AR+EN) | fixed refusal, no promise, stays browsable | **no rule existed** → `advice` intent |
| Angry customer / attacker | injection (AR/EN, hidden inside a long message), system-prompt request, other customers' data, owner's number, discount pressure, complaint, hostile model | rule replies, no lead created, hand-off + notification; hostile model stopped | **no output guard** → `output_guard` in the invoker |
| Public commenter | question, insult, spam link, emoji only, duplicate delivery, own reply, thread reply, 10+/minute cap, price/promise filter | pass | public reply could promise a discount → filter |
| Salesperson | hand-off summary, mandatory lost reason, per-channel report (WhatsApp vs Instagram) | pass | — |
| Lower-privilege staff | auditor read-only; agent cannot invite/checkout | 401/403 not 200 | — |
| Tenant B | API board empty; raw SQL under RLS on 7 tables returns 0 rows | pass | — |
| Infrastructure ("skeptic") | Redis down during chat; duplicate inbound row; webhook redelivered (WhatsApp, IG/Messenger) | — | **Redis down disabled all safety rules** → best-effort state; **redelivery answered twice** → routers drop duplicates |

Round 2 (after the fixes) found nothing new in the cases above; round 3 added only inbound-media mapping tests (item 2). "Two consecutive empty rounds" was reached only for the scenario list in this table — the list itself is limited.

## NOT covered (honest)
- Business-owner journey in the UI (signup, connect channels, add listings, invite staff, quota exhaustion, buy pack, upgrade, invoice/refund, disconnect, delete data): API pieces have unit/DB tests elsewhere and a sandbox payment provider exists (#42), but no end-to-end browser run was done tonight.
- UI: Arabic RTL / English, empty/loading/error states, mobile/desktop, console errors, missing translations, dead buttons — only the existing Playwright/axe suite; not re-run by hand.
- Voice notes / images from customers: the pipeline answers with an honest "can't read this yet" message (flags off); nothing is understood from the media.
- Real-model quality, latency, token cost, tier routing; prices written in words («مليون ريال») slip past the output guard.
- Webhook signature verification for every endpoint was not re-audited tonight (existing tests cover WhatsApp/Meta HMAC).
- Out-of-window WhatsApp without template: covered by `tests/test_whatsapp_window.py` and the follow-up tests, not by this harness.
