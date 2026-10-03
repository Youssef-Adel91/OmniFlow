"use client";

import { useMemo, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { DashboardAnalytics, KeyCount } from "@/lib/api/dashboardAnalytics";
import { CHANNEL_COLORS, CHANNEL_LABELS, STATUS_LABELS, WEEKDAYS, fmtDay, fmtNumber } from "./format";

const tooltipStyle = {
  background: "var(--menu-bg)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  color: "var(--foreground)",
  fontSize: 12,
};

export function VolumeChart({ series }: { series: DashboardAnalytics["series"] }) {
  const [metric, setMetric] = useState<"conversations" | "messages">("conversations");
  const channels = useMemo(() => {
    const set = new Set<string>();
    series.forEach((p) => Object.keys(p.channels).forEach((c) => set.add(c)));
    return [...set];
  }, [series]);
  const data = useMemo(
    () => series.map((p) => ({ date: p.date, ...Object.fromEntries(channels.map((c) => [c, p.channels[c]?.[metric] ?? 0])) })),
    [series, channels, metric],
  );
  const total = series.reduce((a, p) => a + p[metric], 0);

  return (
    <section className="card" aria-label="حجم المحادثات والرسائل">
      <header className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div>
          <h2 className="text-sm font-semibold">{metric === "conversations" ? "المحادثات الجديدة يوميًا" : "الرسائل يوميًا"}</h2>
          <p className="text-xs text-[var(--muted-foreground)]">الإجمالي في الفترة: {fmtNumber(total)} — موزّعة حسب القناة (توقيت الرياض)</p>
        </div>
        <div role="tablist" className="inline-flex rounded-lg border border-[var(--border)] overflow-hidden text-xs">
          {([["conversations", "محادثات"], ["messages", "رسائل"]] as const).map(([k, l]) => (
            <button
              key={k} role="tab" aria-selected={metric === k} onClick={() => setMetric(k)}
              className={`px-3 py-1.5 transition-colors ${metric === k ? "bg-[var(--menu-selected-bg)] text-[var(--menu-selected-fg)] font-semibold" : "text-[var(--muted-foreground)] hover:bg-[var(--menu-hover)]"}`}
            >{l}</button>
          ))}
        </div>
      </header>
      {total === 0 ? <EmptyChart text="لا توجد بيانات في هذه الفترة" /> : (
        <div className="h-64" dir="ltr">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
              <XAxis dataKey="date" tickFormatter={fmtDay} tick={{ fill: "var(--chart-axis)", fontSize: 11 }} stroke="var(--chart-grid)" minTickGap={24} />
              <YAxis allowDecimals={false} tick={{ fill: "var(--chart-axis)", fontSize: 11 }} stroke="var(--chart-grid)" />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(l) => fmtDay(String(l))} formatter={(v, n) => [fmtNumber(Number(v)), CHANNEL_LABELS[String(n)] ?? String(n)]} />
              <Legend formatter={(n) => CHANNEL_LABELS[String(n)] ?? String(n)} wrapperStyle={{ fontSize: 12 }} />
              {channels.map((c) => (
                <Area key={c} type="monotone" dataKey={c} stackId="1" stroke={CHANNEL_COLORS[c] ?? "var(--chart-1)"} fill={CHANNEL_COLORS[c] ?? "var(--chart-1)"} fillOpacity={0.35} strokeWidth={2} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

export function ChannelDonut({ data }: { data: KeyCount[] }) {
  const total = data.reduce((a, d) => a + d.count, 0);
  return (
    <section className="card" aria-label="توزيع القنوات">
      <h2 className="text-sm font-semibold mb-3">توزيع القنوات</h2>
      {total === 0 ? <EmptyChart text="لا محادثات بعد" /> : (
        <div className="h-56" dir="ltr">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={data} dataKey="count" nameKey="key" innerRadius={52} outerRadius={80} paddingAngle={2} stroke="var(--menu-bg)">
                {data.map((d) => <Cell key={d.key} fill={CHANNEL_COLORS[d.key] ?? "var(--chart-1)"} />)}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} formatter={(v, n) => [fmtNumber(Number(v)), CHANNEL_LABELS[String(n)] ?? String(n)]} />
              <Legend formatter={(n) => CHANNEL_LABELS[String(n)] ?? String(n)} wrapperStyle={{ fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

export function Funnel({ steps }: { steps: DashboardAnalytics["funnel"] }) {
  const top = Math.max(1, steps[0]?.count ?? 1);
  return (
    <section className="card" aria-label="قمع المبيعات">
      <h2 className="text-sm font-semibold">قمع المبيعات</h2>
      <p className="text-xs text-[var(--muted-foreground)] mb-3">عدد العملاء في كل مرحلة خلال الفترة</p>
      <ol className="space-y-2.5">
        {steps.map((s, i) => {
          const prev = i === 0 ? null : steps[i - 1].count;
          const conv = prev ? Math.round((s.count / prev) * 100) : null;
          return (
            <li key={s.key}>
              <div className="flex items-center justify-between text-xs mb-1">
                <span>{s.label}</span>
                <span className="text-[var(--muted-foreground)]">
                  <strong className="text-[var(--foreground)]">{fmtNumber(s.count)}</strong>
                  {conv != null && <span className="ms-2" dir="ltr">({conv}٪ من السابق)</span>}
                </span>
              </div>
              <div className="h-2.5 rounded-full bg-[var(--menu-hover)] overflow-hidden" role="presentation">
                <div className="h-full rounded-full" style={{ width: `${Math.max(2, (s.count / top) * 100)}%`, background: "var(--chart-1)", opacity: 1 - i * 0.14 }} />
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function Heatmap({ heatmap }: { heatmap: DashboardAnalytics["heatmap"] }) {
  const { matrix, max } = heatmap;
  const hours = Array.from({ length: 24 }, (_, h) => h);
  return (
    <section className="card" aria-label="أوقات الذروة">
      <h2 className="text-sm font-semibold">أوقات الذروة</h2>
      <p className="text-xs text-[var(--muted-foreground)] mb-3">رسائل العملاء الواردة حسب اليوم والساعة (توقيت الرياض)</p>
      {max === 0 ? <EmptyChart text="لا رسائل واردة في هذه الفترة" /> : (
        <div className="overflow-x-auto" dir="ltr">
          <div className="min-w-[560px]">
            <div className="grid gap-[3px]" style={{ gridTemplateColumns: "64px repeat(24, minmax(0, 1fr))" }}>
              <span />
              {hours.map((h) => (
                <span key={h} className="text-[10px] text-center text-[var(--muted-foreground)]">{h % 3 === 0 ? h : ""}</span>
              ))}
              {matrix.map((row, d) => (
                <FragmentRow key={d} label={WEEKDAYS[d]} row={row} max={max} day={WEEKDAYS[d]} />
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function FragmentRow({ label, row, max, day }: { label: string; row: number[]; max: number; day: string }) {
  return (
    <>
      <span className="text-[11px] text-[var(--muted-foreground)] self-center text-right pe-2" dir="rtl">{label}</span>
      {row.map((n, h) => (
        <span
          key={h}
          title={`${day} ${h}:00 — ${fmtNumber(n)} رسالة`}
          className="aspect-square rounded-[3px] border border-[var(--border)]"
          style={{ background: n === 0 ? "transparent" : `color-mix(in srgb, var(--chart-1) ${Math.max(14, Math.round((n / max) * 100))}%, transparent)` }}
        />
      ))}
    </>
  );
}

export function BarList({ title, subtitle, items, labels, empty }: {
  title: string; subtitle?: string; items: KeyCount[]; labels?: Record<string, string>; empty: string;
}) {
  const max = Math.max(1, ...items.map((i) => i.count));
  const total = items.reduce((a, i) => a + i.count, 0);
  return (
    <section className="card" aria-label={title}>
      <h2 className="text-sm font-semibold">{title}</h2>
      {subtitle && <p className="text-xs text-[var(--muted-foreground)]">{subtitle}</p>}
      {total === 0 ? <EmptyChart text={empty} /> : (
        <ul className="mt-3 space-y-2">
          {items.map((i) => (
            <li key={i.key} className="text-xs">
              <div className="flex justify-between mb-1">
                <span>{labels?.[i.key] ?? i.key}</span>
                <strong>{fmtNumber(i.count)}</strong>
              </div>
              <div className="h-2 rounded-full bg-[var(--menu-hover)]">
                <div className="h-full rounded-full" style={{ width: `${(i.count / max) * 100}%`, background: "var(--chart-2)" }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function EmptyChart({ text }: { text: string }) {
  return <p className="text-xs text-[var(--muted-foreground)] py-8 text-center">{text}</p>;
}

export { STATUS_LABELS };
