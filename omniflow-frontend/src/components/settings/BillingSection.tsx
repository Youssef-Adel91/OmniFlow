"use client";

/** الفوترة والاستهلاك — current plan, usage vs limits, plan change, card on file, invoices, cancel. Admin writes; auditor read-only. */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, CreditCard, Download, FileText, Loader2, RefreshCw } from "lucide-react";
import {
  billingError, cancelSubscription, downloadInvoice, fetchInvoices, fetchPlans, fetchSubscription, fetchTransactions, formatSar,
  INTERVAL_LABEL, KIND_LABEL, planAction, priceOf, resumeSubscription, startCheckout, STATUS_LABEL, TXN_STATUS_LABEL, USAGE_LABEL,
  usagePercent, yearlySavingPercent, type Interval, type InvoiceRow, type Plan, type SubscriptionInfo, type Txn,
} from "@/lib/api/billing";
import { LoadError, SectionHeader, SectionSkeleton, cardClass } from "./common";

const fmtDate = (iso: string | null | undefined) =>
  iso ? new Intl.DateTimeFormat("ar-SA", { dateStyle: "medium", timeZone: "Asia/Riyadh" }).format(new Date(iso)) : "—";

export default function BillingSection() {
  const [sub, setSub] = useState<SubscriptionInfo | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [vatBp, setVatBp] = useState(1500);
  const [txns, setTxns] = useState<Txn[]>([]);
  const [invoices, setInvoices] = useState<InvoiceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [interval, setIntervalState] = useState<Interval>("monthly");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, p, t, i] = await Promise.all([fetchSubscription(), fetchPlans(), fetchTransactions(), fetchInvoices()]);
      setSub(s); setPlans(p.items); setVatBp(p.vat_rate_bp); setTxns(t); setInvoices(i);
      setIntervalState(s.billing_interval ?? "monthly");
    } catch (e) { setError(billingError(e, "تعذّر تحميل بيانات الفوترة.")); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const isAdmin = sub?.viewer_role === "admin";
  const rank = useMemo(() => Object.fromEntries(plans.map((p, i) => [p.code, i])), [plans]);
  const current = sub ? { code: sub.plan.code, interval: sub.billing_interval, status: sub.status, is_exempt: sub.is_exempt } : null;

  const choose = async (plan: Plan) => {
    setBusy(plan.code); setNotice(null);
    try {
      const r = await startCheckout(plan.code, interval);
      if (r.action === "checkout") { window.location.assign(r.checkout_url); return; }
      setNotice({ ok: true, text: `سيسري التغيير عند التجديد القادم (${fmtDate(r.effective_at)}).` });
      await load();
    } catch (e) { setNotice({ ok: false, text: billingError(e, "تعذّر بدء عملية الدفع.") }); } finally { setBusy(null); }
  };

  const act = async (key: string, fn: () => Promise<unknown>, ok: string, fail: string) => {
    setBusy(key); setNotice(null);
    try { await fn(); setNotice({ ok: true, text: ok }); setConfirmCancel(false); await load(); } catch (e) { setNotice({ ok: false, text: billingError(e, fail) }); } finally { setBusy(null); }
  };

  if (loading && !sub) return <div className={cardClass}><SectionSkeleton /></div>;
  if (error && !sub) return <LoadError message={error} onRetry={() => void load()} />;
  if (!sub) return null;

  const pct = usagePercent(sub.usage.ai_messages, sub.usage.limit);
  const barColor = sub.usage.level === "limit_reached" ? "bg-red-400" : sub.usage.level === "warning" ? "bg-amber-300" : "bg-emerald-400";
  const planName = "name_ar" in sub.plan ? sub.plan.name_ar : sub.plan.code;
  const retryLink = sub.pending_checkout?.checkout_url;

  return (
    <div className="space-y-6" data-testid="billing-section">
      <div className={cardClass}>
        <SectionHeader icon={CreditCard} title="الفوترة والاستهلاك" description="باقتك الحالية، استهلاك الشهر، وسجل المدفوعات والفواتير." />

        {sub.is_exempt && <p className="rounded-xl border border-white/15 p-4 text-sm text-gray-100">هذا حساب داخلي معفى من الفوترة.</p>}
        {!sub.payments_enabled && !sub.is_exempt && (
          <p role="status" className="rounded-xl border border-amber-300/40 bg-amber-500/10 p-4 text-sm text-amber-100">الدفع الإلكتروني غير مفعّل بعد على هذه المنصة. تواصل مع الدعم لتفعيل اشتراكك.</p>
        )}
        {notice && (
          <p role={notice.ok ? "status" : "alert"} className={`text-sm flex items-center gap-2 ${notice.ok ? "text-emerald-300" : "text-red-300"}`}>
            {notice.ok ? <CheckCircle2 className="w-4 h-4" aria-hidden /> : <AlertTriangle className="w-4 h-4" aria-hidden />}{notice.text}
          </p>
        )}

        <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
          <div><dt className="text-gray-300">الباقة</dt><dd className="text-lg font-bold text-white">{planName}</dd></div>
          <div><dt className="text-gray-300">الحالة</dt><dd className="text-lg font-bold text-white" data-testid="sub-status">{STATUS_LABEL[sub.status] ?? sub.status}</dd></div>
          <div><dt className="text-gray-300">{sub.status === "trial" ? "تنتهي التجربة" : "التجديد القادم"}</dt>
            <dd className="text-lg font-bold text-white">{fmtDate(sub.status === "trial" ? sub.trial_ends_at : sub.current_period_end)}</dd></div>
          <div><dt className="text-gray-300">وسيلة الدفع</dt>
            <dd className="text-lg font-bold text-white" dir="ltr">{sub.card ? `${sub.card.brand ?? "Card"} •••• ${sub.card.last4}` : "—"}</dd>
            <dd className="text-xs text-gray-300">{sub.auto_renew ? "تجديد تلقائي مفعّل" : "بدون تجديد تلقائي"}</dd></div>
        </dl>

        {sub.pending_plan_code && <p className="text-sm text-gray-100">تغيير مجدول عند التجديد: {sub.pending_plan_code} ({sub.pending_interval ? INTERVAL_LABEL[sub.pending_interval] : ""}).</p>}
        {sub.cancel_at_period_end && <p className="text-sm text-amber-200">سيُلغى الاشتراك في {fmtDate(sub.current_period_end)}. تستمر الخدمة حتى ذلك التاريخ.</p>}

        {(sub.status === "past_due" || sub.last_payment_error) && !sub.is_exempt && (
          <div role="alert" className="rounded-xl border border-red-300/40 bg-red-500/10 p-4 text-sm text-red-100 space-y-2" data-testid="payment-failed">
            <p className="font-bold flex items-center gap-2"><AlertTriangle className="w-4 h-4" aria-hidden />تعذّر تحصيل آخر دفعة</p>
            {sub.last_payment_error && <p dir="auto">السبب: {sub.last_payment_error}</p>}
            {sub.grace_ends_at && <p>يتوقف المساعد الذكي في {fmtDate(sub.grace_ends_at)} إن لم يتم السداد.</p>}
            {isAdmin && retryLink && <a href={retryLink} className="inline-block bg-[#C9A84C] text-[#0A0F1C] px-5 py-2 rounded-lg font-bold">ادفع الآن</a>}
          </div>
        )}

        <div>
          <div className="flex items-center justify-between text-sm text-gray-100 mb-1.5">
            <span>رسائل المساعد هذا الشهر</span>
            <span dir="ltr" data-testid="usage-text">{sub.usage.ai_messages.toLocaleString("en-US")}{sub.usage.limit ? ` / ${sub.usage.limit.toLocaleString("en-US")}` : ""}</span>
          </div>
          {pct != null && (
            <div className="h-2.5 rounded-full bg-white/10 overflow-hidden" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="استهلاك الرسائل">
              <div className={`h-full ${barColor}`} style={{ width: `${pct}%` }} />
            </div>
          )}
          <p className="text-xs text-gray-300 mt-1.5">{USAGE_LABEL[sub.usage.level]}{sub.usage.level !== "ok" && sub.usage.level !== "unlimited" ? " — فكّر في ترقية الباقة." : ""}</p>
        </div>
      </div>

      {!sub.is_exempt && (
        <div className={cardClass}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-lg font-bold text-white">الباقات</h3>
            <div role="group" aria-label="دورة الفوترة" className="flex gap-1">
              {(["monthly", "yearly"] as Interval[]).map((i) => (
                <button key={i} aria-pressed={interval === i} onClick={() => setIntervalState(i)}
                  className={`px-4 py-1.5 rounded-lg text-sm border ${interval === i ? "bg-[#C9A84C] text-[#0A0F1C] border-[#C9A84C] font-bold" : "text-gray-100 border-white/25 hover:bg-white/10"}`}>{INTERVAL_LABEL[i]}</button>
              ))}
            </div>
          </div>
          {!isAdmin && <p className="text-sm text-gray-300">تغيير الباقة والدفع متاحان لمدير الحساب فقط.</p>}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {plans.map((p) => {
              const price = priceOf(p, interval);
              const act = current ? planAction(current, p, interval, rank) : { label: "اشترك", disabled: false };
              const save = yearlySavingPercent(p);
              return (
                <div key={p.code} className="rounded-2xl border border-white/15 p-5 space-y-3 flex flex-col" data-testid={`plan-${p.code}`}>
                  <div>
                    <h4 className="text-lg font-bold text-white">{p.name_ar}</h4>
                    {p.is_placeholder && <span className="inline-block text-xs rounded-full border border-amber-300/50 text-amber-100 px-2 py-0.5 mt-1">سعر تجريبي — غير نهائي</span>}
                  </div>
                  <p className="text-2xl font-bold text-[#E6C766]" dir="ltr">{formatSar(price.subtotal_halalas)}<span className="text-xs text-gray-300 font-normal"> / {interval === "yearly" ? "سنة" : "شهر"}</span></p>
                  <p className="text-xs text-gray-300">قبل ضريبة القيمة المضافة ({vatBp / 100}%) — الإجمالي <span dir="ltr">{formatSar(price.total_halalas)}</span>{interval === "yearly" && save > 0 ? ` · وفّر ${save}%` : ""}</p>
                  <ul className="text-sm text-gray-100 space-y-1 flex-1">
                    {p.limits.seats != null && <li>{p.limits.seats} مستخدمين</li>}
                    {p.limits.messages_per_month != null && <li>{p.limits.messages_per_month.toLocaleString("en-US")} رسالة ذكاء اصطناعي / شهر</li>}
                    {p.limits.channels != null && <li>{p.limits.channels} قنوات</li>}
                    {p.limits.properties != null && <li>{p.limits.properties.toLocaleString("en-US")} عقار</li>}
                  </ul>
                  <button disabled={!isAdmin || act.disabled || busy !== null || !sub.payments_enabled} onClick={() => void choose(p)}
                    className="bg-[#C9A84C] text-[#0A0F1C] rounded-lg py-2.5 font-bold hover:bg-[#D4B55A] disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2">
                    {busy === p.code && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}{act.label}
                  </button>
                </div>
              );
            })}
          </div>
          <p className="text-xs text-gray-300">الدفع عبر صفحة آمنة لدى مزوّد الدفع (مدى، بطاقات، Apple Pay، STC Pay) — لا تُحفظ بيانات البطاقة لدينا. الترقية تُحتسب بالتناسب عن المدة المتبقية.</p>
          {isAdmin && sub.status === "active" && (
            <div className="pt-3 border-t border-white/10 flex flex-wrap items-center gap-3">
              {sub.cancel_at_period_end ? (
                <button disabled={busy !== null} onClick={() => void act("resume", resumeSubscription, "تم التراجع عن الإلغاء.", "تعذّر التراجع عن الإلغاء.")} className="text-sm text-gray-100 border border-white/25 rounded-lg px-4 py-2 hover:bg-white/10">التراجع عن الإلغاء</button>
              ) : confirmCancel ? (
                <>
                  <span className="text-sm text-amber-100">سيستمر اشتراكك حتى {fmtDate(sub.current_period_end)} ثم يتوقف المساعد. متأكد؟</span>
                  <button disabled={busy !== null} onClick={() => void act("cancel", cancelSubscription, "تم جدولة الإلغاء.", "تعذّر إلغاء الاشتراك.")} className="text-sm bg-red-500/80 text-white rounded-lg px-4 py-2 font-bold">نعم، ألغِ الاشتراك</button>
                  <button onClick={() => setConfirmCancel(false)} className="text-sm text-gray-100 underline">لا، أبقِه</button>
                </>
              ) : (
                <button onClick={() => setConfirmCancel(true)} className="text-sm text-red-300 underline">إلغاء الاشتراك</button>
              )}
            </div>
          )}
        </div>
      )}

      <div className={cardClass}>
        <div className="flex items-center justify-between"><h3 className="text-lg font-bold text-white flex items-center gap-2"><FileText className="w-5 h-5 text-[#C9A84C]" aria-hidden />الفواتير</h3>
          <button onClick={() => void load()} aria-label="تحديث" className="p-2 text-gray-200 hover:text-white"><RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /></button></div>
        {invoices.length === 0 ? <p className="text-sm text-gray-300">لا توجد فواتير بعد.</p> : (
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead><tr className="text-gray-300 text-start"><th className="text-start py-2">الرقم</th><th className="text-start">التاريخ</th><th className="text-start">البيان</th><th className="text-start">الإجمالي</th><th><span className="sr-only">تنزيل</span></th></tr></thead>
            <tbody>{invoices.map((i) => (
              <tr key={i.invoice_id} className="border-t border-white/10 text-gray-100">
                <td className="py-2" dir="ltr">{i.invoice_number}</td><td>{fmtDate(i.issued_at)}</td><td className="max-w-[16rem] truncate" dir="auto">{i.description}</td><td dir="ltr">{formatSar(i.total_halalas)}</td>
                <td><button aria-label={`تنزيل الفاتورة ${i.invoice_number}`} onClick={() => void downloadInvoice(i).catch((e) => setNotice({ ok: false, text: billingError(e, "تعذّر تنزيل الفاتورة.") }))} className="p-2 hover:bg-white/10 rounded"><Download className="w-4 h-4" /></button></td>
              </tr>))}</tbody></table></div>
        )}
      </div>

      <div className={cardClass}>
        <h3 className="text-lg font-bold text-white">سجل المدفوعات</h3>
        {txns.length === 0 ? <p className="text-sm text-gray-300">لا توجد عمليات دفع بعد.</p> : (
          <ul className="space-y-2">{txns.map((t) => (
            <li key={t.transaction_id} className="flex flex-wrap items-center justify-between gap-2 text-sm border border-white/10 rounded-lg px-3 py-2 text-gray-100">
              <span>{KIND_LABEL[t.kind] ?? t.kind} · {fmtDate(t.created_at)}</span>
              <span dir="ltr">{formatSar(t.amount_halalas)}</span>
              <span className={t.status === "paid" ? "text-emerald-300" : t.status === "failed" ? "text-red-300" : "text-amber-200"}>{TXN_STATUS_LABEL[t.status] ?? t.status}{t.failure_reason ? ` — ${t.failure_reason}` : ""}</span>
            </li>))}</ul>
        )}
      </div>
    </div>
  );
}
