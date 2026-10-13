# Go-live checklist

Nothing here has been done by the build sessions: they never touched a production service. Tick each box yourself. "Owner" is who can actually do it.

## A. Legal & business (blocks a commercial launch)
- [ ] **Lawyer review and replacement of every placeholder legal text**: privacy policy, terms, refund policy, data-deletion instructions (`omniflow-frontend/src/lib/public/copy.ts`, banners say "مسودة بانتظار مراجعة محامٍ"). Remove the banners only with approved text. *(owner: lawyer + you)*
- [ ] Retention periods approved (`docs/retention.md`) — the defaults are placeholders.
- [ ] Company data entered: legal name, **VAT registration number**, CR number, address (`BILLING_SELLER_*`). Without a VAT number the invoice says "غير مضبوط بعد" and ZATCA cannot be enabled.
- [ ] Pricing confirmed against **PRICING_STUDY.md** (it was not available to the build; numbers came from the P10 brief). Re-read the catalogue seeds (migration 0028) and the public pricing page.
- [ ] Data-protection contact published (placeholder `[…تُستكمل قبل الإطلاق]` in the privacy and deletion texts).

## B. Domain, TLS, hosting
- [ ] Domain + DNS; TLS certificate (nginx config in `omniflow-deploy/nginx`).
- [ ] `PUBLIC_BASE_URL` (backend) and `NEXT_PUBLIC_BASE_URL` (frontend build arg) set to the final origin — needed for sitemap, Open Graph and the Meta data-deletion status link.
- [ ] `.env.prod` filled from `omniflow-deploy/.env.prod.example`; no placeholder left (`docs/env-reference.md`); `APP_ENV=production`.
- [ ] Database role is **not** superuser/BYPASSRLS (`provision-app-role.sql`; `/health/ready` fails otherwise).
- [ ] Backups: `backup.sh` scheduled, **off-host copy configured**, and a restore drill done (`docs/morning-verification.md` §4) — a backup never restored is not a backup.
- [ ] Monitoring/alerting stack started and an alert delivered to a human once.

## C. Clerk
- [ ] Production Clerk instance (live keys `pk_live_`/`sk_live_`), production domain added, allowed redirect URLs, webhook endpoint `/api/v1/webhooks/clerk` with its signing secret.
- [ ] Sign-in/up, dashboard and onboarding verified in a real browser after the provider-placement change (`docs/ui-quality.md`).

## D. Meta (WhatsApp / Instagram / Messenger)
- [ ] Business verification complete; app moved from Development to **Live**.
- [ ] App Review passed for the permissions used (pages_messaging, instagram_manage_messages, instagram_manage_comments, pages_read_engagement, whatsapp_business_messaging/management …).
- [ ] Webhook callback URLs set to the real domain (WhatsApp `/api/v1/webhooks/whatsapp`, Meta `/api/v1/webhooks/meta`) with verify token + app secret (`META_*`); ngrok URLs removed.
- [ ] **Data Deletion Request Callback** = `https://DOMAIN/api/v1/meta/data-deletion`; test with the button in the app dashboard; privacy-policy URL and deletion-instructions URL (`/ar/data-deletion`) entered.
- [ ] Optional: Tech Provider approval → set `META_ES_CONFIG_ID`, turn on `FEATURE_WHATSAPP_EMBEDDED_SIGNUP`, test with a real number. Comment replies: test on a real test Page before `FEATURE_SOCIAL_COMMENT_REPLIES`.
- [ ] Message templates approved for campaigns.

## E. Payments (Moyasar)
- [ ] Live keys only in production (`sk_live_` is refused outside production by design); webhook `secret_token` set; test a real small payment, a failed payment, a refund (staff endpoint) end to end.
- [ ] Refund policy text approved and matching `BILLING_REFUND_WINDOW_DAYS`.
- [ ] `SUPPORT_STAFF_EMAILS` set (staff console, margin view, refunds) — values only in the server env.

## F. ZATCA e-invoicing (do not rush)
- [ ] Legal precondition: VAT-registered entity. Read `docs/einvoice.md` — the module is **not validated by ZATCA**; sandbox only; production sending is double-locked.
- [ ] Onboard in the Fatoora sandbox, get compliance results, fix what ZATCA rejects, then plan production onboarding. Keep `EINVOICE_ENABLED=false` until then.

## G. Secrets & security
- [ ] Rotate every key that ever appeared in a chat, screenshot or log. Generate fresh `FIELD_ENCRYPTION_KEY`, JWT/HMAC secrets, DB/Redis/MinIO passwords.
- [ ] `gitleaks` runs on the CI runner (the step only works if the binary is installed there).
- [ ] Decide on migrating `python-jose` → PyJWT (unfixable advisories; `docs/security-audit.md`).
- [ ] The Content-Security-Policy is **report-only**; read the reports after a staging pass, then promote it.

## H. AI & content
- [ ] LLM key with a spend cap; confirm cost per reply vs the plan prices on real traffic (`/api/v1/billing/admin/unit-economics`).
- [ ] Run the load test on staging (`omniflow-backend/loadtest`) and replace the goals with measured numbers.

## I. Day-one operations
- [ ] Support mailbox / process for tickets (SLA targets are shown to users: 4/8/24/48 h).
- [ ] `RUNBOOK.md` read by whoever is on call; `/health/ready`, `/health/workers`, `/health/lag` bookmarked.
- [ ] Retention job left OFF until §A is signed; the `python -m scripts.retention_run` dry-run report reviewed once.
