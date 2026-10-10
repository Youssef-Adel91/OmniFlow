"use client";

import { useMemo, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { DashboardAnalytics, KeyCount } from "@/lib/api/dashboardAnalytics";
import { CHANNEL_COLORS, CHANNEL_LABELS, fmtDay, fmtNumber } from "./format";
import { EmptyChart } from "./ChartWidgets";

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
