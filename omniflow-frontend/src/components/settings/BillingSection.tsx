"use client";

/** الفوترة والاستهلاك — current plan, usage vs limits, plan change, card on file, invoices, cancel. Admin writes; auditor read-only. */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, CreditCard, Download, FileText, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  billingError, buyAddon, buyTopup, cancelSubscription, downloadCreditNote, downloadInvoice, fetchCreditNotes, fetchInvoices, fetchPlans, fetchSubscription,
  fetchTopupCatalog, fetchTransactions, formatSar, INTERVAL_LABEL, KIND_LABEL, planAction, planFeatureLines, priceOf, QUOTA_ACTION_LABEL, quotaTone,
  removeAddon, resumeSubscription, saveQuotaSettings, startCheckout, STATUS_LABEL, topupLabel, TXN_STATUS_LABEL, USAGE_LABEL, usagePercent, yearlySavingPercent,
  type CreditNoteRow, type Interval, type InvoiceRow, type Plan, type SubscriptionInfo, type TopupCatalog, type Txn,
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
  const [catalog, setCatalog] = useState<TopupCatalog | null>(null);
  const [notes, setNotes] = useState<CreditNoteRow[]>([]);
  const params = useParams<{ locale?: string }>();
  const locale = params?.locale ?? "ar";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, p, t, i, c, n] = await Promise.all([fetchSubscription(), fetchPlans(), fetchTransactions(), fetchInvoices(), fetchTopupCatalog().catch(() => null), fetchCreditNotes().catch(() => [])]);
      setSub(s); setPlans(p.items); setVatBp(p.vat_rate_bp); setTxns(t); setInvoices(i); setCatalog(c); setNotes(n);
      setIntervalState(s.billing_interval ?? "monthly");
    } catch (e) { setError(billingError(e, "تعذّر تحميل بيانات الفوترة.")); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const isAdmin = sub?.viewer_role === "admin";
  const rank = useMemo(() => Object.fromEntries(plans.map((p, i) => [p.code, i])), [plans]);
  const current = sub ? { code: sub.plan.code, interval: sub.billing_interval, status: sub.status, is_exempt: sub.is_exempt } : null;

  const checkoutOf = async (key: string, fn: () => Promise<{ checkout_url: string }>) => {
    setBusy(key); setNotice(null);
    try { window.location.assign((await fn()).checkout_url); } catch (e) { setNotice({ ok: false, text: billingError(e, "تعذّر بدء عملية الدفع.") }); setBusy(null); }
  };

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
          <p className="text-xs text-gray-300 mt-1.5">{USAGE_LABEL[sub.usage.level]}{sub.usage.level !== "ok" && sub.usage.level !== "unlimited" ? " — فكّر في ترقية الباقة أو اشترِ باقة ردود إضافية." : ""}</p>
          {sub.credits && sub.credits.remaining > 0 && (
            <p className="text-sm text-emerald-300 mt-2" data-testid="credits-remaining">رصيد الردود الإضافية: <span dir="ltr">{sub.credits.remaining.toLocaleString("en-US")}</span> رد (تُستهلك بعد حصة الباقة الشهرية، وصلاحيتها 12 شهرًا).</p>
          )}
        </div>
        {sub.quotas && !sub.quotas.unlimited && Object.keys(sub.quotas.resources).length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4" data-testid="quota-usage">
            {Object.entries(sub.quotas.resources).map(([key, r]) => {
              const tone = quotaTone(r.used, r.limit);
              const w = r.limit > 0 ? Math.min(100, Math.round((r.used / r.limit) * 100)) : 0;
              return (
                <div key={key}>
                  <div className="flex justify-between text-sm text-gray-100 mb-1"><span>{r.label_ar}</span><span dir="ltr">{r.used.toLocaleString("en-US")} / {r.limit.toLocaleString("en-US")}</span></div>
                  <div className="h-2 rounded-full bg-white/10 overflow-hidden" role="progressbar" aria-valuenow={w} aria-valuemin={0} aria-valuemax={100} aria-label={r.label_ar}>
                    <div className={`h-full ${tone === "over" ? "bg-red-400" : tone === "warn" ? "bg-amber-300" : "bg-emerald-400"}`} style={{ width: `${w}%` }} />
                  </div>
                  {tone === "over" && <p className="text-xs text-red-300 mt-1">تجاوزت الحد — لا يمكنك الإضافة اليدوية حتى ترقية الباقة.</p>}
                </div>
              );
            })}
          </div>
        )}
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
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
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
                  {p.contact_sales ? (
                    <p className="text-2xl font-bold text-[#E6C766]">تواصل معنا</p>
                  ) : (
                    <>
                      <p className="text-2xl font-bold text-[#E6C766]" dir="ltr">{formatSar(price.subtotal_halalas)}<span className="text-xs text-gray-300 font-normal"> / {interval === "yearly" ? "سنة" : "شهر"}</span></p>
                      <p className="text-xs text-gray-300">+ ضريبة القيمة المضافة ({vatBp / 100}%) — الإجمالي <span dir="ltr">{formatSar(price.total_halalas)}</span>{interval === "yearly" && save > 0 ? ` · وفّر ${save}%` : ""}</p>
                    </>
                  )}
                  <ul className="text-sm text-gray-100 space-y-1 flex-1">{planFeatureLines(p).map((l) => <li key={l}>{l}</li>)}</ul>
                  {p.contact_sales ? (
                    <Link href={`/${locale}/support`} className="bg-white/10 border border-white/25 text-white rounded-lg py-2.5 font-bold text-center hover:bg-white/15">تواصل معنا</Link>
                  ) : (
                    <button disabled={!isAdmin || act.disabled || busy !== null || !sub.payments_enabled} onClick={() => void choose(p)}
                      className="bg-[#C9A84C] text-[#0A0F1C] rounded-lg py-2.5 font-bold hover:bg-[#D4B55A] disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2">
                      {busy === p.code && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}{act.label}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          <p className="text-xs text-gray-300">الأسعار بالريال السعودي وتُضاف إليها ضريبة القيمة المضافة. رسوم رسائل واتساب يدفعها العميل لشركة Meta مباشرة ولا تدخل في سعر الباقة.</p>
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

      {!sub.is_exempt && catalog && (
        <div className={cardClass} data-testid="topups-card">
          <h3 className="text-lg font-bold text-white">ردود وإضافات إضافية</h3>
          {sub.status !== "active" && <p className="text-sm text-gray-300">متاحة بعد الاشتراك في باقة مدفوعة.</p>}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {catalog.packs.map((p) => (
              <div key={p.code} className="rounded-2xl border border-white/15 p-4 space-y-2" data-testid={`pack-${p.code}`}>
                <p className="text-lg font-bold text-white">{topupLabel(p.replies)}</p>
                <p className="text-xl font-bold text-[#E6C766]" dir="ltr">{formatSar(p.subtotal_halalas)}</p>
                <p className="text-xs text-gray-300">+ ضريبة — الإجمالي <span dir="ltr">{formatSar(p.total_halalas)}</span> · صلاحية {p.validity_months} شهرًا · لا تُرحَّل حصة الباقة الشهرية</p>
                <button disabled={!isAdmin || sub.status !== "active" || busy !== null || !sub.payments_enabled} onClick={() => void checkoutOf(p.code, () => buyTopup(p.code))}
                  className="w-full bg-[#C9A84C] text-[#0A0F1C] rounded-lg py-2 font-bold hover:bg-[#D4B55A] disabled:opacity-50 disabled:cursor-not-allowed">اشترِ الباقة</button>
              </div>
            ))}
          </div>
          <div className="space-y-3">
            {catalog.addons.map((a) => (
              <div key={a.code} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 px-4 py-3 text-sm text-gray-100" data-testid={`addon-${a.code}`}>
                <div><p className="font-bold text-white">{a.name_ar}</p>
                  <p className="text-xs text-gray-300"><span dir="ltr">{formatSar(a.monthly.subtotal_halalas)}</span> / شهر + ضريبة{a.code === "seat" && sub.addons ? ` — لديك ${sub.addons.seats} مقعد إضافي` : ""}</p>
                  {!a.available && a.unavailable_reason && <p className="text-xs text-amber-200 mt-1">{a.unavailable_reason}</p>}</div>
                <div className="flex gap-2">
                  <button disabled={!isAdmin || !a.available || sub.status !== "active" || busy !== null} onClick={() => void checkoutOf(a.code, () => buyAddon(a.code as "seat" | "whatsapp_number", 1))}
                    className="border border-white/25 rounded-lg px-4 py-2 hover:bg-white/10 disabled:opacity-50 disabled:cursor-not-allowed">إضافة 1</button>
                  {a.code === "seat" && (sub.addons?.seats ?? 0) > 0 && isAdmin && (
                    <button disabled={busy !== null} onClick={() => void act("rm-seat", () => removeAddon("seat", 1), "تمت إزالة المقعد من التجديد القادم.", "تعذّرت الإزالة (المقاعد مستخدمة؟).")}
                      className="border border-white/25 rounded-lg px-4 py-2 hover:bg-white/10">إزالة 1</button>
                  )}
                </div>
              </div>
            ))}
            <p className="text-xs text-gray-300">تُحتسب الإضافة بالتناسب عن المدة المتبقية من الفترة الحالية، ثم تدخل في سعر التجديد القادم.</p>
          </div>
          {sub.quota_settings && isAdmin && !sub.is_exempt && (
            <div className="pt-4 border-t border-white/10 space-y-3" data-testid="quota-settings">
              <h4 className="font-bold text-white">عند نفاد الردود</h4>
              <div role="radiogroup" aria-label="سلوك المساعد عند نفاد الردود" className="space-y-2">
                {(["handoff", "stop"] as const).map((v) => (
                  <label key={v} className="flex items-center gap-2 text-sm text-gray-100">
                    <input type="radio" name="quota-action" checked={sub.quota_settings!.quota_exhausted_action === v} disabled={busy !== null}
                      onChange={() => void act("quota", () => saveQuotaSettings({ quota_exhausted_action: v }), "تم حفظ الإعداد.", "تعذّر حفظ الإعداد.")} />{QUOTA_ACTION_LABEL[v]}
                  </label>
                ))}
              </div>
              <label className="flex items-start gap-2 text-sm text-gray-100">
                <input type="checkbox" role="switch" className="mt-1" checked={sub.quota_settings.auto_topup_enabled} disabled={busy !== null || !sub.card}
                  onChange={(e) => void act("auto", () => saveQuotaSettings({ auto_topup_enabled: e.target.checked, auto_topup_pack: sub.quota_settings!.auto_topup_pack ?? catalog.packs[0]?.code }),
                    e.target.checked ? "تم تفعيل الشحن التلقائي." : "تم إيقاف الشحن التلقائي.", "تعذّر تغيير الشحن التلقائي.")} />
                <span>شحن تلقائي عند النفاد من بطاقتي المحفوظة (متوقف افتراضيًا؛ بحد أقصى 3 عمليات شهريًا)
                  {!sub.card && <span className="block text-xs text-gray-300">يحتاج بطاقة محفوظة من اشتراك مدفوع.</span>}</span>
              </label>
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

      {notes.length > 0 && (
        <div className={cardClass} data-testid="credit-notes">
          <h3 className="text-lg font-bold text-white">إشعارات دائنة (استردادات)</h3>
          <ul className="space-y-2">{notes.map((n) => (
            <li key={n.credit_note_id} className="flex flex-wrap items-center justify-between gap-2 text-sm border border-white/10 rounded-lg px-3 py-2 text-gray-100">
              <span dir="ltr">{n.credit_note_number}</span><span>{fmtDate(n.issued_at)}</span><span dir="ltr">{formatSar(n.total_halalas)}</span>
              <button aria-label={`تنزيل الإشعار ${n.credit_note_number}`} onClick={() => void downloadCreditNote(n).catch((e) => setNotice({ ok: false, text: billingError(e, "تعذّر تنزيل الإشعار.") }))} className="p-2 hover:bg-white/10 rounded"><Download className="w-4 h-4" /></button>
            </li>))}</ul>
        </div>
      )}

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
