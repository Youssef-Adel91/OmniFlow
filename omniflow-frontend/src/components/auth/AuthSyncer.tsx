"use client";

/**
 * components/auth/AuthSyncer.tsx — Tenant metadata hydrator
 *
 * ⚠️ AUTH LOGIC REMOVED (Sprint 14 — Clerk unification).
 *
 * This component used to bridge the legacy local-JWT session from localStorage
 * into a hand-written `omniflow_token` cookie so the Edge middleware could read
 * it. That whole mechanism is gone:
 *
 *   - Route protection is now handled by `clerkMiddleware` (src/middleware.ts),
 *     which reads Clerk's own session cookie.
 *   - API authorization is handled by the `apiClient` request interceptor,
 *     which pulls a fresh JWT from `window.Clerk.session.getToken()`.
 *
 * There is therefore NO cookie sync and NO token handling left here. The
 * component is kept (and still rendered by the dashboard layout) for one
 * remaining, non-auth purpose: hydrating the tenant display metadata
 * (business name / tier) shown in the Sidebar from GET /settings.
 *
 * If GET /settings is unavailable the failure is swallowed — the Sidebar
 * simply falls back to its neutral placeholders.
 */

import { useEffect } from "react";
import { useAuth } from "@clerk/nextjs";
import { useTenantStore } from "@/store/tenantStore";
import { fetchSettings } from "@/lib/api/settings";

export function AuthSyncer() {
  const { isLoaded, isSignedIn } = useAuth();

  useEffect(() => {
    if (!isLoaded || !isSignedIn) return;

    let cancelled = false;

    (async () => {
      try {
        const settings = await fetchSettings();
        if (cancelled) return;
        useTenantStore.getState().updateTenant({
          businessName: settings.business_name,
          tier: settings.subscription_tier as "economic" | "professional" | "enterprise",
          falLicense: settings.fal_license_number ?? "",
          primaryPhone: settings.whatsapp_phone_number_id ?? undefined,
          logoUrl: settings.logo_url ?? undefined,
        });
      } catch {
        // Non-fatal: the Sidebar renders neutral placeholders without it.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn]);

  return null; // renders nothing — side-effects only
}
