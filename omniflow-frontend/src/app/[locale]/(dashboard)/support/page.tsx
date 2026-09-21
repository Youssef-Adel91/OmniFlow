"use client";

/**
 * app/[locale]/(dashboard)/support/page.tsx — VIP support
 *
 * Wired to `lib/api/support.ts`:
 *   GET   /support/tickets       — existing tickets list
 *   POST  /support/tickets       — real form submit (subject, message)
 *   PATCH /support/tickets/{id}  — close a ticket from the list
 *
 * The backend contract only carries `subject` / `message` / `status`, so the
 * request-type + priority selectors are folded into the subject line rather
 * than sent as separate fields.
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  Headphones,
  Video,
  MessageCircle,
  FileText,
  Send,
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Loader2,
  Inbox,
} from "lucide-react";
import {
  fetchTickets,
  createTicket,
  updateTicket,
  TICKET_STATUS_LABELS,
  type SupportTicket,
} from "@/lib/api/support";

const REQUEST_TYPES = [
  "تعديل شخصية الذكاء الاصطناعي",
  "ربط مع نظام إدارة أملاك خارجي (API)",
  "مشكلة في الإرسال التلقائي",
  "استفسار حول الفوترة",
];

const PRIORITIES = [
  { value: "عالية جداً", label: "عالية جداً (High)" },
  { value: "متوسطة",     label: "متوسطة (Medium)" },
  { value: "عادية",      label: "عادية (Low)" },
];

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("ar-SA", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

export default function SupportPage() {
  // ── Form state ───────────────────────────────────────────────────────────
  const [requestType, setRequestType] = useState(REQUEST_TYPES[0]);
  const [priority, setPriority]       = useState(PRIORITIES[0].value);
  const [subject, setSubject]         = useState("");
  const [message, setMessage]         = useState("");

  const [submitting, setSubmitting]     = useState(false);
  const [submitError, setSubmitError]   = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);

  // ── Tickets list ─────────────────────────────────────────────────────────
  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [closingId, setClosingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const result = await fetchTickets({ page: 1, page_size: 20 });
      setTickets(result.items);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setListError(typeof detail === "string" ? detail : "فشل تحميل التذاكر");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!submitSuccess) return;
    const t = setTimeout(() => setSubmitSuccess(false), 5000);
    return () => clearTimeout(t);
  }, [submitSuccess]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;

    const trimmedSubject = subject.trim();
    const trimmedMessage = message.trim();
    if (!trimmedSubject || !trimmedMessage) {
      setSubmitError("يرجى تعبئة عنوان الطلب والتفاصيل.");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);
    setSubmitSuccess(false);
    try {
      // Fold type + priority into the fields the backend actually accepts.
      const created = await createTicket({
        subject: `[${requestType}] [${priority}] ${trimmedSubject}`,
        message: trimmedMessage,
      });
      setTickets((prev) => [created, ...prev]);
      setSubject("");
      setMessage("");
      setSubmitSuccess(true);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setSubmitError(typeof detail === "string" ? detail : "تعذّر إرسال التذكرة");
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = async (ticket: SupportTicket) => {
    setClosingId(ticket.id);
    try {
      const updated = await updateTicket(ticket.id, { status: "closed" });
      setTickets((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
    } catch {
      setListError("تعذّر إغلاق التذكرة.");
    } finally {
      setClosingId(null);
    }
  };

  const fieldClass =
    "w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white focus:outline-none focus:border-[#C9A84C]/50";

  return (
    <div className="space-y-8" dir="rtl">
      <div>
        <h1 className="text-3xl font-bold text-white flex items-center gap-3">
          <Headphones className="w-8 h-8 text-[#C9A84C]" />
          الدعم الفني VIP
        </h1>
        <p className="text-gray-400 mt-2 text-sm">
          أولوية الوصول لفريق الهندسة لتخصيص منظومتك.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Quick Contact Options */}
        <div className="space-y-4">
          {[
            // Static Tailwind classes (no interpolation) so the JIT compiler keeps them
            { icon: Video, iconWrap: "bg-blue-500/10", iconColor: "text-blue-400", title: "جدولة اجتماع Zoom", desc: "ناقش التخصيصات المتقدمة وربط الأنظمة مع مهندسينا مباشرة." },
            { icon: MessageCircle, iconWrap: "bg-green-500/10", iconColor: "text-green-400", title: "محادثة واتساب فورية", desc: "تواصل سريع مع مدير حسابك المخصص للإجابة على أي استفسار عاجل." },
            { icon: FileText, iconWrap: "bg-purple-500/10", iconColor: "text-purple-400", title: "مركز المساعدة (الوثائق)", desc: "تصفح الشروحات، والأدلة التقنية الخاصة بمنصة OmniFlow." },
          ].map(({ icon: Icon, iconWrap, iconColor, title, desc }) => (
            <div
              key={title}
              className="bg-[#111827] border border-white/10 rounded-2xl p-6 transition-colors"
            >
              <div className={`w-12 h-12 rounded-xl ${iconWrap} flex items-center justify-center mb-4`}>
                <Icon className={`w-6 h-6 ${iconColor}`} />
              </div>
              <h3 className="text-xl font-bold text-white mb-2">{title}</h3>
              <p className="text-sm text-gray-400">{desc}</p>
            </div>
          ))}
        </div>

        {/* Ticket Form + list */}
        <div className="lg:col-span-2 space-y-8">
          <div className="bg-[#111827] border border-[#C9A84C]/20 rounded-2xl p-6 md:p-8 shadow-xl">
            <h2 className="text-2xl font-bold text-white mb-6 border-b border-white/10 pb-4">
              فتح تذكرة دعم فني مخصصة
            </h2>

            <form className="space-y-5" onSubmit={handleSubmit}>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">نوع الطلب</label>
                  <select
                    value={requestType}
                    onChange={(e) => setRequestType(e.target.value)}
                    className={fieldClass}
                  >
                    {REQUEST_TYPES.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">الأولوية</label>
                  <select
                    value={priority}
                    onChange={(e) => setPriority(e.target.value)}
                    className={fieldClass}
                  >
                    {PRIORITIES.map((p) => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">عنوان الطلب</label>
                <input
                  type="text"
                  required
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="مثال: نرغب بربط النظام مع منصة إيجار"
                  className={fieldClass}
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">التفاصيل</label>
                <textarea
                  rows={6}
                  required
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="اشرح لنا بالتفصيل ما الذي تحتاجه..."
                  className={`${fieldClass} resize-none`}
                />
              </div>

              <div className="pt-4 flex flex-wrap items-center gap-4">
                <button
                  type="submit"
                  disabled={submitting}
                  className="bg-[#C9A84C] text-[#0A0F1C] px-8 py-3.5 rounded-xl font-bold hover:bg-[#D4B55A] transition-colors flex items-center justify-center gap-2 w-full md:w-auto disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {submitting ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <Send className="w-5 h-5" />
                  )}
                  إرسال التذكرة لفريق الهندسة
                </button>

                {submitSuccess && (
                  <span className="text-sm text-green-400 flex items-center gap-1.5">
                    <CheckCircle2 className="w-4 h-4" /> تم إرسال التذكرة بنجاح
                  </span>
                )}
                {submitError && (
                  <span role="alert" className="text-sm text-red-400 flex items-center gap-1.5">
                    <AlertTriangle className="w-4 h-4" /> {submitError}
                  </span>
                )}
              </div>
            </form>
          </div>

          {/* Existing tickets */}
          <div className="bg-[#111827] border border-white/5 rounded-2xl p-6 md:p-8 shadow-xl">
            <div className="flex items-center justify-between border-b border-white/10 pb-4 mb-4">
              <h2 className="text-xl font-bold text-white">تذاكري</h2>
              <button
                onClick={() => void load()}
                disabled={loading}
                className="text-gray-400 hover:text-white transition-colors disabled:opacity-50"
                aria-label="تحديث قائمة التذاكر"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
              </button>
            </div>

            {listError && (
              <div
                role="alert"
                className="flex items-center gap-2 bg-red-500/10 border border-red-500/40 text-red-300 rounded-xl px-4 py-3 text-sm mb-4"
              >
                <AlertTriangle className="w-4 h-4" />
                {listError}
              </div>
            )}

            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="h-16 rounded-xl bg-white/5 animate-pulse" />
                ))}
              </div>
            ) : tickets.length === 0 ? (
              <div className="text-center py-12">
                <Inbox className="w-10 h-10 text-gray-600 mx-auto mb-3" />
                <p className="text-gray-400 text-sm">لا توجد تذاكر دعم بعد.</p>
              </div>
            ) : (
              <ul className="space-y-3">
                {tickets.map((ticket) => {
                  const meta = TICKET_STATUS_LABELS[ticket.status];
                  return (
                    <li
                      key={ticket.id}
                      className="border border-white/10 rounded-xl p-4 hover:bg-white/[0.02] transition-colors"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="font-semibold text-gray-100 truncate">{ticket.subject}</p>
                          <p className="text-sm text-gray-400 mt-1 line-clamp-2">{ticket.message}</p>
                          <p className="text-xs text-gray-600 mt-2">{formatDate(ticket.created_at)}</p>
                        </div>
                        <div className="flex flex-col items-end gap-2 shrink-0">
                          <span
                            className="px-2.5 py-1 rounded-md text-xs font-medium border whitespace-nowrap"
                            style={{
                              color: meta.color,
                              background: `${meta.color}18`,
                              borderColor: `${meta.color}40`,
                            }}
                          >
                            {meta.ar}
                          </span>
                          {ticket.status !== "closed" && (
                            <button
                              onClick={() => void handleClose(ticket)}
                              disabled={closingId === ticket.id}
                              className="text-xs text-gray-400 hover:text-white transition-colors disabled:opacity-50"
                            >
                              {closingId === ticket.id ? "جارٍ الإغلاق..." : "إغلاق التذكرة"}
                            </button>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
