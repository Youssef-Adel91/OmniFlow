/**
 * lib/api/dashboard.ts — Dashboard Summary API Client
 *
 * Endpoint contract (backend — Sprint 14):
 *   GET /dashboard/summary → {
 *     total_conversations, active_conversations, total_customers,
 *     hot_leads_count, reports_sold_count
 *   }
 */
import { apiClient } from "@/lib/api/client";

export interface DashboardSummary {
  total_conversations:  number;
  active_conversations: number;
  total_customers:      number;
  hot_leads_count:      number;
  reports_sold_count:   number;
}

/**
 * Fetch the KPI summary for the current tenant.
 *
 * Defensive: any missing counter is coerced to 0 so a partially-implemented
 * backend response never renders `undefined` in the UI.
 */
export async function fetchDashboardSummary(): Promise<DashboardSummary> {
  const { data } = await apiClient.get<any>("/dashboard/summary");

  const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);

  return {
    total_conversations:  n(data?.total_conversations),
    active_conversations: n(data?.active_conversations),
    total_customers:      n(data?.total_customers),
    hot_leads_count:      n(data?.hot_leads_count),
    reports_sold_count:   n(data?.reports_sold_count),
  };
}
