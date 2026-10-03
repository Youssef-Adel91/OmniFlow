"use client";

import { Download, Printer } from "lucide-react";
import { Select } from "@/components/ui/Select";
import type { AnalyticsRange } from "@/lib/api/dashboardAnalytics";
import { CHANNEL_LABELS } from "./format";

export interface FilterState {
  range: AnalyticsRange;
  from: string;
  to: string;
  channel: string;
}

const PRESETS: [AnalyticsRange, string][] = [["7d", "7 أيام"], ["30d", "30 يومًا"], ["90d", "90 يومًا"], ["custom", "مخصّص"]];

export function Filters({ value, onChange, onExportCsv, disabled }: {
  value: FilterState;
  onChange: (v: FilterState) => void;
  onExportCsv: () => void;
  disabled?: boolean;
}) {
  const customInvalid = value.range === "custom" && (!value.from || !value.to || value.from > value.to);
  return (
    <div className="card flex flex-wrap items-end gap-3 mb-6 print:hidden" role="group" aria-label="فلاتر اللوحة">
      <div role="tablist" aria-label="النطاق الزمني" className="inline-flex rounded-lg border border-[var(--border)] overflow-hidden text-xs">
        {PRESETS.map(([k, l]) => (
          <button
            key={k} role="tab" aria-selected={value.range === k} onClick={() => onChange({ ...value, range: k })}
            className={`px-3 py-2 transition-colors ${value.range === k ? "bg-[var(--menu-selected-bg)] text-[var(--menu-selected-fg)] font-semibold" : "text-[var(--muted-foreground)] hover:bg-[var(--menu-hover)]"}`}
          >{l}</button>
        ))}
      </div>

      {value.range === "custom" && (
        <div className="flex items-end gap-2 text-xs">
          <label className="flex flex-col gap-1">من
            <input type="date" value={value.from} max={value.to || undefined} onChange={(e) => onChange({ ...value, from: e.target.value })}
              className="rounded-lg border border-[var(--border)] bg-[var(--menu-bg)] px-2 py-1.5" />
          </label>
          <label className="flex flex-col gap-1">إلى
            <input type="date" value={value.to} min={value.from || undefined} onChange={(e) => onChange({ ...value, to: e.target.value })}
              className="rounded-lg border border-[var(--border)] bg-[var(--menu-bg)] px-2 py-1.5" />
          </label>
          {customInvalid && <span role="alert" className="text-[var(--chart-down)] pb-2">اختر تاريخي بداية ونهاية صحيحين</span>}
        </div>
      )}

      <label className="flex flex-col gap-1 text-xs">القناة
        <Select value={value.channel} onChange={(e) => onChange({ ...value, channel: e.target.value })} aria-label="القناة">
          <option value="">كل القنوات</option>
          {Object.entries(CHANNEL_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </Select>
      </label>

      <div className="ms-auto flex gap-2">
        <button onClick={onExportCsv} disabled={disabled} className="btn btn-ghost h-9 px-3 text-xs gap-1.5 disabled:opacity-50">
          <Download className="w-3.5 h-3.5" aria-hidden />تصدير CSV
        </button>
        <button onClick={() => window.print()} disabled={disabled} className="btn btn-ghost h-9 px-3 text-xs gap-1.5 disabled:opacity-50">
          <Printer className="w-3.5 h-3.5" aria-hidden />تصدير PDF
        </button>
      </div>
    </div>
  );
}

export function isFilterValid(f: FilterState): boolean {
  return f.range !== "custom" || (!!f.from && !!f.to && f.from <= f.to);
}
