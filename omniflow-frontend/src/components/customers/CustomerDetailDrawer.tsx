"use client";

/**
 * components/customers/CustomerDetailDrawer.tsx — customer profile
 *
 * Shows everything the platform knows about one customer (identity + channel ids, consent, conversations,
 * appointments, paid reports, activity log) and the actions on it: edit, archive/restore, marketing
 * consent (PDPL), merge a duplicate identity into this customer, and permanent delete (admin only —
 * the server answers 403 with an Arabic message for other roles).
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Archive, Loader2, Merge, Pencil, RefreshCw, RotateCcw, ShieldCheck, ShieldOff, Trash2, X } from "lucide-react";
import {
  SOURCE_LABELS, customerErrorInfo, deleteCustomer, fetchCustomerDetail, fetchCustomers, mergeCustomers, updateCustomer,
  type Customer, type CustomerDetail,
} from "@/lib/api/customers";

interface Props {
  customerId: string | null;
  onClose: () => void;
  onChanged: () => void;
  onEdit: (c: Customer) => void;
}

const date = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("ar-SA-u-ca-gregory", { dateStyle: "medium", timeStyle: "short" }) : "—");

const ACTIVITY_LABELS: Record<string, string> = {
  "customer.created": "أُضيف العميل", "customer.opt_in_granted": "سُجّلت موافقة التواصل التسويقي", "customer.opt_out": "سُحبت موافقة التواصل التسويقي",
  "customer.archived": "أُرشف العميل", "customer.restored": "استُعيد العميل", "customer.merged": "دُمجت هوية مكررة في هذا العميل",
};

const CHANNEL_LABELS: Record<string, string> = { whatsapp: "واتساب", instagram: "إنستجرام", messenger: "ماسنجر", tiktok: "تيك توك", web: "الويب" };

export default function CustomerDetailDrawer({ customerId, onClose, onChanged, onEdit }: Props) {
  const [data, setData] = useState<CustomerDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeQuery, setMergeQuery] = useState("");
  const [mergeResults, setMergeResults] = useState<Customer[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(async () => {
    if (!customerId) return;
    setLoading(true); setError(null);
    try {
      setData(await fetchCustomerDetail(customerId));
    } catch (e) {
      setError(customerErrorInfo(e, "تعذّر تحميل بيانات العميل").message);
    } finally {
      setLoading(false);
    }
  }, [customerId]);

  useEffect(() => { setData(null); setMergeOpen(false); setConfirmDelete(false); void load(); }, [load]);

  useEffect(() => {
    if (!customerId) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [customerId, busy, onClose]);

  useEffect(() => {
    if (!mergeOpen || mergeQuery.trim().length < 3) { setMergeResults([]); return; }
    const t = setTimeout(async () => {
      try {
        const r = await fetchCustomers({ search: mergeQuery.trim(), page_size: 6 });
        setMergeResults(r.items.filter((c) => c.id !== customerId));
      } catch { setMergeResults([]); }
    }, 350);
    return () => clearTimeout(t);
  }, [mergeQuery, mergeOpen, customerId]);

  if (!customerId) return null;
  const c = data?.customer;

  const run = async (fn: () => Promise<unknown>, fallback: string, reload = true) => {
    setBusy(true); setError(null);
    try { await fn(); onChanged(); if (reload) await load(); }
    catch (e) { setError(customerErrorInfo(e, fallback).message); }
    finally { setBusy(false); }
  };

  const optedOut = !!c?.opt_out_at;

  return (
    <div className="fixed inset-0 z-50 flex justify-start bg-black/50" dir="rtl" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <aside role="dialog" aria-modal="true" aria-label="بيانات العميل"
        className="h-full w-full max-w-xl overflow-y-auto border-e border-white/10 bg-[#111827] text-gray-100 shadow-2xl">
        <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-white/10 bg-[#111827] px-5 py-4">
          <h2 className="text-base font-bold truncate">{c ? (c.display_name || c.whatsapp_profile_name || "عميل بلا اسم") : "العميل"}</h2>
          <div className="flex items-center gap-1">
            <button onClick={() => void load()} disabled={loading} aria-label="تحديث" className="p-1.5 rounded-lg hover:bg-white/10 disabled:opacity-40"><RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /></button>
            <button onClick={onClose} disabled={busy} aria-label="إغلاق" className="p-1.5 rounded-lg hover:bg-white/10 disabled:opacity-40"><X className="w-4 h-4" /></button>
          </div>
        </header>

        <div className="px-5 py-4 space-y-5 text-sm">
          {error && (
            <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-200">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden /><span>{error}</span>
              {!data && <button className="ms-auto underline text-xs" onClick={() => void load()}>إعادة المحاولة</button>}
            </div>
          )}
          {loading && !data && (
            <div className="space-y-3" aria-busy="true" aria-label="جارٍ التحميل">
              {[0, 1, 2, 3].map((i) => <div key={i} className="h-10 rounded-lg bg-white/5 animate-pulse" />)}
            </div>
          )}

          {c && data && (
            <>
              {c.archived_at && <p className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">هذا العميل مؤرشف منذ {date(c.archived_at)} ولا يظهر في القائمة الافتراضية.</p>}

              <section aria-label="الهوية" className="space-y-1.5">
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => onEdit(c)} className="px-3 py-1.5 text-xs rounded-lg border border-white/15 hover:bg-white/10 flex items-center gap-1.5"><Pencil className="w-3.5 h-3.5" aria-hidden />تعديل</button>
                  <button onClick={() => void run(() => updateCustomer(c.id, { archived: !c.archived_at }), "تعذّر الأرشفة")} disabled={busy}
                    className="px-3 py-1.5 text-xs rounded-lg border border-white/15 hover:bg-white/10 flex items-center gap-1.5 disabled:opacity-50">
                    {c.archived_at ? <><RotateCcw className="w-3.5 h-3.5" aria-hidden />استعادة</> : <><Archive className="w-3.5 h-3.5" aria-hidden />أرشفة</>}
                  </button>
                  <button onClick={() => setMergeOpen((v) => !v)} className="px-3 py-1.5 text-xs rounded-lg border border-white/15 hover:bg-white/10 flex items-center gap-1.5"><Merge className="w-3.5 h-3.5" aria-hidden />دمج مكرر</button>
                  <button onClick={() => setConfirmDelete(true)} disabled={busy} className="px-3 py-1.5 text-xs rounded-lg border border-red-500/40 text-red-300 hover:bg-red-500/10 flex items-center gap-1.5 disabled:opacity-50"><Trash2 className="w-3.5 h-3.5" aria-hidden />حذف نهائي</button>
                </div>

                {mergeOpen && (
                  <div className="rounded-lg border border-white/10 p-3 space-y-2" data-testid="merge-panel">
                    <p className="text-xs text-gray-300">ابحث عن نفس الشخص بهوية أخرى (رقم أو اسم) ليُدمج في هذا العميل: تنتقل محادثاته وتُجمع الوسوم والقنوات، ويُعتمد سحب الموافقة إن وُجد في أيٍّ منهما.</p>
                    <input value={mergeQuery} onChange={(e) => setMergeQuery(e.target.value)} placeholder="اكتب 3 أحرف على الأقل…" aria-label="بحث عن عميل مكرر"
                      className="w-full rounded-[10px] border border-white/15 bg-[#0B1121] px-3 py-2 text-sm placeholder-gray-400" />
                    {mergeQuery.trim().length >= 3 && mergeResults.length === 0 && <p className="text-xs text-gray-400">لا نتائج.</p>}
                    <ul className="space-y-1">
                      {mergeResults.map((r) => (
                        <li key={r.id} className="flex items-center justify-between gap-2 rounded-lg bg-white/5 px-3 py-2 text-xs">
                          <span className="truncate">{r.display_name || "بلا اسم"} · <span dir="ltr">{r.phone_e164 ?? r.unified_phone}</span> · {SOURCE_LABELS[r.source] ?? r.source}</span>
                          <button disabled={busy} className="shrink-0 px-2 py-1 rounded bg-[#C9A84C] text-[#0A0F1C] font-semibold disabled:opacity-50"
                            onClick={() => { if (window.confirm(`دمج «${r.display_name || r.phone_e164 || "العميل"}» في هذا العميل؟ لا يمكن التراجع.`)) void run(async () => { await mergeCustomers(c.id, r.id); setMergeOpen(false); setMergeQuery(""); }, "تعذّر الدمج"); }}>
                            دمج
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 text-xs">
                  <dt className="text-gray-400">الجوال</dt><dd dir="ltr" className="text-start">{c.phone_e164 ?? "— (بلا رقم)"}</dd>
                  <dt className="text-gray-400">المصدر</dt><dd>{SOURCE_LABELS[c.source] ?? c.source}</dd>
                  <dt className="text-gray-400">معرّفات القنوات</dt>
                  <dd dir="ltr" className="text-start break-all">{Object.keys(c.channel_user_ids).length ? Object.entries(c.channel_user_ids).map(([k, v]) => `${k}: ${v}`).join(" · ") : "—"}</dd>
                  <dt className="text-gray-400">البريد</dt><dd dir="ltr" className="text-start">{c.email ?? "—"}</dd>
                  <dt className="text-gray-400">المدينة</dt><dd>{c.city ?? "—"}</dd>
                  <dt className="text-gray-400">الوسوم</dt>
                  <dd className="flex flex-wrap gap-1">{c.tags.length ? c.tags.map((t) => <span key={t} className="rounded-full bg-white/10 px-2 py-0.5">{t}</span>) : "—"}</dd>
                  <dt className="text-gray-400">ملاحظات</dt><dd className="whitespace-pre-wrap">{c.notes ?? "—"}</dd>
                  <dt className="text-gray-400">درجة الجاهزية</dt><dd>{c.lead_score}٪ ({c.lead_tier === "hot" ? "ساخن" : c.lead_tier === "warm" ? "دافئ" : "بارد"})</dd>
                  <dt className="text-gray-400">أُضيف</dt><dd>{date(c.created_at)}</dd>
                </dl>
              </section>

              <section aria-label="الموافقة التسويقية" className="rounded-xl border border-white/10 p-3 space-y-2" data-testid="consent-section">
                <h3 className="font-semibold flex items-center gap-2">
                  {c.marketing_opt_in ? <ShieldCheck className="w-4 h-4 text-emerald-400" aria-hidden /> : <ShieldOff className="w-4 h-4 text-gray-400" aria-hidden />}
                  الموافقة على الرسائل التسويقية
                </h3>
                <p className="text-xs text-gray-300">
                  {c.marketing_opt_in
                    ? `موافق منذ ${date(c.opt_in_at)} (المصدر: ${c.opt_in_source ?? "—"}). يدخل في الحملات.`
                    : optedOut
                      ? `ألغى اشتراكه في ${date(c.opt_out_at)}. لا يستلم حملات، ولا يمكن إعادة تفعيل موافقته إلا بموافقة جديدة منه.`
                      : "لا توجد موافقة مسجّلة — لا يستلم أي حملات تسويقية."}
                </p>
                {c.marketing_opt_in ? (
                  <button disabled={busy} onClick={() => { if (window.confirm("سحب موافقة هذا العميل؟ لن يستلم حملات بعد الآن.")) void run(() => updateCustomer(c.id, { marketing_opt_in: false }), "تعذّر سحب الموافقة"); }}
                    className="px-3 py-1.5 text-xs rounded-lg border border-white/15 hover:bg-white/10 disabled:opacity-50">سحب الموافقة</button>
                ) : !optedOut && (
                  <button disabled={busy} onClick={() => { if (window.confirm("هل وافق هذا العميل فعلًا على استلام الرسائل التسويقية؟")) void run(() => updateCustomer(c.id, { marketing_opt_in: true }), "تعذّر تسجيل الموافقة"); }}
                    className="px-3 py-1.5 text-xs rounded-lg border border-white/15 hover:bg-white/10 disabled:opacity-50">تسجيل موافقة العميل</button>
                )}
              </section>

              <Section title={`المحادثات (${data.conversations.length})`} empty="لا توجد محادثات بعد." rows={data.conversations.map((x) => (
                <li key={x.conversation_id} className="flex justify-between gap-2">
                  <span>{CHANNEL_LABELS[x.channel] ?? x.channel} · {x.message_count} رسالة</span><span className="text-gray-400">{date(x.last_message_at)}</span>
                </li>))} />
              <Section title={`المواعيد (${data.appointments.length})`} empty="لا توجد مواعيد." rows={data.appointments.map((x) => (
                <li key={x.appointment_id} className="flex justify-between gap-2"><span>{x.location_note ?? "معاينة"} · {x.status}</span><span className="text-gray-400">{date(x.scheduled_at)}</span></li>))} />
              <Section title={`التقارير المدفوعة (${data.reports.length})`} empty="لا توجد تقارير." rows={data.reports.map((x) => (
                <li key={x.report_id} className="flex justify-between gap-2"><span>{x.report_type}{x.price_sar != null ? ` · ${x.price_sar} ر.س` : ""}</span><span className="text-gray-400">{date(x.created_at)}</span></li>))} />
              <Section title="سجل النشاط" empty="لا يوجد نشاط مسجّل." rows={data.activity.map((x, i) => (
                <li key={i} className="flex justify-between gap-2"><span>{ACTIVITY_LABELS[x.action] ?? x.action}</span><span className="text-gray-400">{date(x.at)}</span></li>))} />
            </>
          )}
        </div>

        {confirmDelete && c && (
          <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" role="alertdialog" aria-modal="true" aria-label="تأكيد الحذف">
            <div className="max-w-sm rounded-2xl border border-red-500/40 bg-[#111827] p-5 space-y-3 text-sm">
              <h3 className="font-bold text-red-300">حذف نهائي للعميل؟</h3>
              <p className="text-gray-300">سيُحذف العميل وكل محادثاته وتقاريره ومواعيده نهائيًا ولا يمكن استرجاعها (حق المحو في PDPL). للإخفاء فقط استخدم «أرشفة». هذا الإجراء للمدير فقط.</p>
              <div className="flex justify-end gap-2">
                <button onClick={() => setConfirmDelete(false)} disabled={busy} className="px-3 py-1.5 rounded-lg border border-white/15 hover:bg-white/10">إلغاء</button>
                <button disabled={busy} onClick={() => void run(async () => { await deleteCustomer(c.id); setConfirmDelete(false); onClose(); }, "تعذّر الحذف", false)}
                  className="px-3 py-1.5 rounded-lg bg-red-600 text-white font-semibold hover:bg-red-500 disabled:opacity-50 flex items-center gap-2">
                  {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}حذف نهائي
                </button>
              </div>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function Section({ title, rows, empty }: { title: string; rows: React.ReactNode[]; empty: string }) {
  return (
    <section aria-label={title}>
      <h3 className="font-semibold mb-1.5">{title}</h3>
      {rows.length === 0 ? <p className="text-xs text-gray-400">{empty}</p> : <ul className="space-y-1.5 text-xs">{rows}</ul>}
    </section>
  );
}
