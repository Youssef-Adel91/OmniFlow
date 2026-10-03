"use client";

/**
 * app/[locale]/(dashboard)/dashboard/page.tsx — Operational analytics dashboard
 *
 * LIVE operational performance (conversations, sales funnel, AI vs. human
 * handling, inventory). Not to be confused with /reports, which is the catalogue
 * and revenue of the paid PDF reports (e.g. deed check 29 SAR).
 *
 * Data: GET /dashboard/analytics (lib/api/dashboardAnalytics.ts). Auto-refreshes
 * when the existing inbox SSE stream delivers an event (debounced).
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocale } from "next-intl";
import {
  AlertTriangle, CalendarCheck, Clock, FileText, Flame, Hourglass, MessageSquare,
  RefreshCw, Users, Bot, UserRoundCog, Banknote, Plug,
} from "lucide-react";
import { useAuthReady } from "@/hooks/useAuthReady";
import { useInboxStream } from "@/hooks/useInboxStream";
import { useInboxStore } from "@/store/inboxStore";
import { fetchDashboardAnalytics, type DashboardAnalytics, type KpiKey } from "@/lib/api/dashboardAnalytics";
import { KpiCard } from "@/components/dashboard/KpiCard";
import { BarList, ChannelDonut, EmptyChart, Funnel, Heatmap, VolumeChart } from "@/components/dashboard/Charts";
import { AttentionPanels, InventoryGaps } from "@/components/dashboard/Panels";
import { Filters, isFilterValid, type FilterState } from "@/components/dashboard/Filters";
import { analyticsToCsv, downloadCsv } from "@/components/dashboard/exportCsv";
import {
  PROPERTY_STATUS_LABELS, PROPERTY_TYPE_LABELS, STATUS_LABELS,
  fmtDuration, fmtNumber, fmtPercent, fmtSar,
} from "@/components/dashboard/format";

interface KpiDef {
  key: KpiKey;
  label: string;
  hint: string;
  icon: React.ComponentType<{ className?: string }>;
  format: (v: number | null) => string;
  lowerIsBetter?: boolean;
  spark?: boolean;
}

const KPI_DEFS: KpiDef[] = [
  { key: "new_conversations", label: "المحادثات الجديدة", hint: "محادثات أُنشئت في الفترة", icon: MessageSquare, format: fmtNumber, spark: true },
  { key: "new_customers", label: "العملاء الجدد", hint: "عملاء سُجّلوا لأول مرة في الفترة", icon: Users, format: fmtNumber },
  { key: "hot_leads", label: "العملاء الساخنون", hint: "درجة تفاعل > 70 أو VIP، لديهم محادثة في الفترة (الدرجات الحالية)", icon: Flame, format: fmtNumber },
  { key: "ai_resolution_rate", label: "حلّها الذكاء الاصطناعي بدون تدخل", hint: "نسبة المحادثات التي لم تحتج موظفًا (لا رسالة موظف ولا تصعيد)", icon: Bot, format: fmtPercent },
  { key: "handoff_rate", label: "معدل التحويل للموظف", hint: "محادثات صُعّدت أو ردّ فيها موظف", icon: UserRoundCog, format: fmtPercent, lowerIsBetter: true },
  { key: "avg_first_response_ai_seconds", label: "متوسط زمن أول رد (ذكاء اصطناعي)", hint: "من أول رسالة للعميل إلى أول رد آلي", icon: Clock, format: fmtDuration, lowerIsBetter: true },
  { key: "avg_first_response_human_seconds", label: "متوسط زمن أول رد (بشري)", hint: "من أول رسالة للعميل إلى أول رد موظف (للمحادثات التي ردّ فيها موظف فقط)", icon: Clock, format: fmtDuration, lowerIsBetter: true },
  { key: "avg_resolution_seconds", label: "متوسط زمن الحل", hint: "للمحادثات المغلقة: آخر تحديث − الإنشاء (تقريب: لا يوجد closed_at مخزَّن)", icon: Hourglass, format: fmtDuration, lowerIsBetter: true },
  { key: "appointments_booked", label: "المواعيد المحجوزة", hint: "مواعيد أُنشئت في الفترة (دون الملغاة)", icon: CalendarCheck, format: fmtNumber },
  { key: "reports_revenue_sar", label: "الإيراد من التقارير", hint: "مجموع التقارير المدفوعة المُسلَّمة في الفترة", icon: Banknote, format: fmtSar },
  { key: "reports_sold", label: "التقارير المُباعة", hint: "تقارير مُسلَّمة في الفترة", icon: FileText, format: fmtNumber },
];

const KPI_LABELS = Object.fromEntries(KPI_DEFS.map((k) => [k.key, k.label]));
const REFRESH_DEBOUNCE_MS = 5_000;

export default function DashboardOverviewPage() {
  const locale = useLocale();
  const authReady = useAuthReady();
  useInboxStream(); // keeps the SSE connection open; we react to its store updates below

  const [filters, setFilters] = useState<FilterState>({ range: "7d", from: "", to: "", channel: "" });
  const [data, setData] = useState<DashboardAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqId = useRef(0);

  const load = useCallback(async (opts?: { silent?: boolean; refresh?: boolean }) => {
    if (!isFilterValid(filters)) return;
    const id = ++reqId.current;
    if (!opts?.silent) { setLoading(true); setError(null); }
    try {
      const res = await fetchDashboardAnalytics({
        range: filters.range, from: filters.from, to: filters.to,
        channel: filters.channel || null, refresh: opts?.refresh,
      });
      if (id === reqId.current) { setData(res); setError(null); }
    } catch (err: any) {
      if (id !== reqId.current) return;
      const detail = err?.response?.data?.detail ?? err?.message;
      if (!opts?.silent) setError(typeof detail === "string" ? detail : "تعذّر تحميل التحليلات");
    } finally {
      if (id === reqId.current && !opts?.silent) setLoading(false);
    }
  }, [filters]);

  useEffect(() => { if (authReady) void load(); }, [authReady, load]);

  // Live refresh: any inbox store change (new message / status update arriving over SSE).
  useEffect(() => {
    if (!authReady) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsub = useInboxStore.subscribe((s, prev) => {
      if (s.messages === prev.messages && s.conversations === prev.conversations) return;
      clearTimeout(timer);
      timer = setTimeout(() => void load({ silent: true, refresh: true }), REFRESH_DEBOUNCE_MS);
    });
    return () => { clearTimeout(timer); unsub(); };
  }, [authReady, load]);

  const spark = useMemo(() => data?.series.map((p) => p.conversations) ?? [], [data]);
  const noActivity = !!data && data.kpis.new_conversations.value === 0 && data.kpis.new_customers.value === 0
    && data.channel_distribution.length === 0;

  return (
    <div className="animate-fade-in" id="analytics-dashboard">
      <div className="page-header">
        <div>
          <h1 className="page-title">لوحة التحكم التحليلية</h1>
          <p className="page-subtitle">أداء تشغيلي حيّ: المحادثات والمبيعات والذكاء الاصطناعي.</p>
        </div>
        <div className="flex items-center gap-2 print:hidden">
          <Link href={`/${locale}/reports`} className="btn btn-outline h-8 px-3 text-xs gap-1.5">
            <FileText className="w-3.5 h-3.5" aria-hidden />عرض التقارير المدفوعة
          </Link>
          <button onClick={() => void load()} disabled={loading} className="btn btn-ghost h-8 px-3 text-xs gap-1.5 disabled:opacity-50" aria-label="تحديث التحليلات">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />تحديث
          </button>
        </div>
      </div>

      <Filters value={filters} onChange={setFilters} disabled={!data}
        onExportCsv={() => data && downloadCsv(`omniflow-analytics-${data.range.from}_${data.range.to}.csv`, analyticsToCsv(data, KPI_LABELS))} />

      {error && (
        <div role="alert" className="flex items-center justify-between gap-3 mb-6 rounded-[var(--radius)] border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          <span className="flex items-center gap-2"><AlertTriangle className="w-4 h-4" />{error}</span>
          <button onClick={() => void load()} className="flex items-center gap-1 text-xs hover:opacity-80"><RefreshCw className="w-3.5 h-3.5" />إعادة المحاولة</button>
        </div>
      )}

      {noActivity && (
        <div className="card text-center py-10 mb-6">
          <Plug className="w-10 h-10 text-[var(--muted-foreground)] mx-auto mb-3" aria-hidden />
          <h2 className="text-base font-semibold mb-1">لا توجد بيانات في هذه الفترة</h2>
          <p className="text-sm text-[var(--muted-foreground)] mb-3">اربط قناة (واتساب أو فيسبوك/إنستجرام) وستظهر المؤشرات تلقائيًا بمجرد وصول أول محادثة، أو وسّع النطاق الزمني.</p>
          <Link href={`/${locale}/settings`} className="btn btn-primary h-9 px-4 text-sm">ربط قناة</Link>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4 mb-6" aria-label="مؤشرات الأداء">
        {KPI_DEFS.map((d) => (
          <KpiCard
            key={d.key} label={d.label} hint={d.hint} icon={d.icon}
            loading={loading && !data}
            value={error && !data ? "—" : d.format(data?.kpis[d.key].value ?? null)}
            changePct={data?.kpis[d.key].change_pct ?? null}
            lowerIsBetter={d.lowerIsBetter}
            spark={d.spark ? spark : undefined}
          />
        ))}
      </div>

      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <div className="xl:col-span-2"><VolumeChart series={data.series} /></div>
            <ChannelDonut data={data.channel_distribution} />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <Funnel steps={data.funnel} />
            <Heatmap heatmap={data.heatmap} />
          </div>

          <AttentionPanels locale={locale} data={data.attention} />

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <BarList title="حالات المحادثات" items={data.status_distribution} labels={STATUS_LABELS} empty="لا محادثات في الفترة" />
            <BarList title="توزيع العملاء حسب درجة التفاعل" subtitle="العملاء النشطون في الفترة" items={data.lead_score_distribution} empty="لا عملاء نشطون في الفترة" />
            <section className="card" aria-label="أكثر المواضيع تكرارًا">
              <h2 className="text-sm font-semibold">أكثر المواضيع تكرارًا</h2>
              <p className="text-xs text-[var(--muted-foreground)] mb-2">{data.topics_note}</p>
              {data.top_topics.every((t) => t.count === 0) ? <EmptyChart text="لا رسائل كافية بعد" /> : (
                <ul className="space-y-1.5 text-xs">
                  {data.top_topics.filter((t) => t.count > 0).map((t) => (
                    <li key={t.topic} className="flex justify-between"><span>{t.topic}</span><strong>{fmtNumber(t.count)}</strong></li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <div className="xl:col-span-1"><InventoryGaps gaps={data.inventory_gaps} /></div>
            <div className="xl:col-span-2 grid grid-cols-1 md:grid-cols-3 gap-4">
              <BarList title="العقارات حسب الحالة" items={data.properties.by_status} labels={PROPERTY_STATUS_LABELS} empty="لا عقارات بعد" />
              <BarList title="العقارات حسب النوع" items={data.properties.by_type} labels={PROPERTY_TYPE_LABELS} empty="لا عقارات بعد" />
              <BarList title="العقارات حسب المدينة" items={data.properties.by_city} empty="لا عقارات بعد" />
            </div>
          </div>
          {data.properties.top_recommended == null && (
            <p className="text-xs text-[var(--muted-foreground)]">ℹ️ أكثر العقارات توصية: {data.properties.top_recommended_unavailable_reason}</p>
          )}

          <details className="card text-xs text-[var(--muted-foreground)] print:hidden">
            <summary className="cursor-pointer font-semibold text-[var(--foreground)]">كيف تُحسب هذه المؤشرات؟</summary>
            <ul className="mt-2 space-y-1 list-disc ps-5">
              {KPI_DEFS.map((d) => <li key={d.key}><strong>{d.label}:</strong> {d.hint}</li>)}
              <li>التغيّر ٪ مقارنةً بفترة سابقة بنفس الطول ({data.range.previous_from} → {data.range.previous_to}).</li>
              <li>التواريخ والساعات بتوقيت الرياض. آخر تحديث: {new Date(data.generated_at).toLocaleTimeString("ar-SA-u-ca-gregory")}.</li>
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}
