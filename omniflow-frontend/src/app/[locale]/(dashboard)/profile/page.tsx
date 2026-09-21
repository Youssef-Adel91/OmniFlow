"use client";

/**
 * app/[locale]/(dashboard)/profile/page.tsx — Current user profile
 *
 * Wired to `lib/api/users.ts`:
 *   GET   /users/me  on mount
 *   PATCH /users/me  on save (full_name, phone_number)
 *
 * Email, password and MFA are owned by Clerk — this page links out to the
 * Clerk-hosted account portal instead of rendering a fake password form.
 */

import React, { useCallback, useEffect, useState } from "react";
import { useUser, useClerk } from "@clerk/nextjs";
import {
  User,
  Mail,
  ShieldAlert,
  KeyRound,
  Save,
  Phone,
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Loader2,
} from "lucide-react";
import {
  fetchCurrentUser,
  updateCurrentUser,
  ROLE_LABELS,
  type CurrentUser,
} from "@/lib/api/users";

function FieldSkeleton() {
  return (
    <div className="space-y-2">
      <div className="h-4 w-28 rounded bg-white/5 animate-pulse" />
      <div className="h-12 w-full rounded-lg bg-white/5 animate-pulse" />
    </div>
  );
}

export default function ProfilePage() {
  const { user } = useUser();
  const { openUserProfile } = useClerk();

  const [profile, setProfile] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  const [fullName, setFullName]       = useState("");
  const [phoneNumber, setPhoneNumber] = useState("");

  const [saving, setSaving]           = useState(false);
  const [saveError, setSaveError]     = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const me = await fetchCurrentUser();
      setProfile(me);
      setFullName(me.full_name ?? "");
      setPhoneNumber(me.phone_number ?? "");
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setError(typeof detail === "string" ? detail : "فشل تحميل بيانات الحساب");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!saveSuccess) return;
    const t = setTimeout(() => setSaveSuccess(false), 4000);
    return () => clearTimeout(t);
  }, [saveSuccess]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saving) return;

    const trimmedName = fullName.trim();
    if (!trimmedName) {
      setSaveError("الاسم الكامل مطلوب.");
      return;
    }

    setSaving(true);
    setSaveError(null);
    setSaveSuccess(false);
    try {
      const updated = await updateCurrentUser({
        full_name: trimmedName,
        phone_number: phoneNumber.trim() || null,
      });
      setProfile(updated);
      setFullName(updated.full_name ?? "");
      setPhoneNumber(updated.phone_number ?? "");
      setSaveSuccess(true);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setSaveError(typeof detail === "string" ? detail : "فشل حفظ البيانات");
    } finally {
      setSaving(false);
    }
  };

  // Email/role fall back to Clerk data when the backend record is incomplete
  const email = profile?.email ?? user?.primaryEmailAddress?.emailAddress ?? "—";
  const roleLabel = profile?.role
    ? ROLE_LABELS[profile.role] ?? profile.role
    : "—";

  const inputClass =
    "w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 pr-12 pl-4 text-white focus:outline-none focus:border-[#C9A84C]/50";
  const readOnlyClass =
    "w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 pr-12 pl-4 text-gray-500 focus:outline-none cursor-not-allowed";

  return (
    <div className="space-y-8 max-w-4xl" dir="rtl">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <User className="w-8 h-8 text-[#C9A84C]" />
            حسابي
          </h1>
          <p className="text-gray-400 mt-2 text-sm">
            إدارة بياناتك الشخصية. بيانات الدخول وكلمة المرور تُدار عبر Clerk.
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

      <div className="bg-[#111827] border border-white/10 rounded-2xl p-6 md:p-8 shadow-xl space-y-8">
        {/* Personal Details */}
        <form className="space-y-5" onSubmit={handleSave}>
          <h2 className="text-xl font-bold text-white border-b border-white/10 pb-4">
            البيانات الشخصية
          </h2>

          {loading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <FieldSkeleton />
              <FieldSkeleton />
              <FieldSkeleton />
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    الاسم الكامل
                  </label>
                  <div className="relative">
                    <User className="absolute right-4 top-3.5 w-5 h-5 text-gray-500" />
                    <input
                      type="text"
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      className={inputClass}
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    رقم الجوال
                  </label>
                  <div className="relative">
                    <Phone className="absolute right-4 top-3.5 w-5 h-5 text-gray-500" />
                    <input
                      type="tel"
                      value={phoneNumber}
                      onChange={(e) => setPhoneNumber(e.target.value)}
                      placeholder="+9665xxxxxxxx"
                      className={inputClass}
                      dir="ltr"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    البريد الإلكتروني (يُدار عبر Clerk)
                  </label>
                  <div className="relative">
                    <Mail className="absolute right-4 top-3.5 w-5 h-5 text-gray-500" />
                    <input
                      type="email"
                      value={email}
                      readOnly
                      className={readOnlyClass}
                      dir="ltr"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    الدور (الصلاحية)
                  </label>
                  <div className="relative">
                    <ShieldAlert className="absolute right-4 top-3.5 w-5 h-5 text-[#C9A84C]" />
                    <input
                      type="text"
                      value={roleLabel}
                      readOnly
                      className="w-full bg-[#0A0F1C] border border-[#C9A84C]/20 rounded-lg py-3 pr-12 pl-4 text-[#C9A84C] font-semibold focus:outline-none cursor-not-allowed"
                    />
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-4">
                <button
                  type="submit"
                  disabled={saving || !profile}
                  className="bg-[#C9A84C] text-[#0A0F1C] px-6 py-2.5 rounded-lg font-bold hover:bg-[#D4B55A] transition-colors flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {saving ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Save className="w-4 h-4" />
                  )}
                  حفظ التغييرات
                </button>

                {saveSuccess && (
                  <span className="text-sm text-green-400 flex items-center gap-1.5">
                    <CheckCircle2 className="w-4 h-4" /> تم حفظ البيانات
                  </span>
                )}
                {saveError && (
                  <span role="alert" className="text-sm text-red-400 flex items-center gap-1.5">
                    <AlertTriangle className="w-4 h-4" /> {saveError}
                  </span>
                )}
              </div>
            </>
          )}
        </form>

        {/* Security — delegated to Clerk */}
        <div className="space-y-4 pt-6 border-t border-white/10">
          <h2 className="text-xl font-bold text-white border-b border-white/10 pb-4 flex items-center gap-2">
            <KeyRound className="w-5 h-5 text-[#C9A84C]" /> الأمان وكلمة المرور
          </h2>
          <p className="text-sm text-gray-400 leading-relaxed">
            كلمة المرور، التحقق بخطوتين، والأجهزة النشطة تُدار بالكامل عبر Clerk.
            اضغط الزر أدناه لفتح لوحة إدارة الحساب الآمنة.
          </p>
          <button
            type="button"
            onClick={() => openUserProfile()}
            className="bg-white/5 border border-white/10 text-white px-6 py-2.5 rounded-lg hover:bg-white/10 transition-colors flex items-center gap-2"
          >
            <KeyRound className="w-4 h-4" />
            إدارة الأمان وكلمة المرور
          </button>
        </div>
      </div>
    </div>
  );
}
