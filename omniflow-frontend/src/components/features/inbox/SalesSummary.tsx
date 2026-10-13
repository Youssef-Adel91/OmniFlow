"use client";

/**
 * SalesSummary — hand-off panel in the inbox side column: what the assistant understood (budget, district, type, financing…), the lead label and score,
 * the deal stage, a booked viewing, and the listings it would suggest. Renders nothing while the sales feature is off (the API answers 404).
 */
import { useEffect, useState } from "react";
import { LABEL_TEXT, STAGE_LABELS, fetchHandoff, type HandoffSummary } from "@/lib/api/deals";

const MISSING_AR: Record<string, string> = { budget: "الميزانية", district: "الحي", property_type: "نوع العقار", purpose: "الغرض (سكن/استثمار)", financing: "طريقة الدفع", rooms: "عدد الغرف" };
const LABEL_STYLE: Record<string, string> = { hot: "bg-red-500/20 text-red-700", warm: "bg-amber-500/20 text-amber-700", cold: "bg-sky-500/20 text-sky-700" };

export function SalesSummary({ conversationId }: { conversationId: string }) {
  const [data, setData] = useState<HandoffSummary | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    fetchHandoff(conversationId).then((d) => { if (alive) setData(d); }).catch(() => { if (alive) setData(null); });
    return () => { alive = false; };
  }, [conversationId]);

  if (!data) return null;
  return (
    <section aria-label="ملخص المبيعات">
      <h3 className="text-2xs font-semibold text-[var(--muted-foreground)] uppercase tracking-wider mb-2">ملخص المبيعات</h3>
      <div className="bg-[var(--background)] rounded-[var(--radius-sm)] border border-[var(--border)] p-3 space-y-2 text-xs">
        <div className="flex items-center gap-2">
          {data.label && <span className={`rounded-full px-2 py-0.5 text-2xs font-semibold ${LABEL_STYLE[data.label] ?? ""}`}>{LABEL_TEXT[data.label] ?? data.label}</span>}
          {data.score != null && <span className="text-[var(--muted-foreground)]">{data.score}/100</span>}
          {data.deal && <span className="ms-auto text-[var(--foreground)]">{STAGE_LABELS[data.deal.stage]}</span>}
        </div>
        <p className="text-[var(--foreground)]">{data.summary || "لم تتضح متطلبات العميل بعد."}</p>
        {data.missing.length > 0 && <p className="text-[var(--muted-foreground)]">ينقص: {data.missing.map((k) => MISSING_AR[k] ?? k).join("، ")}</p>}
        {data.appointment && (
          <p className="text-[var(--foreground)]">معاينة: {new Date(data.appointment.scheduled_at).toLocaleString("ar-SA", { dateStyle: "medium", timeStyle: "short" })}</p>
        )}
        {data.suggested_listings.length > 0 && (
          <div>
            <p className="font-semibold text-[var(--foreground)]">عقارات مناسبة{data.match_note ? ` — ${data.match_note}` : ""}</p>
            <ul className="mt-1 space-y-0.5 text-[var(--muted-foreground)]">
              {data.suggested_listings.map((l) => (
                <li key={l.listing_id}>{[l.type_ar, l.district, l.bedrooms ? `${l.bedrooms} غرف` : null, l.price ? `${Math.round(l.price).toLocaleString("ar-SA")} ريال` : null].filter(Boolean).join(" · ")}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
