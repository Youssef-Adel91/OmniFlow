/** Public pricing catalogue: fetched from the backend (GET /public/catalog) with a static fallback so the marketing site never shows an empty page. */
import { API_BASE_URL } from "@/lib/api/client";

export interface CatalogPlan { code: string; name_ar: string; description_ar: string | null; contact_sales: boolean; limits: Record<string, number | boolean>; monthly_halalas: number; yearly_halalas: number }
export interface Catalog {
  currency: string; vat_rate_bp: number; trial_days: number;
  plans: CatalogPlan[];
  topup_packs: { code: string; replies: number; price_halalas: number; validity_months: number }[];
  addons: { code: string; name_ar: string; monthly_halalas: number; available: boolean }[];
  fromFallback?: boolean;
}

/** Mirrors the seeded catalogue (migrations 0028/0029; source of the numbers: the owner's pricing study). Used only when the API is unreachable. */
export const FALLBACK_CATALOG: Catalog = {
  currency: "SAR", vat_rate_bp: 1500, trial_days: 14, fromFallback: true,
  plans: [
    { code: "starter", name_ar: "الأساسية", description_ar: null, contact_sales: false, monthly_halalas: 29900, yearly_halalas: 299000,
      limits: { messages_per_month: 1500, seats: 2, whatsapp_numbers: 1, properties: 200, contacts: 1000, campaigns: false } },
    { code: "pro", name_ar: "الاحترافية", description_ar: null, contact_sales: false, monthly_halalas: 79900, yearly_halalas: 799000,
      limits: { messages_per_month: 8000, seats: 5, whatsapp_numbers: 1, properties: 1000, contacts: 10000, campaigns: true } },
    { code: "business", name_ar: "الأعمال", description_ar: null, contact_sales: false, monthly_halalas: 179900, yearly_halalas: 1799000,
      limits: { messages_per_month: 25000, seats: 15, whatsapp_numbers: 2, properties: 10000, contacts: 50000, campaigns: true } },
    { code: "enterprise", name_ar: "المؤسسات", description_ar: null, contact_sales: true, monthly_halalas: 0, yearly_halalas: 0, limits: {} },
  ],
  topup_packs: [
    { code: "replies_5k", replies: 5000, price_halalas: 18900, validity_months: 12 },
    { code: "replies_20k", replies: 20000, price_halalas: 69900, validity_months: 12 },
    { code: "replies_50k", replies: 50000, price_halalas: 149900, validity_months: 12 },
  ],
  addons: [{ code: "seat", name_ar: "مقعد إضافي", monthly_halalas: 4900, available: true }, { code: "whatsapp_number", name_ar: "رقم واتساب إضافي", monthly_halalas: 9900, available: false }],
};

export async function fetchCatalog(fetchImpl: typeof fetch = fetch): Promise<Catalog> {
  try {
    const res = await fetchImpl(`${API_BASE_URL}/public/catalog`, { next: { revalidate: 300 } } as RequestInit);
    if (!res.ok) throw new Error(String(res.status));
    const data = (await res.json()) as Catalog;
    if (!Array.isArray(data.plans) || data.plans.length === 0) throw new Error("empty");
    return data;
  } catch {
    return FALLBACK_CATALOG;
  }
}

/** Riyals with two decimals, Western digits, e.g. 29900 -> "299.00". */
export function sar(halalas: number): string { return (halalas / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
export function sarWhole(halalas: number): string { return (halalas / 100).toLocaleString("en-US", { maximumFractionDigits: 0 }); }
/** Price + VAT total for display ("+ VAT" is shown next to the net figure). */
export function withVat(halalas: number, vatBp: number): number { return halalas + Math.floor((halalas * vatBp + 5000) / 10000); }
/** Yearly saving vs 12 monthly payments, whole percent. */
export function yearlySaving(monthly: number, yearly: number): number { const twelve = monthly * 12; return twelve > 0 && yearly < twelve ? Math.round((1 - yearly / twelve) * 100) : 0; }
