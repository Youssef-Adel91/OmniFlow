/**
 * lib/api/broadcasts.ts — WhatsApp broadcast campaigns (backend: gateway/routers/broadcasts.py)
 *
 *   GET  /broadcasts                  → { items, total, page, page_size }
 *   POST /broadcasts                  → Broadcast (draft)
 *   GET  /broadcasts/readiness        → is WhatsApp connected / can templates be listed
 *   GET  /broadcasts/templates        → approved Meta templates of the tenant's WhatsApp Business Account
 *   GET  /broadcasts/preview-audience → reach + why customers are excluded
 *   GET  /broadcasts/{id}/stats       → sent / delivered / read / failed / replies / unsubscribes
 *   POST /broadcasts/{id}/schedule    → body { scheduled_at?, confirmed_recipients }
 *   POST /broadcasts/{id}/cancel
 *
 * Only customers who opted in to marketing (PDPL), never opted out and with a real phone number are ever
 * messaged — the server enforces it; the composer just shows the numbers.
 */
import { apiClient } from "@/lib/api/client";

export type BroadcastStatus = "draft" | "scheduled" | "sending" | "completed" | "cancelled" | "failed" | (string & {});
export type Segment = "all" | "vip" | "engaged" | "new" | "inactive" | "hot";

export interface AudienceSpec {
  segment: Segment;
  tags: string[];
  city: string | null;
  sources: string[];
}

export interface Broadcast {
  id:                   string;
  title:                string;
  message_template:     string;
  target_audience:      AudienceSpec;
  meta_template_id:     string | null;
  template_language:    string;
  template_params:      string[];
  send_rate_per_second: number;
  status:               BroadcastStatus;
  scheduled_at:         string | null;
  started_at:           string | null;
  completed_at:         string | null;
  recipients_count:     number | null;
  last_error:           string | null;
  created_at:           string | null;
}

export interface BroadcastPage { items: Broadcast[]; total: number; page: number; page_size: number }

export interface BroadcastCreate {
  title:                string;
  message_template?:    string;
  target_audience:      Partial<AudienceSpec> & { segment: Segment };
  meta_template_id:     string;
  template_language:    string;
  template_params:      string[];
  send_rate_per_second: number;
}

export interface AudiencePreview {
  target_audience:      string;
  recipients_count:     number;
  total_customers:      number;
  matching_segment:     number;
  excluded_no_consent:  number;
  excluded_opted_out:   number;
  excluded_restricted:  number;
  excluded_no_phone:    number;
}

export interface Readiness { whatsapp_connected: boolean; can_list_templates: boolean; can_send: boolean; reasons: string[] }

export interface MetaTemplate {
  name: string; language: string; category: string | null; body: string; variables: number;
  supported: boolean; unsupported_reason: string | null;
}

export interface BroadcastStats {
  campaign_id: string; status: string; recipients: number;
  sent: number; delivered: number; read: number; failed: number; replies: number; unsubscribes: number;
  rates: { delivered_pct: number; read_pct: number; reply_pct: number; unsubscribe_pct: number };
  top_errors: { error: string; count: number }[];
  reply_window_hours: number; last_error: string | null;
}

// ── Labels ─────────────────────────────────────────────────────────────────

export const SEGMENT_OPTIONS: { value: Segment; label: string; hint: string }[] = [
  { value: "all",      label: "كل العملاء المؤهلين",   hint: "كل من وافق على الرسائل التسويقية" },
  { value: "vip",      label: "عملاء VIP",              hint: "العملاء المميّزون" },
  { value: "engaged",  label: "المتفاعلون",             hint: "VIP أو درجة تفاعل مرتفعة" },
  { value: "hot",      label: "الساخنون",               hint: "الأقرب للشراء" },
  { value: "new",      label: "عملاء جدد",              hint: "أُضيفوا خلال آخر 30 يومًا" },
  { value: "inactive", label: "الخاملون",               hint: "تفاعلهم منخفض وليسوا VIP" },
];

export const SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: "whatsapp", label: "واتساب" }, { value: "instagram", label: "إنستجرام" }, { value: "messenger", label: "ماسنجر" },
  { value: "manual", label: "إضافة يدوية" }, { value: "import", label: "استيراد" },
];

export const BROADCAST_STATUS_LABELS: Record<string, { ar: string; color: string }> = {
  draft:     { ar: "مسودة",        color: "#B4B8CC" },
  scheduled: { ar: "مجدولة",       color: "#C9A84C" },
  sending:   { ar: "جارٍ الإرسال",  color: "#6DB3F2" },
  completed: { ar: "اكتملت",       color: "#6FCF73" },
  sent:      { ar: "اكتملت",       color: "#6FCF73" },
  cancelled: { ar: "ملغاة",        color: "#B4B8CC" },
  failed:    { ar: "فشلت",         color: "#F28B8B" },
};

export const SEND_RATES = [10, 20, 50, 80];

// ── Pure helpers (unit-tested) ──────────────────────────────────────────────

/** `<input type="datetime-local">` gives a wall-clock string; the business works in Asia/Riyadh (UTC+3, no DST). */
export function riyadhInputToIso(value: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2}))?$/.exec(value.trim());
  if (!m) throw new Error("موعد غير صالح");
  return `${m[1]}T${m[2]}:${m[3] ?? "00"}+03:00`;
}

export function formatRiyadh(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("ar-SA-u-ca-gregory", { timeZone: "Asia/Riyadh", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(d) + " (الرياض)";
}

/** Fill a template body for the WhatsApp-style preview: {{1}} ← params[0] with {name}/{city} replaced by sample values. */
export function renderTemplatePreview(body: string, params: string[], sample = { name: "أحمد", city: "الرياض" }): string {
  return body.replace(/\{\{\s*(\d+)\s*\}\}/g, (_m, n: string) => {
    const raw = params[Number(n) - 1];
    return raw && raw.trim() ? raw.replace(/\{name\}/g, sample.name).replace(/\{city\}/g, sample.city) : `{{${n}}}`;
  });
}

export function segmentLabel(a: Partial<AudienceSpec> | string | null | undefined): string {
  const seg = typeof a === "string" ? a : a?.segment ?? "all";
  const base = SEGMENT_OPTIONS.find((o) => o.value === seg)?.label ?? String(seg);
  const extra = typeof a === "object" && a ? [a.tags?.length ? `وسوم: ${a.tags.join("، ")}` : "", a.city ? `مدينة: ${a.city}` : "", a.sources?.length ? `قنوات: ${a.sources.join("، ")}` : ""].filter(Boolean) : [];
  return [base, ...extra].join(" · ");
}

/** Arabic text + code for the structured errors the broadcast endpoints send. */
export function broadcastErrorInfo(err: unknown, fallback = "حدث خطأ غير متوقع"): { message: string; code?: string; currentRecipients?: number } {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && "message" in detail) {
    const d = detail as { message: unknown; code?: string; current_recipients?: number };
    return { message: String(d.message), code: d.code, currentRecipients: d.current_recipients };
  }
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail) && detail[0]?.msg) return { message: String(detail[0].msg) };
  return { message: (err as Error)?.message || fallback };
}

// ── Normalisation ──────────────────────────────────────────────────────────

function normalizeAudience(raw: any): AudienceSpec {
  const o = raw && typeof raw === "object" ? raw : { segment: raw };
  return { segment: (o.segment ?? o.list_type ?? "all") as Segment, tags: Array.isArray(o.tags) ? o.tags : [], city: o.city ?? null, sources: Array.isArray(o.sources) ? o.sources : [] };
}

export function normalizeBroadcast(raw: any): Broadcast {
  return {
    id:                   raw?.campaign_id ?? raw?.id ?? raw?.broadcast_id ?? "",
    title:                raw?.title ?? "",
    message_template:     raw?.message_template ?? raw?.message ?? "",
    target_audience:      normalizeAudience(raw?.target_audience),
    meta_template_id:     raw?.meta_template_id ?? null,
    template_language:    raw?.template_language ?? "ar",
    template_params:      Array.isArray(raw?.template_params) ? raw.template_params : [],
    send_rate_per_second: raw?.send_rate_per_second ?? 20,
    status:               raw?.status ?? "draft",
    scheduled_at:         raw?.scheduled_at ?? null,
    started_at:           raw?.started_at ?? null,
    completed_at:         raw?.completed_at ?? null,
    recipients_count:     raw?.recipients_count ?? null,
    last_error:           raw?.last_error ?? null,
    created_at:           raw?.created_at ?? null,
  };
}

// ── API functions ──────────────────────────────────────────────────────────

export async function fetchBroadcasts(params: { page?: number; page_size?: number; status?: BroadcastStatus } = {}): Promise<BroadcastPage> {
  const page = params.page ?? 1;
  const page_size = params.page_size ?? 20;
  const { data } = await apiClient.get<any>("/broadcasts", { params: { page, page_size, status: params.status || undefined } });
  const raw: any[] = Array.isArray(data) ? data : data?.items ?? [];
  return { items: raw.map(normalizeBroadcast), total: data?.total ?? raw.length, page: data?.page ?? page, page_size: data?.page_size ?? page_size };
}

export async function createBroadcast(body: BroadcastCreate): Promise<Broadcast> {
  const { data } = await apiClient.post<any>("/broadcasts", body);
  return normalizeBroadcast(data);
}

/** Schedule (or send now when `scheduledAt` is omitted). `confirmedRecipients` makes the server refuse if the audience changed. */
export async function scheduleBroadcast(broadcastId: string, opts: { scheduledAt?: string; confirmedRecipients?: number } = {}): Promise<Broadcast> {
  const { data } = await apiClient.post<any>(`/broadcasts/${broadcastId}/schedule`, {
    ...(opts.scheduledAt ? { scheduled_at: opts.scheduledAt } : {}),
    ...(opts.confirmedRecipients != null ? { confirmed_recipients: opts.confirmedRecipients } : {}),
  });
  return normalizeBroadcast(data);
}

export async function cancelBroadcast(broadcastId: string): Promise<Broadcast> {
  const { data } = await apiClient.post<any>(`/broadcasts/${broadcastId}/cancel`);
  return normalizeBroadcast(data);
}

export async function previewAudience(a: Partial<AudienceSpec> & { segment: Segment }): Promise<AudiencePreview> {
  const { data } = await apiClient.get<AudiencePreview>("/broadcasts/preview-audience", {
    params: { target_audience: a.segment, tags: a.tags?.length ? a.tags.join(",") : undefined, city: a.city?.trim() || undefined, sources: a.sources?.length ? a.sources.join(",") : undefined },
  });
  return data;
}

export async function fetchReadiness(): Promise<Readiness> {
  const { data } = await apiClient.get<Readiness>("/broadcasts/readiness");
  return data;
}

export async function fetchTemplates(): Promise<MetaTemplate[]> {
  const { data } = await apiClient.get<{ items: MetaTemplate[] }>("/broadcasts/templates");
  return data.items;
}

export async function fetchBroadcastStats(broadcastId: string): Promise<BroadcastStats> {
  const { data } = await apiClient.get<BroadcastStats>(`/broadcasts/${broadcastId}/stats`);
  return data;
}
