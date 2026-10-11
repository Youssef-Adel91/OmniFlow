"use client";

/** Global notice when any request is refused because the plan's limit is reached (HTTP 402 PLAN_LIMIT_REACHED, see lib/api/client.ts). */
import React, { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, X } from "lucide-react";

interface LimitDetail { message?: string; resource?: string; limit?: number | null; used?: number }

export function PlanLimitNotice({ locale }: { locale: string }) {
  const [detail, setDetail] = useState<LimitDetail | null>(null);

  useEffect(() => {
    const on = (e: Event) => setDetail((e as CustomEvent<LimitDetail>).detail ?? {});
    window.addEventListener("omniflow:plan-limit", on);
    return () => window.removeEventListener("omniflow:plan-limit", on);
  }, []);

  if (!detail) return null;
  return (
    <div role="alert" dir="rtl" data-testid="plan-limit-notice" className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-amber-300/50 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
      <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden />
      <span className="flex-1 min-w-[12rem]">{detail.message ?? "وصلت إلى حد باقتك الحالية."}</span>
      <Link href={`/${locale}/settings#billing`} className="font-bold underline underline-offset-4">ترقية الباقة</Link>
      <button type="button" onClick={() => setDetail(null)} aria-label="إغلاق التنبيه" className="p-1 hover:bg-white/10 rounded"><X className="w-4 h-4" aria-hidden /></button>
    </div>
  );
}
