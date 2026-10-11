"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useLocale } from "next-intl";
import { CheckCircle2, Circle, X } from "lucide-react";
import { dismissChecklist, fetchChecklist, STEP_COPY, type Checklist } from "@/lib/api/onboarding";

/** "First steps" card on the dashboard. Hidden once everything is done or the admin dismisses it; fails silently (it is a convenience, never a blocker). */
export function SetupChecklist() {
  const locale = useLocale();
  const [data, setData] = useState<Checklist | null>(null);

  const load = useCallback(async () => {
    try { setData(await fetchChecklist()); } catch { setData(null); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (!data || data.complete || data.dismissed) return null;
  const pct = Math.round((data.done / data.total) * 100);
  return (
    <section aria-labelledby="setup-title" data-testid="setup-checklist" className="card p-5 mb-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="setup-title" className="font-bold text-white">ابدأ من هنا: جهّز مساحة عملك</h2>
          <p className="text-sm text-gray-300">{data.done} من {data.total} خطوات مكتملة</p>
        </div>
        <button type="button" aria-label="إخفاء قائمة البدء" onClick={() => { setData({ ...data, dismissed: true }); void dismissChecklist().catch(() => undefined); }}
          className="p-1.5 rounded-lg hover:bg-white/10 text-gray-300"><X className="w-4 h-4" aria-hidden /></button>
      </div>
      <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="تقدم الإعداد" className="h-2 rounded-full bg-white/10 overflow-hidden">
        <div className="h-full bg-[#C9A84C]" style={{ width: `${pct}%` }} />
      </div>
      <ol className="grid gap-2 md:grid-cols-2">
        {data.steps.map((s) => {
          const c = STEP_COPY[s.key];
          return (
            <li key={s.key}>
              <Link href={`/${locale}${s.href}`} data-done={s.done} className="flex gap-3 rounded-xl border border-white/10 p-3 hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#C9A84C]">
                {s.done ? <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" aria-label="مكتملة" /> : <Circle className="w-5 h-5 text-gray-400 shrink-0" aria-label="غير مكتملة" />}
                <span><span className={`block text-sm font-semibold ${s.done ? "text-gray-300 line-through" : "text-white"}`}>{c.title}</span><span className="block text-xs text-gray-300">{c.hint}</span></span>
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
