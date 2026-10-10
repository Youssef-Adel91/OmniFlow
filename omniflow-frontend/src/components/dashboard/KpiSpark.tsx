"use client";

import { Area, AreaChart, ResponsiveContainer } from "recharts";

/** Tiny sparkline — kept in its own module so recharts is only downloaded when a KPI card actually has data to plot. */
export default function KpiSpark({ data }: { data: { i: number; v: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
        <Area type="monotone" dataKey="v" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.18} strokeWidth={1.5} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
