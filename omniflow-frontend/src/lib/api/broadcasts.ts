/**
 * lib/api/broadcasts.ts — WhatsApp Broadcast Campaigns API Client
 *
 * Endpoint contracts (backend — Sprint 14):
 *   GET  /broadcasts                → { items, total, page, page_size }
 *   POST /broadcasts                → Broadcast
 *   POST /broadcasts/{id}/schedule  → Broadcast   (body: { scheduled_at })
 *   POST /broadcasts/{id}/cancel    → Broadcast
 */
import { apiClient } from "@/lib/api/client";

// ── Types ──────────────────────────────────────────────────────────────────

export type BroadcastStatus =
  | "draft"
  | "scheduled"
  | "sending"
  | "sent"
  | "cancelled"
  | "failed"
  | string;

export type TargetAudience =
  | "all"
  | "all_vip"
  | "villa_seekers"
  | "dormant"
  | string;

export interface Broadcast {
  id:               string;
  tenant_id?:       string | null;
  title:            string;
  message_template: string;
  target_audience:  TargetAudience;
  status:           BroadcastStatus;
  scheduled_at:     string | null;
  recipients_count?: number | null;
  created_at?:      string | null;
  updated_at?:      string | null;
}

export interface BroadcastPage {
  items:     Broadcast[];
  total:     number;
  page:      number;
  page_size: number;
}

export interface BroadcastCreate {
  title:            string;
  message_template: string;
  target_audience:  TargetAudience;
  /** Optional — when present the backend may create it already scheduled. */
  scheduled_at?:    string | null;
}

export interface ListBroadcastsParams {
  page?:      number;
  page_size?: number;
  status?:    BroadcastStatus;
}

// ── Normalisation ──────────────────────────────────────────────────────────

function normalizeBroadcast(raw: any): Broadcast {
  return {
    id:               raw?.id ?? raw?.broadcast_id ?? "",
    tenant_id:        raw?.tenant_id ?? null,
    title:            raw?.title ?? "",
    message_template: raw?.message_template ?? raw?.message ?? "",
    target_audience:  raw?.target_audience ?? "all",
    status:           raw?.status ?? "draft",
    scheduled_at:     raw?.scheduled_at ?? null,
    recipients_count: raw?.recipients_count ?? raw?.recipient_count ?? null,
    created_at:       raw?.created_at ?? null,
    updated_at:       raw?.updated_at ?? null,
  };
}

// ── API functions ──────────────────────────────────────────────────────────

/** Fetch the tenant's broadcast campaigns. */
export async function fetchBroadcasts(
  params: ListBroadcastsParams = {},
): Promise<BroadcastPage> {
  const page      = params.page ?? 1;
  const page_size = params.page_size ?? 20;

  const { data } = await apiClient.get<any>("/broadcasts", {
    params: { page, page_size, status: params.status || undefined },
  });

  const rawItems: any[] = Array.isArray(data) ? data : data?.items ?? [];

  return {
    items:     rawItems.map(normalizeBroadcast),
    total:     data?.total ?? rawItems.length,
    page:      data?.page ?? page,
    page_size: data?.page_size ?? data?.limit ?? page_size,
  };
}

/** Create a new broadcast campaign (created as a draft unless scheduled_at is set). */
export async function createBroadcast(body: BroadcastCreate): Promise<Broadcast> {
  const { data } = await apiClient.post<any>("/broadcasts", body);
  return normalizeBroadcast(data);
}

/**
 * Schedule an existing broadcast.
 * @param scheduledAt ISO-8601 timestamp. Omit to let the backend send ASAP.
 */
export async function scheduleBroadcast(
  broadcastId: string,
  scheduledAt?: string,
): Promise<Broadcast> {
  const { data } = await apiClient.post<any>(
    `/broadcasts/${broadcastId}/schedule`,
    scheduledAt ? { scheduled_at: scheduledAt } : {},
  );
  return normalizeBroadcast(data);
}

/**
 * Estimate how many customers a target audience currently resolves to.
 * Used to show "تقريباً X عميل" next to the audience picker before sending.
 */
export async function previewAudience(
  targetAudience: TargetAudience,
): Promise<number> {
  const { data } = await apiClient.get<any>("/broadcasts/preview-audience", {
    params: { target_audience: targetAudience },
  });
  const raw = data?.estimated_count ?? data?.count ?? data?.total ?? 0;
  const value = Number(raw);
  return Number.isFinite(value) ? value : 0;
}

/** Cancel a scheduled (not yet sent) broadcast. */
export async function cancelBroadcast(broadcastId: string): Promise<Broadcast> {
  const { data } = await apiClient.post<any>(`/broadcasts/${broadcastId}/cancel`);
  return normalizeBroadcast(data);
}

// ── Labels ─────────────────────────────────────────────────────────────────

export const BROADCAST_STATUS_LABELS: Record<string, { ar: string; color: string }> = {
  draft:     { ar: "مسودة",       color: "#8B8FA8" },
  scheduled: { ar: "مجدولة",      color: "#C9A84C" },
  sending:   { ar: "جارٍ الإرسال", color: "#52A0E0" },
  sent:      { ar: "تم الإرسال",  color: "#4CAF50" },
  cancelled: { ar: "ملغاة",       color: "#6B6B6B" },
  failed:    { ar: "فشلت",        color: "#E05252" },
};

export const TARGET_AUDIENCE_OPTIONS: { value: TargetAudience; label: string }[] = [
  { value: "all",           label: "جميع العملاء" },
  { value: "all_vip",       label: "جميع عملاء VIP (المهتمين بالشراء)" },
  { value: "villa_seekers", label: "الباحثين عن فلل شمال الرياض" },
  { value: "dormant",       label: "العملاء الخاملين (منذ 30 يوم)" },
];
