/** lib/api/privacy.ts — PDPL tools (backend: routers/privacy.py). */
import { apiClient } from "@/lib/api/client";

export interface PrivacySummary { customers: number; erased: number; marketing_opt_in: number; processing_restricted: number; retention_note: string }
export interface ConsentEvent { action: string; at: string | null; actor_user_id: string | null; source: string | null }

export const CONSENT_LABEL: Record<string, string> = {
  "customer.opt_in_granted": "منح موافقة التسويق", "customer.opt_out": "سحب الموافقة / إلغاء الاشتراك", "customer.created": "إنشاء السجل",
  "customer.merged": "دمج سجلين", "customer.import_consent": "موافقة عبر الاستيراد",
};

/** Erasure is only allowed after typing the customer's id exactly. */
export function canConfirmErase(typed: string, customerId: string): boolean { return typed.trim() === customerId; }

export async function fetchPrivacySummary(): Promise<PrivacySummary> { return (await apiClient.get<PrivacySummary>("/privacy/summary")).data; }
export async function fetchConsents(customerId: string): Promise<ConsentEvent[]> { return (await apiClient.get<{ items: ConsentEvent[] }>(`/privacy/customers/${customerId}/consents`)).data.items; }
export async function anonymizeCustomer(customerId: string, reason: string): Promise<{ messages_wiped: number; conversations: number }> {
  return (await apiClient.post(`/privacy/customers/${customerId}/anonymize`, { confirm: customerId, reason })).data;
}
export async function exportCustomer(customerId: string): Promise<void> {
  const { data } = await apiClient.get<Blob>(`/privacy/customers/${customerId}/export`, { responseType: "blob" });
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url; a.download = `customer-${customerId}.json`;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}
