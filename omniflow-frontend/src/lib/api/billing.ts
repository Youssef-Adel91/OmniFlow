/**
 * lib/api/billing.ts — subscriptions, checkout, invoices (backend: routers/billing.py)
 *
 *   GET  /billing/status              every role (banner)
 *   GET  /billing/plans · /subscription · /transactions · /invoices   admin/auditor
 *   POST /billing/checkout            admin → { action: "checkout", checkout_url } | { action: "scheduled", effective_at }
 *   POST /billing/cancel · /resume    admin
 *   GET  /billing/transactions/{id}   admin (syncs with the provider while pending)
 *   GET  /billing/invoices/{id}/pdf   blob
 */
import { apiClient } from "@/lib/api/client";

export type SubStatus = "trial" | "active" | "past_due" | "suspended" | "cancelled" | string;
export type Interval = "monthly" | "yearly";

export interface BillingStatus {
  mode: "full" | "grace" | "blocked";
  status: SubStatus;
  reason: string | null;
  banner: "trial_ending" | "past_due" | null;
  days_left: number | null;
  plan_code: string;
  is_exempt: boolean;
}

export interface PlanPrice { subtotal_halalas: number; total_halalas: number; monthly_equivalent_halalas?: number }
export interface PlanLimits {
  seats?: number; messages_per_month?: number; channels?: number; properties?: number; contacts?: number; whatsapp_numbers?: number; campaigns?: boolean;
}
export interface Plan {
  code: string; name_ar: string; description_ar: string | null; currency: string; is_placeholder: boolean; contact_sales?: boolean;
  limits: PlanLimits;
  monthly: PlanPrice; yearly: PlanPrice;
}
export interface PlansResponse { items: Plan[]; vat_rate_bp: number }

export interface Txn {
  transaction_id: string; kind: string; plan_code: string; billing_interval: Interval; subtotal_halalas: number; vat_halalas: number;
  amount_halalas: number; currency: string; status: "pending" | "paid" | "failed" | "cancelled" | string; provider: string;
  failure_reason: string | null; created_at: string | null; paid_at: string | null; checkout_url: string | null;
}

export interface SubscriptionInfo {
  payments_enabled: boolean; is_exempt: boolean; viewer_role: string; plan: Plan | { code: string }; billing_interval: Interval; status: SubStatus;
  access: { mode: string; reason: string | null; banner: string | null; days_left: number | null };
  trial_ends_at: string | null; current_period_start: string | null; current_period_end: string | null;
  cancel_at_period_end: boolean; grace_ends_at: string | null; pending_plan_code: string | null; pending_interval: Interval | null;
  card: { brand: string | null; last4: string } | null; auto_renew: boolean; last_payment_error: string | null; retry_count: number;
  usage: { ai_messages: number; limit: number | null; level: "ok" | "warning" | "limit_reached" | "unlimited" };
  limits: Plan["limits"]; pending_checkout: Txn | null; last_failed: Txn | null;
  credits?: CreditBalance;
  quotas?: QuotaSnapshot;
  addons?: { seats: number; whatsapp_numbers: number };
  quota_settings?: QuotaSettings;
}

export interface CreditBalance { remaining: number; packs: { pack_code: string; remaining: number; total: number; expires_at: string }[] }
export interface QuotaSnapshot { unlimited: boolean; resources: Record<string, { used: number; limit: number; label_ar: string; over: boolean }>; campaigns?: boolean }
export interface QuotaSettings { quota_exhausted_action: "handoff" | "stop"; auto_topup_enabled: boolean; auto_topup_pack: string | null }
export interface VatPrice { subtotal_halalas: number; vat_halalas: number; total_halalas: number }
export interface TopupPack extends VatPrice { code: string; replies: number; validity_months: number }
export interface AddonRow { code: string; name_ar: string; available: boolean; unavailable_reason: string | null; monthly: VatPrice; yearly: VatPrice }
export interface TopupCatalog { packs: TopupPack[]; addons: AddonRow[]; credits: CreditBalance }
export interface CreditNoteRow { credit_note_id: string; credit_note_number: string; issued_at: string; subtotal_halalas: number; vat_halalas: number; total_halalas: number; reason: string }

export interface InvoiceRow { invoice_id: string; invoice_number: string; issued_at: string; description: string; subtotal_halalas: number; vat_halalas: number; total_halalas: number; currency: string }
export type CheckoutResult = { action: "checkout"; checkout_url: string; transaction: Txn } | { action: "scheduled"; effective_at: string };

// ── pure helpers (unit-tested) ─────────────────────────────────────────────

export function formatSar(halalas: number): string {
  const v = (halalas / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${v} ر.س`;
}

export const STATUS_LABEL: Record<string, string> = {
  trial: "فترة تجريبية", active: "فعّال", past_due: "متأخر السداد", suspended: "موقوف", cancelled: "ملغي",
};
export const KIND_LABEL: Record<string, string> = { initial: "اشتراك", renewal: "تجديد", upgrade: "ترقية", manual: "تفعيل يدوي", topup: "باقة ردود إضافية", addon: "إضافة" };
export const TXN_STATUS_LABEL: Record<string, string> = { pending: "بانتظار الدفع", paid: "مدفوع", failed: "فشل", cancelled: "ملغاة" };
export const INTERVAL_LABEL: Record<Interval, string> = { monthly: "شهري", yearly: "سنوي" };

export type BannerTone = "info" | "warn" | "danger";
export interface Banner { tone: BannerTone; text: string; cta: string | null }

/** What the dashboard banner says for a given gate state. Null = show nothing. */
export function bannerFor(s: BillingStatus | null): Banner | null {
  if (!s || s.is_exempt) return null;
  if (s.mode === "blocked") {
    const why = s.reason === "trial_expired" ? "انتهت الفترة التجريبية" : s.reason === "cancelled" ? "تم إلغاء الاشتراك" : "الاشتراك موقوف لعدم السداد";
    return { tone: "danger", text: `${why}: المساعد الذكي متوقف والحساب في وضع القراءة فقط.`, cta: "جدّد الاشتراك الآن" };
  }
  if (s.banner === "past_due") {
    const d = s.days_left;
    return { tone: "warn", text: `تعذّر تحصيل الاشتراك${d != null ? ` — يتوقف المساعد بعد ${d} ${d === 1 ? "يوم" : "أيام"}` : ""}. حدّث الدفع لتجنّب التوقف.`, cta: "ادفع الآن" };
  }
  if (s.banner === "trial_ending") {
    const d = s.days_left ?? 0;
    return { tone: "info", text: d <= 0 ? "تنتهي الفترة التجريبية اليوم." : `تنتهي الفترة التجريبية خلال ${d} ${d === 1 ? "يوم" : d <= 10 ? "أيام" : "يومًا"}.`, cta: "اختر باقة" };
  }
  return null;
}

export function usagePercent(used: number, limit: number | null): number | null {
  if (!limit || limit <= 0) return null;
  return Math.min(100, Math.round((used / limit) * 100));
}

export const USAGE_LABEL: Record<string, string> = { ok: "ضمن الحد", warning: "اقتربت من الحد", limit_reached: "بلغت الحد", unlimited: "بلا حد" };

/** The bullet list of a plan card, from its limits (a missing key = not limited / not shown). Pure, unit-tested. */
export function planFeatureLines(p: Pick<Plan, "limits" | "contact_sales">): string[] {
  if (p.contact_sales) return ["حدود مخصّصة حسب احتياجك", "دعم مخصّص", "تواصل معنا للحصول على عرض"];
  const l = p.limits;
  const n = (v: number) => v.toLocaleString("en-US");
  const out: string[] = [];
  if (l.messages_per_month != null) out.push(`${n(l.messages_per_month)} رد ذكاء اصطناعي / شهر`);
  if (l.seats != null) out.push(`${n(l.seats)} ${l.seats === 1 ? "مقعد" : "مقاعد"}`);
  if (l.whatsapp_numbers != null) out.push(`${n(l.whatsapp_numbers)} ${l.whatsapp_numbers === 1 ? "رقم واتساب" : "أرقام واتساب"}`);
  if (l.properties != null) out.push(`${n(l.properties)} عقار`);
  if (l.contacts != null) out.push(`${n(l.contacts)} جهة اتصال`);
  if (l.channels != null) out.push(`${n(l.channels)} ${l.channels === 1 ? "قناة" : "قنوات"}`);
  out.push(l.campaigns === false ? "الحملات التسويقية غير متاحة" : "حملات تسويقية (بموافقة العملاء)");
  return out;
}

/** "5,000 رد" style label of a top-up pack. */
export function topupLabel(replies: number): string { return `${replies.toLocaleString("en-US")} رد`; }

/** Colour of a quota bar: red when over the limit, amber from 80 %. */
export function quotaTone(used: number, limit: number): "ok" | "warn" | "over" {
  if (limit > 0 && used > limit) return "over";
  if (limit > 0 && used * 100 >= limit * 80) return "warn";
  return "ok";
}

export const QUOTA_ACTION_LABEL: Record<string, string> = { handoff: "تحويل المحادثة إلى موظف (الافتراضي)", stop: "إيقاف المساعد حتى التجديد" };

export function priceOf(plan: Plan, interval: Interval): PlanPrice { return interval === "yearly" ? plan.yearly : plan.monthly; }

/** Savings of paying yearly vs 12 monthly payments, in whole percent (0 when yearly is not cheaper). */
export function yearlySavingPercent(plan: Plan): number {
  const twelve = plan.monthly.subtotal_halalas * 12;
  if (twelve <= 0 || plan.yearly.subtotal_halalas >= twelve) return 0;
  return Math.round((1 - plan.yearly.subtotal_halalas / twelve) * 100);
}

/** The action label of a plan card, given where the tenant stands. */
export function planAction(current: { code: string; interval: Interval; status: SubStatus; is_exempt: boolean }, plan: Plan, interval: Interval, rank: Record<string, number>): { label: string; disabled: boolean } {
  if (current.is_exempt) return { label: "حساب داخلي", disabled: true };
  const same = current.code === plan.code && current.interval === interval;
  if (current.status === "active") {
    if (same) return { label: "الباقة الحالية — جدّد مبكرًا", disabled: false };
    const up = (rank[plan.code] ?? 0) > (rank[current.code] ?? 0) && interval === current.interval;
    return { label: up ? "ترقية الآن" : "التغيير عند التجديد القادم", disabled: false };
  }
  return { label: current.status === "trial" ? "اشترك" : "جدّد بهذه الباقة", disabled: false };
}

// ── API ───────────────────────────────────────────────────────────────────

export async function fetchBillingStatus(): Promise<BillingStatus> { return (await apiClient.get<BillingStatus>("/billing/status")).data; }
export async function fetchPlans(): Promise<PlansResponse> { return (await apiClient.get<PlansResponse>("/billing/plans")).data; }
export async function fetchSubscription(): Promise<SubscriptionInfo> { return (await apiClient.get<SubscriptionInfo>("/billing/subscription")).data; }
export async function fetchTransactions(): Promise<Txn[]> { return (await apiClient.get<{ items: Txn[] }>("/billing/transactions")).data.items; }
export async function fetchInvoices(): Promise<InvoiceRow[]> { return (await apiClient.get<{ items: InvoiceRow[] }>("/billing/invoices")).data.items; }
export async function fetchTransaction(id: string): Promise<Txn> { return (await apiClient.get<Txn>(`/billing/transactions/${id}`)).data; }
export async function startCheckout(plan_code: string, interval: Interval): Promise<CheckoutResult> {
  return (await apiClient.post<CheckoutResult>("/billing/checkout", { plan_code, interval })).data;
}
export async function fetchTopupCatalog(): Promise<TopupCatalog> { return (await apiClient.get<TopupCatalog>("/billing/topup-packs")).data; }
export async function buyTopup(pack_code: string): Promise<{ checkout_url: string; transaction: Txn }> { return (await apiClient.post("/billing/topups", { pack_code })).data; }
export async function buyAddon(code: "seat" | "whatsapp_number", quantity: number): Promise<{ checkout_url: string; transaction: Txn }> { return (await apiClient.post("/billing/addons", { code, quantity })).data; }
export async function removeAddon(code: "seat" | "whatsapp_number", quantity = 1): Promise<{ addons: { seats: number; whatsapp_numbers: number } }> { return (await apiClient.delete(`/billing/addons/${code}`, { params: { quantity } })).data; }
export async function saveQuotaSettings(body: Partial<QuotaSettings>): Promise<QuotaSettings> { return (await apiClient.put<QuotaSettings>("/billing/quota-settings", body)).data; }
export async function fetchCreditNotes(): Promise<CreditNoteRow[]> { return (await apiClient.get<{ items: CreditNoteRow[] }>("/billing/credit-notes")).data.items; }
export async function downloadCreditNote(n: CreditNoteRow): Promise<void> {
  const { data } = await apiClient.get<Blob>(`/billing/credit-notes/${n.credit_note_id}/pdf`, { responseType: "blob" });
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url; a.download = `${n.credit_note_number}.pdf`;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}
export async function cancelSubscription(): Promise<{ cancel_at_period_end: boolean; effective_at: string | null }> { return (await apiClient.post("/billing/cancel")).data; }
export async function resumeSubscription(): Promise<{ cancel_at_period_end: boolean }> { return (await apiClient.post("/billing/resume")).data; }

export async function downloadInvoice(inv: InvoiceRow): Promise<void> {
  const { data } = await apiClient.get<Blob>(`/billing/invoices/${inv.invoice_id}/pdf`, { responseType: "blob" });
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${inv.invoice_number}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function billingError(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const d = e?.response?.data?.detail as { message?: string } | string | undefined;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && typeof d.message === "string") return d.message;
  return fallback;
}
