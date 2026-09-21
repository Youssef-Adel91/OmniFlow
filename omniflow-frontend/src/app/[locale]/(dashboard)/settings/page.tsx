"use client";

/**
 * app/[locale]/(dashboard)/settings/page.tsx — Tenant settings
 *
 * Tabs:
 *   1. api        — WhatsApp API config (read-only token + phone number ID)
 *   2. company    — Business name, FAL number, max AI conversations
 *   3. ai         — AI Personality / system prompt  [NEW]
 *   4. logo       — Company logo upload             [NEW]
 *
 * API wiring:
 *   GET   /settings             → load initial state
 *   PATCH /settings             → tab 1 & 2 saves
 *   PATCH /settings/ai-personality → tab 3 save
 *   POST  /settings/logo        → tab 4 upload
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Settings as SettingsIcon,
  Key,
  Building2,
  Save,
  MessageSquare,
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Loader2,
  Lock,
  Bot,
  Image as ImageIcon,
  Upload,
  Trash2,
  BookOpen,
  ArrowLeft,
} from "lucide-react";
import {
  fetchSettings,
  updateSettings,
  updateAiPersonality,
  uploadLogo,
  TIER_LABELS,
  type TenantSettings,
} from "@/lib/api/settings";
import { useTenantStore } from "@/store/tenantStore";

type TabKey = "api" | "company" | "ai" | "logo";

// ── Skeleton ───────────────────────────────────────────────────────────────

function FieldSkeleton() {
  return (
    <div className="space-y-2">
      <div className="h-4 w-32 rounded bg-white/5 animate-pulse" />
      <div className="h-12 w-full rounded-lg bg-white/5 animate-pulse" />
    </div>
  );
}

// ── Inline feedback helpers ────────────────────────────────────────────────

function SaveFeedback({
  success,
  error,
}: {
  success: boolean;
  error: string | null;
}) {
  if (success)
    return (
      <span className="text-sm text-green-400 flex items-center gap-1.5">
        <CheckCircle2 className="w-4 h-4" /> تم الحفظ بنجاح
      </span>
    );
  if (error)
    return (
      <span role="alert" className="text-sm text-red-400 flex items-center gap-1.5">
        <AlertTriangle className="w-4 h-4" /> {error}
      </span>
    );
  return null;
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<TabKey>("api");

  // مستخدم لبناء روابط تحترم اللغة الحالية (مثل رابط صفحة قاعدة المعرفة).
  const routeParams = useParams();
  const localeParam =
    (Array.isArray(routeParams?.locale) ? routeParams.locale[0] : routeParams?.locale) ??
    "ar";

  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);

  // ── tab 1 & 2 — general settings state ───────────────────────────────────
  const [saving, setSaving]           = useState(false);
  const [saveError, setSaveError]     = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [businessName, setBusinessName]           = useState("");
  const [phoneNumberId, setPhoneNumberId]         = useState("");
  const [maxAiConversations, setMaxAiConversations] = useState("");

  // ── tab 3 — AI personality ────────────────────────────────────────────────
  const [aiPrompt, setAiPrompt]               = useState("");
  const [aiSaving, setAiSaving]               = useState(false);
  const [aiSaveError, setAiSaveError]         = useState<string | null>(null);
  const [aiSaveSuccess, setAiSaveSuccess]     = useState(false);

  // ── tab 4 — logo upload ────────────────────────────────────────────────────
  const [logoUploading, setLogoUploading]     = useState(false);
  const [logoError, setLogoError]             = useState<string | null>(null);
  const [logoSuccess, setLogoSuccess]         = useState(false);
  const [logoPreview, setLogoPreview]         = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const updateTenant = useTenantStore((s) => s.updateTenant);

  const applySettings = useCallback((s: TenantSettings) => {
    setSettings(s);
    setBusinessName(s.business_name ?? "");
    setPhoneNumberId(s.whatsapp_phone_number_id ?? "");
    setMaxAiConversations(
      s.max_ai_conversations != null ? String(s.max_ai_conversations) : "",
    );
    setAiPrompt(s.ai_system_prompt ?? "");
    if (s.logo_url) setLogoPreview(s.logo_url);
    // Keep the sidebar tenant badge in sync
    updateTenant({
      businessName: s.business_name,
      tier: s.subscription_tier as "economic" | "professional" | "enterprise",
    });
  }, [updateTenant]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      applySettings(await fetchSettings());
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setError(typeof detail === "string" ? detail : "فشل تحميل الإعدادات");
    } finally {
      setLoading(false);
    }
  }, [applySettings]);

  useEffect(() => { void load(); }, [load]);

  // Auto-dismiss all success flashes
  useEffect(() => {
    if (!saveSuccess) return;
    const t = setTimeout(() => setSaveSuccess(false), 4000);
    return () => clearTimeout(t);
  }, [saveSuccess]);

  useEffect(() => {
    if (!aiSaveSuccess) return;
    const t = setTimeout(() => setAiSaveSuccess(false), 4000);
    return () => clearTimeout(t);
  }, [aiSaveSuccess]);

  useEffect(() => {
    if (!logoSuccess) return;
    const t = setTimeout(() => setLogoSuccess(false), 4000);
    return () => clearTimeout(t);
  }, [logoSuccess]);

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    const parsedMax =
      maxAiConversations.trim() === "" ? null : Number(maxAiConversations);

    if (parsedMax != null && (!Number.isFinite(parsedMax) || parsedMax < 0)) {
      setSaveError("الحد الأقصى للمحادثات يجب أن يكون رقماً صحيحاً موجباً.");
      setSaving(false);
      return;
    }

    try {
      const updated = await updateSettings({
        business_name: businessName.trim(),
        whatsapp_phone_number_id: phoneNumberId.trim() || null,
        max_ai_conversations: parsedMax,
      });
      applySettings(updated);
      setSaveSuccess(true);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setSaveError(typeof detail === "string" ? detail : "فشل حفظ الإعدادات");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAiPersonality = async (e: React.FormEvent) => {
    e.preventDefault();
    if (aiSaving) return;
    if (aiPrompt.trim().length < 10) {
      setAiSaveError("يجب أن يكون النص على الأقل 10 أحرف.");
      return;
    }
    setAiSaving(true);
    setAiSaveError(null);
    setAiSaveSuccess(false);
    try {
      const updated = await updateAiPersonality(aiPrompt.trim());
      applySettings(updated);
      setAiSaveSuccess(true);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setAiSaveError(typeof detail === "string" ? detail : "فشل حفظ شخصية الذكاء الاصطناعي");
    } finally {
      setAiSaving(false);
    }
  };

  const handleLogoChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    // Local preview
    const reader = new FileReader();
    reader.onload = () => setLogoPreview(reader.result as string);
    reader.readAsDataURL(file);
  };

  const handleLogoUpload = async () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    setLogoUploading(true);
    setLogoError(null);
    setLogoSuccess(false);
    try {
      const result = await uploadLogo(file);
      setLogoPreview(result.logo_url);
      setSettings((prev) => prev ? { ...prev, logo_url: result.logo_url } : prev);
      setLogoSuccess(true);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setLogoError(typeof detail === "string" ? detail : "فشل رفع الشعار");
    } finally {
      setLogoUploading(false);
    }
  };

  const handleLogoClear = () => {
    setLogoPreview(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    setLogoError(null);
    setLogoSuccess(false);
  };

  // ── Styles ────────────────────────────────────────────────────────────────

  const inputClass =
    "w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white focus:outline-none focus:border-[#C9A84C]/50 transition-colors";
  const readOnlyClass =
    "w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-gray-500 focus:outline-none cursor-not-allowed";
  const textareaClass =
    "w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white focus:outline-none focus:border-[#C9A84C]/50 transition-colors resize-none";

  const tabs: { key: TabKey; label: string; icon: React.ElementType }[] = [
    { key: "api",     label: "WhatsApp API",         icon: Key },
    { key: "company", label: "بيانات الشركة",        icon: Building2 },
    { key: "ai",      label: "شخصية الذكاء الاصطناعي", icon: Bot },
    { key: "logo",    label: "شعار الشركة",           icon: ImageIcon },
  ];

  return (
    <div className="space-y-8" dir="rtl">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <SettingsIcon className="w-8 h-8 text-[#C9A84C]" />
            الإعدادات
          </h1>
          <p className="text-gray-400 mt-2 text-sm">
            تكوين حسابك وقنوات الاتصال الخاصة بمنصة OmniFlow.
          </p>
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="bg-white/5 border border-white/10 text-white px-4 py-2 rounded-lg flex items-center gap-2 hover:bg-white/10 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          تحديث
        </button>
      </div>

      {/* Load error */}
      {error && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 bg-red-500/10 border border-red-500/40 text-red-300 rounded-xl px-4 py-3 text-sm"
        >
          <span className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            {error}
          </span>
          <button
            onClick={() => void load()}
            className="flex items-center gap-1 text-xs hover:opacity-80"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            إعادة المحاولة
          </button>
        </div>
      )}

      <div className="flex flex-col md:flex-row gap-8">
        {/* Vertical Tabs */}
        <div className="w-full md:w-64 flex flex-col gap-2">
          {tabs.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-3 px-4 py-3 rounded-xl font-medium transition-colors text-right ${
                activeTab === key
                  ? "bg-[#C9A84C]/10 text-[#C9A84C] border border-[#C9A84C]/20"
                  : "text-gray-400 hover:bg-white/5 hover:text-white"
              }`}
            >
              <Icon className="w-5 h-5" />
              {label}
            </button>
          ))}

          {/* Subscription tier badge */}
          {settings && (
            <div className="mt-4 px-4 py-3 rounded-xl bg-[#111827] border border-white/10">
              <p className="text-xs text-gray-500">الباقة الحالية</p>
              <p className="text-sm font-bold text-[#C9A84C] mt-1">
                {TIER_LABELS[settings.subscription_tier] ?? settings.subscription_tier}
              </p>
            </div>
          )}
        </div>

        {/* Tab Content */}
        <div className="flex-1">

          {/* ── Tab 1: WhatsApp API ──────────────────────────────────────── */}
          {activeTab === "api" && (
            <form
              onSubmit={handleSave}
              className="bg-[#111827] border border-white/5 rounded-2xl p-6 md:p-8 shadow-xl space-y-6"
            >
              <h2 className="text-xl font-bold text-white border-b border-white/10 pb-4 flex items-center gap-2">
                <MessageSquare className="w-5 h-5 text-[#C9A84C]" /> إعدادات ربط الواتساب
              </h2>

              {loading ? (
                <div className="space-y-5">
                  <FieldSkeleton />
                  <FieldSkeleton />
                </div>
              ) : (
                <div className="space-y-5">
                  {/* Masked Meta token — read only */}
                  <div>
                    <label className="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-2">
                      <Lock className="w-3.5 h-3.5 text-gray-500" />
                      Meta Access Token (مُقنّع — للقراءة فقط)
                    </label>
                    <input
                      type="text"
                      value={settings?.meta_access_token ?? "غير مربوط"}
                      readOnly
                      aria-readonly="true"
                      className={readOnlyClass}
                      dir="ltr"
                    />
                    <p className="text-xs text-gray-500 mt-2">
                      لأسباب أمنية لا يتم عرض المفتاح كاملاً. لتغييره استخدم صفحة الربط (Onboarding).
                    </p>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-2">
                      Phone Number ID
                    </label>
                    <input
                      type="text"
                      value={phoneNumberId}
                      onChange={(e) => setPhoneNumberId(e.target.value)}
                      placeholder="103...xxxx"
                      className={inputClass}
                      dir="ltr"
                    />
                  </div>

                  {settings?.whatsapp_waba_id && (
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-2">
                        WABA ID
                      </label>
                      <input
                        type="text"
                        value={settings.whatsapp_waba_id}
                        readOnly
                        className={readOnlyClass}
                        dir="ltr"
                      />
                    </div>
                  )}

                  {settings?.whatsapp_phone_number_id && settings?.meta_access_token && (
                    <p className="text-sm text-green-400 flex items-center gap-1.5">
                      <CheckCircle2 className="w-4 h-4" /> القناة مربوطة
                    </p>
                  )}
                </div>
              )}

              {!loading && (
                <div className="pt-6 mt-2 border-t border-white/10 flex flex-wrap items-center gap-4">
                  <button
                    type="submit"
                    disabled={saving || !settings}
                    className="bg-[#C9A84C] text-[#0A0F1C] px-6 py-3 rounded-lg font-bold hover:bg-[#D4B55A] transition-colors flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    {saving ? <Loader2 className="w-5 h-5 animate-spin" /> : <Save className="w-5 h-5" />}
                    حفظ التعديلات
                  </button>
                  <SaveFeedback success={saveSuccess} error={saveError} />
                </div>
              )}
            </form>
          )}

          {/* ── Tab 2: Company Data ──────────────────────────────────────── */}
          {activeTab === "company" && (
            <form
              onSubmit={handleSave}
              className="bg-[#111827] border border-white/5 rounded-2xl p-6 md:p-8 shadow-xl space-y-6"
            >
              <h2 className="text-xl font-bold text-white border-b border-white/10 pb-4 flex items-center gap-2">
                <Building2 className="w-5 h-5 text-[#C9A84C]" /> بيانات الشركة
              </h2>

              {loading ? (
                <div className="space-y-5">
                  <FieldSkeleton />
                  <FieldSkeleton />
                </div>
              ) : (
                <div className="space-y-5">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-2">
                        اسم الشركة
                      </label>
                      <input
                        type="text"
                        value={businessName}
                        onChange={(e) => setBusinessName(e.target.value)}
                        className={inputClass}
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-2">
                        رقم رخصة فال (FAL)
                      </label>
                      <input
                        type="text"
                        value={settings?.fal_license_number ?? "—"}
                        readOnly
                        className={readOnlyClass}
                        dir="ltr"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-2">
                      الحد الأقصى لمحادثات الذكاء الاصطناعي (شهرياً)
                    </label>
                    <input
                      type="number"
                      min={0}
                      value={maxAiConversations}
                      onChange={(e) => setMaxAiConversations(e.target.value)}
                      placeholder="بدون حد"
                      className={inputClass}
                      dir="ltr"
                    />
                  </div>
                </div>
              )}

              {!loading && (
                <div className="pt-6 mt-2 border-t border-white/10 flex flex-wrap items-center gap-4">
                  <button
                    type="submit"
                    disabled={saving || !settings}
                    className="bg-[#C9A84C] text-[#0A0F1C] px-6 py-3 rounded-lg font-bold hover:bg-[#D4B55A] transition-colors flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    {saving ? <Loader2 className="w-5 h-5 animate-spin" /> : <Save className="w-5 h-5" />}
                    حفظ التعديلات
                  </button>
                  <SaveFeedback success={saveSuccess} error={saveError} />
                </div>
              )}
            </form>
          )}

          {/* ── Tab 3: AI Personality ────────────────────────────────────── */}
          {activeTab === "ai" && (
            <form
              onSubmit={handleSaveAiPersonality}
              className="bg-[#111827] border border-white/5 rounded-2xl p-6 md:p-8 shadow-xl space-y-6"
            >
              <div className="border-b border-white/10 pb-4">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <Bot className="w-5 h-5 text-[#C9A84C]" /> شخصية الذكاء الاصطناعي
                </h2>
                <p className="text-gray-400 text-sm mt-2">
                  اكتب نصاً يحدد شخصية المساعد وأسلوبه في التواصل مع عملائك. يُضاف هذا النص
                  تلقائياً في بداية كل محادثة جديدة.
                </p>
              </div>

              {/* اختصار لصفحة قاعدة المعرفة — هنا "الأسلوب"، وهناك "المعلومات". */}
              <Link
                href={`/${localeParam}/knowledge`}
                className="flex items-center justify-between gap-3 bg-[#C9A84C]/10 border border-[#C9A84C]/30 rounded-xl px-4 py-3 hover:bg-[#C9A84C]/15 transition-colors"
              >
                <span className="flex items-start gap-2.5 text-sm text-[#E4D5A5] leading-relaxed">
                  <BookOpen className="w-4 h-4 shrink-0 mt-0.5 text-[#C9A84C]" />
                  <span>
                    هنا تحدد <strong>أسلوب</strong> المساعد فقط. أما{" "}
                    <strong>معلومات شركتك</strong> (الخدمات، الأسعار، الأسئلة الشائعة،
                    المستندات) فمكانها صفحة «معلومات شركتك».
                  </span>
                </span>
                <ArrowLeft className="w-4 h-4 text-[#C9A84C] shrink-0" />
              </Link>

              {loading ? (
                <FieldSkeleton />
              ) : (
                <div className="space-y-4">
                  <label className="block text-sm font-medium text-gray-300">
                    System Prompt (نص الشخصية)
                  </label>
                  <textarea
                    id="ai-system-prompt"
                    rows={12}
                    value={aiPrompt}
                    onChange={(e) => setAiPrompt(e.target.value)}
                    placeholder="مثال: أنت مساعد عقاري محترف يعمل لدى {اسم الشركة}. تتحدث بأسلوب ودي ومهني باللغة العربية..."
                    className={textareaClass}
                    dir="rtl"
                    maxLength={8000}
                  />
                  <div className="flex justify-between text-xs text-gray-500">
                    <span>الحد الأدنى: 10 أحرف</span>
                    <span>{aiPrompt.length} / 8000</span>
                  </div>

                  {/* Placeholder guide */}
                  <div className="bg-white/5 border border-white/10 rounded-xl p-4 text-sm text-gray-400 space-y-1">
                    <p className="text-gray-300 font-medium mb-2">💡 نصائح لكتابة شخصية مميزة:</p>
                    <ul className="list-disc list-inside space-y-1 text-xs">
                      <li>حدد اسم المساعد وانتمائه للشركة</li>
                      <li>اذكر اللهجة المفضلة (فصحى / عامية سعودية)</li>
                      <li>حدد السلوك عند عدم معرفة الإجابة</li>
                      <li>ضع قيوداً على المواضيع خارج نطاق العمل</li>
                    </ul>
                  </div>
                </div>
              )}

              {!loading && (
                <div className="pt-6 mt-2 border-t border-white/10 flex flex-wrap items-center gap-4">
                  <button
                    type="submit"
                    disabled={aiSaving || !settings}
                    className="bg-[#C9A84C] text-[#0A0F1C] px-6 py-3 rounded-lg font-bold hover:bg-[#D4B55A] transition-colors flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    {aiSaving ? <Loader2 className="w-5 h-5 animate-spin" /> : <Bot className="w-5 h-5" />}
                    حفظ الشخصية
                  </button>
                  <SaveFeedback success={aiSaveSuccess} error={aiSaveError} />
                </div>
              )}
            </form>
          )}

          {/* ── Tab 4: Logo Upload ───────────────────────────────────────── */}
          {activeTab === "logo" && (
            <div className="bg-[#111827] border border-white/5 rounded-2xl p-6 md:p-8 shadow-xl space-y-6">
              <div className="border-b border-white/10 pb-4">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <ImageIcon className="w-5 h-5 text-[#C9A84C]" /> شعار الشركة
                </h2>
                <p className="text-gray-400 text-sm mt-2">
                  ارفع شعار شركتك ليظهر في التقارير والواجهات المُرسلة للعملاء.
                  الصيغ المقبولة: PNG، JPEG، WebP، SVG — الحجم الأقصى 5 ميغابايت.
                </p>
              </div>

              {loading ? (
                <FieldSkeleton />
              ) : (
                <div className="space-y-6">
                  {/* Current / Preview Logo */}
                  <div className="flex flex-col items-center gap-4">
                    {logoPreview ? (
                      <div className="relative group">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={logoPreview}
                          alt="شعار الشركة"
                          className="w-40 h-40 object-contain rounded-2xl border border-white/10 bg-white/5 p-3"
                        />
                        <button
                          type="button"
                          onClick={handleLogoClear}
                          className="absolute -top-2 -left-2 bg-red-500/80 hover:bg-red-500 text-white rounded-full p-1 opacity-0 group-hover:opacity-100 transition-opacity"
                          title="إزالة الشعار"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ) : (
                      <div className="w-40 h-40 rounded-2xl border-2 border-dashed border-white/20 bg-white/5 flex flex-col items-center justify-center gap-2 text-gray-500">
                        <ImageIcon className="w-10 h-10" />
                        <span className="text-xs">لا يوجد شعار</span>
                      </div>
                    )}
                  </div>

                  {/* File picker */}
                  <div
                    className="border-2 border-dashed border-white/20 rounded-xl p-6 text-center cursor-pointer hover:border-[#C9A84C]/40 hover:bg-[#C9A84C]/5 transition-all"
                    onClick={() => fileInputRef.current?.click()}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && fileInputRef.current?.click()}
                  >
                    <Upload className="w-8 h-8 text-gray-500 mx-auto mb-2" />
                    <p className="text-gray-400 text-sm">
                      اضغط لاختيار ملف أو اسحبه هنا
                    </p>
                    <p className="text-gray-600 text-xs mt-1">
                      PNG, JPEG, WebP, SVG — حتى 5 MB
                    </p>
                    <input
                      ref={fileInputRef}
                      id="logo-file-input"
                      type="file"
                      accept="image/png,image/jpeg,image/webp,image/svg+xml"
                      className="hidden"
                      onChange={handleLogoChange}
                    />
                  </div>

                  {/* Upload button */}
                  <div className="flex flex-wrap items-center gap-4">
                    <button
                      type="button"
                      id="logo-upload-btn"
                      disabled={logoUploading || !settings}
                      onClick={handleLogoUpload}
                      className="bg-[#C9A84C] text-[#0A0F1C] px-6 py-3 rounded-lg font-bold hover:bg-[#D4B55A] transition-colors flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                      {logoUploading ? (
                        <Loader2 className="w-5 h-5 animate-spin" />
                      ) : (
                        <Upload className="w-5 h-5" />
                      )}
                      رفع الشعار
                    </button>
                    <SaveFeedback success={logoSuccess} error={logoError} />
                  </div>
                </div>
              )}
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
