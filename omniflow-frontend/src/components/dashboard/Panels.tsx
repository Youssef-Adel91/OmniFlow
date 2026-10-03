"use client";

import Link from "next/link";
import { AlertTriangle, Clock, Flame, MessageSquare } from "lucide-react";
import type { AttentionItem, DashboardAnalytics } from "@/lib/api/dashboardAnalytics";
import { CHANNEL_LABELS, STATUS_LABELS, fmtDuration, fmtNumber } from "./format";
import { EmptyChart } from "./Charts";

function who(i: AttentionItem) {
  return i.name || i.phone || "عميل بدون اسم";
}

function List({ title, icon: Icon, tone, items, empty, render }: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  tone: string;
  items: AttentionItem[];
  empty: string;
  render: (i: AttentionItem) => React.ReactNode;
}) {
  return (
    <section className="card" aria-label={title}>
      <h2 className="text-sm font-semibold flex items-center gap-2 mb-2">
        <Icon className={`w-4 h-4 ${tone}`} aria-hidden />
        {title}
        <span className="ms-auto text-xs text-[var(--muted-foreground)]">{fmtNumber(items.length)}</span>
      </h2>
      {items.length === 0 ? <EmptyChart text={empty} /> : (
        <ul className="divide-y divide-[var(--border)]">
          {items.map((i, idx) => (
            <li key={`${i.conversation_id ?? i.customer_id}-${idx}`} className="py-2 text-xs flex items-center justify-between gap-2">
              {render(i)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function AttentionPanels({ locale, data }: { locale: string; data: DashboardAnalytics["attention"] }) {
  const inbox = `/${locale}/inbox`;
  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <List
        title="يحتاج اهتمامك الآن: ينتظرون رد موظف" icon={MessageSquare} tone="text-[var(--chart-4)]"
        items={data.awaiting_human} empty="لا توجد محادثات تنتظر ردًا بشريًا 🎉"
        render={(i) => (
          <>
            <span className="min-w-0">
              <strong className="block truncate">{who(i)}</strong>
              <span className="text-[var(--muted-foreground)]">
                {CHANNEL_LABELS[i.channel ?? ""] ?? i.channel} · {STATUS_LABELS[i.status ?? ""] ?? i.status}
              </span>
            </span>
            <span className="flex items-center gap-2 shrink-0">
              <span className="text-[var(--muted-foreground)]">ينتظر {fmtDuration(i.waiting_seconds)}</span>
              <Link href={inbox} className="text-[var(--menu-selected-fg)] underline">فتح</Link>
            </span>
          </>
        )}
      />
      <List
        title={`تجاوزت الـ SLA (${fmtNumber(data.sla_minutes)} دقيقة)`} icon={AlertTriangle} tone="text-[var(--chart-down)]"
        items={data.sla_breached} empty="لا محادثات متأخرة عن الـ SLA"
        render={(i) => (
          <>
            <span className="min-w-0"><strong className="block truncate">{who(i)}</strong>
              <span className="text-[var(--muted-foreground)]">{CHANNEL_LABELS[i.channel ?? ""] ?? i.channel}</span></span>
            <span className="flex items-center gap-2 shrink-0 text-[var(--chart-down)]">
              <Clock className="w-3.5 h-3.5" aria-hidden />{fmtDuration(i.waiting_seconds)}
              <Link href={inbox} className="text-[var(--menu-selected-fg)] underline">فتح</Link>
            </span>
          </>
        )}
      />
      <List
        title="أحدث العملاء الساخنين" icon={Flame} tone="text-[var(--chart-4)]"
        items={data.hot_leads} empty="لا يوجد عملاء ساخنون بعد"
        render={(i) => (
          <>
            <span className="min-w-0"><strong className="block truncate">{who(i)}</strong>
              <span className="text-[var(--muted-foreground)]" dir="ltr">{i.phone}</span></span>
            <span className="flex items-center gap-2 shrink-0">
              {i.is_vip && <span className="badge badge-gold">VIP</span>}
              <span className="font-semibold">🔥 {fmtNumber(i.score)}</span>
              <Link href={inbox} className="text-[var(--menu-selected-fg)] underline">فتح</Link>
            </span>
          </>
        )}
      />
    </div>
  );
}

export function InventoryGaps({ gaps }: { gaps: DashboardAnalytics["inventory_gaps"] }) {
  return (
    <section className="card" aria-label="فجوات المخزون">
      <h2 className="text-sm font-semibold">فجوات المخزون</h2>
      <p className="text-xs text-[var(--muted-foreground)] mb-2">مواقع يطلبها العملاء مقابل العقارات النشطة المطابقة</p>
      {gaps.length === 0 ? <EmptyChart text="لم يذكر العملاء مواقع محددة بعد" /> : (
        <table className="w-full text-xs">
          <thead><tr className="text-[var(--muted-foreground)] text-start"><th className="text-start py-1">الموقع المطلوب</th><th>طلبات</th><th>عقارات مطابقة</th></tr></thead>
          <tbody>
            {gaps.map((g) => (
              <tr key={g.location} className="border-t border-[var(--border)] text-center">
                <td className="text-start py-1.5">{g.location}</td>
                <td>{fmtNumber(g.requests)}</td>
                <td className={g.matching_listings === 0 ? "text-[var(--chart-down)] font-semibold" : ""}>
                  {g.matching_listings === 0 ? "لا يوجد" : fmtNumber(g.matching_listings)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
