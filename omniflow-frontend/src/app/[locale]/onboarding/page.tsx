"use client";

import React, { useState, useEffect } from "react";
import { useRouter, useParams } from "next/navigation";
import {
  ShieldCheck,
  Calendar,
  Key,
  Settings,
  ArrowLeft,
  Loader2,
  CheckCircle2,
  X,
  Zap,
  Building2,
  Link2,
  ChevronDown,
  AlertCircle,
} from "lucide-react";
// Auth is fully handled by Clerk: `apiClient` attaches the Clerk bearer token
// automatically, and `clerkMiddleware` protects this route. There is no local
// token/cookie to write here anymore (the old `_writeAuthCookie` helper and the
// legacy tenantStore auth thunks were removed in the Sprint 14 unification).
import { apiClient } from "@/lib/api/client";
import {
  startFacebookConnect,
  selectFacebookPage,
  type FacebookPageOption,
} from "@/lib/api/facebookOauth";

// ── Simple in-page Toast ─────────────────────────────────────────────────────
type ToastType = "success" | "error";

interface Toast {
  id: number;
  type: ToastType;
  message: string;
}

let _toastId = 0;

function ToastContainer({
  toasts,
  onRemove,
}: {
  toasts: Toast[];
  onRemove: (id: number) => void;
}) {
  return (
    <div className="fixed top-6 left-1/2 -translate-x-1/2 z-[9999] flex flex-col gap-3 items-center w-full max-w-md px-4">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`w-full flex items-start gap-3 px-5 py-4 rounded-xl shadow-2xl border text-sm font-medium animate-in fade-in slide-in-from-top-4 duration-300 ${
            t.type === "success"
              ? "bg-green-900/90 border-green-500/40 text-green-100"
              : "bg-red-900/90 border-red-500/40 text-red-100"
          }`}
          dir="rtl"
        >
          {t.type === "success" ? (
            <CheckCircle2 className="w-5 h-5 text-green-400 shrink-0 mt-0.5" />
          ) : (
            <X className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
          )}
          <span className="flex-1">{t.message}</span>
          <button
            onClick={() => onRemove(t.id)}
            className="opacity-60 hover:opacity-100 transition-opacity"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      ))}
    </div>
  );
}

// Real-time "N / limit" counter so a business owner sees they're near the
// backend's max length before submitting, instead of a bare 422 after
// writing a full answer. Turns red past the limit rather than blocking
// typing outright (paste/IME input shouldn't get silently truncated).
function CharCounter({ value, limit }: { value: string; limit: number }) {
  const over = value.length > limit;
  return (
    <span className={`text-xs ${over ? "text-red-400" : "text-gray-500"}`}>
      {value.length.toLocaleString("ar")} / {limit.toLocaleString("ar")}
    </span>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function OnboardingPage() {
  const params = useParams<{ locale: string }>();
  const router = useRouter();

  const [isLoadingSelfService, setIsLoadingSelfService] = useState(false);
  const [isLoadingWhiteGlove, setIsLoadingWhiteGlove] = useState(false);
  const [showWhiteGloveSuccess, setShowWhiteGloveSuccess] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [formData, setFormData] = useState({
    metaAccessToken: "",
    phoneId: "",
    wabaId: "",
  });
  // "Tell us about your business" — previously missing from onboarding
  // entirely; this is what actually feeds the AI's company-knowledge block
  // (see IMPLEMENTATION_STATUS.md). Shared by both onboarding paths below.
  const [businessProfile, setBusinessProfile] = useState({
    businessName: "",
    businessCategory: "",
    aboutText: "",
    productsText: "", // comma-separated, split into a list on submit
    detailedInstructions: "",
    // Distinct from detailedInstructions: that field feeds the AI's
    // company-FACTS block (policies/FAQs, capped inside a shared 2500-char
    // knowledge block). This feeds Tenant.custom_ai_instructions — a
    // separate, uncapped column composed together with the base persona
    // (ai_system_prompt) in company_context.get_tenant_persona() — see
    // tenants.py's update_tenant_onboarding.
    aiInstructions: "",
  });
  // Backend hard limits (schemas.py TenantOnboardingUpdate) — mirrored here
  // so a business owner sees they're near/over a limit before submitting,
  // instead of typing a full answer and getting an opaque 422 on save.
  const FIELD_LIMITS = {
    aboutText: 2000,
    detailedInstructions: 4000,
    aiInstructions: 4000,
  } as const;
  const [knowledgeFiles, setKnowledgeFiles] = useState<File[]>([]);
  const [isUploadingFiles, setIsUploadingFiles] = useState(false);
  const businessNameValid = businessProfile.businessName.trim().length >= 2;

  // "Connect with Facebook" (Messenger + Instagram) -- replaces a manual
  // form for this part of onboarding. See lib/api/facebookOauth.ts.
  const [isConnectingFacebook, setIsConnectingFacebook] = useState(false);
  const [facebookConnected, setFacebookConnected] = useState<
    { pageName: string; instagramUsername: string | null } | null
  >(null);
  const [facebookPagePicker, setFacebookPagePicker] = useState<
    { connectionId: string; pages: FacebookPageOption[] } | null
  >(null);
  const [isSelectingPage, setIsSelectingPage] = useState(false);
  // WhatsApp still has no OAuth equivalent yet (Embedded Signup is a later
  // phase) -- the manual Developer Mode form below stays as the only way to
  // connect it, just demoted behind this toggle instead of always-visible.
  const [showManualWhatsappForm, setShowManualWhatsappForm] = useState(false);

  // Returns an Arabic label naming the first field over its backend limit,
  // or null if everything fits — used to block submission with one clear
  // toast instead of the raw Pydantic 422 the API would otherwise return.
  const FIELD_LABELS: Record<keyof typeof FIELD_LIMITS, string> = {
    aboutText: "نبذة عن الشركة",
    detailedInstructions: "سياسات الشركة والأسئلة الشائعة",
    aiInstructions: "تعليمات سلوك المساعد الذكي",
  };
  const overLimitFieldLabel = (): string | null => {
    for (const key of Object.keys(FIELD_LIMITS) as (keyof typeof FIELD_LIMITS)[]) {
      if (businessProfile[key].length > FIELD_LIMITS[key]) {
        return FIELD_LABELS[key];
      }
    }
    return null;
  };

  const businessProfilePayload = () => {
    const products = businessProfile.productsText
      .split(",")
      .map((p) => p.trim())
      .filter(Boolean);
    return {
      business_name: businessProfile.businessName.trim(),
      business_category: businessProfile.businessCategory.trim() || undefined,
      about_text: businessProfile.aboutText.trim() || undefined,
      products: products.length ? products : undefined,
      detailed_instructions: businessProfile.detailedInstructions.trim() || undefined,
      ai_instructions: businessProfile.aiInstructions.trim() || undefined,
    };
  };

  // Uploads whatever files were attached in the business-profile section to
  // the real Knowledge Base pipeline (S3 -> Celery -> chunk/embed -> Qdrant)
  // — the same one the dashboard's separate Knowledge screen uses. Best
  // effort: a failed file doesn't block finishing onboarding, since the
  // WhatsApp link (the other half of this form) already succeeded by the
  // time this runs.
  const uploadKnowledgeFiles = async () => {
    if (knowledgeFiles.length === 0) return;
    setIsUploadingFiles(true);
    const { uploadKnowledgeDocument } = await import("@/lib/api/knowledge");
    let failed = 0;
    for (const file of knowledgeFiles) {
      try {
        await uploadKnowledgeDocument(file);
      } catch {
        failed += 1;
      }
    }
    setIsUploadingFiles(false);
    if (failed > 0) {
      addToast("error", `فشل رفع ${failed} من ${knowledgeFiles.length} ملف. يمكنك إعادة المحاولة من صفحة قاعدة المعرفة.`);
    } else {
      addToast("success", `تم رفع ${knowledgeFiles.length} ملف بنجاح إلى قاعدة المعرفة.`);
    }
  };
  // Auto-dismiss toasts after 5 s
  useEffect(() => {
    if (toasts.length === 0) return;
    const timer = setTimeout(() => {
      setToasts((prev) => prev.slice(1));
    }, 5000);
    return () => clearTimeout(timer);
  }, [toasts]);

  const addToast = (type: ToastType, message: string) => {
    const id = ++_toastId;
    setToasts((prev) => [...prev, { id, type, message }]);
  };

  const removeToast = (id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  // ── Connect with Facebook (Messenger + Instagram) ──────────────────────────
  const handleConnectFacebook = async () => {
    setIsConnectingFacebook(true);
    try {
      const result = await startFacebookConnect();
      if (result.status === "success") {
        setFacebookConnected({
          pageName: result.page_name,
          instagramUsername: result.instagram_username,
        });
        addToast("success", `تم ربط صفحة "${result.page_name}" بنجاح 🎉`);
      } else if (result.status === "needs_selection") {
        setFacebookPagePicker({ connectionId: result.connection_id, pages: result.pages });
      } else if (result.status === "error") {
        addToast("error", result.message);
      }
      // "cancelled" (user closed the popup) — no toast, they clearly meant to stop.
    } catch (err: any) {
      const detail = err.response?.data?.detail || "تعذّر بدء عملية الربط بفيسبوك. حاول مرة أخرى.";
      addToast("error", typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setIsConnectingFacebook(false);
    }
  };

  const handleSelectFacebookPage = async (pageId: string) => {
    if (!facebookPagePicker) return;
    setIsSelectingPage(true);
    try {
      const page = await selectFacebookPage(facebookPagePicker.connectionId, pageId);
      setFacebookConnected({
        pageName: page.page_name,
        instagramUsername: page.instagram_username,
      });
      setFacebookPagePicker(null);
      addToast("success", `تم ربط صفحة "${page.page_name}" بنجاح 🎉`);
    } catch (err: any) {
      const detail = err.response?.data?.detail || "تعذّر إتمام ربط الصفحة. حاول مرة أخرى.";
      addToast("error", typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setIsSelectingPage(false);
    }
  };

  // ── Self-Service Submit ────────────────────────────────────────────────────
  const handleSelfServiceSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!businessNameValid) {
      addToast("error", "من فضلك أدخل اسم الشركة أولاً (في القسم أعلاه).");
      return;
    }
    const overLimit = overLimitFieldLabel();
    if (overLimit) {
      addToast("error", `حقل "${overLimit}" يتجاوز الحد الأقصى المسموح — يرجى تقليل النص.`);
      return;
    }
    setIsLoadingSelfService(true);

    try {
      await apiClient.patch("/tenants/onboarding", {
        option: "self_service",
        ...businessProfilePayload(),
        meta_access_token: formData.metaAccessToken,
        whatsapp_phone_number_id: formData.phoneId,
        whatsapp_waba_id: formData.wabaId,
      });
      await uploadKnowledgeFiles();

      addToast("success", "🎉 تم ربط قنواتك بنجاح! جاري الانتقال إلى لوحة التحكم...");

      // Bulletproof redirect: try Next.js router first, then hard-navigate as fallback
      setTimeout(() => {
        router.push(`/${params.locale}/inbox`);
        // Fallback in case Next.js routing is cached/stuck during the demo
        if (typeof window !== "undefined") {
          window.location.href = `/${params.locale}/inbox`;
        }
      }, 1500);
    } catch (err: any) {
      const detail =
        err.response?.data?.detail || "فشل إتمام الربط الذاتي. يرجى المحاولة مجدداً.";
      addToast("error", typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setIsLoadingSelfService(false);
    }
  };

  // ── White-Glove Submit ─────────────────────────────────────────────────────
  const handleWhiteGloveSubmit = async () => {
    if (!businessNameValid) {
      addToast("error", "من فضلك أدخل اسم الشركة أولاً (في القسم أعلاه).");
      return;
    }
    const overLimit = overLimitFieldLabel();
    if (overLimit) {
      addToast("error", `حقل "${overLimit}" يتجاوز الحد الأقصى المسموح — يرجى تقليل النص.`);
      return;
    }
    setIsLoadingWhiteGlove(true);

    try {
      await apiClient.patch("/tenants/onboarding", {
        option: "white_glove",
        ...businessProfilePayload(),
      });
      await uploadKnowledgeFiles();

      // Show full-screen success state instead of navigating immediately
      setShowWhiteGloveSuccess(true);
    } catch (err: any) {
      const detail =
        err.response?.data?.detail ||
        "فشل تسجيل الطلب. يرجى المحاولة مجدداً.";
      addToast("error", typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setIsLoadingWhiteGlove(false);
    }
  };

  // ── White-Glove Success Screen ─────────────────────────────────────────────
  if (showWhiteGloveSuccess) {
    return (
      <>
        <ToastContainer toasts={toasts} onRemove={removeToast} />
        <div
          className="min-h-screen bg-[#0A0F1C] flex flex-col items-center justify-center p-6 text-center"
          dir="rtl"
        >
          <div className="bg-[#111827] border border-[#C9A84C]/30 p-12 rounded-3xl max-w-lg shadow-2xl shadow-[#C9A84C]/5 relative overflow-hidden">
            {/* Glow */}
            <div className="absolute inset-0 bg-gradient-to-br from-[#C9A84C]/10 via-transparent to-transparent pointer-events-none" />

            <div className="relative z-10">
              <div className="w-20 h-20 rounded-full bg-[#C9A84C]/10 border border-[#C9A84C]/30 flex items-center justify-center mx-auto mb-6">
                <CheckCircle2 className="w-10 h-10 text-[#C9A84C]" />
              </div>

              <h2 className="text-3xl font-bold text-white mb-3">
                تم تسجيل طلبك بنجاح!
              </h2>
              <p className="text-gray-400 text-base leading-relaxed mb-2">
                سيتواصل معك أحد خبرائنا قريباً لإتمام عملية الربط الشامل.
              </p>
              <p className="text-[#C9A84C]/80 text-sm mb-8">
                تم إضافتك إلى قائمة الأولويات VIP — متوسط وقت الاستجابة أقل من 24 ساعة.
              </p>

              <button
                onClick={() => router.push(`/${params.locale}/inbox`)}
                className="w-full bg-[#C9A84C] text-[#0A0F1C] py-4 rounded-xl font-bold text-lg hover:bg-[#D4B55A] transition-all hover:shadow-lg hover:shadow-[#C9A84C]/20 flex items-center justify-center gap-2"
              >
                <Zap className="w-5 h-5" />
                الذهاب إلى لوحة التحكم
              </button>
            </div>
          </div>
        </div>
      </>
    );
  }

  // ── Main Onboarding Screen ─────────────────────────────────────────────────
  return (
    <>
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      <div className="min-h-screen bg-[#0A0F1C] text-white" dir="rtl">
        {/* ── Business Profile (shared by both paths below) ────────────────── */}
        <div className="max-w-3xl mx-auto px-6 pt-10 pb-2 md:pt-14">
          <div className="bg-[#111827] border border-white/10 rounded-2xl p-6 md:p-8">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#C9A84C]/10 border border-[#C9A84C]/20 text-[#C9A84C] text-sm mb-4">
              <Building2 className="w-4 h-4" />
              <span>عن شركتك</span>
            </div>
            <p className="text-gray-400 text-sm mb-5">
              هذه المعلومات هي ما يعتمد عليه المساعد الذكي في الرد على عملائك — عبّئها هنا قبل ربط واتساب.
            </p>
            <div className="grid md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  اسم الشركة <span className="text-[#C9A84C]">*</span>
                </label>
                <input
                  type="text"
                  required
                  value={businessProfile.businessName}
                  onChange={(e) =>
                    setBusinessProfile({ ...businessProfile, businessName: e.target.value })
                  }
                  placeholder="مثال: نعيم"
                  className="w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent transition-all"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  التصنيف / المجال
                </label>
                <input
                  type="text"
                  value={businessProfile.businessCategory}
                  onChange={(e) =>
                    setBusinessProfile({ ...businessProfile, businessCategory: e.target.value })
                  }
                  placeholder="مثال: أجهزة مساج احترافية"
                  className="w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent transition-all"
                />
              </div>
            </div>
            <div className="mt-4">
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-gray-300">
                  نبذة عن الشركة
                </label>
                <CharCounter value={businessProfile.aboutText} limit={FIELD_LIMITS.aboutText} />
              </div>
              <textarea
                value={businessProfile.aboutText}
                onChange={(e) =>
                  setBusinessProfile({ ...businessProfile, aboutText: e.target.value })
                }
                rows={4}
                placeholder="اكتب قصة علامتك التجارية بإيجاز... مثال: متجر متخصص في أجهزة المساج المنزلي منذ 2019، نستهدف العملاء الباحثين عن حلول علاج طبيعي بالمنزل."
                className="w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent transition-all resize-none"
              />
            </div>
            <div className="mt-4">
              <label className="block text-sm font-medium text-gray-300 mb-2">
                المنتجات (افصل بينها بفاصلة)
              </label>
              <input
                type="text"
                value={businessProfile.productsText}
                onChange={(e) =>
                  setBusinessProfile({ ...businessProfile, productsText: e.target.value })
                }
                placeholder="مثال: أجهزة مساج القدم، أجهزة مساج الركبة"
                className="w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent transition-all"
              />
            </div>
            <div className="mt-4">
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-gray-300">
                  سياسات الشركة والأسئلة الشائعة{" "}
                  <span className="text-gray-500 font-normal">(معلومات حقيقية عن عملك)</span>
                </label>
                <CharCounter
                  value={businessProfile.detailedInstructions}
                  limit={FIELD_LIMITS.detailedInstructions}
                />
              </div>
              <textarea
                value={businessProfile.detailedInstructions}
                onChange={(e) =>
                  setBusinessProfile({ ...businessProfile, detailedInstructions: e.target.value })
                }
                rows={4}
                placeholder="مثال: سياسة الاستبدال والاسترجاع خلال 14 يوم، مواعيد الشحن (2-5 أيام عمل)، أسعار المنتجات، كيفية الرد على استفسارات الضمان..."
                className="w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent transition-all resize-none"
              />
              <p className="text-xs text-gray-500 mt-1.5">
                هذه المعلومات "الحقائق" — ما هو صحيح عن شركتك. لتحديد كيف يتحدث المساعد ويتعامل مع العملاء، استخدم الحقل التالي.
              </p>
            </div>
            <div className="mt-4">
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-gray-300">
                  تعليمات خاصة لسلوك المساعد الذكي مع العملاء{" "}
                  <span className="text-gray-500 font-normal">(اختياري)</span>
                </label>
                <CharCounter
                  value={businessProfile.aiInstructions}
                  limit={FIELD_LIMITS.aiInstructions}
                />
              </div>
              <textarea
                value={businessProfile.aiInstructions}
                onChange={(e) =>
                  setBusinessProfile({ ...businessProfile, aiInstructions: e.target.value })
                }
                rows={4}
                placeholder={
                  "مثال: تحدث بأسلوب ودود وغير رسمي مع لهجة سعودية خفيفة. إذا اعترض العميل على السعر، اذكر أن هناك خصم 10% عند الشراء من موقعنا وضمان استرجاع 30 يوم. اسأل دائماً إذا كان العميل يريد إتمام الطلب الآن قبل إنهاء المحادثة. لا تناقش أسعار المنافسين."
                }
                className="w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent transition-all resize-none"
              />
              <p className="text-xs text-gray-500 mt-1.5">
                نغمة الحديث، كيفية التعامل مع تردد العميل أو اعتراضه على السعر، وكيفية توجيه المحادثة لإتمام عملية الشراء. هذا يغيّر فعلياً طريقة رد المساعد الذكي على عملائك — يمكنك تعديله في أي وقت من الإعدادات لاحقاً.
              </p>
            </div>
            <div className="mt-4">
              <label className="block text-sm font-medium text-gray-300 mb-2">
                ملفات إضافية (كتالوج، قائمة أسعار، أسئلة شائعة — PDF/DOCX/TXT/CSV، حتى 20 ميجا لكل ملف)
              </label>
              <input
                type="file"
                multiple
                accept=".pdf,.docx,.txt,.csv"
                onChange={(e) => setKnowledgeFiles(Array.from(e.target.files || []))}
                className="w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-gray-300 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-[#C9A84C]/10 file:text-[#C9A84C] file:cursor-pointer hover:file:bg-[#C9A84C]/20"
              />
              {knowledgeFiles.length > 0 && (
                <p className="text-xs text-gray-500 mt-2">
                  {knowledgeFiles.length} ملف جاهز للرفع — سيُرفع تلقائيًا بعد إتمام الربط، ويُعالج من قاعدة المعرفة (استخراج → تقسيم → فهرسة).
                </p>
              )}
              {isUploadingFiles && (
                <p className="text-xs text-[#C9A84C] mt-2 flex items-center gap-2">
                  <Loader2 className="w-3 h-3 animate-spin" /> جاري رفع الملفات...
                </p>
              )}
            </div>
          </div>
        </div>

      <div
        className="flex flex-col md:flex-row"
      >
        {/* ── Left: White-Glove ─────────────────────────────────────────── */}
        <div className="flex-1 p-8 md:p-16 flex flex-col justify-center border-b md:border-b-0 md:border-l border-white/10 relative overflow-hidden">
          <div className="absolute top-0 right-0 w-full h-full bg-gradient-to-br from-[#C9A84C]/5 to-transparent pointer-events-none" />

          <div className="relative z-10 max-w-md mx-auto">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#C9A84C]/10 border border-[#C9A84C]/20 text-[#C9A84C] text-sm mb-6">
              <ShieldCheck className="w-4 h-4" />
              <span>VIP Service</span>
            </div>

            <h1 className="text-4xl font-bold mb-4 text-white">مساعدة الخبراء</h1>
            <p className="text-gray-400 text-lg leading-relaxed mb-8">
              دع فريق المهندسين لدينا يتكفل بجميع الخطوات التقنية. سنقوم بإنشاء
              الحسابات، ربط الأرقام، وتكوين منظومة OmniFlow AI بالكامل نيابة عنك.
            </p>

            <ul className="space-y-4 mb-10">
              {[
                "إعداد Meta Business Manager",
                "توثيق حساب الواتساب الرسمي",
                "ربط قنوات التواصل الإضافية",
                "جلسة تدريبية مخصصة لفريقك",
              ].map((item, idx) => (
                <li key={idx} className="flex items-center gap-3 text-gray-300">
                  <div className="w-2 h-2 rounded-full bg-[#C9A84C] shrink-0" />
                  {item}
                </li>
              ))}
            </ul>

            <button
              onClick={handleWhiteGloveSubmit}
              disabled={isLoadingWhiteGlove}
              className="w-full group bg-white/5 hover:bg-[#C9A84C]/10 border border-white/10 hover:border-[#C9A84C]/30 p-4 rounded-xl flex items-center justify-between transition-all disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <div className="flex items-center gap-3">
                <Calendar className="w-6 h-6 text-[#C9A84C]" />
                <span className="font-semibold text-lg">حجز موعد مع خبير</span>
              </div>
              {isLoadingWhiteGlove ? (
                <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
              ) : (
                <ArrowLeft className="w-5 h-5 text-gray-400 group-hover:-translate-x-1 transition-transform" />
              )}
            </button>

            <p className="text-center text-xs text-gray-600 mt-4">
              سيتم التواصل معك خلال 24 ساعة عمل
            </p>
          </div>
        </div>

        {/* ── Right: Self-Service ───────────────────────────────────────── */}
        <div className="flex-1 p-8 md:p-16 flex flex-col justify-center relative overflow-hidden bg-[#0A0F1C]/50">
          <div className="max-w-md mx-auto w-full relative z-10">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 text-sm mb-6">
              <Settings className="w-4 h-4" />
              <span>الربط الذاتي</span>
            </div>

            <h2 className="text-4xl font-bold mb-4 text-white">ماسنجر وإنستجرام</h2>
            <p className="text-gray-400 text-lg leading-relaxed mb-6">
              اربط حسابك على فيسبوك مرة واحدة — هنكتشف صفحاتك وحساب
              إنستجرام المرتبط تلقائيًا، من غير ما تدخل أي أكواد يدويًا.
            </p>

            {facebookConnected ? (
              <div className="bg-green-900/20 border border-green-500/30 rounded-xl p-5 mb-6">
                <div className="flex items-center gap-2 text-green-400 font-semibold mb-1">
                  <CheckCircle2 className="w-5 h-5" />
                  <span>تم الربط: {facebookConnected.pageName}</span>
                </div>
                {facebookConnected.instagramUsername && (
                  <p className="text-sm text-gray-400 mr-7">
                    حساب إنستجرام المرتبط: @{facebookConnected.instagramUsername}
                  </p>
                )}
                {!facebookConnected.instagramUsername && (
                  <p className="text-xs text-gray-500 mr-7 mt-1">
                    لا يوجد حساب إنستجرام تجاري مرتبط بهذه الصفحة حاليًا — يمكنك
                    ربطه لاحقًا من إعدادات صفحتك على فيسبوك ثم إعادة الربط هنا.
                  </p>
                )}
              </div>
            ) : facebookPagePicker ? (
              <div className="bg-[#111827] border border-white/10 rounded-xl p-5 mb-6">
                <p className="text-sm text-gray-300 mb-3 flex items-center gap-2">
                  <AlertCircle className="w-4 h-4 text-[#C9A84C] shrink-0" />
                  لديك أكثر من صفحة فيسبوك — اختر الصفحة اللي عايز تربطها:
                </p>
                <div className="space-y-2">
                  {facebookPagePicker.pages.map((page) => (
                    <button
                      key={page.page_id}
                      type="button"
                      disabled={isSelectingPage}
                      onClick={() => handleSelectFacebookPage(page.page_id)}
                      className="w-full text-right bg-white/5 hover:bg-[#C9A84C]/10 border border-white/10 hover:border-[#C9A84C]/30 rounded-lg p-3 transition-all disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-between"
                    >
                      <span>
                        <span className="block font-medium text-white">{page.page_name}</span>
                        {page.instagram_username && (
                          <span className="block text-xs text-gray-500">
                            @{page.instagram_username}
                          </span>
                        )}
                      </span>
                      {isSelectingPage ? (
                        <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                      ) : (
                        <ArrowLeft className="w-4 h-4 text-gray-500" />
                      )}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={handleConnectFacebook}
                disabled={isConnectingFacebook}
                className="w-full bg-[#1877F2] hover:bg-[#1465D1] text-white py-4 rounded-xl font-bold text-lg transition-all flex items-center justify-center gap-2 mb-6 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {isConnectingFacebook ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <Link2 className="w-5 h-5" />
                )}
                <span>اربط بفيسبوك</span>
              </button>
            )}

            <div className="border-t border-white/10 my-6" />

            <button
              type="button"
              onClick={() => setShowManualWhatsappForm((v) => !v)}
              className="w-full flex items-center justify-between text-gray-400 hover:text-gray-200 transition-colors mb-2"
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <Key className="w-4 h-4" />
                ربط واتساب يدويًا (Developer Mode)
              </span>
              <ChevronDown
                className={`w-4 h-4 transition-transform ${showManualWhatsappForm ? "rotate-180" : ""}`}
              />
            </button>
            <p className="text-xs text-gray-600 mb-4">
              واتساب لسه محتاج ربط يدوي حاليًا (Embedded Signup زي فيسبوك قريبًا).
            </p>

            {showManualWhatsappForm && (
            <>
            <p className="text-gray-400 text-base leading-relaxed mb-2">
              إذا كان لديك حساب Meta Business Manager موثق وأرقام جاهزة، يمكنك
              إدخال المفاتيح مباشرة للبدء فوراً.
            </p>
            <p className="text-gray-500 text-sm leading-relaxed mb-6">
              القيم الثلاثة دي كلها بتلاقيها في نفس الصفحة:{" "}
              <a
                href="https://developers.facebook.com/docs/whatsapp/cloud-api/get-started"
                target="_blank"
                rel="noopener noreferrer"
                className="text-[#C9A84C] underline hover:text-[#D4B55A]"
              >
                لوحة تطبيقك على Meta for Developers ← WhatsApp ← API Setup
              </a>
              .
            </p>

            <form onSubmit={handleSelfServiceSubmit} className="space-y-5">
              {/* Meta Access Token */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Meta Access Token
                </label>
                <div className="relative">
                  <Key className="absolute right-3 top-3.5 w-5 h-5 text-gray-500" />
                  <input
                    type="text"
                    required
                    value={formData.metaAccessToken}
                    onChange={(e) =>
                      setFormData({ ...formData, metaAccessToken: e.target.value })
                    }
                    placeholder="EAA...xxxx"
                    className={`w-full bg-[#111827] border rounded-lg py-3 pr-10 pl-4 text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent transition-all ${
                      formData.metaAccessToken
                        ? "border-[#C9A84C]/40"
                        : "border-white/10"
                    }`}
                    dir="ltr"
                  />
                </div>
                <p className="text-xs text-gray-500 mt-1.5">
                  توكن الوصول (Access Token) من نفس صفحة API Setup — توكن
                  مؤقت صالح 24 ساعة للتجربة، أو توكن دائم لو عندك System User
                  على Business Manager.
                </p>
              </div>

              {/* Phone Number ID */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Phone Number ID
                </label>
                <input
                  type="text"
                  required
                  value={formData.phoneId}
                  onChange={(e) =>
                    setFormData({ ...formData, phoneId: e.target.value })
                  }
                  placeholder="103...xxxx"
                  className={`w-full bg-[#111827] border rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent transition-all ${
                    formData.phoneId ? "border-[#C9A84C]/40" : "border-white/10"
                  }`}
                  dir="ltr"
                />
                <p className="text-xs text-gray-500 mt-1.5">
                  معرّف تقني من ميتا لرقم واتساب الخاص بك — مش رقم التليفون
                  نفسه. موجود تحت "From" في صفحة API Setup.
                </p>
              </div>

              {/* WABA ID */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  WhatsApp Business Account ID
                </label>
                <input
                  type="text"
                  required
                  value={formData.wabaId}
                  onChange={(e) =>
                    setFormData({ ...formData, wabaId: e.target.value })
                  }
                  placeholder="104...xxxx"
                  className={`w-full bg-[#111827] border rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent transition-all ${
                    formData.wabaId ? "border-[#C9A84C]/40" : "border-white/10"
                  }`}
                  dir="ltr"
                />
                <p className="text-xs text-gray-500 mt-1.5">
                  معرّف حساب الواتساب التجاري نفسه (WABA) — مختلف عن Phone
                  Number ID أعلاه. تلاقيه في نفس صفحة API Setup تحت اسم حسابك.
                </p>
              </div>

              <button
                type="submit"
                disabled={isLoadingSelfService}
                className="w-full bg-[#C9A84C] text-[#0A0F1C] py-4 rounded-xl font-bold text-lg hover:bg-[#D4B55A] transition-all hover:shadow-lg hover:shadow-[#C9A84C]/20 flex items-center justify-center gap-2 mt-4 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {isLoadingSelfService ? (
                  <>
                    <Loader2 className="w-5 h-5 animate-spin" />
                    <span>جاري التحقق...</span>
                  </>
                ) : (
                  <>
                    <Zap className="w-5 h-5" />
                    <span>التحقق والحفظ</span>
                  </>
                )}
              </button>
            </form>
            </>
            )}
          </div>
        </div>
      </div>
      </div>
    </>
  );
}
