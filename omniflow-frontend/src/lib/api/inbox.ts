/**
 * lib/api/inbox.ts — Smart Inbox API Service
 *
 * All functions use the singleton Axios instance (apiClient) which
 * automatically attaches:
 *   - Authorization: Bearer <token>
 *   - X-Tenant-ID: <tenantId>
 *   - Content-Type: application/json
 *
 * Endpoint contracts (FastAPI backend — Sprint 12):
 *   GET  /api/v1/conversations                       → ConversationDTO[]
 *   GET  /api/v1/conversations/{id}/messages         → MessageDTO[]
 *   POST /api/v1/conversations/{id}/messages         → MessageDTO
 *   POST /api/v1/conversations/{id}/takeover         → ConversationDTO (status patch)
 *   POST /api/v1/conversations/{id}/return-to-ai     → ConversationDTO (status patch)
 *
 * All DTOs mirror the Zustand store types so the store can use them directly
 * without a mapping layer.
 */

import { apiClient } from "./client";
import type { Conversation, Message } from "@/store/inboxStore";

// ── Response envelope (FastAPI paginated list) ─────────────────────────────

export interface PaginatedResponse<T> {
  items:   T[];
  total:   number;
  offset:  number;
  limit:   number;
}

// ── Request payloads ──────────────────────────────────────────────────────────

export interface SendMessagePayload {
  text:        string;
  senderType?: "human_agent"; // always human_agent when sent from the dashboard
}

// ── Channel + sort_by filter params ──────────────────────────────────────────
export type ChannelFilter = "ALL" | "whatsapp" | "instagram" | "tiktok" | "x" | "web";
export type SortByOption  = "recent" | "hot_leads";

// ── API functions ─────────────────────────────────────────────────────────────

/**
 * Fetch the paginated list of active conversations for the current tenant.
 * The X-Tenant-ID + JWT are injected automatically by the Axios interceptor.
 *
 * @param limit   - Number of conversations per page (default 50)
 * @param offset  - Pagination offset (default 0)
 * @param channel - Optional channel filter ('whatsapp', 'instagram', etc.)
 * @param sort_by - Optional sort: 'recent' | 'hot_leads'
 */
export async function fetchConversations(
  limit = 50,
  offset = 0,
  channel?: ChannelFilter,
  sort_by?: SortByOption,
): Promise<Conversation[]> {
  const params: Record<string, unknown> = { limit, offset };
  if (channel && channel !== "ALL") params.channel = channel;
  if (sort_by)                       params.sort_by = sort_by;

  const { data } = await apiClient.get<any>(
    "/conversations",
    { params },
  );
  
  const rawItems: any[] = Array.isArray(data) ? data : data.items ?? [];
  
  // Map backend ConversationResponse to frontend Conversation interface
  return rawItems.map((item) => ({
    id:            item.id ?? item.conversation_id,
    tenantId:      item.tenant_id,
    customerPhone: item.customer_phone ?? item.customer?.unified_phone ?? "",
    customerName:  item.customer_name ?? item.customer?.display_name ?? item.customer?.whatsapp_profile_name ?? "عميل غير معروف",
    customerAvatar: undefined,
    channel:       item.channel,
    status:        item.status,
    lastMessage:   item.last_message ?? "رسالة جديدة...",
    lastMessageAt: item.last_message_at ?? item.created_at,
    unreadCount:   item.unread_count ?? 0,
    isAiActive:    item.is_ai_active ?? true,
    leadScore:     item.lead_score ?? 0,
  }));
}

/**
 * Fetch all messages for a specific conversation.
 * Called when the user selects a conversation in the ConversationList pane.
 *
 * @param conversationId - UUID of the target conversation
 * @param limit          - Max messages to return (default 100)
 */
export async function fetchMessages(
  conversationId: string,
  limit = 100,
): Promise<Message[]> {
  const { data } = await apiClient.get<any>(
    `/conversations/${conversationId}/messages`,
    { params: { limit } },
  );

  const rawItems: any[] = Array.isArray(data) ? data : data.items ?? [];

  // Map backend MessageResponse to frontend Message interface
  return rawItems.map((item) => ({
    id:             item.id ?? item.message_id,
    conversationId: item.conversation_id,
    senderType:     item.sender_type,
    messageType:    item.message_type,
    text:           item.text ?? item.text_content ?? undefined,
    mediaUrl:       item.s3_media_url || undefined,
    createdAt:      item.created_at,
    isRead:         true, // Backend doesn't track per-message read state yet
    latencyMs:      item.latency_ms || undefined,
  }));
}

/**
 * Send a message from the human agent to the customer.
 * Only valid when the conversation's status is HUMAN_ACTIVE or ESCALATED.
 *
 * @param conversationId - UUID of the target conversation
 * @param text           - Message body text
 * @returns              - The persisted Message record from the backend
 */
export async function sendMessage(
  conversationId: string,
  text: string,
): Promise<Message> {
  const payload: SendMessagePayload = { text, senderType: "human_agent" };
  const { data } = await apiClient.post<Message>(
    `/conversations/${conversationId}/messages`,
    payload,
  );
  return data;
}

/**
 * Escalate: switch the conversation from AI control to a human agent.
 * Backend will update `status → HUMAN_ACTIVE` and `is_ai_active → false`.
 *
 * @param conversationId - UUID of the conversation to take over
 */
export async function takeOverConversation(
  conversationId: string,
): Promise<Conversation> {
  const { data } = await apiClient.post<Conversation>(
    `/conversations/${conversationId}/takeover`,
  );
  return data;
}

/**
 * Return control back to the AI bot.
 * Backend will update `status → AI_ACTIVE` and `is_ai_active → true`.
 *
 * @param conversationId - UUID of the conversation to hand back
 */
export async function returnConversationToAI(
  conversationId: string,
): Promise<Conversation> {
  const { data } = await apiClient.post<Conversation>(
    `/conversations/${conversationId}/return-to-ai`,
  );
  return data;
}
