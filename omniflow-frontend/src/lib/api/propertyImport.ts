/**
 * lib/api/propertyImport.ts — bulk property import client
 * (backend: gateway/routers/property_import.py, prefix /properties/import).
 */
import { apiClient } from "@/lib/api/client";

export type ImportStatus =
  | "uploaded" | "validated" | "queued" | "running" | "completed" | "failed" | "cancelled";

export interface ImportField { name: string; label: string; required: boolean; example: string }
export interface MappingSuggestion { field: string; header: string | null; confidence: number; source: "auto" | "llm" | "template" | "manual" }

export interface UploadResult {
  import_id: string;
  file_kind: string;
  filename: string;
  total_rows: number;
  detected_columns: string[];
  suggested_mapping: MappingSuggestion[];
  fields: ImportField[];
  sample_rows: Record<string, string>[];
  extracted_by_llm: boolean;
  notes: string[];
  limits: { max_file_mb: number; max_rows: number };
  /** only the admin role may activate listings without REGA verification */
  can_activate: boolean;
}

export type OnDuplicate = "skip" | "update" | "create_new";
export interface ImportOptions {
  on_duplicate: OnDuplicate;
  defaults: Partial<Record<"property_type" | "status" | "city" | "district", string>>;
  /** admin only: mark imported listings VERIFIED_ACTIVE (is_verified stays false) and index them now */
  activate?: boolean;
  // ── customers import only ──
  /** tags added to every imported customer */
  tags?: string[];
  /** the importer declares they hold the customers' consent to be contacted (PDPL); nothing is opted in without it */
  consent_confirmed?: boolean;
  /** mark every imported customer without an explicit consent column value as opted in (needs consent_confirmed) */
  opt_in_all?: boolean;
}

export interface RowIssue { field: string; message: string; severity: "error" | "warning" }
export interface PreviewRow { row: number; outcome: "create" | "update" | "skip" | "error"; values: Record<string, unknown>; issues: RowIssue[] }
export interface ValidationCounts {
  total: number; valid: number; with_warnings: number; errors: number; duplicate_in_file: number;
  existing_in_db: number; will_create: number; will_update: number; will_skip: number;
  /** customers import: rows recorded as marketing opt-in / asked for it but already opted out (ignored) */
  will_opt_in?: number; opted_out_ignored?: number;
}
export interface ValidationSummary {
  counts: ValidationCounts;
  preview: PreviewRow[];
  issues: (RowIssue & { row: number })[];
  ignored_columns: string[];
  required_missing: string[];
  extracted_by_llm: boolean;
  consent?: { asked: boolean; confirmed: boolean; blocked: boolean };
}

export interface ImportJob {
  import_id: string; kind: string; status: ImportStatus; filename: string; file_kind: string;
  total_rows: number; processed_rows: number; percent: number;
  created: number; updated: number; skipped: number; failed: number; indexed: number;
  index_failed: number; index_error: string | null;
  has_error_report: boolean; error_message: string | null; cancel_requested: boolean;
  options: ImportOptions | null; mapping: Record<string, string> | null; summary: (ValidationSummary & { result?: { opted_in?: number; opted_out?: number } }) | null;
  created_at: string | null; started_at: string | null; finished_at: string | null;
}

export interface MappingTemplate { template_id: string; name: string; mapping: Record<string, string> }

/** Shown when imported listings will be "pending verification": the assistant only sees approved (VERIFIED_ACTIVE) ones. */
export const PENDING_WARNING = "العقارات قيد التحقق لن يراها المساعد حتى يتم اعتمادها";
export const ACTIVATE_WARNING =
  "لم يتم التحقق من هذه العقارات عبر الهيئة العامة للعقار (REGA). بتفعيلها فورًا ستصبح مرئية للمساعد وللعملاء وتتحمّل أنت مسؤولية صحة بياناتها. سيُسجَّل هذا الإجراء باسمك.";

export function showPendingWarning(options: Pick<ImportOptions, "defaults" | "activate">): boolean {
  if (options.activate) return false;
  const st = options.defaults.status;
  return !st || st === "PENDING_VERIFICATION";
}

/** How many listings the job should have indexed (created + updated, or every row of a re-index job) and how many were. */
export function indexTotals(job: Pick<ImportJob, "kind" | "created" | "updated" | "indexed" | "index_failed" | "total_rows">) {
  const expected = job.kind === "reindex" ? job.total_rows : job.created + job.updated;
  return { expected, indexed: job.indexed, failed: job.index_failed, missing: Math.max(0, expected - job.indexed) };
}

export const ACTIVE_STATUSES: ImportStatus[] = ["queued", "running"];
export const TERMINAL_STATUSES: ImportStatus[] = ["completed", "failed", "cancelled"];

export interface ImportAdapter {
  kind: "properties" | "customers";
  upload: (file: File, onProgress?: (pct: number) => void) => Promise<UploadResult>;
  validate: (importId: string, body: { mapping: Record<string, string | null>; options: ImportOptions; save_template_as?: string }) =>
    Promise<ValidationSummary & { status: ImportStatus; mapping: Record<string, string>; options: ImportOptions }>;
  commit: (importId: string) => Promise<ImportJob>;
  fetch: (importId: string) => Promise<ImportJob>;
  cancel: (importId: string) => Promise<ImportJob>;
  listTemplates: () => Promise<MappingTemplate[]>;
  deleteTemplate: (templateId: string) => Promise<void>;
  downloadTemplate: () => Promise<void>;
  downloadErrors: (importId: string) => Promise<void>;
}

/** One client for every importer: same endpoints under a different prefix (backend: services/property_import.py, customer_import.py). */
export function makeImportAdapter(kind: ImportAdapter["kind"], base: string, templateFile: string): ImportAdapter {
  return {
    kind,
    async upload(file, onProgress) {
      const form = new FormData();
      form.append("file", file);
      const { data } = await apiClient.post<UploadResult>(`${base}/upload`, form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 180_000,    // PDF/image extraction by the model can take a while
        onUploadProgress: (e) => e.total && onProgress?.(Math.round((e.loaded / e.total) * 100)),
      });
      return data;
    },
    async validate(importId, body) {
      const { data } = await apiClient.post(`${base}/${importId}/validate`, body, { timeout: 120_000 });
      return data;
    },
    async commit(importId) {
      const { data } = await apiClient.post<ImportJob>(`${base}/${importId}/commit`);
      return data;
    },
    async fetch(importId) {
      const { data } = await apiClient.get<ImportJob>(`${base}/${importId}`);
      return data;
    },
    async cancel(importId) {
      const { data } = await apiClient.post<ImportJob>(`${base}/${importId}/cancel`);
      return data;
    },
    async listTemplates() {
      const { data } = await apiClient.get<{ items: MappingTemplate[] }>(`${base}/templates`);
      return data.items;
    },
    async deleteTemplate(templateId) {
      await apiClient.delete(`${base}/templates/${templateId}`);
    },
    downloadTemplate: () => download(`${base}/template`, templateFile),
    downloadErrors: (importId) => download(`${base}/${importId}/errors.csv`, `import-${importId}-errors.csv`),
  };
}

export const propertiesAdapter = makeImportAdapter("properties", "/properties/import", "omniflow-properties-template.xlsx");

export const uploadImportFile = propertiesAdapter.upload;
export const validateImport = propertiesAdapter.validate;
export const commitImport = propertiesAdapter.commit;
export const fetchImport = propertiesAdapter.fetch;
export const cancelImport = propertiesAdapter.cancel;
export const listMappingTemplates = propertiesAdapter.listTemplates;
export const deleteMappingTemplate = propertiesAdapter.deleteTemplate;

async function download(path: string, filename: string): Promise<void> {
  const { data } = await apiClient.get<Blob>(path, { responseType: "blob" });
  saveBlob(data, filename);
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export const downloadImportTemplate = propertiesAdapter.downloadTemplate;
export const downloadImportErrors = propertiesAdapter.downloadErrors;

/** Arabic message from an axios error (backend sends {detail: {code, message}} or a string). */
export function importErrorMessage(err: unknown, fallback = "حدث خطأ غير متوقع"): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return (err as Error)?.message || fallback;
}
