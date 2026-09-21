"use client";

/**
 * app/[locale]/(dashboard)/knowledge/page.tsx — قاعدة معرفة الذكاء الاصطناعي
 *
 * هذه الصفحة هي "مكان تلقين الذكاء الاصطناعي بمعلومات شركتك".
 * كل ما يُدخل هنا يستخدمه المساعد الذكي مباشرة للرد على عملائك في الواتساب.
 *
 * قسمان:
 *   1. معلومات الشركة (حقول منظمة)  → GET/PATCH /knowledge/profile
 *   2. مستندات الشركة (رفع وفهرسة)  → GET/POST/DELETE/reindex /knowledge/documents
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  Save,
  RefreshCw,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  Plus,
  Trash2,
  X,
  UploadCloud,
  FileText,
  RotateCcw,
  Sparkles,
  Building2,
  Phone,
  HelpCircle,
  MapPin,
  Link2,
} from "lucide-react";
import {
  fetchCompanyProfile,
  updateCompanyProfile,
  fetchKnowledgeDocuments,
  uploadKnowledgeDocument,
  deleteKnowledgeDocument,
  reindexKnowledgeDocument,
  formatFileSize,
  DOCUMENT_STATUS_LABELS,
  ACCEPTED_DOCUMENT_TYPES,
  type CompanyProfile,
  type FaqItem,
  type KnowledgeDocument,
} from "@/lib/api/knowledge";

// ── Constants ──────────────────────────────────────────────────────────────

/** روابط التواصل المعروضة كحقول ثابتة — تُخزَّن في social_links كـ map. */
const SOCIAL_PLATFORMS: { key: string; label: string; placeholder: string }[] = [
  { key: "website",   label: "الموقع الإلكتروني", placeholder: "https://example.com" },
  { key: "instagram", label: "إنستغرام",          placeholder: "https://instagram.com/…" },
  { key: "x",         label: "منصة X (تويتر)",    placeholder: "https://x.com/…" },
  { key: "snapchat",  label: "سناب شات",          placeholder: "https://snapchat.com/add/…" },
  { key: "tiktok",    label: "تيك توك",           placeholder: "https://tiktok.com/@…" },
  { key: "linkedin",  label: "لينكدإن",           placeholder: "https://linkedin.com/company/…" },
];

/** المستندات التي ما زالت تُعالَج تُحدَّث تلقائياً كل 8 ثوانٍ. */
const POLL_INTERVAL_MS = 8_000;

const EMPTY_PROFILE: CompanyProfile = {
  business_description:  "",
  services_offered:      "",
  target_areas:          [],
  pricing_policy:        "",
  working_hours:         "",
  contact_phone:         "",
  contact_email:         "",
  contact_address:       "",
  social_links:          {},
  unique_selling_points: "",
  policies_text:         "",
  faq:                   [],
  updated_at:            null,
};

// ── Shared field styles (نفس نمط properties/broadcasts) ────────────────────

const FIELD_CLASS =
  "w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:border-[#C9A84C]/50 transition-colors text-sm";

function SectionCard({
  icon: Icon,
  title,
  hint,
  children,
}: {
  icon: React.ElementType;
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-[#111827] border border-white/10 rounded-2xl p-6 shadow-xl">
      <div className="border-b border-white/5 pb-4 mb-6">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <Icon className="w-5 h-5 text-[#C9A84C]" />
          {title}
        </h2>
        {hint && <p className="text-xs text-gray-500 mt-1.5 leading-relaxed">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-300 mb-2">{label}</label>
      {children}
      {hint && <p className="text-xs text-gray-600 mt-1.5">{hint}</p>}
    </div>
  );
}

function FieldSkeleton() {
  return (
    <div className="space-y-2">
      <div className="h-4 w-32 rounded bg-white/5 animate-pulse" />
      <div className="h-12 w-full rounded-lg bg-white/5 animate-pulse" />
    </div>
  );
}

// ── Tags input (المناطق المستهدفة) ─────────────────────────────────────────

function TagsInput({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");

  const commit = () => {
    const tag = draft.trim();
    if (!tag) return;
    if (!value.includes(tag)) onChange([...value, tag]);
    setDraft("");
  };

  return (
    <div className="bg-[#0A0F1C] border border-white/10 rounded-lg p-2 focus-within:border-[#C9A84C]/50 transition-colors">
      <div className="flex flex-wrap gap-2">
        {value.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-[#C9A84C]/15 border border-[#C9A84C]/40 text-[#C9A84C]"
          >
            {tag}
            <button
              type="button"
              onClick={() => onChange(value.filter((t) => t !== tag))}
              aria-label={`حذف ${tag}`}
              className="hover:text-white transition-colors"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commit();
            } else if (e.key === "Backspace" && !draft && value.length) {
              onChange(value.slice(0, -1));
            }
          }}
          onBlur={commit}
          placeholder={value.length ? "" : placeholder}
          className="flex-1 min-w-[140px] bg-transparent border-0 outline-none text-white placeholder-gray-600 text-sm px-2 py-1.5"
        />
      </div>
    </div>
  );
}

// ── Document status badge ──────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const meta = DOCUMENT_STATUS_LABELS[status] ?? { ar: status, color: "#8B8FA8" };
  const spinning = status === "processing";
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border whitespace-nowrap"
      style={{
        color: meta.color,
        background: `${meta.color}18`,
        borderColor: `${meta.color}40`,
      }}
    >
      {spinning && <Loader2 className="w-3 h-3 animate-spin" />}
      {meta.ar}
    </span>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function KnowledgePage() {
  // ── Profile state ────────────────────────────────────────────────────────
  const [profile, setProfile]         = useState<CompanyProfile>(EMPTY_PROFILE);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError]     = useState<string | null>(null);
  const [saving, setSaving]                 = useState(false);
  const [saveError, setSaveError]           = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess]       = useState(false);

  // ── Documents state ──────────────────────────────────────────────────────
  const [documents, setDocuments]     = useState<KnowledgeDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [docsError, setDocsError]     = useState<string | null>(null);
  const [uploading, setUploading]     = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragOver, setDragOver]       = useState(false);
  const [busyDocId, setBusyDocId]     = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Loaders ──────────────────────────────────────────────────────────────

  const loadProfile = useCallback(async () => {
    setProfileLoading(true);
    setProfileError(null);
    try {
      const data = await fetchCompanyProfile();
      // نستبدل القيم null بسلاسل فارغة حتى تبقى الحقول "controlled".
      setProfile({
        ...data,
        business_description:  data.business_description ?? "",
        services_offered:      data.services_offered ?? "",
        pricing_policy:        data.pricing_policy ?? "",
        working_hours:         data.working_hours ?? "",
        contact_phone:         data.contact_phone ?? "",
        contact_email:         data.contact_email ?? "",
        contact_address:       data.contact_address ?? "",
        unique_selling_points: data.unique_selling_points ?? "",
        policies_text:         data.policies_text ?? "",
      });
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setProfileError(
        typeof detail === "string" ? detail : "تعذّر تحميل معلومات الشركة",
      );
    } finally {
      setProfileLoading(false);
    }
  }, []);

  const loadDocuments = useCallback(async (silent = false) => {
    if (!silent) setDocsLoading(true);
    setDocsError(null);
    try {
      const result = await fetchKnowledgeDocuments();
      setDocuments(result.items);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setDocsError(typeof detail === "string" ? detail : "تعذّر تحميل المستندات");
    } finally {
      if (!silent) setDocsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProfile();
    void loadDocuments();
  }, [loadProfile, loadDocuments]);

  // تحديث تلقائي طالما هناك مستند قيد المعالجة.
  const hasProcessing = useMemo(
    () => documents.some((d) => d.status === "processing" || d.status === "uploaded"),
    [documents],
  );

  useEffect(() => {
    if (!hasProcessing) return;
    const id = setInterval(() => void loadDocuments(true), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [hasProcessing, loadDocuments]);

  // إخفاء رسالة النجاح تلقائياً.
  useEffect(() => {
    if (!saveSuccess) return;
    const t = setTimeout(() => setSaveSuccess(false), 4000);
    return () => clearTimeout(t);
  }, [saveSuccess]);

  // ── Profile helpers ──────────────────────────────────────────────────────

  const patch = <K extends keyof CompanyProfile>(key: K, value: CompanyProfile[K]) =>
    setProfile((prev) => ({ ...prev, [key]: value }));

  const setSocial = (key: string, value: string) =>
    setProfile((prev) => {
      const next = { ...prev.social_links };
      if (value.trim()) next[key] = value;
      else delete next[key];
      return { ...prev, social_links: next };
    });

  const setFaq = (index: number, patchItem: Partial<FaqItem>) =>
    setProfile((prev) => ({
      ...prev,
      faq: prev.faq.map((f, i) => (i === index ? { ...f, ...patchItem } : f)),
    }));

  const addFaq = () =>
    setProfile((prev) => ({ ...prev, faq: [...prev.faq, { question: "", answer: "" }] }));

  const removeFaq = (index: number) =>
    setProfile((prev) => ({ ...prev, faq: prev.faq.filter((_, i) => i !== index) }));

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    // نرسل كل الحقول (الـ endpoint يقبل partial، وإرسال الكل آمن).
    // الحقول الفارغة تُرسل كـ null حتى يستطيع المستخدم مسح قيمة سابقة.
    const orNull = (v: string | null) => (v && v.trim() ? v.trim() : null);

    try {
      const saved = await updateCompanyProfile({
        business_description:  orNull(profile.business_description),
        services_offered:      orNull(profile.services_offered),
        target_areas:          profile.target_areas,
        pricing_policy:        orNull(profile.pricing_policy),
        working_hours:         orNull(profile.working_hours),
        contact_phone:         orNull(profile.contact_phone),
        contact_email:         orNull(profile.contact_email),
        contact_address:       orNull(profile.contact_address),
        social_links:          profile.social_links,
        unique_selling_points: orNull(profile.unique_selling_points),
        policies_text:         orNull(profile.policies_text),
        faq: profile.faq
          .map((f) => ({ question: f.question.trim(), answer: f.answer.trim() }))
          .filter((f) => f.question || f.answer),
      });
      setProfile({
        ...saved,
        business_description:  saved.business_description ?? "",
        services_offered:      saved.services_offered ?? "",
        pricing_policy:        saved.pricing_policy ?? "",
        working_hours:         saved.working_hours ?? "",
        contact_phone:         saved.contact_phone ?? "",
        contact_email:         saved.contact_email ?? "",
        contact_address:       saved.contact_address ?? "",
        unique_selling_points: saved.unique_selling_points ?? "",
        policies_text:         saved.policies_text ?? "",
      });
      setSaveSuccess(true);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setSaveError(typeof detail === "string" ? detail : "تعذّر حفظ المعلومات");
    } finally {
      setSaving(false);
    }
  };

  // ── Document handlers ────────────────────────────────────────────────────

  const handleFiles = async (files: FileList | File[] | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setUploadError(null);
    try {
      for (const file of Array.from(files)) {
        const created = await uploadKnowledgeDocument(file);
        setDocuments((prev) => [created, ...prev]);
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setUploadError(typeof detail === "string" ? detail : "تعذّر رفع الملف");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDelete = async (doc: KnowledgeDocument) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        `سيتم حذف "${doc.title || doc.original_filename || "المستند"}" نهائياً من قاعدة معرفة الذكاء الاصطناعي. هل أنت متأكد؟`,
      )
    ) {
      return;
    }
    setBusyDocId(doc.document_id);
    try {
      await deleteKnowledgeDocument(doc.document_id);
      setDocuments((prev) => prev.filter((d) => d.document_id !== doc.document_id));
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setDocsError(typeof detail === "string" ? detail : "تعذّر حذف المستند");
    } finally {
      setBusyDocId(null);
    }
  };

  const handleReindex = async (doc: KnowledgeDocument) => {
    setBusyDocId(doc.document_id);
    setDocsError(null);
    try {
      const updated = await reindexKnowledgeDocument(doc.document_id);
      setDocuments((prev) =>
        prev.map((d) => (d.document_id === updated.document_id ? updated : d)),
      );
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setDocsError(typeof detail === "string" ? detail : "تعذّرت إعادة الفهرسة");
    } finally {
      setBusyDocId(null);
    }
  };

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="space-y-8" dir="rtl">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <BookOpen className="w-8 h-8 text-[#C9A84C]" />
            معلومات شركتك (تلقين الذكاء الاصطناعي)
          </h1>
          <p className="text-gray-400 mt-2 text-sm max-w-3xl leading-relaxed">
            كل ما تكتبه هنا يستخدمه المساعد الذكي مباشرة للرد على عملائك. كلما كانت
            المعلومات أدق وأشمل، كانت ردود الذكاء الاصطناعي أقرب لأسلوب شركتك وأقل
            احتمالاً للخطأ.
          </p>
        </div>
        <button
          onClick={() => {
            void loadProfile();
            void loadDocuments();
          }}
          disabled={profileLoading || docsLoading}
          className="bg-white/5 border border-white/10 text-white px-4 py-2 rounded-lg flex items-center gap-2 hover:bg-white/10 transition-colors disabled:opacity-50 shrink-0"
        >
          <RefreshCw
            className={`w-4 h-4 ${profileLoading || docsLoading ? "animate-spin" : ""}`}
          />
          تحديث
        </button>
      </div>

      {/* Explainer banner */}
      <div className="flex items-start gap-3 bg-[#C9A84C]/10 border border-[#C9A84C]/30 rounded-xl px-4 py-3">
        <Sparkles className="w-5 h-5 text-[#C9A84C] shrink-0 mt-0.5" />
        <p className="text-sm text-[#E4D5A5] leading-relaxed">
          هذه الصفحة هي «عقل» المساعد الذكي. املأ الحقول أدناه بمعلومات شركتك، وارفع
          الكتالوجات وقوائم الأسعار وأي مستندات تريد أن يعرفها الذكاء الاصطناعي —
          سيقرأها ويستخدمها في محادثات الواتساب مع عملائك.
        </p>
      </div>

      {/* Profile load error */}
      {profileError && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 bg-red-500/10 border border-red-500/40 text-red-300 rounded-xl px-4 py-3 text-sm"
        >
          <span className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            {profileError}
          </span>
          <button
            onClick={() => void loadProfile()}
            className="flex items-center gap-1 text-xs hover:opacity-80"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            إعادة المحاولة
          </button>
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-8">
        {/* ── 1. نبذة عن الشركة ─────────────────────────────────────────── */}
        <SectionCard
          icon={Building2}
          title="نبذة عن الشركة وخدماتها"
          hint="اشرح للذكاء الاصطناعي من أنتم وماذا تقدّمون، كما لو كنت تشرح لموظف جديد في أول يوم."
        >
          {profileLoading ? (
            <div className="space-y-5">
              <FieldSkeleton />
              <FieldSkeleton />
              <FieldSkeleton />
            </div>
          ) : (
            <div className="space-y-5">
              <Field
                label="وصف الشركة"
                hint="مثال: مكتب عقاري في الرياض متخصص في بيع وتأجير الفلل والشقق منذ 2015."
              >
                <textarea
                  rows={4}
                  value={profile.business_description ?? ""}
                  onChange={(e) => patch("business_description", e.target.value)}
                  placeholder="من نحن؟ متى تأسست الشركة؟ ما مجال تخصصنا؟"
                  className={`${FIELD_CLASS} resize-none`}
                />
              </Field>

              <Field
                label="الخدمات المقدَّمة"
                hint="اكتب كل خدمة في سطر: بيع، تأجير، إدارة أملاك، تسويق عقاري، استشارات استثمارية…"
              >
                <textarea
                  rows={4}
                  value={profile.services_offered ?? ""}
                  onChange={(e) => patch("services_offered", e.target.value)}
                  placeholder="- بيع العقارات السكنية&#10;- التأجير السنوي&#10;- إدارة الأملاك"
                  className={`${FIELD_CLASS} resize-none`}
                />
              </Field>

              <Field
                label="المناطق المستهدفة"
                hint="اكتب اسم المنطقة واضغط Enter لإضافتها."
              >
                <TagsInput
                  value={profile.target_areas}
                  onChange={(next) => patch("target_areas", next)}
                  placeholder="مثال: شمال الرياض، حي النرجس…"
                />
              </Field>

              <Field
                label="نقاط التميّز"
                hint="لماذا يختارك العميل بدل غيرك؟ الذكاء الاصطناعي سيستخدمها في الإقناع."
              >
                <textarea
                  rows={3}
                  value={profile.unique_selling_points ?? ""}
                  onChange={(e) => patch("unique_selling_points", e.target.value)}
                  placeholder="- أكثر من 500 صفقة ناجحة&#10;- مستشار مخصص لكل عميل"
                  className={`${FIELD_CLASS} resize-none`}
                />
              </Field>
            </div>
          )}
        </SectionCard>

        {/* ── 2. الأسعار والسياسات وساعات العمل ─────────────────────────── */}
        <SectionCard
          icon={FileText}
          title="سياسة الأسعار وساعات العمل والسياسات العامة"
          hint="حدّد ما يُسمح للذكاء الاصطناعي بقوله عن الأسعار، ومتى تستقبلون العملاء."
        >
          {profileLoading ? (
            <div className="space-y-5">
              <FieldSkeleton />
              <FieldSkeleton />
            </div>
          ) : (
            <div className="space-y-5">
              <Field
                label="سياسة الأسعار"
                hint="مثال: لا نذكر السعر النهائي في المحادثة، نطلب من العميل تحديد موعد معاينة."
              >
                <textarea
                  rows={3}
                  value={profile.pricing_policy ?? ""}
                  onChange={(e) => patch("pricing_policy", e.target.value)}
                  placeholder="كيف نتعامل مع أسئلة الأسعار والعمولات والتفاوض؟"
                  className={`${FIELD_CLASS} resize-none`}
                />
              </Field>

              <Field label="ساعات العمل" hint="مثال: الأحد – الخميس، 9 صباحاً حتى 6 مساءً.">
                <input
                  type="text"
                  value={profile.working_hours ?? ""}
                  onChange={(e) => patch("working_hours", e.target.value)}
                  placeholder="الأحد – الخميس 9:00 ص – 6:00 م"
                  className={FIELD_CLASS}
                />
              </Field>

              <Field
                label="السياسات العامة"
                hint="سياسة الإلغاء، العربون، الضمانات، الشروط والأحكام… أي شيء يجب أن يلتزم به الرد."
              >
                <textarea
                  rows={4}
                  value={profile.policies_text ?? ""}
                  onChange={(e) => patch("policies_text", e.target.value)}
                  placeholder="اكتب السياسات التي يجب أن يلتزم بها المساعد الذكي عند الرد."
                  className={`${FIELD_CLASS} resize-none`}
                />
              </Field>
            </div>
          )}
        </SectionCard>

        {/* ── 3. بيانات التواصل ──────────────────────────────────────────── */}
        <SectionCard
          icon={Phone}
          title="بيانات التواصل"
          hint="يستخدمها المساعد عندما يطلب العميل التحدث مع موظف أو زيارة المكتب."
        >
          {profileLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <FieldSkeleton />
              <FieldSkeleton />
            </div>
          ) : (
            <div className="space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <Field label="رقم الهاتف">
                  <input
                    type="tel"
                    dir="ltr"
                    value={profile.contact_phone ?? ""}
                    onChange={(e) => patch("contact_phone", e.target.value)}
                    placeholder="+966 5X XXX XXXX"
                    className={`${FIELD_CLASS} text-left`}
                  />
                </Field>
                <Field label="البريد الإلكتروني">
                  <input
                    type="email"
                    dir="ltr"
                    value={profile.contact_email ?? ""}
                    onChange={(e) => patch("contact_email", e.target.value)}
                    placeholder="info@example.com"
                    className={`${FIELD_CLASS} text-left`}
                  />
                </Field>
              </div>

              <Field label="العنوان">
                <div className="relative">
                  <MapPin className="w-4 h-4 text-gray-600 absolute top-1/2 -translate-y-1/2 right-3 pointer-events-none" />
                  <input
                    type="text"
                    value={profile.contact_address ?? ""}
                    onChange={(e) => patch("contact_address", e.target.value)}
                    placeholder="الرياض، حي الياسمين، طريق أنس بن مالك"
                    className={`${FIELD_CLASS} pr-9`}
                  />
                </div>
              </Field>

              <div>
                <p className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
                  <Link2 className="w-4 h-4 text-gray-500" />
                  روابط التواصل الاجتماعي
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {SOCIAL_PLATFORMS.map((platform) => (
                    <div key={platform.key}>
                      <label className="block text-xs text-gray-500 mb-1.5">
                        {platform.label}
                      </label>
                      <input
                        type="url"
                        dir="ltr"
                        value={profile.social_links[platform.key] ?? ""}
                        onChange={(e) => setSocial(platform.key, e.target.value)}
                        placeholder={platform.placeholder}
                        className={`${FIELD_CLASS} text-left py-2.5`}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </SectionCard>

        {/* ── 4. الأسئلة الشائعة ─────────────────────────────────────────── */}
        <SectionCard
          icon={HelpCircle}
          title="الأسئلة الشائعة (FAQ)"
          hint="أضف الأسئلة التي يكررها عملاؤك مع الجواب المعتمد — الذكاء الاصطناعي سيرد بها حرفياً تقريباً."
        >
          {profileLoading ? (
            <div className="space-y-4">
              <FieldSkeleton />
              <FieldSkeleton />
            </div>
          ) : (
            <div className="space-y-4">
              {profile.faq.length === 0 && (
                <div className="text-center py-8 border border-dashed border-white/10 rounded-xl">
                  <HelpCircle className="w-8 h-8 text-gray-600 mx-auto mb-2" />
                  <p className="text-sm text-gray-400">لم تُضف أي سؤال شائع بعد.</p>
                  <p className="text-xs text-gray-600 mt-1">
                    ابدأ بأكثر 5 أسئلة يسألها عملاؤك يومياً.
                  </p>
                </div>
              )}

              {profile.faq.map((item, index) => (
                <div
                  key={index}
                  className="border border-white/10 rounded-xl p-4 bg-[#0A0F1C]/60 space-y-3"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs font-semibold text-[#C9A84C]">
                      سؤال #{index + 1}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeFaq(index)}
                      aria-label={`حذف السؤال ${index + 1}`}
                      className="text-gray-500 hover:text-red-400 transition-colors flex items-center gap-1 text-xs"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      حذف
                    </button>
                  </div>
                  <input
                    type="text"
                    value={item.question}
                    onChange={(e) => setFaq(index, { question: e.target.value })}
                    placeholder="السؤال — مثال: هل تقبلون التقسيط؟"
                    className={FIELD_CLASS}
                  />
                  <textarea
                    rows={3}
                    value={item.answer}
                    onChange={(e) => setFaq(index, { answer: e.target.value })}
                    placeholder="الجواب المعتمد الذي تريد أن يرد به المساعد الذكي."
                    className={`${FIELD_CLASS} resize-none`}
                  />
                </div>
              ))}

              <button
                type="button"
                onClick={addFaq}
                className="w-full border border-dashed border-white/15 text-gray-300 rounded-xl py-3 flex items-center justify-center gap-2 text-sm hover:border-[#C9A84C]/50 hover:text-[#C9A84C] transition-colors"
              >
                <Plus className="w-4 h-4" />
                إضافة سؤال وجواب
              </button>
            </div>
          )}
        </SectionCard>

        {/* ── Save bar ───────────────────────────────────────────────────── */}
        <div className="sticky bottom-4 z-10">
          <div className="bg-[#111827]/95 backdrop-blur border border-white/10 rounded-2xl px-5 py-4 shadow-2xl flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="text-xs text-gray-500">
              {profile.updated_at
                ? `آخر تحديث: ${new Intl.DateTimeFormat("ar-SA", {
                    dateStyle: "medium",
                    timeStyle: "short",
                  }).format(new Date(profile.updated_at))}`
                : "لم تُحفظ معلومات الشركة بعد."}
            </div>
            <div className="flex items-center gap-4">
              {saveSuccess && (
                <span className="text-sm text-green-400 flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4" /> تم الحفظ بنجاح
                </span>
              )}
              {saveError && (
                <span
                  role="alert"
                  className="text-sm text-red-400 flex items-center gap-1.5"
                >
                  <AlertTriangle className="w-4 h-4" /> {saveError}
                </span>
              )}
              <button
                type="submit"
                disabled={saving || profileLoading}
                className="bg-[#C9A84C] text-[#0A0F1C] px-6 py-3 rounded-xl font-bold hover:bg-[#D4B55A] transition-all flex items-center justify-center gap-2 shadow-lg shadow-[#C9A84C]/10 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {saving ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <Save className="w-5 h-5" />
                )}
                حفظ معلومات الشركة
              </button>
            </div>
          </div>
        </div>
      </form>

      {/* ── 5. المستندات ───────────────────────────────────────────────── */}
      <SectionCard
        icon={UploadCloud}
        title="مستندات الشركة"
        hint="ارفع الكتالوجات، قوائم الأسعار، كراسات المشاريع، أو أي ملف يحتوي معلومات تريد أن يعرفها الذكاء الاصطناعي. سيتم تحليل الملف وفهرسته تلقائياً."
      >
        {/* Drop zone */}
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            void handleFiles(e.dataTransfer.files);
          }}
          onClick={() => fileInputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") fileInputRef.current?.click();
          }}
          className={`border-2 border-dashed rounded-2xl p-10 text-center cursor-pointer transition-colors ${
            dragOver
              ? "border-[#C9A84C] bg-[#C9A84C]/10"
              : "border-white/15 hover:border-[#C9A84C]/50 bg-[#0A0F1C]/40"
          }`}
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_DOCUMENT_TYPES}
            onChange={(e) => void handleFiles(e.target.files)}
            className="hidden"
          />
          {uploading ? (
            <>
              <Loader2 className="w-10 h-10 text-[#C9A84C] mx-auto mb-3 animate-spin" />
              <p className="text-sm text-gray-300">جارٍ رفع الملف…</p>
            </>
          ) : (
            <>
              <UploadCloud className="w-10 h-10 text-gray-500 mx-auto mb-3" />
              <p className="text-sm text-gray-200 font-medium">
                اسحب الملفات هنا أو اضغط للاختيار
              </p>
              <p className="text-xs text-gray-600 mt-1.5">
                PDF، Word، Excel، أو نص عادي — يمكنك رفع أكثر من ملف دفعة واحدة.
              </p>
            </>
          )}
        </div>

        {uploadError && (
          <p role="alert" className="text-sm text-red-400 flex items-center gap-1.5 mt-4">
            <AlertTriangle className="w-4 h-4" /> {uploadError}
          </p>
        )}

        {docsError && (
          <div
            role="alert"
            className="flex items-center justify-between gap-3 bg-red-500/10 border border-red-500/40 text-red-300 rounded-xl px-4 py-3 text-sm mt-4"
          >
            <span className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              {docsError}
            </span>
            <button
              onClick={() => void loadDocuments()}
              className="text-xs hover:opacity-80"
            >
              إعادة المحاولة
            </button>
          </div>
        )}

        {/* Documents list */}
        <div className="mt-6">
          {docsLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="h-20 rounded-xl bg-white/5 animate-pulse" />
              ))}
            </div>
          ) : documents.length === 0 ? (
            <div className="text-center py-12">
              <FileText className="w-10 h-10 text-gray-600 mx-auto mb-3" />
              <p className="text-gray-400 text-sm">لا توجد مستندات مرفوعة بعد.</p>
              <p className="text-gray-600 text-xs mt-1">
                ابدأ برفع كتالوج مشاريعك أو ملف الأسئلة الشائعة.
              </p>
            </div>
          ) : (
            <ul className="space-y-3">
              {documents.map((doc) => (
                <li
                  key={doc.document_id}
                  className="border border-white/10 rounded-xl p-4 hover:bg-white/[0.02] transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-3 min-w-0">
                      <div className="w-10 h-10 rounded-lg bg-[#C9A84C]/10 flex items-center justify-center shrink-0">
                        <FileText className="w-5 h-5 text-[#C9A84C]" />
                      </div>
                      <div className="min-w-0">
                        <p className="font-semibold text-gray-100 truncate">
                          {doc.title || doc.original_filename || "مستند بدون اسم"}
                        </p>
                        <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-500 flex-wrap">
                          {doc.original_filename && doc.title && (
                            <span className="truncate max-w-[220px]">
                              {doc.original_filename}
                            </span>
                          )}
                          <span>{formatFileSize(doc.file_size_bytes)}</span>
                          {doc.chunk_count != null && doc.chunk_count > 0 && (
                            <span>{doc.chunk_count.toLocaleString("ar")} مقطع مفهرس</span>
                          )}
                          {doc.created_at && (
                            <span>
                              {new Intl.DateTimeFormat("ar-SA", {
                                dateStyle: "medium",
                              }).format(new Date(doc.created_at))}
                            </span>
                          )}
                        </div>
                        {doc.status === "failed" && doc.error_message && (
                          <p className="text-xs text-red-400 mt-2">{doc.error_message}</p>
                        )}
                      </div>
                    </div>

                    <div className="flex flex-col items-end gap-2 shrink-0">
                      <StatusBadge status={doc.status} />
                      <div className="flex items-center gap-3">
                        {doc.status === "failed" && (
                          <button
                            type="button"
                            onClick={() => void handleReindex(doc)}
                            disabled={busyDocId === doc.document_id}
                            className="text-xs text-gray-400 hover:text-[#C9A84C] transition-colors flex items-center gap-1 disabled:opacity-50"
                          >
                            {busyDocId === doc.document_id ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <RotateCcw className="w-3 h-3" />
                            )}
                            إعادة المحاولة
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => void handleDelete(doc)}
                          disabled={busyDocId === doc.document_id}
                          aria-label="حذف المستند"
                          className="text-xs text-gray-400 hover:text-red-400 transition-colors flex items-center gap-1 disabled:opacity-50"
                        >
                          {busyDocId === doc.document_id ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : (
                            <Trash2 className="w-3 h-3" />
                          )}
                          حذف
                        </button>
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </SectionCard>
    </div>
  );
}
