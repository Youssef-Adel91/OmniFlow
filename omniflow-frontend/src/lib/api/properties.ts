/**
 * lib/api/properties.ts — Property Listing API Client
 *
 * Typed wrappers around the authenticated Axios singleton for all
 * CRUD operations on /api/v1/properties.
 *
 * All calls automatically inherit:
 *   - Authorization: Bearer <token>   (from apiClient request interceptor)
 *   - X-Tenant-ID: <tenant_id>        (from apiClient request interceptor)
 *   - Silent 401 refresh              (from apiClient response interceptor)
 */
import { apiClient } from "@/lib/api/client";

// ── TypeScript types (mirrors backend PropertyListingResponse) ──────────────

export type PropertyType =
  | "apartment"
  | "villa"
  | "land"
  | "commercial"
  | "daily_rental"
  | "office"
  | "warehouse";

export type ListingStatus =
  | "PENDING_VERIFICATION"
  | "VERIFIED_ACTIVE"
  | "VERIFICATION_FAILED"
  | "SUSPENDED"
  | "SOLD"
  | "RENTED"
  | "WITHDRAWN";

export interface PropertyListing {
  listing_id: string;
  tenant_id: string;
  rega_ad_number: string;
  property_type: PropertyType;
  status: ListingStatus;
  is_verified: boolean;
  city: string | null;
  district: string | null;
  latitude: number | null;
  longitude: number | null;
  price: number | null;
  area_sqm: number | null;
  bedrooms: number | null;
  bathrooms: number | null;
  description_ar: string | null;
  description_en: string | null;
  qdrant_point_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface PropertyListingPage {
  items: PropertyListing[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

export interface PropertyListingCreate {
  rega_ad_number?: string | null;
  property_type: PropertyType;
  status?: ListingStatus;
  city?: string | null;
  district?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  price?: number | null;
  area_sqm?: number | null;
  bedrooms?: number | null;
  bathrooms?: number | null;
  description_ar?: string | null;
  description_en?: string | null;
}

export type PropertyListingUpdate = Partial<PropertyListingCreate> & {
  is_verified?: boolean;
};

export type PropertySort = "newest" | "oldest" | "price_asc" | "price_desc" | "area_desc";

export interface ListPropertiesParams {
  page?: number;
  limit?: number;
  status?: ListingStatus;
  property_type?: PropertyType;
  city?: string;
  /** REGA number, city, district or description (server-side, wildcard-safe). */
  search?: string;
  price_min?: number;
  price_max?: number;
  sort?: PropertySort;
}

// ── Human-readable labels (bilingual) ─────────────────────────────────────

export const PROPERTY_TYPE_LABELS: Record<PropertyType, { ar: string; en: string }> = {
  apartment:    { ar: "شقة",           en: "Apartment"    },
  villa:        { ar: "فيلا",          en: "Villa"        },
  land:         { ar: "أرض",           en: "Land"         },
  commercial:   { ar: "تجاري",         en: "Commercial"   },
  daily_rental: { ar: "إيجار يومي",   en: "Daily Rental" },
  office:       { ar: "مكتب",          en: "Office"       },
  warehouse:    { ar: "مستودع",        en: "Warehouse"    },
};

export const LISTING_STATUS_LABELS: Record<ListingStatus, { ar: string; en: string; color: string }> = {
  PENDING_VERIFICATION: { ar: "قيد التحقق",    en: "Pending",      color: "#8B8FA8" },
  VERIFIED_ACTIVE:      { ar: "نشط ومعتمد",   en: "Active",       color: "#C9A84C" },
  VERIFICATION_FAILED:  { ar: "فشل التحقق",   en: "Failed",       color: "#E05252" },
  SUSPENDED:            { ar: "موقوف",         en: "Suspended",    color: "#E05252" },
  SOLD:                 { ar: "مباع",          en: "Sold",         color: "#52A0E0" },
  RENTED:               { ar: "مؤجر",          en: "Rented",       color: "#52A0E0" },
  WITHDRAWN:            { ar: "مسحوب",         en: "Withdrawn",    color: "#6B6B6B" },
};

// ── API Functions ──────────────────────────────────────────────────────────

/**
 * Fetch a paginated list of property listings for the current tenant.
 */
export async function listProperties(
  params: ListPropertiesParams = {}
): Promise<PropertyListingPage> {
  const { data } = await apiClient.get<PropertyListingPage>("/properties", {
    params: {
      page:          params.page ?? 1,
      limit:         params.limit ?? 20,
      status:        params.status,
      property_type: params.property_type,
      city:          params.city?.trim() || undefined,
      search:        params.search?.trim() || undefined,
      price_min:     params.price_min,
      price_max:     params.price_max,
      sort:          params.sort,
    },
  });
  return data;
}

export interface BulkResult { requested: number; not_found: number; deleted?: number; updated?: number }

/** Delete up to 500 listings at once. */
export async function bulkDeleteProperties(ids: string[]): Promise<BulkResult> {
  const { data } = await apiClient.post<BulkResult>("/properties/bulk/delete", { ids });
  return data;
}

/** Change the status of up to 500 listings at once. */
export async function bulkSetPropertyStatus(ids: string[], status: ListingStatus): Promise<BulkResult> {
  const { data } = await apiClient.post<BulkResult>("/properties/bulk/status", { ids, status });
  return data;
}

/** Download the CSV for the current filters (server-side; formula-injection safe). */
export async function exportPropertiesCsv(params: Omit<ListPropertiesParams, "page" | "limit"> = {}): Promise<Blob> {
  const { data } = await apiClient.get<Blob>("/properties/export.csv", {
    responseType: "blob",
    params: {
      status: params.status, property_type: params.property_type, city: params.city?.trim() || undefined,
      search: params.search?.trim() || undefined, price_min: params.price_min, price_max: params.price_max, sort: params.sort,
    },
  });
  return data;
}

/**
 * Fetch a single property listing by ID.
 */
export async function getProperty(listingId: string): Promise<PropertyListing> {
  const { data } = await apiClient.get<PropertyListing>(`/properties/${listingId}`);
  return data;
}

/**
 * Create a new property listing.
 * rega_ad_number is optional — backend auto-generates DEV-REGA if absent.
 */
export async function createProperty(
  body: PropertyListingCreate
): Promise<PropertyListing> {
  const { data } = await apiClient.post<PropertyListing>("/properties", body);
  return data;
}

/**
 * Partially update a property listing (PATCH semantics on the backend).
 */
export async function updateProperty(
  listingId: string,
  body: PropertyListingUpdate
): Promise<PropertyListing> {
  const { data } = await apiClient.put<PropertyListing>(
    `/properties/${listingId}`,
    body
  );
  return data;
}

/**
 * Delete a property listing. Returns void on 204.
 */
export async function deleteProperty(listingId: string): Promise<void> {
  await apiClient.delete(`/properties/${listingId}`);
}
