/**
 * lib/api/users.ts — Current User (profile) API Client
 *
 * Endpoint contracts (backend — Sprint 14):
 *   GET   /users/me → CurrentUser
 *   PATCH /users/me → CurrentUser   (full_name, phone_number)
 *
 * Identity itself lives in Clerk; this endpoint only exposes the
 * application-side profile record linked to the Clerk user.
 */
import { apiClient } from "@/lib/api/client";

// ── Types ──────────────────────────────────────────────────────────────────

export interface CurrentUser {
  id:           string;
  tenant_id?:   string | null;
  full_name:    string;
  phone_number: string | null;
  /** Read-only — sourced from Clerk. */
  email?:       string | null;
  role?:        string | null;
  created_at?:  string | null;
}

export interface CurrentUserUpdate {
  full_name?:    string;
  phone_number?: string | null;
}

// ── Normalisation ──────────────────────────────────────────────────────────

function normalizeUser(raw: any): CurrentUser {
  return {
    id:           raw?.id ?? raw?.user_id ?? "",
    tenant_id:    raw?.tenant_id ?? null,
    full_name:    raw?.full_name ?? "",
    phone_number: raw?.phone_number ?? null,
    email:        raw?.email ?? null,
    role:         raw?.role ?? null,
    created_at:   raw?.created_at ?? null,
  };
}

// ── API functions ──────────────────────────────────────────────────────────

/** Fetch the currently authenticated user's profile. */
export async function fetchCurrentUser(): Promise<CurrentUser> {
  const { data } = await apiClient.get<any>("/users/me");
  return normalizeUser(data);
}

/** Update the current user's profile (name / phone). */
export async function updateCurrentUser(
  body: CurrentUserUpdate,
): Promise<CurrentUser> {
  const { data } = await apiClient.patch<any>("/users/me", body);
  return normalizeUser(data);
}

// ── Labels ─────────────────────────────────────────────────────────────────

export const ROLE_LABELS: Record<string, string> = {
  admin:   "مدير عام (Admin)",
  agent:   "وكيل (Agent)",
  auditor: "مدقق (Auditor)",
  owner:   "مالك الحساب (Owner)",
};
