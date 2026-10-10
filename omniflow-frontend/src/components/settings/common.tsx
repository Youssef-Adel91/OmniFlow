"use client";

import React from "react";
import { AlertTriangle, CheckCircle2, Loader2, Save } from "lucide-react";

export const inputClass =
  "w-full bg-[#0A0F1C] border border-white/15 rounded-lg py-2.5 px-3 text-sm text-white placeholder-gray-400 focus:outline-none focus:border-[#C9A84C]/60 transition-colors disabled:opacity-60";
export const readOnlyClass =
  "w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-2.5 px-3 text-sm text-gray-300 cursor-not-allowed";
export const cardClass = "bg-[#111827] border border-white/10 rounded-2xl p-6 md:p-8 shadow-xl space-y-6";

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: React.ReactNode; children: React.ReactNode; htmlFor?: string }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="block text-sm font-medium text-gray-200 mb-1.5">{label}</label>
      {children}
      {hint && <p className="text-xs text-gray-300 mt-1.5 leading-relaxed">{hint}</p>}
    </div>
  );
}

export function Toggle({ checked, onChange, label, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; disabled?: boolean }) {
  return (
    <label className={`flex items-start gap-3 ${disabled ? "opacity-60" : "cursor-pointer"}`}>
      <input type="checkbox" role="switch" className="mt-1 w-4 h-4" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span><span className="block text-sm text-gray-100">{label}</span>{hint && <span className="block text-xs text-gray-300 mt-0.5">{hint}</span>}</span>
    </label>
  );
}

export function SectionHeader({ icon: Icon, title, description }: { icon: React.ElementType; title: string; description: string }) {
  return (
    <div className="border-b border-white/10 pb-4">
      <h2 className="text-xl font-bold text-white flex items-center gap-2"><Icon className="w-5 h-5 text-[#C9A84C]" aria-hidden />{title}</h2>
      <p className="text-gray-300 text-sm mt-2 leading-relaxed">{description}</p>
    </div>
  );
}

/** Save bar shared by every section: disabled until something changed, with an explicit "unsaved changes" hint. */
export function SaveBar({ dirty, saving, error, savedAt, onSave, onReset, label = "حفظ التغييرات" }: {
  dirty: boolean; saving: boolean; error: string | null; savedAt: number | null; onSave: () => void; onReset: () => void; label?: string;
}) {
  return (
    <div className="pt-5 mt-2 border-t border-white/10 flex flex-wrap items-center gap-3" role="group" aria-label="حفظ القسم">
      <button type="button" onClick={onSave} disabled={!dirty || saving}
        className="bg-[#C9A84C] text-[#0A0F1C] px-6 py-2.5 rounded-lg font-bold hover:bg-[#D4B55A] transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed">
        {saving ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Save className="w-4 h-4" aria-hidden />}{label}
      </button>
      {dirty && !saving && <button type="button" onClick={onReset} className="text-sm text-gray-200 underline">تراجع عن التغييرات</button>}
      {dirty && <span className="text-sm text-amber-200" data-testid="unsaved">تغييرات غير محفوظة</span>}
      {!dirty && savedAt && !error && <span className="text-sm text-emerald-300 flex items-center gap-1.5" role="status"><CheckCircle2 className="w-4 h-4" aria-hidden />تم الحفظ</span>}
      {error && <span role="alert" className="text-sm text-red-300 flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" aria-hidden />{error}</span>}
    </div>
  );
}

export function SectionSkeleton() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="جارٍ التحميل">
      {[0, 1, 2].map((i) => <div key={i}><div className="h-4 w-32 rounded bg-white/5 animate-pulse mb-2" /><div className="h-11 w-full rounded-lg bg-white/5 animate-pulse" /></div>)}
    </div>
  );
}

export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex items-center justify-between gap-3 bg-red-500/10 border border-red-500/40 text-red-200 rounded-xl px-4 py-3 text-sm">
      <span className="flex items-center gap-2"><AlertTriangle className="w-4 h-4" aria-hidden />{message}</span>
      <button onClick={onRetry} className="text-xs underline">إعادة المحاولة</button>
    </div>
  );
}
