/**
 * lib/api/support.ts — Support tickets, threads, attachments, help center (backend: routers/support.py)
 *
 *   GET   /support/meta · /support/kb/search · /support/status
 *   GET   /support/tickets          POST /support/tickets
 *   GET   /support/tickets/{id}     PATCH (open|closed)
 *   POST  /support/tickets/{id}/messages · /attachments
 *   GET   /support/attachments/{id} (blob)
 */
import { apiClient } from "@/lib/api/client";

export type TicketStatus = "open" | "in_progress" | "closed";

export interface SupportTicket {
  id: string;
  reference: string;
  subject: string;
  message: string;
  status: TicketStatus;
  category: string;
  priority: string;
  first_response_at: string | null;
  closed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SupportTicketPage { items: SupportTicket[]; total: number; page: number; page_size: number }
export interface ThreadMessage { id: string; author_type: "customer" | "support" | "system"; kind: "message" | "status"; body: string; created_at: string }
export interface Attachment { id: string; filename: string; content_type: string; size_bytes: number; created_at: string }
export interface TicketDetail { ticket: SupportTicket; messages: ThreadMessage[]; attachments: Attachment[] }
export interface SupportMeta {
  categories: { id: string; label: string }[];
  priorities: { id: string; label: string; target_hours: number }[];
  contacts: { whatsapp: string | null; zoom: string | null; docs: string | null };
  attachments: { max_bytes: number; max_per_ticket: number; kinds: string[] };
}
export interface KbArticle { id: string; title: string; answer: string; category: string; link: string | null; link_label: string | null }
export interface PlatformStatus { checks: Record<string, string>; all_ok: boolean; checked_at: string }

const STATUSES = ["open", "in_progress", "closed"];

/* eslint-disable @typescript-eslint/no-explicit-any */
export function normalizeTicket(raw: any): SupportTicket {
  const status = STATUSES.includes(raw?.status) ? raw.status : "open";
  return {
    id: raw?.ticket_id ?? raw?.id ?? "",
    reference: raw?.reference ?? "",
    subject: raw?.subject ?? "",
    message: raw?.message ?? "",
    status,
    category: raw?.category ?? "other",
    priority: raw?.priority ?? "normal",
    first_response_at: raw?.first_response_at ?? null,
    closed_at: raw?.closed_at ?? null,
    created_at: raw?.created_at ?? null,
    updated_at: raw?.updated_at ?? null,
  };
}
const normMessage = (m: any): ThreadMessage => ({ id: m.message_id ?? m.id, author_type: m.author_type, kind: m.kind ?? "message", body: m.body ?? "", created_at: m.created_at });
const normAttachment = (a: any): Attachment => ({ id: a.attachment_id ?? a.id, filename: a.filename, content_type: a.content_type, size_bytes: a.size_bytes, created_at: a.created_at });

// ── Pure helpers (unit-tested) ─────────────────────────────────────────────

export const TICKET_STATUS_LABELS: Record<TicketStatus, { ar: string; tone: "gold" | "blue" | "green" }> = {
  open: { ar: "مفتوحة", tone: "gold" },
  in_progress: { ar: "قيد المعالجة", tone: "blue" },
  closed: { ar: "مغلقة", tone: "green" },
};

/** Human wording for a response-time target ("خلال 4 ساعات"); it is a target, never a promise. */
export function formatTarget(hours: number): string {
  if (hours <= 0) return "";
  if (hours % 24 === 0) return hours === 24 ? "خلال يوم" : hours === 48 ? "خلال يومين" : `خلال ${hours / 24} أيام`;
  return hours === 1 ? "خلال ساعة" : hours === 2 ? "خلال ساعتين" : hours <= 10 ? `خلال ${hours} ساعات` : `خلال ${hours} ساعة`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} بايت`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} ك.ب`;
  return `${(n / 1024 / 1024).toFixed(1)} م.ب`;
}

const ALLOWED_EXT = ["png", "jpg", "jpeg", "webp", "pdf", "docx", "xlsx", "csv"];

/** Client-side pre-check mirroring the server (which re-validates the real content). Returns an Arabic error or null. */
export function validateAttachment(file: { name: string; size: number }, existing: number, meta?: Pick<SupportMeta["attachments"], "max_bytes" | "max_per_ticket">): string | null {
  const max = meta?.max_bytes ?? 5 * 1024 * 1024;
  const limit = meta?.max_per_ticket ?? 5;
  if (existing >= limit) return `الحد الأقصى ${limit} ملفات لكل تذكرة.`;
  if (file.size <= 0) return "الملف فارغ.";
  if (file.size > max) return `حجم «${file.name}» يتجاوز ${Math.round(max / 1024 / 1024)} ميجابايت.`;
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  if (!ALLOWED_EXT.includes(ext)) return `نوع «${file.name}» غير مسموح. المسموح: صور، PDF، Word، Excel، CSV.`;
  return null;
}

export function apiErrorMessage(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const d = e?.response?.data?.detail as { message?: string } | string | undefined;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && typeof d.message === "string") return d.message;
  return fallback;
}

// ── API ───────────────────────────────────────────────────────────────────

export async function fetchMeta(): Promise<SupportMeta> {
  const { data } = await apiClient.get<SupportMeta>("/support/meta");
  return data;
}

export async function searchKb(q: string): Promise<KbArticle[]> {
  const { data } = await apiClient.get<{ items: KbArticle[] }>("/support/kb/search", { params: { q } });
  return data.items ?? [];
}

export async function fetchKb(): Promise<KbArticle[]> {
  const { data } = await apiClient.get<{ items: KbArticle[] }>("/support/kb");
  return data.items ?? [];
}

export async function fetchPlatformStatus(): Promise<PlatformStatus> {
  const { data } = await apiClient.get<PlatformStatus>("/support/status");
  return data;
}

export interface ListTicketsParams { page?: number; page_size?: number; status?: TicketStatus | ""; category?: string; priority?: string }

export async function fetchTickets(params: ListTicketsParams = {}): Promise<SupportTicketPage> {
  const page = params.page ?? 1;
  const page_size = params.page_size ?? 20;
  const { data } = await apiClient.get<any>("/support/tickets", {
    params: { page, page_size, status: params.status || undefined, category: params.category || undefined, priority: params.priority || undefined },
  });
  const raw: any[] = Array.isArray(data) ? data : data?.items ?? [];
  return { items: raw.map(normalizeTicket), total: data?.total ?? raw.length, page: data?.page ?? page, page_size: data?.page_size ?? page_size };
}

export interface SupportTicketCreate { subject: string; message: string; category: string; priority: string }

export async function createTicket(body: SupportTicketCreate): Promise<SupportTicket> {
  const { data } = await apiClient.post<any>("/support/tickets", body);
  return normalizeTicket(data);
}

export async function fetchTicket(id: string): Promise<TicketDetail> {
  const { data } = await apiClient.get<any>(`/support/tickets/${id}`);
  return { ticket: normalizeTicket(data.ticket), messages: (data.messages ?? []).map(normMessage), attachments: (data.attachments ?? []).map(normAttachment) };
}

export async function sendMessage(id: string, body: string): Promise<ThreadMessage> {
  const { data } = await apiClient.post<any>(`/support/tickets/${id}/messages`, { body });
  return normMessage(data);
}

export async function updateTicket(id: string, status: "open" | "closed"): Promise<SupportTicket> {
  const { data } = await apiClient.patch<any>(`/support/tickets/${id}`, { status });
  return normalizeTicket(data);
}

export async function uploadAttachment(id: string, file: File): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<any>(`/support/tickets/${id}/attachments`, form, { headers: { "Content-Type": "multipart/form-data" }, timeout: 60_000 });
  return normAttachment(data);
}

export async function downloadAttachment(a: Attachment): Promise<void> {
  const { data } = await apiClient.get<Blob>(`/support/attachments/${a.id}`, { responseType: "blob" });
  const url = URL.createObjectURL(data);
  const link = document.createElement("a");
  link.href = url;
  link.download = a.filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
