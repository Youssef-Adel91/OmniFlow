/**
 * lib/api/dashboardAnalytics.ts — GET /dashboard/analytics client.
 *
 * Every number comes from the backend (SQL aggregates, tenant-scoped). Metric
 * definitions live in omniflow-backend/src/shared/services/dashboard_analytics.py.
 */
import { apiClient } from "@/lib/api/client";

export type AnalyticsRange = "7d" | "30d" | "90d" | "custom";

export interface Kpi {
  value: number | null;
  previous: number | null;
  change_pct: number | null;
}

export type KpiKey =
  | "new_conversations" | "new_customers" | "hot_leads"
  | "ai_resolution_rate" | "handoff_rate"
  | "avg_first_response_ai_seconds" | "avg_first_response_human_seconds"
  | "avg_resolution_seconds" | "appointments_booked"
  | "reports_revenue_sar" | "reports_sold";

export interface SeriesPoint {
  date: string;
  conversations: number;
  messages: number;
  channels: Record<string, { conversations: number; messages: number }>;
}

export interface KeyCount { key: string; count: number }

export interface AttentionItem {
  conversation_id?: string | null;
  customer_id?: string;
  channel?: string;
  status?: string;
  name: string;
  phone: string;
  score?: number;
  is_vip?: boolean;
  waiting_seconds?: number;
}

export interface DashboardAnalytics {
  range: { from: string; to: string; days: number; previous_from: string; previous_to: string; timezone: string };
  channel: string | null;
  kpis: Record<KpiKey, Kpi>;
  series: SeriesPoint[];
  funnel: { key: string; label: string; count: number }[];
  heatmap: { matrix: number[][]; max: number; first_day: "sunday" };
  channel_distribution: KeyCount[];
  status_distribution: KeyCount[];
  lead_score_distribution: KeyCount[];
  properties: {
    by_status: KeyCount[]; by_type: KeyCount[]; by_city: KeyCount[];
    top_recommended: unknown[] | null; top_recommended_unavailable_reason: string;
  };
  inventory_gaps: { location: string; requests: number; matching_listings: number }[];
  top_topics: { topic: string; count: number }[];
  topics_note: string;
  attention: {
    sla_minutes: number;
    hot_leads: AttentionItem[];
    awaiting_human: AttentionItem[];
    sla_breached: AttentionItem[];
  };
  generated_at: string;
}

export interface AnalyticsParams {
  range: AnalyticsRange;
  from?: string;
  to?: string;
  channel?: string | null;
  /** Bypass the 60 s server cache (used by live auto-refresh). */
  refresh?: boolean;
}

export async function fetchDashboardAnalytics(p: AnalyticsParams): Promise<DashboardAnalytics> {
  const params: Record<string, string | boolean> = { range: p.range };
  if (p.range === "custom") {
    if (p.from) params.from = p.from;
    if (p.to) params.to = p.to;
  }
  if (p.channel) params.channel = p.channel;
  if (p.refresh) params.refresh = true;
  const { data } = await apiClient.get<DashboardAnalytics>("/dashboard/analytics", { params });
  return data;
}
