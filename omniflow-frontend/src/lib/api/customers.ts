/**
 * lib/api/customers.ts — Customers (CRM) API Client
 *
 * Typed wrappers around the authenticated Axios singleton for
 * /api/v1/customers.
 *
 * Endpoint contracts (backend — Sprint 14):
 *   GET   /customers             → { items, total, page, page_size }
 *   GET   /customers/{id}        → CustomerResponse
 *   PATCH /customers/{id}        → CustomerResponse
 *   POST  /customers             → add manually            (admin/agent)
 *   GET   /customers/{id}/detail → profile + conversations + appointments + reports + activity
 *   POST  /customers/{id}/merge  → fold a duplicate identity into this customer
 *   DELETE /customers/{id}       → permanent delete        (admin only)
 *   GET   /customers/export.csv  → filtered export
 *   Bulk import: lib/api/customerImport.ts (/customers/import/...)
 *
 * The Clerk bearer token is attached automatically by the apiClient
 * request interceptor.
 */
import { apiClient } from "@/lib/api/client";
import { makeImportAdapter } from "@/lib/api/propertyImport";

// ── Types (mirror backend CustomerResponse) ────────────────────────────────

export type LeadTier = "hot" | "warm" | "cold";

export interface Customer {
  id:                       string;
  tenant_id?:               string | null;
  display_name:             string | null;
  unified_phone:            string | null;
  whatsapp_profile_name?:   string | null;
  email?:                   string | null;
  is_vip:                   boolean;
  is_processing_restricted: boolean;
  vcard_state?:             string | null;
  vcard_opened_at?:         string | null;
  /** 0-100 purchase-likelihood score, computed server-side (see lead_scoring.py). */
  lead_score:               number;
  lead_tier:                LeadTier;
  last_interaction_at?:     string | null;
  created_at?:              string | null;
  updated_at?:              string | null;
  // ── identity & CRM ──
  /** the REAL phone number (E.164). `unified_phone` may hold a platform user id for Instagram/Messenger customers. */
  phone_e164:               string | null;
  source:                   CustomerSource;
  channel_user_ids:         Record<string, string>;
  city:                     string | null;
  tags:                     string[];
  notes:                    string | null;
  // ── PDPL marketing consent ──
  marketing_opt_in:         boolean;
  opt_in_source:            string | null;
  opt_in_at:                string | null;
  opt_out_at:               string | null;
  archived_at:              string | null;
}

export type CustomerSource = "whatsapp" | "instagram" | "messenger" | "manual" | "import" | (string & {});

export const SOURCE_LABELS: Record<string, string> = {
  whatsapp: "واتساب", instagram: "إنستجرام", messenger: "ماسنجر", manual: "إضافة يدوية", import: "استيراد",
};

/** Standard paginated envelope used by the customers endpoints. */
export interface CustomerPage {
  items:     Customer[];
  total:     number;
  page:      number;
  page_size: number;
}

export interface ListCustomersParams {
  page?:      number;
  page_size?: number;
  /** Free-text search (name / phone) — ignored by the backend if unsupported. */
  search?:    string;
  is_vip?:    boolean;
  source?:    string;
  tag?:       string;
  city?:      string;
  /** marketing consent: true = opted in, false = not opted in */
  opt_in?:    boolean;
  archived?:  boolean;
  /** 'lead_score' returns the ranked (highest-purchase-likelihood-first) view. */
  sort?:      "created_at" | "lead_score";
}

/** PATCH body — only the three fields the backend accepts. */
export interface CustomerUpdate {
  display_name?:             string;
  is_vip?:                   boolean;
  is_processing_restricted?: boolean;
  email?:                    string;
  city?:                     string;
  tags?:                     string[];
  notes?:                    string;
  /** attach / replace the real phone (409 DUPLICATE_PHONE + existing_customer_id when another customer owns it) */
  phone?:                    string;
  marketing_opt_in?:         boolean;
  /** true = archive, false = restore */
  archived?:                 boolean;
}

export interface CustomerCreate {
  phone:             string;
  display_name?:     string;
  email?:            string;
  city?:             string;
  tags?:             string[];
  notes?:            string;
  is_vip?:           boolean;
  marketing_opt_in?: boolean;
}

// ── Normalisation ──────────────────────────────────────────────────────────

/**
 * The backend may return either a bare array or the paginated envelope, and
 * customer IDs may arrive as `id` or `customer_id`. Normalise both shapes so
 * the UI only ever deals with `CustomerPage`.
 */
function normalizeCustomer(raw: any): Customer {
  return {
    id:                       raw.id ?? raw.customer_id ?? "",
    tenant_id:                raw.tenant_id ?? null,
    display_name:             raw.display_name ?? raw.whatsapp_profile_name ?? null,
    unified_phone:            raw.unified_phone ?? raw.phone ?? raw.phone_number ?? null,
    whatsapp_profile_name:    raw.whatsapp_profile_name ?? null,
    email:                    raw.email ?? null,
    is_vip:                   Boolean(raw.is_vip),
    is_processing_restricted: Boolean(raw.is_processing_restricted),
    vcard_state:              raw.vcard_state ?? null,
    vcard_opened_at:          raw.vcard_opened_at ?? null,
    lead_score:               raw.lead_score ?? 0,
    lead_tier:                (raw.lead_tier as LeadTier) ?? "cold",
    last_interaction_at:      raw.last_interaction_at ?? raw.updated_at ?? null,
    created_at:               raw.created_at ?? null,
    updated_at:               raw.updated_at ?? null,
    phone_e164:               raw.phone_e164 ?? null,
    source:                   raw.source ?? "whatsapp",
    channel_user_ids:         raw.channel_user_ids ?? {},
    city:                     raw.city ?? null,
    tags:                     Array.isArray(raw.tags) ? raw.tags : [],
    notes:                    raw.notes ?? null,
    marketing_opt_in:         Boolean(raw.marketing_opt_in),
    opt_in_source:            raw.opt_in_source ?? null,
    opt_in_at:                raw.opt_in_at ?? null,
    opt_out_at:               raw.opt_out_at ?? null,
    archived_at:              raw.archived_at ?? null,
  };
}

function normalizePage(data: any, fallbackPage: number, fallbackSize: number): CustomerPage {
  const rawItems: any[] = Array.isArray(data) ? data : data?.items ?? [];
  return {
    items:     rawItems.map(normalizeCustomer),
    total:     data?.total ?? rawItems.length,
    page:      data?.page ?? fallbackPage,
    page_size: data?.page_size ?? data?.limit ?? fallbackSize,
  };
}

// ── API functions ──────────────────────────────────────────────────────────

/** Fetch a paginated list of customers for the current tenant. */
export async function fetchCustomers(
  params: ListCustomersParams = {},
): Promise<CustomerPage> {
  const page      = params.page ?? 1;
  const page_size = params.page_size ?? 20;

  const { data } = await apiClient.get<any>("/customers", {
    params: {
      page,
      page_size,
      search: params.search || undefined,
      is_vip: params.is_vip,
      source: params.source || undefined,
      tag: params.tag?.trim() || undefined,
      city: params.city?.trim() || undefined,
      opt_in: params.opt_in,
      archived: params.archived || undefined,
      sort: params.sort,
    },
  });

  return normalizePage(data, page, page_size);
}

/** Fetch a single customer by ID. */
export async function fetchCustomer(customerId: string): Promise<Customer> {
  const { data } = await apiClient.get<any>(`/customers/${customerId}`);
  return normalizeCustomer(data);
}

/** Partially update a customer (display name / VIP / processing restriction). */
export async function updateCustomer(
  customerId: string,
  body: CustomerUpdate,
): Promise<Customer> {
  const { data } = await apiClient.patch<any>(`/customers/${customerId}`, body);
  return normalizeCustomer(data);
}

/** Add a customer manually. The phone is normalised server-side (0501234567 → +966501234567). */
export async function createCustomer(body: CustomerCreate): Promise<Customer> {
  const { data } = await apiClient.post<any>("/customers", body);
  return normalizeCustomer(data);
}

/** Fold a duplicate identity (`sourceId`) into `customerId`: conversations move, identities and tags merge, opt-out wins. */
export async function mergeCustomers(customerId: string, sourceId: string): Promise<Customer> {
  const { data } = await apiClient.post<any>(`/customers/${customerId}/merge`, { source_customer_id: sourceId });
  return normalizeCustomer(data);
}

/** Permanently delete (admin only; PDPL erasure). Prefer archiving. */
export async function deleteCustomer(customerId: string): Promise<void> {
  await apiClient.delete(`/customers/${customerId}`);
}

export interface CustomerDetail {
  customer: Customer;
  conversations: { conversation_id: string; channel: string; status: string; message_count: number; last_message_at: string | null; created_at: string | null }[];
  appointments:  { appointment_id: string; scheduled_at: string; status: string; location_note: string | null }[];
  reports:       { report_id: string; report_type: string; price_sar: number | null; is_delivered: boolean; created_at: string | null }[];
  activity:      { action: string; details: Record<string, unknown> | null; at: string | null; actor_user_id: string | null }[];
}

export async function fetchCustomerDetail(customerId: string): Promise<CustomerDetail> {
  const { data } = await apiClient.get<any>(`/customers/${customerId}/detail`);
  return { ...data, customer: normalizeCustomer(data.customer) };
}

/** Server-side CSV of the same filters the list uses. */
export async function exportCustomersCsv(params: Omit<ListCustomersParams, "page" | "page_size" | "sort"> = {}): Promise<Blob> {
  const { data } = await apiClient.get<Blob>("/customers/export.csv", {
    responseType: "blob",
    params: {
      search: params.search?.trim() || undefined, source: params.source || undefined, tag: params.tag?.trim() || undefined,
      city: params.city?.trim() || undefined, opt_in: params.opt_in, archived: params.archived || undefined,
      status: params.is_vip ? "vip" : undefined,
    },
  });
  return data;
}

/** The shared import wizard, pointed at the customers importer. */
export const customersImportAdapter = makeImportAdapter("customers", "/customers/import", "omniflow-customers-template.xlsx");

/** Arabic message for the structured errors the customers endpoints send ({detail: {code, message, existing_customer_id?}}). */
export function customerErrorInfo(err: unknown, fallback = "حدث خطأ غير متوقع"): { message: string; code?: string; existingId?: string } {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && "message" in detail) {
    const d = detail as { message: unknown; code?: string; existing_customer_id?: string };
    return { message: String(d.message), code: d.code, existingId: d.existing_customer_id };
  }
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail) && detail[0]?.msg) return { message: String(detail[0].msg) };
  return { message: (err as Error)?.message || fallback };
}
