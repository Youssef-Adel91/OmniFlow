# UI quality pass (night 2, item 9)

## Found by measuring, fixed
| Finding | Evidence | Fix |
|---|---|---|
| **Public-page headings were nearly invisible** (global `h1–h6` colour navy `#1a2332` on the navy `#0a1128` background) | axe `color-contrast`: ratio **1.18:1** on every landing/pricing/data-deletion heading (and 1.1:1 on plan names) | `PublicShell`, 404 and error pages force `text-inherit` on headings |
| `robots.txt` / `sitemap.xml` were redirected to `/ar/robots.txt` (404) by the locale middleware | `curl -i /robots.txt` → 307 | excluded from the middleware matcher; now 200 |
| Unknown URLs, marketing and legal pages went through Clerk's handshake (in development a new browser is bounced to `*.clerk.accounts.dev`) | real Chromium: `ERR_NAME_NOT_RESOLVED` on `/ar` | Clerk middleware now runs only for the signed-in app and auth pages |
| No error boundary, no 404, no loading state anywhere | `find src/app -name error.tsx` → none | `global-error`, `[locale]/error`, dashboard `error` + `loading`, localized `not-found` + catch-all (real 404 status) |

## Bundle size (first-load JS, uncompressed, from `.next/diagnostics/route-bundle-stats.json`)
| Route | Before (KB) | After (KB) |
|---|---:|---:|
| `/[locale]` (landing), pricing, privacy, terms, refund, data-deletion | 665 | **513** |
| `/[locale]/data-deletion/status` | 717 | 565 |
| unknown URL (404) | 657 | 505 |
| `/[locale]/properties` | 1104 | **956** |
| `/[locale]/settings` | 1040 | **964** |
| `/[locale]/customers` | 1007 | **954** |
| other dashboard pages, auth pages | unchanged | unchanged |

How: Clerk's client bundle (~150 KB) no longer loads on public pages (`AppProviders` wraps only dashboard/onboarding/auth layouts); the import wizard, property form, customer dialogs and the non-default settings sections load on demand. The dashboard already lazy-loaded its charts (recharts). Remaining weight is the framework + Clerk + next-intl; gzip/real-network numbers were not measured.

## Automated tests (`e2e/public.spec.ts`, run for real in headless Chromium against `next start`)
38 tests pass: RTL/LTR + language switch, no invented claims, pricing shows VAT/Meta fees/4 plans, legal draft banners, data-deletion status states, localized 404 (ar/en, status 404), robots/sitemap, titles/descriptions, skip-link + FAQ keyboard use, and **axe WCAG 2.0/2.1 A+AA (incl. colour contrast) with no serious/critical violations on 7 pages × ar/en × desktop/mobile (375 px, no sideways scroll)**.

## Not covered / not run
- `e2e/flows.spec.ts` (login, import, conversation, customer profile, ticket, billing) is written but **never run**: it needs a Clerk test user and the backend stack. The pre-existing `human_simulation` / `user_flow` specs target the removed local-login and are stale.
- The signed-in app was not audited with axe or checked at mobile width (needs a session). Sidebar/inbox/dashboard contrast, labels and keyboard navigation remain unverified; measure them with the same `AxeBuilder` once a test user exists.
- Moving Clerk off public pages changes provider placement: verify sign-in, sign-up, dashboard and onboarding in a browser with real Clerk keys before merging.
