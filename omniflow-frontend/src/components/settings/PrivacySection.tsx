"use client";

/** الخصوصية والامتثال (PDPL) — find a customer, see consent history, export their data, or erase their identity. Admin only for actions. */
import React, { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle, CheckCircle2, Download, Loader2, Search, ShieldCheck, Trash2 } from "lucide-react";
import { fetchCustomers, type Customer } from "@/lib/api/customers";
import { anonymizeCustomer, CONSENT_LABEL, canConfirmErase, exportCustomer, fetchConsents, fetchPrivacySummary, type ConsentEvent, type PrivacySummary } from "@/lib/api/privacy";
import { LoadError, SectionHeader, SectionSkeleton, cardClass, inputClass } from "./common";

const fmt = (iso: string | null) => (iso ? new Intl.DateTimeFormat("ar-SA", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Riyadh" }).format(new Date(iso)) : "—");
const msg = (e: unknown, fb: string) => { const d = (e as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail; return d?.message ?? fb; };

export default function PrivacySection() {
  const params = useParams();
  const locale = (Array.isArray(params?.locale) ? params.locale[0] : params?.locale) ?? "ar";
  const [summary, setSummary] = useState<PrivacySummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Customer[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<Customer | null>(null);
  const [consents, setConsents] = useState<ConsentEvent[] | null>(null);
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const loadSummary = React.useCallback(() => { fetchPrivacySummary().then((s) => { setSummary(s); setSummaryError(null); }).catch(() => setSummaryError("تعذّر تحميل ملخص الخصوصية.")); }, []);
  React.useEffect(() => { loadSummary(); }, [loadSummary]);

  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    if (q.trim().length < 3) return;
    setSearching(true); setSelected(null); setNotice(null);
    try { const r = await fetchCustomers({ search: q.trim(), page: 1, page_size: 8 }); setResults(r.items); } catch (err) { setNotice({ ok: false, text: msg(err, "تعذّر البحث.") }); } finally { setSearching(false); }
  };
  const pick = async (c: Customer) => {
    setSelected(c); setTyped(""); setReason(""); setConsents(null); setNotice(null);
    try { setConsents(await fetchConsents(c.id)); } catch { setConsents([]); }
  };
  const doExport = async () => { if (!selected) return; setBusy("export"); setNotice(null); try { await exportCustomer(selected.id); setNotice({ ok: true, text: "تم تنزيل ملف البيانات." }); } catch (e) { setNotice({ ok: false, text: msg(e, "تعذّر التصدير (متاح للمدير فقط).") }); } finally { setBusy(null); } };
  const doErase = async () => {
    if (!selected || !canConfirmErase(typed, selected.id)) return;
    setBusy("erase"); setNotice(null);
    try { const r = await anonymizeCustomer(selected.id, reason); setNotice({ ok: true, text: `تم المحو: ${r.messages_wiped} رسالة أُزيل نصها. لا يمكن التراجع.` }); setSelected(null); setResults(null); loadSummary(); }
    catch (e) { setNotice({ ok: false, text: msg(e, "تعذّر تنفيذ المحو (متاح للمدير فقط).") }); } finally { setBusy(null); }
  };

  return (
    <div className="space-y-6" data-testid="privacy-section">
      <div className={cardClass}>
        <SectionHeader icon={ShieldCheck} title="الخصوصية والامتثال (PDPL)" description="حقوق أصحاب البيانات: الاطلاع، التصدير، والمحو. كل إجراء يُسجَّل في سجل التدقيق." />
        {summaryError && <LoadError message={summaryError} onRetry={loadSummary} />}
        {!summary && !summaryError && <SectionSkeleton />}
        {summary && (
          <>
            <dl className="grid grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
              <div><dt className="text-gray-300">العملاء</dt><dd className="text-xl font-bold text-white">{summary.customers}</dd></div>
              <div><dt className="text-gray-300">موافقون على التسويق</dt><dd className="text-xl font-bold text-white">{summary.marketing_opt_in}</dd></div>
              <div><dt className="text-gray-300">مقيّدو المعالجة</dt><dd className="text-xl font-bold text-white">{summary.processing_restricted}</dd></div>
              <div><dt className="text-gray-300">سجلات ممحوّة</dt><dd className="text-xl font-bold text-white">{summary.erased}</dd></div>
            </dl>
            <p className="text-sm text-gray-100 leading-relaxed">{summary.retention_note}</p>
          </>
        )}
        <p className="text-sm text-gray-100">الصفحتان العامتان: <Link className="underline text-[#E6C766]" href={`/${locale}/privacy`}>سياسة الخصوصية</Link> · <Link className="underline text-[#E6C766]" href={`/${locale}/terms`}>الشروط والأحكام</Link> (نصّان مؤقتان بانتظار المراجعة القانونية).</p>
      </div>

      <div className={cardClass}>
        <h3 className="text-lg font-bold text-white">طلب من عميل</h3>
        <form onSubmit={(e) => void search(e)} className="flex flex-wrap gap-3">
          <label htmlFor="priv-q" className="sr-only">ابحث برقم الجوال أو الاسم</label>
          <input id="priv-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="رقم الجوال أو الاسم (3 أحرف على الأقل)" className={`${inputClass} flex-1 min-w-[14rem]`} />
          <button type="submit" disabled={searching || q.trim().length < 3} className="bg-[#C9A84C] text-[#0A0F1C] px-5 py-2.5 rounded-lg font-bold inline-flex items-center gap-2 disabled:opacity-50">
            {searching ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Search className="w-4 h-4" aria-hidden />}بحث</button>
        </form>
        {notice && <p role={notice.ok ? "status" : "alert"} className={`text-sm flex items-center gap-2 ${notice.ok ? "text-emerald-300" : "text-red-300"}`}>{notice.ok ? <CheckCircle2 className="w-4 h-4" aria-hidden /> : <AlertTriangle className="w-4 h-4" aria-hidden />}{notice.text}</p>}
        {results && results.length === 0 && <p className="text-sm text-gray-300">لا نتائج مطابقة.</p>}
        {results && results.length > 0 && !selected && (
          <ul className="space-y-2">{results.map((c) => (
            <li key={c.id}><button onClick={() => void pick(c)} className="w-full text-start border border-white/15 rounded-lg px-4 py-3 hover:bg-white/5 text-gray-100">
              <span className="font-semibold" dir="auto">{c.display_name || "بدون اسم"}</span> <span className="text-sm text-gray-300" dir="ltr">{c.phone_e164 || ""}</span></button></li>))}</ul>
        )}

        {selected && (
          <div className="space-y-4 border-t border-white/10 pt-4">
            <p className="text-white font-semibold" dir="auto">{selected.display_name || "بدون اسم"} <span className="text-sm text-gray-300 font-normal" dir="ltr">{selected.phone_e164 || ""}</span></p>
            <div>
              <h4 className="text-sm font-bold text-gray-100 mb-2">سجل الموافقات</h4>
              {consents === null ? <p className="text-sm text-gray-300">جارٍ التحميل…</p> : consents.length === 0 ? <p className="text-sm text-gray-300">لا أحداث موافقة مسجّلة.</p> : (
                <ul className="text-sm text-gray-100 space-y-1">{consents.map((c, i) => <li key={i}>{CONSENT_LABEL[c.action] ?? c.action} — {fmt(c.at)}{c.source ? ` (${c.source})` : ""}</li>)}</ul>)}
            </div>
            <button onClick={() => void doExport()} disabled={busy !== null} className="text-sm text-gray-100 border border-white/25 rounded-lg px-4 py-2 hover:bg-white/10 inline-flex items-center gap-2 disabled:opacity-50">
              {busy === "export" ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Download className="w-4 h-4" aria-hidden />}تصدير بيانات العميل (JSON)</button>
            <div className="rounded-xl border border-red-300/40 bg-red-500/10 p-4 space-y-3">
              <h4 className="font-bold text-red-100 flex items-center gap-2"><Trash2 className="w-4 h-4" aria-hidden />محو هوية العميل (نهائي)</h4>
              <p className="text-sm text-red-100">يُزال الاسم والجوال والبريد والملاحظات ونصوص كل الرسائل، ويُحظر التواصل معه. تبقى الأعداد الإحصائية وسجل التدقيق. لا يمكن التراجع.</p>
              <label htmlFor="priv-reason" className="block text-sm text-red-100">سبب الطلب (اختياري)</label>
              <input id="priv-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} className={inputClass} />
              <label htmlFor="priv-confirm" className="block text-sm text-red-100">للتأكيد اكتب معرّف العميل: <code dir="ltr" className="text-xs select-all">{selected.id}</code></label>
              <input id="priv-confirm" dir="ltr" value={typed} onChange={(e) => setTyped(e.target.value)} className={inputClass} />
              <button onClick={() => void doErase()} disabled={busy !== null || !canConfirmErase(typed, selected.id)} className="bg-red-500/80 text-white font-bold rounded-lg px-5 py-2 disabled:opacity-40 inline-flex items-center gap-2">
                {busy === "erase" && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}محو نهائي</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
