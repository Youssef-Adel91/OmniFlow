"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
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
} from "lucide-react";
// Auth is fully handled by Clerk: `apiClient` attaches the Clerk bearer token
// automatically, and `clerkMiddleware` protects this route. There is no local
// token/cookie to write here anymore (the old `_writeAuthCookie` helper and the
// legacy tenantStore auth thunks were removed in the Sprint 14 unification).
import { apiClient } from "@/lib/api/client";

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

// ── Main Page ────────────────────────────────────────────────────────────────

export default function OnboardingPage({
  params,
}: {
  params: { locale: string };
}) {
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

  // ── Self-Service Submit ────────────────────────────────────────────────────
  const handleSelfServiceSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoadingSelfService(true);

    try {
      await apiClient.patch("/tenants/onboarding", {
        option: "self_service",
        meta_access_token: formData.metaAccessToken,
        whatsapp_phone_number_id: formData.phoneId,
        whatsapp_waba_id: formData.wabaId,
      });

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
    setIsLoadingWhiteGlove(true);

    try {
      await apiClient.patch("/tenants/onboarding", { option: "white_glove" });

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

              <div className="bg-white/5 border border-white/10 rounded-xl p-4 mb-8 text-right">
                <p className="text-xs text-gray-500 mb-1">رقم الطلب</p>
                <p className="text-white font-mono text-sm">
                  WG-{Math.random().toString(36).toUpperCase().slice(2, 10)}
                </p>
              </div>

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

      <div
        className="min-h-screen flex flex-col md:flex-row bg-[#0A0F1C] text-white"
        dir="rtl"
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
              <span>Developer Mode</span>
            </div>

            <h2 className="text-4xl font-bold mb-4 text-white">الربط الذاتي</h2>
            <p className="text-gray-400 text-lg leading-relaxed mb-8">
              إذا كان لديك حساب Meta Business Manager موثق وأرقام جاهزة، يمكنك
              إدخال المفاتيح مباشرة للبدء فوراً.
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
          </div>
        </div>
      </div>
    </>
  );
}
