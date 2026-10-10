"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Campaign result bars. Separate module so recharts loads only when someone opens a campaign's stats. */
export default function CampaignBars({ bars }: { bars: { name: string; value: number; color: string }[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={bars} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
        <XAxis dataKey="name" tick={{ fill: "#D1D5DB", fontSize: 11 }} />
        <YAxis allowDecimals={false} tick={{ fill: "#D1D5DB", fontSize: 11 }} />
        <Tooltip contentStyle={{ background: "#0A0F1C", border: "1px solid rgba(255,255,255,0.15)", color: "#fff" }} />
        <Bar dataKey="value" radius={[4, 4, 0, 0]}>{bars.map((b) => <Cell key={b.name} fill={b.color} />)}</Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
