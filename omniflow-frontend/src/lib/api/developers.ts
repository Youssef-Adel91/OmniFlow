/**
 * lib/api/developers.ts — Settings → Developers (backend: routers/developers.py)
 *
 *   GET /developers/overview · POST|DELETE /developers/api-keys · GET /developers/api-keys/{id}/usage
 *   POST|PATCH|DELETE /developers/webhooks · POST …/rotate-secret · POST …/test · GET …/deliveries
 */
import { apiClient } from "@/lib/api/client";

export interface ApiKeyRow { key_id: string; name: string; prefix: string; created_at: string | null; last_used_at: string | null; revoked_at: string | null }
export interface WebhookRow { endpoint_id: string; url: string; description: string | null; events: string[]; active: boolean; consecutive_failures: number; disabled_reason: string | null; created_at: string | null }
export interface DeliveryRow { delivery_id: string; event_id: string; event_type: string; status: "pending" | "delivered" | "failed"; attempts: number; last_status_code: number | null; last_error: string | null; next_attempt_at: string | null; created_at: string | null; delivered_at: string | null }
export interface DevOverview {
  api_keys: ApiKeyRow[]; webhooks: WebhookRow[]; events: string[];
  usage: { last_24h: number; last_7d: number };
  limits: { max_active_keys: number; max_webhooks: number; rate_limit_per_minute: number };
}

export const EVENT_LABELS: Record<string, string> = {
  "conversation.created": "محادثة جديدة",
  "conversation.handoff": "تحويل محادثة إلى موظف",
  "customer.created": "عميل جديد",
};

export async function fetchOverview(): Promise<DevOverview> {
  return (await apiClient.get<DevOverview>("/developers/overview")).data;
}
export async function createApiKey(name: string): Promise<ApiKeyRow & { key: string }> {
  return (await apiClient.post("/developers/api-keys", { name })).data;
}
export async function revokeApiKey(id: string): Promise<void> { await apiClient.delete(`/developers/api-keys/${id}`); }
export async function createWebhook(body: { url: string; events: string[]; description?: string }): Promise<WebhookRow & { secret: string }> {
  return (await apiClient.post("/developers/webhooks", body)).data;
}
export async function updateWebhook(id: string, body: Partial<{ url: string; events: string[]; description: string; active: boolean }>): Promise<WebhookRow> {
  return (await apiClient.patch(`/developers/webhooks/${id}`, body)).data;
}
export async function deleteWebhook(id: string): Promise<void> { await apiClient.delete(`/developers/webhooks/${id}`); }
export async function rotateSecret(id: string): Promise<{ secret: string }> { return (await apiClient.post(`/developers/webhooks/${id}/rotate-secret`)).data; }
export async function testWebhook(id: string): Promise<DeliveryRow> { return (await apiClient.post(`/developers/webhooks/${id}/test`)).data; }
export async function fetchDeliveries(id: string): Promise<DeliveryRow[]> {
  return (await apiClient.get<{ items: DeliveryRow[] }>(`/developers/webhooks/${id}/deliveries`)).data.items;
}

/** Human-readable outcome of a delivery for the log table. */
export function deliveryLabel(d: Pick<DeliveryRow, "status" | "last_status_code" | "last_error" | "attempts">): string {
  if (d.status === "delivered") return `تم التسليم (HTTP ${d.last_status_code ?? "—"})`;
  if (d.status === "failed") return `فشل نهائيًا بعد ${d.attempts} محاولات${d.last_error ? ` — ${d.last_error}` : ""}`;
  return d.attempts ? `سيعاد المحاولة (${d.last_error ?? `HTTP ${d.last_status_code}`})` : "في الانتظار";
}
