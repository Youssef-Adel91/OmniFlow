"use client";

/** لوحة فريق الدعم — only for OmniFlow staff (emails in SUPPORT_STAFF_EMAILS). Everyone else is told the page does not exist. */
import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Headphones, Inbox, Loader2, RefreshCw, Send, X } from "lucide-react";
import {
  apiErrorMessage, fetchIsStaff, fetchStaffTicket, fetchStaffTickets, staffQueueOrder, staffReply, TICKET_STATUS_LABELS,
  type StaffTicket, type SupportTicket, type ThreadMessage, type TicketStatus,
} from "@/lib/api/support";
import { formatTicketDate, STATUS_TONE, fieldClass } from "@/components/support/common";

const PRIORITY_LABEL: Record<string, string> = { urgent: "عاجل", high: "مرتفع", normal: "عادي", low: "منخفض" };
const FILTERS: { id: TicketStatus | ""; label: string }[] = [{ id: "", label: "الكل" }, { id: "open", label: "مفتوحة" }, { id: "in_progress", label: "قيد المعالجة" }, { id: "closed", label: "مغلقة" }];

export default function StaffSupportPage() {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [filter, setFilter] = useState<TicketStatus | "">("open");
  const [items, setItems] = useState<StaffTicket[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ ticket: SupportTicket; messages: ThreadMessage[] } | null>(null);
  const [reply, setReply] = useState("");
  const [nextStatus, setNextStatus] = useState<TicketStatus | "">("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => { fetchIsStaff().then(setAllowed); }, []);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { const r = await fetchStaffTickets({ status: filter }); setItems([...r.items].sort(staffQueueOrder)); setTotal(r.total); } catch (e) { setError(apiErrorMessage(e, "تعذّر تحميل التذاكر.")); } finally { setLoading(false); }
  }, [filter]);
  useEffect(() => { if (allowed) void load(); }, [allowed, load]);

  const openTicket = async (id: string) => {
    setOpen(id); setDetail(null); setReply(""); setNextStatus(""); setNotice(null);
    try { setDetail(await fetchStaffTicket(id)); } catch (e) { setNotice({ ok: false, text: apiErrorMessage(e, "تعذّر فتح التذكرة.") }); }
  };
  const send = async () => {
    if (!open || !reply.trim()) return;
    setBusy(true); setNotice(null);
    try { await staffReply(open, reply.trim(), nextStatus || undefined); setReply(""); setNextStatus(""); setNotice({ ok: true, text: "أُرسل الرد وأُشعر العميل." }); setDetail(await fetchStaffTicket(open)); void load(); }
    catch (e) { setNotice({ ok: false, text: apiErrorMessage(e, "تعذّر إرسال الرد.") }); } finally { setBusy(false); }
  };

  if (allowed === null) return <p className="text-gray-100" role="status">جارٍ التحقق…</p>;
  if (!allowed) return <div className="max-w-md mx-auto text-center py-20 text-gray-100" dir="rtl"><h1 className="text-2xl font-bold mb-2">الصفحة غير موجودة</h1><p className="text-gray-300">هذه اللوحة لفريق الدعم فقط.</p></div>;

  return (
    <div className="space-y-6" dir="rtl">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-white flex items-center gap-3"><Headphones className="w-7 h-7 text-[#C9A84C]" aria-hidden />لوحة فريق الدعم</h1>
        <div className="flex items-center gap-2">
          <div role="group" aria-label="تصفية الحالة" className="flex gap-1">{FILTERS.map((f) => (
            <button key={f.id || "all"} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)} className={`px-3 py-1.5 rounded-lg text-xs border ${filter === f.id ? "bg-[#C9A84C] text-[#0A0F1C] border-[#C9A84C] font-bold" : "text-gray-100 border-white/20 hover:bg-white/10"}`}>{f.label}</button>))}</div>
          <button onClick={() => void load()} aria-label="تحديث" className="p-2 text-gray-200"><RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /></button>
        </div>
      </div>
      {error && <p role="alert" className="text-sm text-red-300 flex items-center gap-2"><AlertTriangle className="w-4 h-4" aria-hidden />{error} <button onClick={() => void load()} className="underline">إعادة المحاولة</button></p>}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <section className="lg:col-span-2 bg-[#111827] border border-white/10 rounded-2xl p-4" aria-label="قائمة التذاكر">
          <p className="text-xs text-gray-300 mb-3">{total} تذكرة — غير المُجاب عنها أولًا ثم حسب الأولوية</p>
          {loading ? <div className="space-y-2" aria-busy="true">{[0, 1, 2, 3].map((i) => <div key={i} className="h-16 rounded-xl bg-white/5 animate-pulse" />)}</div>
            : items.length === 0 ? <div className="text-center py-10 text-gray-300"><Inbox className="w-9 h-9 mx-auto mb-2 text-gray-400" aria-hidden />لا توجد تذاكر.</div> : (
              <ul className="space-y-2">{items.map((t) => (
                <li key={t.id}><button onClick={() => void openTicket(t.id)} aria-current={open === t.id} className={`w-full text-start rounded-xl border p-3 hover:bg-white/5 ${open === t.id ? "border-[#C9A84C]/60 bg-white/5" : "border-white/10"}`}>
                  <div className="flex items-center justify-between gap-2"><span className="font-semibold text-sm text-gray-100 truncate" dir="auto">{t.subject}</span>
                    <span className={`text-[11px] rounded-md border px-2 py-0.5 ${STATUS_TONE[TICKET_STATUS_LABELS[t.status].tone]}`}>{TICKET_STATUS_LABELS[t.status].ar}</span></div>
                  <p className="text-xs text-gray-300 mt-1" dir="auto">{t.tenant_name} · <span dir="ltr" className="font-mono">{t.reference}</span></p>
                  <p className="text-xs text-gray-300 mt-0.5">{PRIORITY_LABEL[t.priority] ?? t.priority} · {formatTicketDate(t.created_at)}{t.awaiting_reply ? " · بانتظار الرد" : ""}</p></button></li>))}</ul>)}
        </section>

        <section className="lg:col-span-3 bg-[#111827] border border-white/10 rounded-2xl p-5 min-h-[20rem]" aria-label="تفاصيل التذكرة">
          {!open ? <p className="text-gray-300 text-sm">اختر تذكرة من القائمة.</p> : !detail ? <p className="text-gray-100 flex items-center gap-2" role="status"><Loader2 className="w-4 h-4 animate-spin" aria-hidden />جارٍ التحميل…</p> : (
            <div className="space-y-4">
              <div className="flex items-start justify-between gap-2"><div><p className="text-xs text-gray-300 font-mono" dir="ltr">{detail.ticket.reference}</p><h2 className="text-lg font-bold text-white" dir="auto">{detail.ticket.subject}</h2></div>
                <button onClick={() => { setOpen(null); setDetail(null); }} aria-label="إغلاق" className="p-2 hover:bg-white/10 rounded"><X className="w-4 h-4" /></button></div>
              <ol className="space-y-2">{detail.messages.map((m) => m.kind === "status" ? <li key={m.id} className="text-center text-xs text-gray-300">— {m.body} —</li> : (
                <li key={m.id} className={`rounded-xl border p-3 ${m.author_type === "support" ? "bg-[#C9A84C]/10 border-[#C9A84C]/30" : "bg-white/5 border-white/10"}`}>
                  <div className="flex justify-between text-xs text-gray-300 mb-1"><span className="font-semibold text-gray-100">{m.author_type === "support" ? "فريق الدعم" : "العميل"}</span><span>{formatTicketDate(m.created_at)}</span></div>
                  <p className="text-sm text-gray-100 whitespace-pre-wrap break-words" dir="auto">{m.body}</p></li>))}</ol>
              <div className="space-y-2 border-t border-white/10 pt-3">
                <label htmlFor="staff-reply" className="block text-sm font-medium text-gray-100">الرد</label>
                <textarea id="staff-reply" rows={4} maxLength={10000} value={reply} onChange={(e) => setReply(e.target.value)} className={fieldClass} />
                <div className="flex flex-wrap items-center gap-3">
                  <label htmlFor="staff-status" className="text-sm text-gray-100">بعد الإرسال:</label>
                  <select id="staff-status" value={nextStatus} onChange={(e) => setNextStatus(e.target.value as TicketStatus | "")} className={`${fieldClass} !w-44`}>
                    <option value="">تلقائي (قيد المعالجة)</option><option value="in_progress">قيد المعالجة</option><option value="closed">إغلاق التذكرة</option><option value="open">إبقاؤها مفتوحة</option></select>
                  <button onClick={() => void send()} disabled={busy || !reply.trim()} className="bg-[#C9A84C] text-[#0A0F1C] px-5 py-2 rounded-lg font-bold inline-flex items-center gap-2 disabled:opacity-50">
                    {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Send className="w-4 h-4" aria-hidden />}إرسال</button>
                </div>
                {notice && <p role={notice.ok ? "status" : "alert"} className={`text-sm flex items-center gap-2 ${notice.ok ? "text-emerald-300" : "text-red-300"}`}>{notice.ok ? <CheckCircle2 className="w-4 h-4" aria-hidden /> : <AlertTriangle className="w-4 h-4" aria-hidden />}{notice.text}</p>}
              </div>
            </div>)}
        </section>
      </div>
    </div>
  );
}
