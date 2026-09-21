"use client";

/**
 * components/auth/AuthHydrator.tsx — Zustand persist hydration trigger
 *
 * ⚠️ This component NO LONGER performs any authentication work.
 *
 * Sprint 14 (auth unification): the legacy local-JWT session (localStorage
 * `omniflow_token` + hand-written auth cookies) was removed. Clerk is now the
 * only auth system — route protection lives in `src/middleware.ts` and the API
 * bearer token is read from `window.Clerk.session.getToken()` inside
 * `lib/api/client.ts`. Nothing here reads or writes credentials.
 *
 * What it still does:
 *   `tenantStore` is configured with `skipHydration: true` so Zustand does not
 *   auto-rehydrate its persisted UI preferences (locale, sidebar collapsed
 *   state, tenant display metadata) during the SSR import — that would cause a
 *   React hydration mismatch. This client component defers the rehydration to
 *   after the first commit and then flips the `_hydrated` flag.
 *
 * Placement: rendered in `app/[locale]/layout.tsx` so it covers every route.
 */

import { useEffect } from "react";
import { useTenantStore } from "@/store/tenantStore";

export function AuthHydrator() {
  useEffect(() => {
    // Trigger the deferred Zustand persist rehydration (UI preferences only)
    const persist = useTenantStore.persist;
    if (persist?.rehydrate) {
      persist.rehydrate();
    }
    useTenantStore.getState().setHydrated();
  }, []);

  return null;
}
