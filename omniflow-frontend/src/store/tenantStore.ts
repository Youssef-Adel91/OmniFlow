/**
 * store/tenantStore.ts — Tenant & UI Preferences Store
 *
 * ⚠️ AUTHENTICATION IS OWNED BY CLERK — NOT BY THIS STORE.
 *
 * Sprint 14 (auth unification):
 *   The legacy local JWT auth system that used to live here
 *   (`login` / `register` / `logout`, `token` / `refreshToken`,
 *   localStorage `omniflow_token` and hand-written `document.cookie` writes)
 *   has been REMOVED entirely.
 *
 *   The single source of truth for the session is now Clerk:
 *     - Route protection      → `clerkMiddleware` in `src/middleware.ts`
 *     - Sign in / sign up UI  → `/[locale]/sign-in`, `/[locale]/sign-up`
 *     - Sign out              → `useClerk().signOut()` (see Sidebar/TopNav)
 *     - API authorization     → `apiClient` request interceptor reads the
 *                               token from `window.Clerk.session.getToken()`
 *
 *   Do NOT re-introduce token storage here. Anything that needs the JWT
 *   should go through `lib/api/client.ts`.
 *
 * What remains in this store:
 *   - `tenant`            — display metadata for the active tenant
 *                           (business name / tier), hydrated from GET /settings
 *   - UI preferences      — locale, sidebar collapsed state
 *   - `unreadCount`       — inbox notification badge
 *
 * Persisted to localStorage via zustand/middleware/persist (UI preferences and
 * tenant display metadata only — never credentials). Hydration is triggered
 * explicitly by `components/auth/AuthHydrator.tsx` to avoid an SSR/CSR mismatch.
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { Locale } from "@/i18n/config";

// ── Types ────────────────────────────────────────────────────────────────────

export interface TenantInfo {
  tenantId:     string;
  businessName: string;
  falLicense:   string;
  tier:         "economic" | "professional" | "enterprise";
  status:       "trial" | "active" | "suspended";
  logoUrl?:     string;
  primaryPhone?: string; // WhatsApp Phone Number ID
}

// ── Store shape ───────────────────────────────────────────────────────────────

interface TenantState {
  // ── Tenant display metadata (NOT credentials) ─────────────────────────────
  tenant: TenantInfo | null;

  // ── Hydration gate ────────────────────────────────────────────────────────
  // Tracks whether zustand/persist has finished rehydrating from localStorage.
  _hydrated: boolean;

  // ── UI Preferences ────────────────────────────────────────────────────────
  locale:           Locale;
  sidebarCollapsed: boolean;
  activeRoute:      string;

  // ── Notification badge ────────────────────────────────────────────────────
  unreadCount: number;

  // ── Actions ───────────────────────────────────────────────────────────────
  setLocale:           (locale: Locale) => void;
  toggleSidebar:       () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setActiveRoute:      (route: string) => void;
  setUnreadCount:      (count: number) => void;
  incrementUnread:     () => void;
  setTenant:           (tenant: TenantInfo | null) => void;
  updateTenant:        (partial: Partial<TenantInfo>) => void;
  /** Clears tenant metadata + badges. Called on Clerk sign-out. */
  resetTenantState:    () => void;
  /** Called by AuthHydrator after persist rehydrates. */
  setHydrated:         () => void;
}

// ── Store implementation ──────────────────────────────────────────────────────

export const useTenantStore = create<TenantState>()(
  persist(
    (set) => ({
      // ── Initial state ────────────────────────────────────────────────────
      tenant:           null,
      _hydrated:        false,
      locale:           "ar",
      sidebarCollapsed: false,
      activeRoute:      "/",
      unreadCount:      0,

      setHydrated: () => set({ _hydrated: true }),

      // ── Locale ────────────────────────────────────────────────────────────
      setLocale: (locale) => set({ locale }),

      // ── Sidebar ───────────────────────────────────────────────────────────
      toggleSidebar:       () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),

      // ── Navigation ────────────────────────────────────────────────────────
      setActiveRoute: (route) => set({ activeRoute: route }),

      // ── Notifications ─────────────────────────────────────────────────────
      setUnreadCount:  (count) => set({ unreadCount: count }),
      incrementUnread: ()      => set((s) => ({ unreadCount: s.unreadCount + 1 })),

      // ── Tenant ────────────────────────────────────────────────────────────
      setTenant: (tenant) => set({ tenant }),

      updateTenant: (partial) =>
        set((s) => ({
          tenant: s.tenant ? { ...s.tenant, ...partial } : ({ ...partial } as TenantInfo),
        })),

      resetTenantState: () => set({ tenant: null, unreadCount: 0 }),
    }),

    {
      name:    "omniflow-tenant-store",
      storage: createJSONStorage(() =>
        typeof window !== "undefined" ? localStorage : ({} as Storage)
      ),
      // skipHydration prevents the store from auto-rehydrating on import — we
      // trigger it manually via AuthHydrator so components can gate on
      // `_hydrated` and avoid an SSR/CSR mismatch flash.
      skipHydration: true,
      // Persist UI preferences + tenant display metadata only. NO credentials.
      partialize: (state) => ({
        locale:           state.locale,
        sidebarCollapsed: state.sidebarCollapsed,
        tenant:           state.tenant,
      }),
    }
  )
);

// ── Selector hooks (performance: avoid re-renders for unrelated state) ────────

export const useLocale      = () => useTenantStore((s) => s.locale);
export const useTenant      = () => useTenantStore((s) => s.tenant);
export const useIsHydrated  = () => useTenantStore((s) => s._hydrated);
export const useSidebar     = () => useTenantStore((s) => ({
  collapsed:    s.sidebarCollapsed,
  toggle:       s.toggleSidebar,
  setCollapsed: s.setSidebarCollapsed,
}));
export const useUnreadCount = () => useTenantStore((s) => s.unreadCount);
