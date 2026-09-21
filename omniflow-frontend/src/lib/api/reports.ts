/**
 * lib/api/reports.ts — Paid Reports API Client
 *
 * Endpoint contracts (backend — Sprint 14):
 *   GET /reports        → { items, total, page, page_size }
 *                         query params: customer_id, report_type, date_from, date_to
 *   GET /reports/{id}   → ReportResponse (includes `s3_url`)
 *   GET /reports/analytics/revenue?period=monthly → { items: [{ month, total_revenue, count }] }
 */
import { apiClient } from "@/lib/api/client";

// ── Types ──────────────────────────────────────────────────────────────────

export type ReportType = "market_analysis" | "property_valuation" | "investment" | string;

export type ReportStatus = "pending" | "processing" | "ready" | "failed" | string;

export interface Report {
  id:            string;
  tenant_id?:    string | null;
  customer_id:   string | null;
  customer_name?: string | null;
  report_type:   ReportType;
  status?:       ReportStatus;
  title?:        string | null;
  /** Presigned S3 URL for downloading the generated PDF. */
  s3_url:        string | null;
  price?:        number | null;
  currency?:     string | null;
  created_at?:   string | null;
  updated_at?:   string | null;
}

export interface ReportPage {
  items:     Report[];
  total:     number;
  page:      number;
  page_size: number;
}

export interface ListReportsParams {
  page?:        number;
  page_size?:   number;
  customer_id?: string;
  report_type?: ReportType;
  /** ISO date (YYYY-MM-DD) — inclusive lower bound on `created_at`. */
  date_from?:   string;
  /** ISO date (YYYY-MM-DD) — inclusive upper bound on `created_at`. */
  date_to?:     string;
}

export type RevenuePeriod = "monthly" | "weekly" | "daily" | string;

export interface RevenuePoint {
  /** Bucket label, e.g. "2026-07". */
  month:         string;
  total_revenue: number;
  count:         number;
}

export interface RevenueAnalytics {
  items: RevenuePoint[];
}

// ── Normalisation ──────────────────────────────────────────────────────────

function normalizeReport(raw: any): Report {
  return {
    id:            raw.id ?? raw.report_id ?? "",
    tenant_id:     raw.tenant_id ?? null,
    customer_id:   raw.customer_id ?? null,
    customer_name: raw.customer_name ?? raw.customer?.display_name ?? null,
    report_type:   raw.report_type ?? "unknown",
    status:        raw.status ?? (raw.s3_url ? "ready" : "pending"),
    title:         raw.title ?? null,
    // The contract says `s3_url`; tolerate `url` / `download_url` variants.
    s3_url:        raw.s3_url ?? raw.url ?? raw.download_url ?? null,
    price:         raw.price ?? null,
    currency:      raw.currency ?? "SAR",
    created_at:    raw.created_at ?? null,
    updated_at:    raw.updated_at ?? null,
  };
}

// ── API functions ──────────────────────────────────────────────────────────

/** Fetch a paginated list of purchased/generated reports. */
export async function fetchReports(
  params: ListReportsParams = {},
): Promise<ReportPage> {
  const page      = params.page ?? 1;
  const page_size = params.page_size ?? 20;

  const { data } = await apiClient.get<any>("/reports", {
    params: {
      page,
      page_size,
      customer_id: params.customer_id || undefined,
      report_type: params.report_type || undefined,
      date_from:   params.date_from || undefined,
      date_to:     params.date_to || undefined,
    },
  });

  const rawItems: any[] = Array.isArray(data) ? data : data?.items ?? [];

  return {
    items:     rawItems.map(normalizeReport),
    total:     data?.total ?? rawItems.length,
    page:      data?.page ?? page,
    page_size: data?.page_size ?? data?.limit ?? page_size,
  };
}

/** Fetch a single report (used to obtain a fresh presigned `s3_url`). */
export async function fetchReport(reportId: string): Promise<Report> {
  const { data } = await apiClient.get<any>(`/reports/${reportId}`);
  return normalizeReport(data);
}

/**
 * Fetch **every** report matching the filters by walking the pages.
 * Used by the client-side CSV export so the file contains the full data set
 * rather than just the page currently on screen.
 *
 * A hard cap of 50 pages (5 000 rows) prevents a runaway loop if the backend
 * ever reports an inconsistent `total`.
 */
export async function fetchAllReports(
  params: Omit<ListReportsParams, "page" | "page_size"> = {},
  pageSize = 100,
): Promise<Report[]> {
  const all: Report[] = [];
  const MAX_PAGES = 50;

  for (let page = 1; page <= MAX_PAGES; page++) {
    const result = await fetchReports({ ...params, page, page_size: pageSize });
    all.push(...result.items);

    if (result.items.length < pageSize) break;
    if (all.length >= result.total) break;
  }

  return all;
}

/**
 * Revenue aggregated per period for the analytics chart.
 * Tolerates `period`/`bucket`/`label` as alternative keys for `month`, and
 * `revenue`/`total` for `total_revenue`.
 */
export async function fetchRevenueAnalytics(
  period: RevenuePeriod = "monthly",
): Promise<RevenueAnalytics> {
  const { data } = await apiClient.get<any>("/reports/analytics/revenue", {
    params: { period },
  });

  const rawItems: any[] = Array.isArray(data) ? data : data?.items ?? [];

  return {
    items: rawItems.map((raw: any) => ({
      month: String(raw?.month ?? raw?.period ?? raw?.bucket ?? raw?.label ?? ""),
      total_revenue: Number(raw?.total_revenue ?? raw?.revenue ?? raw?.total ?? 0) || 0,
      count: Number(raw?.count ?? raw?.reports_count ?? 0) || 0,
    })),
  };
}

// ── Labels (bilingual) ─────────────────────────────────────────────────────

export const REPORT_TYPE_LABELS: Record<string, string> = {
  market_analysis:    "تحليل السوق",
  property_valuation: "تقييم عقاري",
  investment:         "دراسة استثمارية",
};

export const REPORT_STATUS_LABELS: Record<string, { ar: string; color: string }> = {
  pending:    { ar: "قيد الانتظار", color: "#8B8FA8" },
  processing: { ar: "قيد الإنشاء",  color: "#52A0E0" },
  ready:      { ar: "جاهز",         color: "#4CAF50" },
  failed:     { ar: "فشل",          color: "#E05252" },
};
