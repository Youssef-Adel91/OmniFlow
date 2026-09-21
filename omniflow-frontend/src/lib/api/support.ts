/**
 * lib/api/support.ts — Support Tickets API Client
 *
 * Endpoint contracts (backend — Sprint 14):
 *   GET   /support/tickets        → { items, total, page, page_size }
 *   POST  /support/tickets        → SupportTicket   (subject, message)
 *   PATCH /support/tickets/{id}   → SupportTicket   (status)
 */
import { apiClient } from "@/lib/api/client";

// ── Types ──────────────────────────────────────────────────────────────────

export type TicketStatus = "open" | "in_progress" | "closed";

export interface SupportTicket {
  id:          string;
  tenant_id?:  string | null;
  subject:     string;
  message:     string;
  status:      TicketStatus;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SupportTicketPage {
  items:     SupportTicket[];
  total:     number;
  page:      number;
  page_size: number;
}

export interface SupportTicketCreate {
  subject: string;
  message: string;
}

export interface SupportTicketUpdate {
  status: TicketStatus;
}

export interface ListTicketsParams {
  page?:      number;
  page_size?: number;
  status?:    TicketStatus;
}

// ── Normalisation ──────────────────────────────────────────────────────────

function normalizeTicket(raw: any): SupportTicket {
  const status = (raw?.status ?? "open") as TicketStatus;
  return {
    id:         raw?.id ?? raw?.ticket_id ?? "",
    tenant_id:  raw?.tenant_id ?? null,
    subject:    raw?.subject ?? "",
    message:    raw?.message ?? raw?.body ?? "",
    status:     (["open", "in_progress", "closed"] as string[]).includes(status)
      ? status
      : "open",
    created_at: raw?.created_at ?? null,
    updated_at: raw?.updated_at ?? null,
  };
}

// ── API functions ──────────────────────────────────────────────────────────

/** Fetch the tenant's support tickets. */
export async function fetchTickets(
  params: ListTicketsParams = {},
): Promise<SupportTicketPage> {
  const page      = params.page ?? 1;
  const page_size = params.page_size ?? 20;

  const { data } = await apiClient.get<any>("/support/tickets", {
    params: { page, page_size, status: params.status || undefined },
  });

  const rawItems: any[] = Array.isArray(data) ? data : data?.items ?? [];

  return {
    items:     rawItems.map(normalizeTicket),
    total:     data?.total ?? rawItems.length,
    page:      data?.page ?? page,
    page_size: data?.page_size ?? data?.limit ?? page_size,
  };
}

/** Open a new support ticket. */
export async function createTicket(
  body: SupportTicketCreate,
): Promise<SupportTicket> {
  const { data } = await apiClient.post<any>("/support/tickets", body);
  return normalizeTicket(data);
}

/** Update a ticket's status (e.g. close it from the dashboard). */
export async function updateTicket(
  ticketId: string,
  body: SupportTicketUpdate,
): Promise<SupportTicket> {
  const { data } = await apiClient.patch<any>(`/support/tickets/${ticketId}`, body);
  return normalizeTicket(data);
}

// ── Labels ─────────────────────────────────────────────────────────────────

export const TICKET_STATUS_LABELS: Record<TicketStatus, { ar: string; color: string }> = {
  open:        { ar: "مفتوحة",      color: "#C9A84C" },
  in_progress: { ar: "قيد المعالجة", color: "#52A0E0" },
  closed:      { ar: "مغلقة",        color: "#4CAF50" },
};
