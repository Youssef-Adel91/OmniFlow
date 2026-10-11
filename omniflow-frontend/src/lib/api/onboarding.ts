/**
 * lib/api/onboarding.ts — setup checklist + WhatsApp Embedded Signup (backend: routers/onboarding.py, routers/whatsapp_signup.py)
 *
 *   GET  /onboarding/checklist · POST /onboarding/checklist/dismiss
 *   GET  /integrations/whatsapp/embedded-signup/config   (404 while the backend flag is off)
 *   POST /integrations/whatsapp/embedded-signup/complete (admin)
 */
import { apiClient } from "@/lib/api/client";

export type ChecklistKey = "company" | "persona" | "properties" | "channel" | "bot_test";
export interface ChecklistStep { key: ChecklistKey; done: boolean; href: string }
export interface Checklist { steps: ChecklistStep[]; done: number; total: number; complete: boolean; dismissed: boolean }

export async function fetchChecklist(): Promise<Checklist> {
  const { data } = await apiClient.get<Checklist>("/onboarding/checklist");
  return data;
}
export async function dismissChecklist(): Promise<void> {
  await apiClient.post("/onboarding/checklist/dismiss");
}

export const STEP_COPY: Record<ChecklistKey, { title: string; hint: string }> = {
  company: { title: "أضف بيانات شركتك", hint: "نبذة عن الشركة وخدماتها ومناطق عملها ليجيب المساعد بدقة." },
  persona: { title: "اضبط شخصية المساعد", hint: "النبرة وتعليمات التعامل مع العملاء." },
  properties: { title: "أضف عقاراتك", hint: "ارفع ملف Excel/CSV أو أضف عقارًا يدويًا." },
  channel: { title: "اربط قناة واتساب أو فيسبوك/إنستجرام", hint: "لتصل رسائل العملاء إلى OmniFlow." },
  bot_test: { title: "جرّب المساعد", hint: "اسأله سؤالًا من «اختبار البوت» قبل تشغيله على العملاء." },
};

// ── WhatsApp Embedded Signup ─────────────────────────────────────────────────
export interface SignupConfig { enabled: boolean; app_id?: string | null; config_id?: string | null; graph_version?: string | null }

/** Returns null when the feature is off (backend answers 404) or unreachable — the UI then simply shows nothing. */
export async function fetchSignupConfig(): Promise<SignupConfig | null> {
  try {
    const { data } = await apiClient.get<SignupConfig>("/integrations/whatsapp/embedded-signup/config");
    return data.enabled ? data : null;
  } catch {
    return null;
  }
}
export async function completeSignup(body: { code: string; waba_id: string; phone_number_id: string }) {
  const { data } = await apiClient.post<{ connected: boolean; phone_number_id: string; waba_id: string; display_phone_number: string | null }>(
    "/integrations/whatsapp/embedded-signup/complete", body);
  return data;
}

/** Parses the `WA_EMBEDDED_SIGNUP` postMessage Meta sends from the popup. Only facebook.com origins are trusted. */
export function parseSignupMessage(origin: string, raw: unknown): { waba_id: string; phone_number_id: string } | { cancelled: true } | null {
  let host = "";
  try { host = new URL(origin).hostname; } catch { return null; }
  if (host !== "facebook.com" && !host.endsWith(".facebook.com")) return null;
  let msg: unknown = raw;
  if (typeof raw === "string") { try { msg = JSON.parse(raw); } catch { return null; } }
  if (!msg || typeof msg !== "object") return null;
  const m = msg as { type?: string; event?: string; data?: { waba_id?: string; phone_number_id?: string } };
  if (m.type !== "WA_EMBEDDED_SIGNUP") return null;
  if (m.event === "FINISH" && m.data?.waba_id && m.data?.phone_number_id && /^\d+$/.test(m.data.waba_id) && /^\d+$/.test(m.data.phone_number_id)) {
    return { waba_id: m.data.waba_id, phone_number_id: m.data.phone_number_id };
  }
  if (m.event === "CANCEL") return { cancelled: true };
  return null;
}
