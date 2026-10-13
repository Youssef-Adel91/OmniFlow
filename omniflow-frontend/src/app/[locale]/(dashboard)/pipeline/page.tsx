"use client";

/**
 * app/[locale]/(dashboard)/pipeline/page.tsx — deals board (Kanban)
 *
 * One column per stage. Moving a deal uses an explicit "move to" menu on the card (keyboard and screen-reader friendly, works on phones); closing as
 * "lost" asks for the reason. Shows a clear "not enabled" state while FEATURE_SALES_CLOSING is off on the server.
 */
import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Loader2, RefreshCw, AlertTriangle } from "lucide-react";
import {
  LABEL_TEXT, LOST_REASON_LABELS, STAGE_LABELS, dealErrorMessage, fetchBoard, moveDeal,
  type Board, type DealCard, type DealStage, type LostReason,
} from "@/lib/api/deals";

const LABEL_STYLE: Record<string, string> = { hot: "bg-red-500/20 text-red-200", warm: "bg-amber-500/20 text-amber-200", cold: "bg-sky-500/20 text-sky-200" };
const field = "w-full bg-[#0A0F1C] border border-white/15 rounded-lg py-1.5 px-2 text-xs text-white focus:outline-none focus:border-[#C9A84C]/60";

function money(v: number | null) { return v == null ? null : `${Math.round(v).toLocaleString("ar-SA")} ريال`; }

function Card({ deal, locale, onMove }: { deal: DealCard; locale: string; onMove: (d: DealCard, stage: DealStage, reason?: LostReason) => Promise<void> }) {
  const [pickingReason, setPickingReason] = useState(false);
  const [busy, setBusy] = useState(false);
  const go = async (stage: DealStage, reason?: LostReason) => { setBusy(true); try { await onMove(deal, stage, reason); } finally { setBusy(false); setPickingReason(false); } };
  return (
    <li className="rounded-xl border border-white/10 bg-white/5 p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-white truncate">{deal.customer_name}</p>
        {deal.lead_label && <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${LABEL_STYLE[deal.lead_label]}`}>{LABEL_TEXT[deal.lead_label]}</span>}
      </div>
      <p className="text-xs text-gray-300">
        {[deal.property_type, deal.district, deal.budget_max ? `حتى ${money(deal.budget_max)}` : null].filter(Boolean).join(" · ") || "لم تتحدد المتطلبات بعد"}
      </p>
      {(deal.offer_sar || deal.value_sar) && <p className="text-xs text-[#C9A84C]">{deal.value_sar ? `القيمة ${money(deal.value_sar)}` : `عرض العميل ${money(deal.offer_sar)}`}</p>}
      {deal.stage === "lost" && deal.lost_reason && <p className="text-xs text-gray-300">السبب: {LOST_REASON_LABELS[deal.lost_reason]}</p>}
      <div className="flex items-center gap-2">
        {deal.conversation_id && <Link href={`/${locale}/inbox?conversation=${deal.conversation_id}`} className="shrink-0 text-xs text-[#C9A84C] underline">المحادثة</Link>}
        <label className="sr-only" htmlFor={`mv-${deal.deal_id}`}>نقل الصفقة</label>
        <select id={`mv-${deal.deal_id}`} className={field} disabled={busy} value="" onChange={(e) => {
          const st = e.target.value as DealStage;
          if (!st) return;
          if (st === "lost") setPickingReason(true); else void go(st);
        }}>
          <option value="">نقل إلى…</option>
          {(Object.keys(STAGE_LABELS) as DealStage[]).filter((s) => s !== deal.stage).map((s) => <option key={s} value={s}>{STAGE_LABELS[s]}</option>)}
        </select>
      </div>
      {pickingReason && (
        <div className="space-y-1">
          <label className="text-xs text-gray-200" htmlFor={`lr-${deal.deal_id}`}>سبب الخسارة</label>
          <select id={`lr-${deal.deal_id}`} className={field} defaultValue="" onChange={(e) => { if (e.target.value) void go("lost", e.target.value as LostReason); }}>
            <option value="">اختر السبب…</option>
            {(Object.keys(LOST_REASON_LABELS) as LostReason[]).map((r) => <option key={r} value={r}>{LOST_REASON_LABELS[r]}</option>)}
          </select>
        </div>
      )}
    </li>
  );
}

export default function PipelinePage() {
  const { locale } = useParams<{ locale: string }>();
  const [board, setBoard] = useState<Board | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [mine, setMine] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try { setBoard(await fetchBoard(mine)); } catch (e) { setError(dealErrorMessage(e)); setBoard(undefined); }
  }, [mine]);
  useEffect(() => { void load(); }, [load]);

  const onMove = async (d: DealCard, stage: DealStage, reason?: LostReason) => {
    setError(null);
    try { await moveDeal(d.deal_id, { stage, lost_reason: reason }); await load(); } catch (e) { setError(dealErrorMessage(e)); }
  };

  return (
    <div className="space-y-4 p-4 md:p-6" dir="rtl">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold text-white">مسار الصفقات</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-200"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> صفقاتي فقط</label>
          <button type="button" onClick={() => void load()} className="inline-flex items-center gap-1 rounded-lg border border-white/15 px-3 py-1.5 text-sm text-white hover:bg-white/5"><RefreshCw className="h-4 w-4" aria-hidden /> تحديث</button>
        </div>
      </header>
      {error && <p role="alert" className="flex items-center gap-2 rounded-lg bg-red-500/10 p-3 text-sm text-red-200"><AlertTriangle className="h-4 w-4" aria-hidden />{error}</p>}
      {board === undefined && !error && <p className="flex items-center gap-2 text-gray-300" role="status"><Loader2 className="h-4 w-4 animate-spin" aria-hidden /> جارٍ التحميل…</p>}
      {board === null && (
        <div className="rounded-xl border border-white/10 bg-white/5 p-6 text-center text-gray-200">
          <p className="font-semibold">مساعد إغلاق الصفقات غير مفعّل بعد.</p>
          <p className="mt-1 text-sm text-gray-300">يُفعَّل من الإعدادات بعد تفعيله على مستوى المنصة.</p>
        </div>
      )}
      {board && (
        <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
          {board.stages.map((stage) => (
            <section key={stage} aria-label={STAGE_LABELS[stage]} className="min-w-0">
              <h2 className="mb-2 flex items-center justify-between text-sm font-bold text-white">{STAGE_LABELS[stage]}<span className="rounded-full bg-white/10 px-2 text-xs">{board.columns[stage].total}</span></h2>
              {board.columns[stage].cards.length === 0
                ? <p className="rounded-lg border border-dashed border-white/10 p-3 text-xs text-gray-400">لا توجد صفقات</p>
                : <ul className="space-y-2">{board.columns[stage].cards.map((d) => <Card key={d.deal_id} deal={d} locale={locale} onMove={onMove} />)}</ul>}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
