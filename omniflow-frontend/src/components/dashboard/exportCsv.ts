import type { DashboardAnalytics } from "@/lib/api/dashboardAnalytics";
import { CHANNEL_LABELS } from "./format";

/** Neutralise spreadsheet formula injection (=,+,-,@, tab, CR) and quote. */
export function csvCell(v: unknown): string {
  let s = v == null ? "" : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return `"${s.replace(/"/g, '""')}"`;
}

export function analyticsToCsv(d: DashboardAnalytics, kpiLabels: Record<string, string>): string {
  const rows: unknown[][] = [
    ["الفترة", `${d.range.from} → ${d.range.to}`, "القناة", d.channel ? CHANNEL_LABELS[d.channel] ?? d.channel : "الكل"],
    [],
    ["المؤشر", "القيمة", "الفترة السابقة", "التغير ٪"],
    ...Object.entries(d.kpis).map(([k, v]) => [kpiLabels[k] ?? k, v.value, v.previous, v.change_pct]),
    [],
    ["التاريخ", "محادثات", "رسائل"],
    ...d.series.map((p) => [p.date, p.conversations, p.messages]),
    [],
    ["القمع", "العدد"],
    ...d.funnel.map((f) => [f.label, f.count]),
  ];
  return "﻿" + rows.map((r) => r.map(csvCell).join(",")).join("\r\n");
}

export function downloadCsv(filename: string, csv: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
