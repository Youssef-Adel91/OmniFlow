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
 *
 * The Clerk bearer token is attached automatically by the apiClient
 * request interceptor.
 */
import { apiClient } from "@/lib/api/client";

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
}

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
  /** 'lead_score' returns the ranked (highest-purchase-likelihood-first) view. */
  sort?:      "created_at" | "lead_score";
}

/** PATCH body — only the three fields the backend accepts. */
export interface CustomerUpdate {
  display_name?:             string;
  is_vip?:                   boolean;
  is_processing_restricted?: boolean;
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
