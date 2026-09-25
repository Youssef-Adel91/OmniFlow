/**
 * lib/api/settings.ts — Tenant Settings API Client
 *
 * Endpoint contracts (backend):
 *   GET   /settings                → TenantSettings
 *   PATCH /settings                → TenantSettings
 *   PATCH /settings/ai-personality → TenantSettings
 *   POST  /settings/logo           → LogoUploadResponse
 *
 * `meta_access_token` is returned MASKED by the backend and must therefore
 * never be sent back in a PATCH body — it is read-only in the UI.
 */
import { apiClient } from "@/lib/api/client";

// ── Types ──────────────────────────────────────────────────────────────────

export type SubscriptionTier = "economic" | "professional" | "enterprise" | string;

export interface TenantSettings {
  business_name:             string;
  subscription_tier:         SubscriptionTier;
  whatsapp_phone_number_id:  string | null;
  max_ai_conversations:      number | null;
  /** Masked by the backend (e.g. "EAAD••••••wxyz") — display only. */
  meta_access_token:         string | null;
  /** Optional extras the backend may include; harmless when absent. */
  fal_license_number?:       string | null;
  whatsapp_waba_id?:         string | null;
  logo_url?:                 string | null;
  ai_system_prompt?:         string | null;
}

/**
 * Only the fields the UI is allowed to change via the generic PATCH.
 * whatsapp_phone_number_id/meta_access_token/whatsapp_waba_id are NOT here —
 * the backend's TenantSettingsPatch schema never accepted them (silently
 * dropped, not an error), so they must go through PATCH /tenants/onboarding
 * instead. See the "reconnect via onboarding" link in the settings page.
 */
export interface TenantSettingsUpdate {
  business_name?:            string;
  max_ai_conversations?:     number | null;
}

export interface LogoUploadResponse {
  logo_url: string;
}

// ── Normalisation ──────────────────────────────────────────────────────────

function normalizeSettings(raw: any): TenantSettings {
  return {
    business_name:            raw?.business_name ?? "",
    subscription_tier:        raw?.subscription_tier ?? "economic",
    whatsapp_phone_number_id: raw?.whatsapp_phone_number_id ?? null,
    max_ai_conversations:
      typeof raw?.max_ai_conversations === "number" ? raw.max_ai_conversations : null,
    meta_access_token:        raw?.meta_access_token ?? null,
    fal_license_number:       raw?.fal_license_number ?? null,
    whatsapp_waba_id:         raw?.whatsapp_waba_id ?? null,
    logo_url:                 raw?.logo_url ?? null,
    ai_system_prompt:         raw?.ai_system_prompt ?? null,
  };
}

// ── API functions ──────────────────────────────────────────────────────────

/** Fetch the current tenant's settings. */
export async function fetchSettings(): Promise<TenantSettings> {
  const { data } = await apiClient.get<any>("/settings");
  return normalizeSettings(data);
}

/**
 * Partially update the tenant settings.
 * `meta_access_token` is intentionally not accepted here — it is masked
 * server-side and is rotated via the onboarding flow instead.
 */
export async function updateSettings(
  body: TenantSettingsUpdate,
): Promise<TenantSettings> {
  const { data } = await apiClient.patch<any>("/settings", body);
  return normalizeSettings(data);
}

/**
 * Update the tenant's AI Personality (system prompt).
 * Requires admin role.
 */
export async function updateAiPersonality(
  systemPrompt: string,
): Promise<TenantSettings> {
  const { data } = await apiClient.patch<any>("/settings/ai-personality", {
    system_prompt: systemPrompt,
  });
  return normalizeSettings(data);
}

/**
 * Upload a company logo image.
 * Accepts PNG, JPEG, WebP, or SVG — max 5 MB.
 * Returns the public URL of the uploaded logo.
 */
export async function uploadLogo(file: File): Promise<LogoUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<LogoUploadResponse>("/settings/logo", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

// ── Labels ─────────────────────────────────────────────────────────────────

export const TIER_LABELS: Record<string, string> = {
  economic:     "الباقة الاقتصادية",
  professional: "الباقة الاحترافية",
  enterprise:   "باقة المؤسسات",
};
