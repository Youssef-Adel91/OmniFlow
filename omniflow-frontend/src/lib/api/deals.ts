/**
 * lib/api/deals.ts — Sales pipeline (deals) API client
 *
 *   GET   /deals/board                      → Kanban columns
 *   PATCH /deals/{id}                       → move / close (won | lost + reason) / value
 *   GET   /deals/by-conversation/{id}       → hand-off summary for the inbox panel
 *   GET   /deals/report?days=30             → conversion by channel
 *
 * The whole API answers 404 while FEATURE_SALES_CLOSING is off on the server: callers treat that as "not enabled".
 */
import { apiClient } from "@/lib/api/client";

export type DealStage = "new" | "qualified" | "viewing" | "negotiation" | "won" | "lost";
export type LostReason = "price" | "no_response" | "competitor" | "financing" | "not_interested" | "other";

export const STAGE_LABELS: Record<DealStage, string> = {
  new: "جديد", qualified: "مؤهَّل", viewing: "معاينة", negotiation: "تفاوض", won: "تم البيع", lost: "خسارة",
};
export const LOST_REASON_LABELS: Record<LostReason, string> = {
  price: "السعر", no_response: "لم يرد", competitor: "اختار منافسًا", financing: "التمويل", not_interested: "لم يعد مهتمًا", other: "سبب آخر",
};
export const LABEL_TEXT: Record<string, string> = { hot: "ساخن", warm: "دافئ", cold: "بارد" };

export interface DealCard {
  deal_id: string; stage: DealStage; lead_label: "hot" | "warm" | "cold" | null; score: number; customer_id: string; customer_name: string;
  conversation_id: string | null; channel: string | null; value_sar: number | null; offer_sar: number | null; assigned_user_id: string | null;
  lost_reason: LostReason | null; lost_note: string | null; budget_max: number | null; district: string | null; property_type: string | null;
  updated_at: string | null; stage_changed_at: string | null;
}
export interface Board { stages: DealStage[]; columns: Record<DealStage, { label: string; total: number; cards: DealCard[] }> }

export interface HandoffSummary {
  label: string | null; score: number | null; summary: string; missing: string[];
  deal: DealCard | null;
  appointment: { appointment_id: string; scheduled_at: string; location_note: string | null; source: string | null } | null;
  suggested_listings: { listing_id: string; type_ar: string; district: string | null; price: number | null; bedrooms: number | null }[];
  match_note: string;
}

/** `null` when the feature is not enabled (404). */
export async function fetchBoard(mine = false): Promise<Board | null> {
  try {
    const { data } = await apiClient.get<Board>("/deals/board", { params: { mine } });
    return data;
  } catch (e: any) {
    if (e?.response?.status === 404) return null;
    throw e;
  }
}

export async function moveDeal(dealId: string, body: { stage?: DealStage; lost_reason?: LostReason; lost_note?: string; value_sar?: number }): Promise<DealCard> {
  const { data } = await apiClient.patch<DealCard>(`/deals/${dealId}`, body);
  return data;
}

export async function fetchHandoff(conversationId: string): Promise<HandoffSummary | null> {
  try {
    const { data } = await apiClient.get<HandoffSummary>(`/deals/by-conversation/${conversationId}`);
    return data;
  } catch (e: any) {
    if (e?.response?.status === 404) return null;
    throw e;
  }
}

export function dealErrorMessage(e: any): string {
  const d = e?.response?.data?.detail;
  return (typeof d === "object" && d?.message) || "تعذّر تنفيذ العملية، حاول مرة أخرى.";
}
