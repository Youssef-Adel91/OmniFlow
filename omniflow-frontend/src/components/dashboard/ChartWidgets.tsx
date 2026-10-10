"use client";

import type { DashboardAnalytics, KeyCount } from "@/lib/api/dashboardAnalytics";
import { STATUS_LABELS, WEEKDAYS, fmtNumber } from "./format";

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
