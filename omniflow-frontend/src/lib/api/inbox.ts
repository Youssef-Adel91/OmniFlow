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

// ── Shared REST / SSE DTO mapping ──────────────────────────────────────────

const STATUS_MAP: Record<string, Conversation["status"]> = {
  ai_active: "BOT_ACTIVE", bot_active: "BOT_ACTIVE",
  human_active: "AGENT_ACTIVE", agent_active: "AGENT_ACTIVE",
  escalated: "ESCALATED", dormant: "DORMANT", closed: "CLOSED",
};

export function mapConversationPatch(item: Record<string, any>): Partial<Conversation> & { id: string } {
  const patch: Partial<Conversation> & { id: string } = { id: item.id ?? item.conversation_id };
  const fields: Record<string, keyof Conversation> = {
    tenant_id: "tenantId", customer_phone: "customerPhone", customer_name: "customerName",
    channel: "channel", last_message: "lastMessage", last_message_at: "lastMessageAt",
    unread_count: "unreadCount", is_ai_active: "isAiActive", lead_score: "leadScore",
  };
  for (const [source, target] of Object.entries(fields)) {
    if (item[source] !== undefined) (patch as Record<string, unknown>)[target] = item[source];
  }
  if (item.status) patch.status = STATUS_MAP[String(item.status).toLowerCase()] ?? "BOT_ACTIVE";
  return patch;
}

export function mapConversation(item: Record<string, any>): Conversation {
  return {
    tenantId: "", customerPhone: "", customerName: "عميل غير معروف",
    channel: "whatsapp", status: "BOT_ACTIVE", lastMessage: "", lastMessageAt: "",
    unreadCount: 0, isAiActive: true, leadScore: 0,
    ...mapConversationPatch(item),
  };
}

export function mapMessage(item: Record<string, any>): Message {
  return {
    id: item.id ?? item.message_id,
    conversationId: item.conversation_id ?? item.conversationId,
    senderType: item.sender_type ?? item.senderType,
    messageType: item.message_type ?? item.messageType ?? "text",
    text: item.text ?? item.text_content ?? "",
    mediaUrl: item.s3_media_url ?? item.media_url ?? item.mediaUrl,
    createdAt: item.created_at ?? item.createdAt,
    isRead: item.is_read ?? item.isRead ?? false,
    latencyMs: item.latency_ms ?? item.latencyMs,
    deliveryStatus: item.delivery_status ?? item.deliveryStatus,
    tier: item.tier, model: item.model,
  };
}

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
  
  return rawItems.map(mapConversation);
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

  return rawItems.map(mapMessage);
}

export interface PropertyRecommendation {
  title: string;
  price: number;
  area: number;
  district: string;
  score: number;
}

/**
 * Real-estate property suggestions for a conversation (item 10) — semantic
 * search over the tenant's Qdrant-indexed listings, using the conversation's
 * own recent customer messages as the query. Returns [] if the conversation
 * has no customer messages yet or nothing scores above threshold.
 */
export async function fetchRecommendations(
  conversationId: string,
): Promise<PropertyRecommendation[]> {
  const { data } = await apiClient.get<PropertyRecommendation[]>(
    `/conversations/${conversationId}/recommendations`,
  );
  return Array.isArray(data) ? data : [];
}

export interface ConversationNoteDTO {
  note_id: string;
  body: string;
  severity: "info" | "warning";
  created_at: string;
}

/** Add an internal note to a conversation (item 11) — never shown to the customer. */
export async function addConversationNote(
  conversationId: string,
  body: string,
  severity: "info" | "warning" = "warning",
): Promise<ConversationNoteDTO> {
  const { data } = await apiClient.post<ConversationNoteDTO>(
    `/conversations/${conversationId}/notes`,
    { body, severity },
  );
  return data;
}

export interface AppointmentDTO {
  appointment_id: string;
  scheduled_at: string;
  location_note: string | null;
  status: "scheduled" | "completed" | "cancelled";
}

/**
 * Schedule a viewing appointment (item 11). Deliberately minimal — a date +
 * location note, not calendar sync or automated reminders.
 */
export async function scheduleAppointment(
  conversationId: string,
  scheduledAt: string,
  locationNote?: string,
): Promise<AppointmentDTO> {
  const { data } = await apiClient.post<AppointmentDTO>(
    `/conversations/${conversationId}/appointments`,
    { scheduled_at: scheduledAt, location_note: locationNote || undefined },
  );
  return data;
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
  const { data } = await apiClient.post<Record<string, any>>(
    `/conversations/${conversationId}/messages`,
    payload,
  );
  return mapMessage(data);
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
  const { data } = await apiClient.post<Record<string, any>>(
    `/conversations/${conversationId}/takeover`,
  );
  return mapConversation(data);
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
  const { data } = await apiClient.post<Record<string, any>>(
    `/conversations/${conversationId}/return-to-ai`,
  );
  return mapConversation(data);
}
