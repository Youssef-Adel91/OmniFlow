/**
 * lib/api/knowledge.ts — Knowledge Base API Client
 *
 * The Knowledge Base is where the tenant "teaches" the AI everything about
 * their company: structured profile fields + uploaded documents that get
 * chunked and indexed for RAG retrieval.
 *
 * Endpoint contracts (backend — Sprint 15):
 *   GET    /knowledge/profile                    → CompanyProfile
 *   PATCH  /knowledge/profile                    → CompanyProfile (partial)
 *   GET    /knowledge/documents                  → { items, total }
 *   POST   /knowledge/documents                  → KnowledgeDocument  (multipart: file, title?)
 *   DELETE /knowledge/documents/{id}             → 204
 *   POST   /knowledge/documents/{id}/reindex     → KnowledgeDocument
 *
 * All fields on the profile are nullable — a freshly-provisioned tenant has an
 * empty profile and the UI must render gracefully in that state.
 */
import { apiClient } from "@/lib/api/client";

// ── Types ──────────────────────────────────────────────────────────────────

export interface FaqItem {
  question: string;
  answer:   string;
}

export interface CompanyProfile {
  business_description:  string | null;
  services_offered:      string | null;
  /** Free-form list of geographic areas the tenant operates in. */
  target_areas:          string[];
  pricing_policy:        string | null;
  working_hours:         string | null;
  contact_phone:         string | null;
  contact_email:         string | null;
  contact_address:       string | null;
  /** Map of platform → URL (e.g. { instagram: "https://..." }). */
  social_links:          Record<string, string>;
  unique_selling_points: string | null;
  policies_text:         string | null;
  faq:                   FaqItem[];
  updated_at:            string | null;
}

/** Partial update payload — only send the keys the user actually changed. */
export type CompanyProfileUpdate = Partial<Omit<CompanyProfile, "updated_at">>;

export type DocumentStatus =
  | "uploaded"
  | "processing"
  | "indexed"
  | "failed"
  | string;

export interface KnowledgeDocument {
  document_id:       string;
  title:             string | null;
  original_filename: string | null;
  file_type:         string | null;
  file_size_bytes:   number | null;
  status:            DocumentStatus;
  chunk_count:       number | null;
  error_message:     string | null;
  created_at:        string | null;
  indexed_at:        string | null;
}

export interface KnowledgeDocumentList {
  items: KnowledgeDocument[];
  total: number;
}

// ── Normalisation ──────────────────────────────────────────────────────────

/**
 * `target_areas` is expected as a JSON array. Tolerate a comma-separated
 * string in case the backend stores it as free text.
 */
function normalizeStringList(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw.map((v) => String(v).trim()).filter(Boolean);
  }
  if (typeof raw === "string" && raw.trim()) {
    return raw
      .split(/[,\n،]/)
      .map((v) => v.trim())
      .filter(Boolean);
  }
  return [];
}

function normalizeFaq(raw: unknown): FaqItem[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry: any) => ({
      question: String(entry?.question ?? entry?.q ?? "").trim(),
      answer:   String(entry?.answer ?? entry?.a ?? "").trim(),
    }))
    .filter((f) => f.question || f.answer);
}

function normalizeSocialLinks(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (value == null) continue;
    const str = String(value).trim();
    if (str) out[key] = str;
  }
  return out;
}

export function normalizeProfile(raw: any): CompanyProfile {
  return {
    business_description:  raw?.business_description ?? null,
    services_offered:      raw?.services_offered ?? null,
    target_areas:          normalizeStringList(raw?.target_areas),
    pricing_policy:        raw?.pricing_policy ?? null,
    working_hours:         raw?.working_hours ?? null,
    contact_phone:         raw?.contact_phone ?? null,
    contact_email:         raw?.contact_email ?? null,
    contact_address:       raw?.contact_address ?? null,
    social_links:          normalizeSocialLinks(raw?.social_links),
    unique_selling_points: raw?.unique_selling_points ?? null,
    policies_text:         raw?.policies_text ?? null,
    faq:                   normalizeFaq(raw?.faq),
    updated_at:            raw?.updated_at ?? null,
  };
}

function normalizeDocument(raw: any): KnowledgeDocument {
  return {
    document_id:       raw?.document_id ?? raw?.id ?? "",
    title:             raw?.title ?? null,
    original_filename: raw?.original_filename ?? raw?.filename ?? null,
    file_type:         raw?.file_type ?? raw?.content_type ?? null,
    file_size_bytes:
      typeof raw?.file_size_bytes === "number"
        ? raw.file_size_bytes
        : typeof raw?.size === "number"
          ? raw.size
          : null,
    status:            raw?.status ?? "uploaded",
    chunk_count:
      typeof raw?.chunk_count === "number" ? raw.chunk_count : null,
    error_message:     raw?.error_message ?? null,
    created_at:        raw?.created_at ?? null,
    indexed_at:        raw?.indexed_at ?? null,
  };
}

// ── API functions ──────────────────────────────────────────────────────────

/** Fetch the tenant's company knowledge profile. */
export async function fetchCompanyProfile(): Promise<CompanyProfile> {
  const { data } = await apiClient.get<any>("/knowledge/profile");
  return normalizeProfile(data);
}

/** Partially update the company knowledge profile. */
export async function updateCompanyProfile(
  body: CompanyProfileUpdate,
): Promise<CompanyProfile> {
  const { data } = await apiClient.patch<any>("/knowledge/profile", body);
  return normalizeProfile(data);
}

/** List the uploaded knowledge documents. */
export async function fetchKnowledgeDocuments(): Promise<KnowledgeDocumentList> {
  const { data } = await apiClient.get<any>("/knowledge/documents");
  const rawItems: any[] = Array.isArray(data) ? data : data?.items ?? [];
  return {
    items: rawItems.map(normalizeDocument),
    total: data?.total ?? rawItems.length,
  };
}

/**
 * Upload a document to the knowledge base.
 * The backend accepts the binary under the `file` field plus an optional
 * `title`, and returns the document with `status = "processing"`.
 */
export async function uploadKnowledgeDocument(
  file: File,
  title?: string,
): Promise<KnowledgeDocument> {
  const form = new FormData();
  form.append("file", file);
  if (title && title.trim()) form.append("title", title.trim());

  const { data } = await apiClient.post<any>("/knowledge/documents", form, {
    headers: { "Content-Type": "multipart/form-data" },
    // Large PDFs can take a while to upload on a slow connection.
    timeout: 120_000,
  });
  return normalizeDocument(data);
}

/** Permanently delete a document (and its indexed chunks). */
export async function deleteKnowledgeDocument(documentId: string): Promise<void> {
  await apiClient.delete(`/knowledge/documents/${documentId}`);
}

/** Re-run chunking + embedding for a document (used after a failure). */
export async function reindexKnowledgeDocument(
  documentId: string,
): Promise<KnowledgeDocument> {
  const { data } = await apiClient.post<any>(
    `/knowledge/documents/${documentId}/reindex`,
  );
  return normalizeDocument(data);
}

// ── Labels ─────────────────────────────────────────────────────────────────

export const DOCUMENT_STATUS_LABELS: Record<
  string,
  { ar: string; color: string }
> = {
  uploaded:   { ar: "تم الرفع",      color: "#8B8FA8" },
  processing: { ar: "قيد المعالجة",  color: "#52A0E0" },
  indexed:    { ar: "مفهرس وجاهز",   color: "#4CAF50" },
  failed:     { ar: "فشل",           color: "#E05252" },
};

/** Extensions the backend is expected to accept for indexing. */
export const ACCEPTED_DOCUMENT_TYPES = ".pdf,.doc,.docx,.txt,.md,.csv,.xlsx";

/** Human-readable file size (Arabic locale). */
export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
