"use client";

import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { fmtPercent } from "./format";

interface Props {
  label: string;
  value: string;
  hint?: string;
  icon: React.ComponentType<{ className?: string }>;
  changePct: number | null;
  previousLabel?: string;
  /** For metrics where a decrease is good (reply time, hand-off rate). */
  lowerIsBetter?: boolean;
  spark?: number[];
  loading?: boolean;
}

export function KpiCard({ label, value, hint, icon: Icon, changePct, previousLabel, lowerIsBetter, spark, loading }: Props) {
  if (loading) {
    return (
      <div className="card flex flex-col gap-3" aria-busy="true">
        <div className="skeleton h-9 w-9 rounded" />
        <div className="skeleton h-8 w-24 rounded" />
        <div className="skeleton h-3 w-32 rounded" />
      </div>
    );
  }

  const flat = changePct == null || changePct === 0;
  const good = !flat && (changePct! > 0) !== !!lowerIsBetter;
  const tone = flat ? "var(--muted-foreground)" : good ? "var(--chart-up)" : "var(--chart-down)";
  const Arrow = flat ? Minus : changePct! > 0 ? ArrowUpRight : ArrowDownRight;
  const sparkData = (spark ?? []).map((v, i) => ({ i, v }));

  return (
    <div className="card flex flex-col gap-2" title={hint}>
      <div className="flex items-start justify-between gap-2">
        <span className="w-9 h-9 rounded-[var(--radius-sm)] bg-[var(--menu-selected-bg)] text-[var(--menu-selected-fg)] flex items-center justify-center">
          <Icon className="w-4 h-4" />
        </span>
        {sparkData.length > 1 && (
          <div className="h-9 w-24" aria-hidden>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={sparkData} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
                <Area type="monotone" dataKey="v" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.18} strokeWidth={1.5} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
      <p className="text-2xl font-bold text-[var(--foreground)] leading-tight">{value}</p>
      <p className="text-xs text-[var(--muted-foreground)] leading-snug">{label}</p>
      <p className="text-xs font-medium flex items-center gap-1" style={{ color: tone }}>
        <Arrow className="w-3.5 h-3.5" aria-hidden />
        <span dir="ltr">{changePct == null ? "—" : fmtPercent(Math.abs(changePct))}</span>
        <span className="text-[var(--muted-foreground)] font-normal">{previousLabel ?? "مقابل الفترة السابقة"}</span>
      </p>
    </div>
  );
}
