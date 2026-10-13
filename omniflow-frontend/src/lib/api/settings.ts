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

export type SubscriptionTier = "starter" | "pro" | "business" | "enterprise" | (string & {});

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
  /** Tone/objection-handling/closing-sale guidance, distinct from ai_system_prompt. */
  custom_ai_instructions?:   string | null;
  /** E.164 contact number shown on the VCard sent to new customers (item 7). */
  whatsapp_display_phone_number?: string | null;
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
  whatsapp_display_phone_number?: string;
}

export interface LogoUploadResponse {
  logo_url: string;
}

// ── Normalisation ──────────────────────────────────────────────────────────

function normalizeSettings(raw: any): TenantSettings {
  return {
    business_name:            raw?.business_name ?? "",
    subscription_tier:        raw?.subscription_tier ?? "starter",
    whatsapp_phone_number_id: raw?.whatsapp_phone_number_id ?? null,
    max_ai_conversations:
      typeof raw?.max_ai_conversations === "number" ? raw.max_ai_conversations : null,
    meta_access_token:        raw?.meta_access_token ?? null,
    fal_license_number:       raw?.fal_license_number ?? null,
    whatsapp_waba_id:         raw?.whatsapp_waba_id ?? null,
    logo_url:                 raw?.logo_url ?? null,
    ai_system_prompt:         raw?.ai_system_prompt ?? null,
    custom_ai_instructions:  raw?.custom_ai_instructions ?? null,
    whatsapp_display_phone_number: raw?.whatsapp_display_phone_number ?? null,
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
 * Update the tenant's AI Personality (system prompt) and, optionally, the
 * distinct custom AI behavior instructions (tone/objection-handling/closing
 * guidance). Pass `customInstructions` as `undefined` to leave it untouched,
 * or `""` to explicitly clear it. Requires admin role.
 */
export async function updateAiPersonality(
  systemPrompt: string,
  customInstructions?: string,
): Promise<TenantSettings> {
  const { data } = await apiClient.patch<any>("/settings/ai-personality", {
    system_prompt: systemPrompt,
    ...(customInstructions !== undefined ? { custom_instructions: customInstructions } : {}),
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
  starter:      "الأساسية (Starter)",
  pro:          "الاحترافية (Pro)",
  business:     "الأعمال (Business)",
  enterprise:   "المؤسسات (Enterprise)",
  // legacy placeholder tiers from before the real plans: shown as the plan they were replaced by, never as "الاقتصادية"
  economic:     "الأساسية (Starter)",
  professional: "الاحترافية (Pro)",
};

// ══════════════════════════════════════════════════════════════════════════════
// Preferences (business hours · human handoff · AI behaviour · identity)
//   GET /settings/preferences            PUT /settings/preferences/{section}   (admin)
// Channels
//   GET /settings/channels   POST /settings/channels/{whatsapp|facebook}/check   POST /settings/channels/facebook/disconnect
//   GET /settings/channels/facebook/diagnostics   POST /settings/channels/whatsapp/test-message   GET /settings/comments/activity
// Bot test
//   POST /settings/ai/test
// ══════════════════════════════════════════════════════════════════════════════

export type DayKey = "sun" | "mon" | "tue" | "wed" | "thu" | "fri" | "sat";
export const DAY_KEYS: DayKey[] = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
export const DAY_LABELS: Record<DayKey, string> = { sun: "الأحد", mon: "الإثنين", tue: "الثلاثاء", wed: "الأربعاء", thu: "الخميس", fri: "الجمعة", sat: "السبت" };

export interface BusinessHours {
  enabled: boolean;
  timezone: string;
  days: Partial<Record<DayKey, [string, string][]>>;
  out_of_hours_message: string;
}
export interface HandoffPrefs {
  auto_escalate: boolean;
  keywords: string[];
  on_low_confidence: boolean;
  round_robin: boolean;
  handoff_message: string;
  sla_minutes: number;
}
export interface AiConstraints { no_price_without_inventory: boolean; no_legal_or_financial_advice: boolean; no_competitor_mentions: boolean; ask_budget_early: boolean }
export interface AiBehavior {
  tone: "formal" | "friendly" | "concise" | "persuasive";
  dialect: "msa" | "saudi" | "gulf" | "egyptian";
  language: "ar" | "en" | "auto";
  welcome_message: string;
  quick_replies: string[];
  constraints: AiConstraints;
}
export interface IdentityPrefs { language: "ar" | "en"; timezone: string; currency: string }

export type CommentMode = "public_reply" | "private_dm" | "public_and_private";
export interface SocialCommentsPrefs {
  enabled: boolean;
  mode: CommentMode;
  only_business_hours: boolean;
  max_per_hour: number;
  ignore_keywords: string[];
  auto_hide_abusive: boolean;
  hide_keywords: string[];
  public_template: string;
  allow_price_in_private: boolean;
}
export const DEFAULT_PUBLIC_TEMPLATE = "أهلاً {name} 🌟 شكراً لتعليقك! أرسلنا لك التفاصيل على الخاص.";
export const DEFAULT_SOCIAL_COMMENTS: SocialCommentsPrefs = {
  enabled: false, mode: "public_reply", only_business_hours: false, max_per_hour: 60, ignore_keywords: [], auto_hide_abusive: true,
  hide_keywords: [], public_template: DEFAULT_PUBLIC_TEMPLATE, allow_price_in_private: false,
};
export interface SalesPrefs {
  enabled: boolean; ask_questions: boolean; max_questions: number; booking_enabled: boolean; slot_minutes: number; days_ahead: number; min_notice_hours: number;
  max_parallel_viewings: number; viewing_hours: { timezone: string; days: Record<string, string[][]> }; viewing_location_note: string; reminder_hours_before: number;
  followup_enabled: boolean; followup_delays_hours: number[]; followup_messages: string[]; whatsapp_followup_template: string; whatsapp_followup_template_lang: string;
}
export type PrefSection = "business_hours" | "handoff" | "ai_behavior" | "identity" | "social_comments" | "sales";
export interface PreferencesResponse {
  preferences: { business_hours: BusinessHours; handoff: HandoffPrefs; ai_behavior: AiBehavior; identity: IdentityPrefs; social_comments: SocialCommentsPrefs; sales?: SalesPrefs };
  features?: { social_comment_replies?: boolean; sales_closing?: boolean };
  configured: Record<PrefSection, boolean>;
  currencies: string[];
  updated_at: string | null;
}

export const TONE_OPTIONS: { value: AiBehavior["tone"]; label: string }[] = [
  { value: "friendly", label: "ودود وقريب" }, { value: "formal", label: "رسمي ومهذّب" },
  { value: "concise", label: "مختصر ومباشر" }, { value: "persuasive", label: "مقنع (دون ضغط)" },
];
export const DIALECT_OPTIONS: { value: AiBehavior["dialect"]; label: string }[] = [
  { value: "saudi", label: "سعودية بيضاء" }, { value: "gulf", label: "خليجية" }, { value: "msa", label: "فصحى مبسّطة" }, { value: "egyptian", label: "مصرية" },
];
export const LANGUAGE_OPTIONS: { value: AiBehavior["language"]; label: string }[] = [
  { value: "ar", label: "العربية دائمًا" }, { value: "en", label: "الإنجليزية دائمًا" }, { value: "auto", label: "بلغة العميل" },
];
export const TIMEZONES: { value: string; label: string }[] = [
  { value: "Asia/Riyadh", label: "الرياض (UTC+3)" }, { value: "Asia/Dubai", label: "دبي (UTC+4)" }, { value: "Asia/Kuwait", label: "الكويت (UTC+3)" },
  { value: "Asia/Qatar", label: "الدوحة (UTC+3)" }, { value: "Asia/Bahrain", label: "المنامة (UTC+3)" }, { value: "Africa/Cairo", label: "القاهرة" },
  { value: "Europe/London", label: "لندن" }, { value: "UTC", label: "UTC" },
];
export const CURRENCY_LABELS: Record<string, string> = {
  SAR: "ريال سعودي (SAR)", AED: "درهم إماراتي (AED)", KWD: "دينار كويتي (KWD)", QAR: "ريال قطري (QAR)", BHD: "دينار بحريني (BHD)",
  OMR: "ريال عُماني (OMR)", EGP: "جنيه مصري (EGP)", USD: "دولار أمريكي (USD)", EUR: "يورو (EUR)",
};

export async function fetchPreferences(): Promise<PreferencesResponse> {
  const { data } = await apiClient.get<PreferencesResponse>("/settings/preferences");
  return data;
}

export async function savePreferences<S extends PrefSection>(section: S, value: PreferencesResponse["preferences"][S]): Promise<PreferencesResponse["preferences"][S]> {
  const { data } = await apiClient.put<{ section: S; value: PreferencesResponse["preferences"][S] }>(`/settings/preferences/${section}`, value);
  return data.value;
}

export interface WhatsappChannel {
  reconnect_needed?: boolean; auth_failed_at?: string | null;
  connected: boolean; phone_number_id: string | null; waba_id: string | null; display_phone_number: string | null; token: string | null;
  templates_ready: boolean; last_inbound_at: string | null; issues: string[];
}
export interface FacebookChannel {
  reconnect_needed?: boolean; auth_failed_at?: string | null;
  connected: boolean; page_id: string | null; instagram_account_id: string | null; messenger: boolean; instagram: boolean;
  last_inbound_at: string | null; issues: string[];
}
export interface ChannelsStatus { whatsapp: WhatsappChannel; facebook: FacebookChannel }
export interface ChannelCheck { ok: boolean; detail: string; checked_at: string; needs_reconnect?: boolean; info?: Record<string, string> }

export async function fetchChannels(): Promise<ChannelsStatus> {
  const { data } = await apiClient.get<ChannelsStatus>("/settings/channels");
  return data;
}
export async function checkChannel(channel: "whatsapp" | "facebook"): Promise<ChannelCheck> {
  const { data } = await apiClient.post<ChannelCheck>(`/settings/channels/${channel}/check`, undefined, { timeout: 20_000 });
  return data;
}
export interface PageDiagnostics {
  connected: boolean; ok: boolean; reachable?: boolean; token_valid?: boolean | null; expires_at?: string | null;
  scopes?: string[]; subscribed_fields?: string[]; missing_scopes?: string[]; missing_fields?: string[]; needs_reconnect?: boolean; issues: string[];
}
export async function fetchFacebookDiagnostics(): Promise<PageDiagnostics> {
  const { data } = await apiClient.get<PageDiagnostics>("/settings/channels/facebook/diagnostics", { timeout: 25_000 });
  return data;
}
export type CommentActivityAction = "hidden" | "hide_failed" | "ignored" | "public_reply" | "private_dm" | "public_and_private";
export interface CommentActivityItem {
  at: string; platform: "facebook" | "instagram"; comment_id: string; action: CommentActivityAction; reason: string;
  preview: string; name: string | null; public_reply: string | null;
}
export async function fetchCommentActivity(): Promise<CommentActivityItem[]> {
  const { data } = await apiClient.get<{ items: CommentActivityItem[] }>("/settings/comments/activity");
  return data.items;
}
export interface WhatsappTestResult { ok: boolean; method?: "text" | "template"; message_id?: string; detail: string; needs_reconnect?: boolean }
export async function sendWhatsappTestMessage(to: string): Promise<WhatsappTestResult> {
  const { data } = await apiClient.post<WhatsappTestResult>("/settings/channels/whatsapp/test-message", { to }, { timeout: 30_000 });
  return data;
}

/** Arabic label for one decision in the "recent comments" list (pure; unit-tested). */
export function commentActionLabel(action: CommentActivityAction, reason: string): { text: string; tone: "ok" | "warn" | "muted" } {
  switch (action) {
    case "hidden": return { text: reason === "spam" ? "أُخفي (سبام/رابط)" : "أُخفي (إساءة)", tone: "warn" };
    case "hide_failed": return { text: "تعذّر الإخفاء — أخفِه يدويًا", tone: "warn" };
    case "public_reply": return { text: "رد علني", tone: "ok" };
    case "private_dm": return { text: "رد خاص", tone: "ok" };
    case "public_and_private": return { text: "رد علني + رد خاص", tone: "ok" };
    default: {
      const why: Record<string, string> = {
        no_text: "إيموجي/بدون نص", ignore_keyword: "كلمة تجاهل", outside_business_hours: "خارج ساعات العمل", rate_limited: "تجاوز حد الدقيقة", hourly_cap: "تجاوز حد الساعة",
      };
      return { text: `تجاهل — ${why[reason] ?? reason}`, tone: "muted" };
    }
  }
}

export async function disconnectFacebook(): Promise<{ connected: boolean; note: string }> {
  const { data } = await apiClient.post<{ connected: boolean; note: string }>("/settings/channels/facebook/disconnect");
  return data;
}
export async function testBot(message: string): Promise<{ reply: string; model: string | null; latency_ms: number; note: string }> {
  const { data } = await apiClient.post("/settings/ai/test", { message }, { timeout: 60_000 });
  return data;
}

// ── Pure helpers (unit-tested) ──────────────────────────────────────────────

export const sameJson = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

/** Client-side mirror of the server rules, so the form can explain the problem before saving. Returns Arabic messages. */
export function validateHours(h: BusinessHours): string[] {
  const errors: string[] = [];
  let anyOpen = false;
  for (const day of DAY_KEYS) {
    const rows = [...(h.days[day] ?? [])].sort((a, b) => a[0].localeCompare(b[0]));
    rows.forEach(([s, e]) => {
      anyOpen = true;
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(s) || !/^([01]\d|2[0-3]):[0-5]\d$/.test(e)) errors.push(`${DAY_LABELS[day]}: صيغة الوقت غير صحيحة`);
      else if (s >= e) errors.push(`${DAY_LABELS[day]}: نهاية الفترة (${e}) يجب أن تكون بعد بدايتها (${s})`);
    });
    for (let i = 1; i < rows.length; i++) if (rows[i][0] < rows[i - 1][1]) errors.push(`${DAY_LABELS[day]}: فترات متداخلة`);
  }
  if (h.enabled && !anyOpen) errors.push("فعّلت ساعات العمل لكن كل الأيام مغلقة");
  return errors;
}

/** "الأحد–الخميس 09:00–17:00 · الجمعة مغلق" style summary used under the hours editor. */
export function summarizeHours(h: BusinessHours): string {
  if (!h.enabled) return "ساعات العمل غير مفعّلة: يعتبر النظام أنكم متاحون دائمًا.";
  const parts = DAY_KEYS.map((d) => ({ d, key: (h.days[d] ?? []).map(([s, e]) => `${s}–${e}`).join("، ") }));
  const groups: { days: DayKey[]; key: string }[] = [];
  for (const p of parts) {
    const last = groups[groups.length - 1];
    if (last && last.key === p.key) last.days.push(p.d); else groups.push({ days: [p.d], key: p.key });
  }
  return groups.map((g) => `${g.days.length > 1 ? `${DAY_LABELS[g.days[0]]}–${DAY_LABELS[g.days[g.days.length - 1]]}` : DAY_LABELS[g.days[0]]} ${g.key || "مغلق"}`).join(" · ");
}

export function parseKeywords(raw: string): string[] {
  return Array.from(new Set(raw.split(/[\n,،;]+/).map((k) => k.trim()).filter(Boolean)));
}

/** Arabic message from the backend's structured errors ({detail: {code, message}} / string / validation list). */
export function settingsErrorMessage(err: unknown, fallback = "حدث خطأ غير متوقع"): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && "message" in detail) return String((detail as { message: unknown }).message);
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return (err as Error)?.message || fallback;
}
