"use client";

/**
 * components/properties/ImportWizard.tsx — bulk property import wizard
 *
 *   1. upload     CSV / TSV / XLSX / XLS / JSON, or PDF / DOCX / image (read by the AI model)
 *   2. mapping    file columns -> fields (auto-suggested, editable, saved as templates),
 *                 defaults for blank cells, and what to do with duplicate REGA numbers
 *   3. preview    dry run: counts, first 50 rows, issues -- nothing is written yet
 *   4. progress   background import with live counters, cancel, and the error report
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Loader2, Save, Sparkles, UploadCloud, X, XCircle,
} from "lucide-react";
import { Select } from "@/components/ui/Select";
import { LISTING_STATUS_LABELS, PROPERTY_TYPE_LABELS } from "@/lib/api/properties";
import {
  ACTIVE_STATUSES, TERMINAL_STATUSES, cancelImport, commitImport, deleteMappingTemplate, downloadImportErrors,
  downloadImportTemplate, fetchImport, importErrorMessage, listMappingTemplates, uploadImportFile, validateImport,
  type ImportJob, type ImportOptions, type MappingSuggestion, type MappingTemplate, type OnDuplicate,
  type UploadResult, type ValidationSummary,
} from "@/lib/api/propertyImport";

type Step = "upload" | "mapping" | "preview" | "progress";

const ACCEPT = ".csv,.tsv,.xlsx,.xls,.json,.pdf,.docx,.png,.jpg,.jpeg,.webp";
const POLL_MS = 1500;
const STEPS: [Step, string][] = [["upload", "رفع الملف"], ["mapping", "ربط الأعمدة"], ["preview", "المعاينة والتحقق"], ["progress", "الاستيراد"]];

const DUPLICATE_CHOICES: { value: OnDuplicate; label: string; hint: string }[] = [
  { value: "skip", label: "تخطّي المكرر", hint: "لا يُمس العقار الموجود (الأكثر أمانًا)" },
  { value: "update", label: "تحديث الموجود", hint: "تُحدَّث الحقول المعبّأة في الملف فقط" },
  { value: "create_new", label: "إنشاء نسخة جديدة", hint: "يُنشأ عقار جديد برقم مؤقت DEV-IMP" },
];

const OUTCOME: Record<string, { label: string; cls: string }> = {
  create: { label: "جديد", cls: "text-[var(--chart-5)]" },
  update: { label: "تحديث", cls: "text-[var(--chart-2)]" },
  skip: { label: "تخطّي", cls: "text-[var(--muted-foreground)]" },
  error: { label: "خطأ", cls: "text-[var(--chart-down)]" },
};

const fmt = (n: number | null | undefined) => new Intl.NumberFormat("ar-SA-u-ca-gregory").format(n ?? 0);

export default function ImportWizard({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [step, setStep] = useState<Step>("upload");
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [sources, setSources] = useState<Record<string, MappingSuggestion["source"]>>({});
  const [options, setOptions] = useState<ImportOptions>({ on_duplicate: "skip", defaults: {} });
  const [summary, setSummary] = useState<ValidationSummary | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [templates, setTemplates] = useState<MappingTemplate[]>([]);
  const [templateName, setTemplateName] = useState("");
  const [busy, setBusy] = useState<null | "upload" | "validate" | "commit">(null);
  const [uploadPct, setUploadPct] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const reset = useCallback(() => {
    setStep("upload"); setUpload(null); setMapping({}); setSources({}); setOptions({ on_duplicate: "skip", defaults: {} });
    setSummary(null); setJob(null); setTemplateName(""); setBusy(null); setUploadPct(0); setError(null);
  }, []);

  const running = job != null && ACTIVE_STATUSES.includes(job.status);
  const closable = busy === null;

  const close = useCallback(() => {
    if (!closable) return;
    if (job && TERMINAL_STATUSES.includes(job.status)) onDone();
    onClose();
    reset();
  }, [closable, job, onClose, onDone, reset]);

  useEffect(() => {
    if (!open) return;
    dialogRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  useEffect(() => {
    if (open) listMappingTemplates().then(setTemplates).catch(() => setTemplates([]));
  }, [open]);

  // Poll the background job.
  useEffect(() => {
    if (step !== "progress" || !job || TERMINAL_STATUSES.includes(job.status)) return;
    const id = job.import_id;
    const t = setInterval(async () => {
      try {
        setJob(await fetchImport(id));
      } catch { /* transient: keep polling */ }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [step, job]);

  const headers = upload?.detected_columns ?? [];
  const fieldByName = useMemo(() => Object.fromEntries((upload?.fields ?? []).map((f) => [f.name, f])), [upload]);
  const missingRequired = (upload?.fields ?? []).filter((f) => f.required && !mapping[f.name] && !options.defaults[f.name as keyof ImportOptions["defaults"]]);

  const pickFile = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    setBusy("upload"); setUploadPct(0);
    try {
      const res = await uploadImportFile(file, setUploadPct);
      setUpload(res);
      const m: Record<string, string> = {}; const src: Record<string, MappingSuggestion["source"]> = {};
      res.suggested_mapping.forEach((s) => { if (s.header) { m[s.field] = s.header; src[s.field] = s.source; } });
      setMapping(m); setSources(src);
      setStep("mapping");
    } catch (e) {
      setError(importErrorMessage(e, "تعذّر رفع الملف"));
    } finally {
      setBusy(null);
    }
  };

  const setField = (field: string, header: string) => {
    setMapping((prev) => {
      const next = { ...prev };
      // a column can feed only one field
      if (header) for (const k of Object.keys(next)) if (next[k] === header && k !== field) delete next[k];
      if (header) next[field] = header; else delete next[field];
      return next;
    });
    setSources((s) => ({ ...s, [field]: "manual" }));
  };

  const applyTemplate = (t: MappingTemplate) => {
    const m: Record<string, string> = {}; const src: Record<string, MappingSuggestion["source"]> = {};
    const used = new Set<string>();
    for (const [field, header] of Object.entries(t.mapping)) {
      if (headers.includes(header) && !used.has(header) && fieldByName[field]) { m[field] = header; src[field] = "template"; used.add(header); }
    }
    setMapping(m); setSources(src);
  };

  const runValidate = async () => {
    if (!upload) return;
    setBusy("validate"); setError(null);
    try {
      const res = await validateImport(upload.import_id, {
        mapping, options, save_template_as: templateName.trim() || undefined,
      });
      setSummary(res);
      if (templateName.trim()) listMappingTemplates().then(setTemplates).catch(() => undefined);
      setStep("preview");
    } catch (e) {
      setError(importErrorMessage(e, "تعذّر التحقق من الملف"));
    } finally {
      setBusy(null);
    }
  };

  const runCommit = async () => {
    if (!upload) return;
    setBusy("commit"); setError(null);
    try {
      setJob(await commitImport(upload.import_id));
      setStep("progress");
    } catch (e) {
      setError(importErrorMessage(e, "تعذّر بدء الاستيراد"));
    } finally {
      setBusy(null);
    }
  };

  const runCancel = async () => {
    if (!job) return;
    try { setJob(await cancelImport(job.import_id)); } catch (e) { setError(importErrorMessage(e)); }
  };

  if (!open) return null;

  const stepIndex = STEPS.findIndex(([k]) => k === step);
  const counts = summary?.counts;
  const blocked = !!summary && (summary.required_missing.length > 0 || (counts?.valid ?? 0) === 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div
        ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="import-title"
        className="w-full max-w-4xl max-h-[92vh] flex flex-col rounded-2xl border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)] shadow-2xl outline-none"
      >
        <header className="flex items-center justify-between gap-3 px-5 py-4 border-b border-[var(--border)]">
          <h2 id="import-title" className="text-base font-bold flex items-center gap-2">
            <FileSpreadsheet className="w-5 h-5 text-[var(--chart-1)]" aria-hidden />استيراد عقارات
          </h2>
          <button onClick={close} disabled={!closable} aria-label="إغلاق" className="p-1.5 rounded-lg hover:bg-[var(--menu-hover)] disabled:opacity-40">
            <X className="w-4 h-4" />
          </button>
        </header>

        <ol className="flex items-center gap-2 px-5 py-3 text-xs border-b border-[var(--border)] overflow-x-auto" aria-label="خطوات الاستيراد">
          {STEPS.map(([k, label], i) => (
            <li key={k} aria-current={i === stepIndex ? "step" : undefined}
              className={`flex items-center gap-2 whitespace-nowrap ${i === stepIndex ? "font-bold text-[var(--menu-selected-fg)]" : i < stepIndex ? "text-[var(--chart-5)]" : "text-[var(--muted-foreground)]"}`}>
              <span className="w-5 h-5 rounded-full border border-current flex items-center justify-center text-[10px]">{i < stepIndex ? "✓" : i + 1}</span>
              {label}{i < STEPS.length - 1 && <span aria-hidden className="mx-1 opacity-40">›</span>}
            </li>
          ))}
        </ol>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {error && (
            <div role="alert" className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden /><span>{error}</span>
            </div>
          )}

          {step === "upload" && (
            <div className="space-y-4">
              <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
                onDrop={(e) => { e.preventDefault(); setDragOver(false); void pickFile(e.dataTransfer.files?.[0]); }}
                className={`rounded-xl border-2 border-dashed p-8 text-center transition-colors ${dragOver ? "border-[var(--chart-1)] bg-[var(--menu-selected-bg)]" : "border-[var(--border)]"}`}
              >
                {busy === "upload" ? (
                  <div className="space-y-2" aria-busy="true">
                    <Loader2 className="w-8 h-8 mx-auto animate-spin text-[var(--chart-1)]" />
                    <p className="text-sm">{uploadPct < 100 ? `جارٍ الرفع… ${uploadPct}٪` : "جارٍ قراءة الملف (قد يستغرق ملف PDF أو صورة وقتًا)…"}</p>
                  </div>
                ) : (
                  <>
                    <UploadCloud className="w-9 h-9 mx-auto mb-2 text-[var(--muted-foreground)]" aria-hidden />
                    <p className="text-sm mb-1">اسحب الملف هنا أو</p>
                    <button onClick={() => fileRef.current?.click()} className="btn btn-primary h-9 px-4 text-sm">اختر ملفًا</button>
                    <input ref={fileRef} type="file" accept={ACCEPT} className="hidden" aria-label="اختيار ملف الاستيراد"
                      onChange={(e) => { void pickFile(e.target.files?.[0]); e.target.value = ""; }} />
                    <p className="text-xs text-[var(--muted-foreground)] mt-3">
                      CSV · TSV · Excel (XLSX/XLS) · JSON — أو PDF / Word / صورة يقرأها الذكاء الاصطناعي. حتى 20 ميجابايت و20,000 صف.
                    </p>
                  </>
                )}
              </div>
              <button onClick={() => void downloadImportTemplate().catch((e) => setError(importErrorMessage(e)))}
                className="text-sm text-[var(--menu-selected-fg)] underline flex items-center gap-1.5">
                <Download className="w-4 h-4" aria-hidden />تنزيل قالب Excel جاهز (بعناوين عربية وصف مثال)
              </button>
            </div>
          )}

          {step === "mapping" && upload && (
            <div className="space-y-4">
              <p className="text-sm text-[var(--muted-foreground)]">
                <strong className="text-[var(--foreground)]">{upload.filename}</strong> — {fmt(upload.total_rows)} صف، {fmt(headers.length)} عمود. راجع الربط المقترح وعدّله عند الحاجة.
              </p>
              {upload.extracted_by_llm && (
                <div className="flex items-start gap-2 rounded-lg border border-[var(--border)] bg-[var(--menu-selected-bg)] px-3 py-2 text-xs">
                  <Sparkles className="w-4 h-4 shrink-0 text-[var(--chart-1)]" aria-hidden />
                  <span>{upload.notes.join(" ") || "استُخرجت البيانات بالذكاء الاصطناعي — راجع المعاينة بعناية."}</span>
                </div>
              )}

              {templates.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-[var(--muted-foreground)]">قوالبي:</span>
                  {templates.map((t) => (
                    <span key={t.template_id} className="inline-flex items-center rounded-full border border-[var(--border)] overflow-hidden">
                      <button onClick={() => applyTemplate(t)} className="px-3 py-1 hover:bg-[var(--menu-hover)]">{t.name}</button>
                      <button aria-label={`حذف القالب ${t.name}`} className="px-2 py-1 hover:bg-[var(--menu-hover)] text-[var(--muted-foreground)]"
                        onClick={() => void deleteMappingTemplate(t.template_id).then(() => setTemplates((p) => p.filter((x) => x.template_id !== t.template_id)))}>×</button>
                    </span>
                  ))}
                </div>
              )}

              <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
                <table className="w-full text-sm">
                  <thead className="bg-[var(--menu-hover)] text-xs text-[var(--muted-foreground)]">
                    <tr><th className="text-start p-2">الحقل</th><th className="text-start p-2">عمود الملف</th><th className="text-start p-2">عيّنة من البيانات</th></tr>
                  </thead>
                  <tbody>
                    {upload.fields.map((f) => {
                      const header = mapping[f.name] ?? "";
                      const sample = header ? upload.sample_rows.map((r) => r[header]).filter(Boolean).slice(0, 3).join(" · ") : "";
                      const src = sources[f.name];
                      return (
                        <tr key={f.name} className="border-t border-[var(--border)]">
                          <td className="p-2 whitespace-nowrap">
                            {f.label}{f.required && <span className="text-[var(--chart-down)] ms-1" title="مطلوب">*</span>}
                          </td>
                          <td className="p-2">
                            <div className="flex items-center gap-2">
                              <Select value={header} onChange={(e) => setField(f.name, e.target.value)} aria-label={`عمود ${f.label}`} className="min-w-[10rem]">
                                <option value="">— غير مربوط —</option>
                                {headers.map((h) => <option key={h} value={h}>{h}</option>)}
                              </Select>
                              {header && src && src !== "manual" && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--menu-selected-bg)] text-[var(--menu-selected-fg)]">
                                  {src === "llm" ? "ذكاء اصطناعي" : src === "template" ? "قالب" : "تلقائي"}
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="p-2 text-xs text-[var(--muted-foreground)] max-w-[16rem] truncate" title={sample}>{sample || (f.example ? `مثال: ${f.example}` : "")}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <fieldset className="rounded-xl border border-[var(--border)] p-3">
                <legend className="px-2 text-xs font-semibold">قيم افتراضية للخلايا الفارغة (اختياري)</legend>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                  <label className="flex flex-col gap-1">نوع العقار
                    <Select value={options.defaults.property_type ?? ""} onChange={(e) => setOptions((o) => ({ ...o, defaults: { ...o.defaults, property_type: e.target.value || undefined } }))}>
                      <option value="">—</option>
                      {Object.entries(PROPERTY_TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v.ar}</option>)}
                    </Select>
                  </label>
                  <label className="flex flex-col gap-1">الحالة
                    <Select value={options.defaults.status ?? ""} onChange={(e) => setOptions((o) => ({ ...o, defaults: { ...o.defaults, status: e.target.value || undefined } }))}>
                      <option value="">قيد التحقق (الافتراضي)</option>
                      {Object.entries(LISTING_STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v.ar}</option>)}
                    </Select>
                  </label>
                  {(["city", "district"] as const).map((k) => (
                    <label key={k} className="flex flex-col gap-1">{k === "city" ? "المدينة" : "الحي"}
                      <input value={options.defaults[k] ?? ""} maxLength={100}
                        onChange={(e) => setOptions((o) => ({ ...o, defaults: { ...o.defaults, [k]: e.target.value || undefined } }))}
                        className="rounded-[10px] border border-[var(--border)] bg-[var(--menu-bg)] px-3 py-2 text-sm" />
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset className="rounded-xl border border-[var(--border)] p-3">
                <legend className="px-2 text-xs font-semibold">إذا كان رقم الإعلان (REGA) موجودًا مسبقًا</legend>
                <div className="grid md:grid-cols-3 gap-2">
                  {DUPLICATE_CHOICES.map((c) => (
                    <label key={c.value} className={`flex items-start gap-2 rounded-lg border p-2 cursor-pointer text-xs ${options.on_duplicate === c.value ? "border-[var(--chart-1)] bg-[var(--menu-selected-bg)]" : "border-[var(--border)]"}`}>
                      <input type="radio" name="on_duplicate" checked={options.on_duplicate === c.value} onChange={() => setOptions((o) => ({ ...o, on_duplicate: c.value }))} className="mt-0.5" />
                      <span><strong className="block">{c.label}</strong><span className="text-[var(--muted-foreground)]">{c.hint}</span></span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <label className="flex items-center gap-2 text-xs">
                <Save className="w-4 h-4 text-[var(--muted-foreground)]" aria-hidden />
                <input value={templateName} onChange={(e) => setTemplateName(e.target.value)} maxLength={100} placeholder="احفظ هذا الربط كقالب (اسم القالب)"
                  className="flex-1 rounded-[10px] border border-[var(--border)] bg-[var(--menu-bg)] px-3 py-2 text-sm" />
              </label>
              {missingRequired.length > 0 && (
                <p role="status" className="text-xs text-[var(--chart-down)]">
                  حقول مطلوبة غير مربوطة: {missingRequired.map((f) => f.label).join("، ")} — اربطها أو حدّد قيمة افتراضية.
                </p>
              )}
            </div>
          )}

          {step === "preview" && summary && counts && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3" aria-label="نتيجة التحقق">
                {[
                  ["إجمالي الصفوف", counts.total, ""],
                  ["صالحة", counts.valid, "text-[var(--chart-5)]"],
                  ["بها أخطاء", counts.errors, counts.errors ? "text-[var(--chart-down)]" : ""],
                  ["تحذيرات", counts.with_warnings, counts.with_warnings ? "text-[var(--chart-4)]" : ""],
                  ["سيُنشأ", counts.will_create, ""],
                  ["سيُحدَّث", counts.will_update, ""],
                  ["سيُتخطّى", counts.will_skip, ""],
                  ["مكرر داخل الملف", counts.duplicate_in_file, counts.duplicate_in_file ? "text-[var(--chart-down)]" : ""],
                ].map(([label, value, cls]) => (
                  <div key={String(label)} className="rounded-xl border border-[var(--border)] p-3">
                    <div className={`text-xl font-bold ${cls}`}>{fmt(value as number)}</div>
                    <div className="text-xs text-[var(--muted-foreground)]">{label}</div>
                  </div>
                ))}
              </div>
              {summary.required_missing.length > 0 && (
                <p role="alert" className="text-sm text-[var(--chart-down)]">حقول مطلوبة غير مربوطة: {summary.required_missing.join("، ")}.</p>
              )}
              {summary.ignored_columns.length > 0 && (
                <p className="text-xs text-[var(--muted-foreground)]">أعمدة لن تُستورد: {summary.ignored_columns.join("، ")}</p>
              )}

              <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
                <table className="w-full text-xs">
                  <caption className="sr-only">معاينة أول {summary.preview.length} صفًا</caption>
                  <thead className="bg-[var(--menu-hover)] text-[var(--muted-foreground)]">
                    <tr><th className="p-2 text-start">#</th><th className="p-2 text-start">النتيجة</th><th className="p-2 text-start">النوع</th><th className="p-2 text-start">المدينة / الحي</th><th className="p-2 text-start">السعر</th><th className="p-2 text-start">REGA</th><th className="p-2 text-start">ملاحظات</th></tr>
                  </thead>
                  <tbody>
                    {summary.preview.map((r) => {
                      const v = r.values as Record<string, string | number | undefined>;
                      return (
                        <tr key={r.row} className="border-t border-[var(--border)] align-top">
                          <td className="p-2">{fmt(r.row)}</td>
                          <td className={`p-2 font-semibold ${OUTCOME[r.outcome].cls}`}>{OUTCOME[r.outcome].label}</td>
                          <td className="p-2">{v.property_type ? PROPERTY_TYPE_LABELS[v.property_type as keyof typeof PROPERTY_TYPE_LABELS]?.ar : "—"}</td>
                          <td className="p-2">{[v.city, v.district].filter(Boolean).join(" / ") || "—"}</td>
                          <td className="p-2">{v.price != null ? fmt(Number(v.price)) : "—"}</td>
                          <td className="p-2" dir="ltr">{v.rega_ad_number ?? "—"}</td>
                          <td className="p-2 text-[var(--chart-down)]">{r.issues.map((i) => i.message).join("؛ ")}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {counts.errors > 0 && (
                <details className="text-xs">
                  <summary className="cursor-pointer font-semibold">أول {fmt(summary.issues.length)} ملاحظة</summary>
                  <ul className="mt-2 space-y-1 list-disc ps-5 text-[var(--muted-foreground)]">
                    {summary.issues.map((i, n) => <li key={n}>صف {fmt(i.row)}: {i.message}</li>)}
                  </ul>
                </details>
              )}
              <p className="text-xs text-[var(--muted-foreground)]">
                الصفوف التي بها أخطاء لن تُستورد، وستجدها مع سبب كل صف في تقرير الأخطاء بعد الانتهاء. العقارات المستوردة تُنشأ بحالة «قيد التحقق» ما لم تحدد الحالة في الملف.
              </p>
            </div>
          )}

          {step === "progress" && job && (
            <div className="space-y-4" aria-live="polite">
              <div className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2">
                  {running && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}
                  {job.status === "completed" && <CheckCircle2 className="w-4 h-4 text-[var(--chart-5)]" aria-hidden />}
                  {(job.status === "failed" || job.status === "cancelled") && <XCircle className="w-4 h-4 text-[var(--chart-down)]" aria-hidden />}
                  {{ queued: "في الانتظار…", running: job.cancel_requested ? "جارٍ الإيقاف…" : "جارٍ الاستيراد…", completed: "اكتمل الاستيراد", failed: "توقّف الاستيراد بسبب خطأ", cancelled: "أُلغي الاستيراد", uploaded: "", validated: "" }[job.status]}
                </span>
                <span className="font-semibold">{fmt(job.processed_rows)} / {fmt(job.total_rows)} ({job.percent}٪)</span>
              </div>
              <div className="h-3 rounded-full bg-[var(--menu-hover)] overflow-hidden" role="progressbar" aria-valuenow={Math.round(job.percent)} aria-valuemin={0} aria-valuemax={100}>
                <div className="h-full bg-[var(--chart-1)] transition-all duration-500" style={{ width: `${job.percent}%` }} />
              </div>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-center">
                {[["أُنشئ", job.created], ["حُدّث", job.updated], ["تُخطّي", job.skipped], ["فشل", job.failed], ["فُهرس للبحث", job.indexed]].map(([l, v]) => (
                  <div key={String(l)} className="rounded-xl border border-[var(--border)] p-3">
                    <div className="text-xl font-bold">{fmt(v as number)}</div><div className="text-xs text-[var(--muted-foreground)]">{l}</div>
                  </div>
                ))}
              </div>
              {job.status === "completed" && job.indexed < job.created + job.updated && (
                <p className="text-xs text-[var(--chart-4)]">بعض العقارات لم تُفهرس للبحث الذكي بعد (خدمة الفهرسة غير متاحة مؤقتًا). تُفهرس تلقائيًا عند تعديل العقار.</p>
              )}
              {job.error_message && <p role="alert" className="text-sm text-[var(--chart-down)]">{job.error_message}</p>}
              {job.has_error_report && (
                <button onClick={() => void downloadImportErrors(job.import_id).catch((e) => setError(importErrorMessage(e)))} className="btn btn-outline h-9 px-3 text-sm gap-1.5">
                  <Download className="w-4 h-4" aria-hidden />تنزيل تقرير الصفوف الفاشلة/المتخطّاة (CSV)
                </button>
              )}
              {running && <p className="text-xs text-[var(--muted-foreground)]">يمكنك إغلاق هذه النافذة؛ يستمر الاستيراد في الخلفية.</p>}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-between gap-3 px-5 py-3 border-t border-[var(--border)]">
          <div>
            {step === "mapping" && <button className="btn btn-ghost h-9 px-3 text-sm" onClick={() => { reset(); }}>ملف آخر</button>}
            {step === "preview" && <button className="btn btn-ghost h-9 px-3 text-sm" onClick={() => setStep("mapping")}>رجوع للربط</button>}
            {step === "progress" && running && <button className="btn btn-ghost h-9 px-3 text-sm" onClick={() => void runCancel()} disabled={job?.cancel_requested}>إلغاء الاستيراد</button>}
            {step === "progress" && job?.status === "failed" && <button className="btn btn-outline h-9 px-3 text-sm" onClick={() => void runCommit()} disabled={busy !== null}>إعادة المحاولة (يكمل من حيث توقّف)</button>}
          </div>
          <div className="flex gap-2">
            {step === "mapping" && (
              <button className="btn btn-primary h-9 px-4 text-sm" onClick={() => void runValidate()} disabled={busy !== null || missingRequired.length > 0}>
                {busy === "validate" ? <Loader2 className="w-4 h-4 animate-spin" /> : "معاينة والتحقق"}
              </button>
            )}
            {step === "preview" && (
              <button className="btn btn-primary h-9 px-4 text-sm" onClick={() => void runCommit()} disabled={busy !== null || blocked}>
                {busy === "commit" ? <Loader2 className="w-4 h-4 animate-spin" /> : `استيراد ${fmt((counts?.will_create ?? 0) + (counts?.will_update ?? 0))} عقارًا`}
              </button>
            )}
            {step === "progress" && (
              <button className="btn btn-primary h-9 px-4 text-sm" onClick={close}>{running ? "إخفاء" : "تم"}</button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}
