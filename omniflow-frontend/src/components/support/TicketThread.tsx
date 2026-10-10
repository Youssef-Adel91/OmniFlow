"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, Loader2, Paperclip, RefreshCw, Send, X } from "lucide-react";
import {
  apiErrorMessage, downloadAttachment, fetchTicket, formatBytes, sendMessage, TICKET_STATUS_LABELS, updateTicket,
  uploadAttachment, validateAttachment, type SupportMeta, type TicketDetail,
} from "@/lib/api/support";
import { formatTicketDate, STATUS_TONE } from "./common";

const AUTHOR: Record<string, string> = { customer: "أنت", support: "فريق الدعم", system: "النظام" };

export function TicketThread({ ticketId, meta, onClose, onChanged }: { ticketId: string; meta: SupportMeta | null; onClose: () => void; onChanged: () => void }) {
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try { setDetail(await fetchTicket(ticketId)); } catch (e) { setError(apiErrorMessage(e, "تعذّر تحميل التذكرة.")); } finally { setLoading(false); }
  }, [ticketId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { panelRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    setActionError(null);
    try { await fn(); await load(); onChanged(); } catch (e) { setActionError(apiErrorMessage(e, fallback)); } finally { setBusy(false); }
  };

  const t = detail?.ticket;
  const closed = t?.status === "closed";

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60" onClick={onClose}>
      <aside ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="تفاصيل التذكرة" dir="rtl" onClick={(e) => e.stopPropagation()}
        className="w-full max-w-xl h-full overflow-y-auto bg-[#111827] border-s border-white/10 p-5 space-y-4 focus:outline-none">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs text-gray-300 font-mono" dir="ltr">{t?.reference || "…"}</p>
            <h2 className="text-lg font-bold text-white break-words">{t?.subject ?? "تفاصيل التذكرة"}</h2>
          </div>
          <button onClick={onClose} aria-label="إغلاق" className="p-2 rounded-lg text-gray-200 hover:bg-white/10"><X className="w-5 h-5" /></button>
        </div>

        {loading && !detail && <div className="space-y-3" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="h-16 rounded-xl bg-white/5 animate-pulse" />)}</div>}
        {error && (
          <div role="alert" className="flex items-center gap-2 bg-red-500/10 border border-red-500/40 text-red-200 rounded-xl px-4 py-3 text-sm">
            <AlertTriangle className="w-4 h-4 shrink-0" /> <span className="flex-1">{error}</span>
            <button onClick={() => void load()} className="underline inline-flex items-center gap-1"><RefreshCw className="w-3.5 h-3.5" />إعادة المحاولة</button>
          </div>
        )}

        {t && detail && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className={`px-2.5 py-1 rounded-md border font-medium ${STATUS_TONE[TICKET_STATUS_LABELS[t.status].tone]}`}>{TICKET_STATUS_LABELS[t.status].ar}</span>
              <span className="text-gray-300">فُتحت {formatTicketDate(t.created_at)}</span>
              <button disabled={busy} onClick={() => void run(() => updateTicket(t.id, closed ? "open" : "closed"), "تعذّر تحديث الحالة.")}
                className="ms-auto text-gray-100 border border-white/20 rounded-lg px-3 py-1.5 hover:bg-white/10 disabled:opacity-50">
                {closed ? "إعادة فتح التذكرة" : "إغلاق التذكرة"}
              </button>
            </div>

            <ol className="space-y-3" aria-label="سجل المحادثة">
              {detail.messages.map((m) => m.kind === "status" ? (
                <li key={m.id} className="text-center text-xs text-gray-300">— {m.body} · {formatTicketDate(m.created_at)} —</li>
              ) : (
                <li key={m.id} className={`rounded-xl p-3 border ${m.author_type === "support" ? "bg-[#C9A84C]/10 border-[#C9A84C]/30" : "bg-white/5 border-white/10"}`}>
                  <div className="flex justify-between text-xs text-gray-300 mb-1"><span className="font-semibold text-gray-100">{AUTHOR[m.author_type] ?? m.author_type}</span><span>{formatTicketDate(m.created_at)}</span></div>
                  <p className="text-sm text-gray-100 whitespace-pre-wrap break-words">{m.body}</p>
                </li>
              ))}
            </ol>

            {detail.attachments.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-100 mb-2">المرفقات</h3>
                <ul className="space-y-1.5">
                  {detail.attachments.map((a) => (
                    <li key={a.id} className="flex items-center gap-2 text-sm bg-white/5 border border-white/10 rounded-lg px-3 py-2">
                      <Paperclip className="w-4 h-4 text-gray-300 shrink-0" aria-hidden />
                      <span className="truncate flex-1 text-gray-100" dir="auto">{a.filename}</span>
                      <span className="text-xs text-gray-300">{formatBytes(a.size_bytes)}</span>
                      <button aria-label={`تنزيل ${a.filename}`} onClick={() => void downloadAttachment(a).catch((e) => setActionError(apiErrorMessage(e, "تعذّر تنزيل الملف.")))}
                        className="p-1.5 rounded hover:bg-white/10 text-gray-100"><Download className="w-4 h-4" /></button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <form className="space-y-2 pt-2 border-t border-white/10" onSubmit={(e) => { e.preventDefault(); if (reply.trim()) void run(async () => { await sendMessage(t.id, reply.trim()); setReply(""); }, "تعذّر إرسال الرد."); }}>
              <label htmlFor="ticket-reply" className="block text-sm font-medium text-gray-100">{closed ? "رد جديد (يُعيد فتح التذكرة)" : "إضافة رد"}</label>
              <textarea id="ticket-reply" rows={3} value={reply} maxLength={10000} onChange={(e) => setReply(e.target.value)}
                className="w-full bg-[#0A0F1C] border border-white/15 rounded-lg py-2.5 px-3 text-sm text-white placeholder-gray-400 focus:outline-none focus:border-[#C9A84C]/60" />
              <div className="flex items-center gap-2">
                <button type="submit" disabled={busy || !reply.trim()} className="bg-[#C9A84C] text-[#0A0F1C] px-5 py-2 rounded-lg font-bold text-sm inline-flex items-center gap-2 disabled:opacity-60">
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} إرسال
                </button>
                <input ref={fileRef} type="file" className="sr-only" id="ticket-file" accept=".png,.jpg,.jpeg,.webp,.pdf,.docx,.xlsx,.csv" aria-label="إرفاق ملف"
                  onChange={(e) => {
                    const f = e.target.files?.[0]; e.target.value = "";
                    if (!f) return;
                    const bad = validateAttachment(f, detail.attachments.length, meta?.attachments);
                    if (bad) { setActionError(bad); return; }
                    void run(() => uploadAttachment(t.id, f), "تعذّر رفع الملف.");
                  }} />
                <button type="button" disabled={busy} onClick={() => fileRef.current?.click()} className="text-gray-100 border border-white/20 rounded-lg px-4 py-2 text-sm inline-flex items-center gap-2 hover:bg-white/10 disabled:opacity-50">
                  <Paperclip className="w-4 h-4" /> إرفاق ملف
                </button>
              </div>
              <p className="text-xs text-gray-300">صور، PDF، Word، Excel، CSV — حتى 5 ميجابايت للملف و5 ملفات للتذكرة.</p>
              {actionError && <p role="alert" className="text-sm text-red-300 flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" />{actionError}</p>}
            </form>
          </>
        )}
      </aside>
    </div>
  );
}
