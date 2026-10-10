"use client";

/**
 * Support center: instant help-center answers → ticket form (category, priority, attachments) → my tickets with a
 * real thread. Contact channels (WhatsApp / meeting / docs) are shown only when the operator configured them.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle, CheckCircle2, ChevronLeft, FileText, Headphones, Inbox, Loader2, MessageCircle, Paperclip, RefreshCw, Search, Send, Video, X } from "lucide-react";
import {
  apiErrorMessage, createTicket, fetchIsStaff, fetchMeta, fetchPlatformStatus, fetchTickets, formatBytes, formatTarget, searchKb, TICKET_STATUS_LABELS,
  uploadAttachment, validateAttachment, type KbArticle, type PlatformStatus, type SupportMeta, type SupportTicket, type TicketStatus,
} from "@/lib/api/support";
import { Select } from "@/components/ui/Select";
import { TicketThread } from "@/components/support/TicketThread";
import { fieldClass, formatTicketDate, STATUS_TONE } from "@/components/support/common";

const FILTERS: { id: TicketStatus | ""; label: string }[] = [
  { id: "", label: "الكل" }, { id: "open", label: "مفتوحة" }, { id: "in_progress", label: "قيد المعالجة" }, { id: "closed", label: "مغلقة" },
];

export default function SupportPage() {
  const params = useParams();
  const locale = (Array.isArray(params?.locale) ? params.locale[0] : params?.locale) ?? "ar";

  const [meta, setMeta] = useState<SupportMeta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [platform, setPlatform] = useState<PlatformStatus | null>(null);
  const [isStaff, setIsStaff] = useState(false);

  // Help center
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<KbArticle[] | null>(null);
  const [kbError, setKbError] = useState(false);
  const [openHit, setOpenHit] = useState<string | null>(null);

  // Form
  const [category, setCategory] = useState("technical");
  const [priority, setPriority] = useState("normal");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ reference: string; failedFiles: number } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Tickets
  const [filter, setFilter] = useState<TicketStatus | "">("");
  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const loadTickets = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const r = await fetchTickets({ page: 1, page_size: 50, status: filter });
      setTickets(r.items);
      setTotal(r.total);
    } catch (e) { setListError(apiErrorMessage(e, "تعذّر تحميل التذاكر.")); } finally { setLoading(false); }
  }, [filter]);
  useEffect(() => { void loadTickets(); }, [loadTickets]);

  useEffect(() => {
    fetchMeta().then(setMeta).catch((e) => setMetaError(apiErrorMessage(e, "تعذّر تحميل خيارات الدعم.")));
    fetchPlatformStatus().then(setPlatform).catch(() => setPlatform(null));
    fetchIsStaff().then(setIsStaff);
  }, []);

  // Debounced help-center search (answers before a ticket is opened).
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits(null); setKbError(false); return; }
    const h = setTimeout(() => {
      searchKb(term).then((r) => { setHits(r); setKbError(false); }).catch(() => setKbError(true));
    }, 300);
    return () => clearTimeout(h);
  }, [q]);

  const target = meta?.priorities.find((p) => p.id === priority);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const next = [...files];
    for (const f of Array.from(list)) {
      const bad = validateAttachment(f, next.length, meta?.attachments);
      if (bad) { setFileError(bad); break; }
      next.push(f);
      setFileError(null);
    }
    setFiles(next);
    if (fileRef.current) fileRef.current.value = "";
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    if (subject.trim().length < 3 || !message.trim()) { setSubmitError("اكتب عنوانًا (3 أحرف على الأقل) وتفاصيل المشكلة."); return; }
    setSubmitting(true);
    setSubmitError(null);
    setCreated(null);
    try {
      const t = await createTicket({ subject: subject.trim(), message: message.trim(), category, priority });
      let failed = 0;
      for (const f of files) { try { await uploadAttachment(t.id, f); } catch { failed += 1; } }
      setCreated({ reference: t.reference, failedFiles: failed });
      setSubject(""); setMessage(""); setFiles([]);
      void loadTickets();
    } catch (err) { setSubmitError(apiErrorMessage(err, "تعذّر إرسال التذكرة. حاول مرة أخرى.")); } finally { setSubmitting(false); }
  };

  const contacts = meta?.contacts;
  const channelCards = [
    contacts?.whatsapp && { href: contacts.whatsapp, icon: MessageCircle, title: "محادثة واتساب مع الدعم", desc: "للاستفسارات العاجلة." },
    contacts?.zoom && { href: contacts.zoom, icon: Video, title: "جدولة اجتماع", desc: "احجز موعدًا مع فريق الدعم." },
    contacts?.docs && { href: contacts.docs, icon: FileText, title: "الوثائق", desc: "شروحات وأدلة المنصة." },
  ].filter(Boolean) as { href: string; icon: React.ElementType; title: string; desc: string }[];

  return (
    <div className="space-y-6" dir="rtl">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold text-white flex items-center gap-3"><Headphones className="w-7 h-7 text-[#C9A84C]" aria-hidden />مركز الدعم</h1>
          <p className="text-gray-300 mt-2 text-sm">ابحث أولًا في الأسئلة الشائعة، وإن لم تجد حلًّا افتح تذكرة وسنتابعها معك هنا.</p>
        </div>
        {platform && (
          <div role="status" className={`text-xs rounded-lg border px-3 py-2 ${platform.all_ok ? STATUS_TONE.green : STATUS_TONE.gold}`}>
            {platform.all_ok ? "كل خدمات المنصة تعمل" : `بعض الخدمات غير متاحة حاليًا: ${Object.entries(platform.checks).filter(([, v]) => v !== "ok").map(([k]) => ({ database: "قاعدة البيانات", redis: "الذاكرة المؤقتة", kafka: "الرسائل" }[k] ?? k)).join("، ")}`}
          </div>
        )}
      </div>
      {isStaff && <p><Link href={`/${locale}/support/staff`} className="text-[#E6C766] underline text-sm">فتح لوحة فريق الدعم</Link></p>}
      {metaError && <p role="alert" className="text-sm text-red-300 flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" />{metaError}</p>}

      {/* Help center */}
      <section aria-labelledby="kb-title" className="bg-[#111827] border border-white/10 rounded-2xl p-5 space-y-3">
        <h2 id="kb-title" className="text-lg font-bold text-white">ابحث عن إجابتك</h2>
        <div className="relative">
          <Search className="w-4 h-4 text-gray-300 absolute top-1/2 -translate-y-1/2 start-3" aria-hidden />
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="ابحث في مركز المساعدة" placeholder="مثال: البوت ما يرد، استيراد العقارات، الحملة لم تصل…" className={`${fieldClass} ps-9`} />
        </div>
        {kbError && <p role="alert" className="text-sm text-red-300">تعذّر البحث الآن.</p>}
        {hits && hits.length === 0 && !kbError && <p className="text-sm text-gray-300">لا توجد مقالات مطابقة — افتح تذكرة أدناه وسنساعدك.</p>}
        {hits && hits.length > 0 && (
          <ul className="space-y-2">
            {hits.map((a) => (
              <li key={a.id} className="border border-white/10 rounded-xl">
                <button type="button" aria-expanded={openHit === a.id} onClick={() => setOpenHit(openHit === a.id ? null : a.id)} className="w-full text-start px-4 py-3 text-sm font-semibold text-gray-100 flex items-center justify-between gap-2">
                  {a.title}<ChevronLeft className={`w-4 h-4 transition-transform ${openHit === a.id ? "-rotate-90" : ""}`} aria-hidden />
                </button>
                {openHit === a.id && (
                  <div className="px-4 pb-3 text-sm text-gray-200 leading-relaxed">
                    <p>{a.answer}</p>
                    {a.link && <Link href={`/${locale}${a.link}`} className="inline-block mt-2 text-[#E6C766] underline">{a.link_label ?? "فتح الصفحة"}</Link>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="space-y-4 lg:order-2">
          {channelCards.length > 0 ? channelCards.map(({ href, icon: Icon, title, desc }) => (
            <a key={title} href={href} target="_blank" rel="noopener noreferrer" className="block bg-[#111827] border border-white/10 rounded-2xl p-5 hover:border-[#C9A84C]/40 transition-colors">
              <Icon className="w-6 h-6 text-[#C9A84C] mb-2" aria-hidden /><h3 className="font-bold text-white">{title}</h3><p className="text-sm text-gray-300 mt-1">{desc}</p>
            </a>
          )) : (
            <div className="bg-[#111827] border border-white/10 rounded-2xl p-5 text-sm text-gray-300">قناة الدعم الأساسية هي التذاكر. ستظهر هنا قنوات إضافية (واتساب، اجتماع) عند تفعيلها من إدارة المنصة.</div>
          )}
        </div>

        <div className="lg:col-span-2 space-y-6 lg:order-1">
          <form onSubmit={submit} className="bg-[#111827] border border-[#C9A84C]/20 rounded-2xl p-5 md:p-6 space-y-4" aria-labelledby="new-ticket">
            <h2 id="new-ticket" className="text-xl font-bold text-white border-b border-white/10 pb-3">فتح تذكرة جديدة</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label htmlFor="t-cat" className="block text-sm font-medium text-gray-100 mb-1.5">نوع الطلب</label>
                <Select id="t-cat" value={category} onChange={(e) => setCategory(e.target.value)} className="w-full">
                  {(meta?.categories ?? [{ id: "technical", label: "مشكلة تقنية" }]).map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </Select>
              </div>
              <div>
                <label htmlFor="t-pri" className="block text-sm font-medium text-gray-100 mb-1.5">الأولوية</label>
                <Select id="t-pri" value={priority} onChange={(e) => setPriority(e.target.value)} className="w-full">
                  {(meta?.priorities ?? [{ id: "normal", label: "عادي", target_hours: 24 }]).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </Select>
                {target && <p className="text-xs text-gray-300 mt-1.5">الاستجابة المستهدفة {formatTarget(target.target_hours)} (هدف تشغيلي وليس التزامًا تعاقديًا).</p>}
              </div>
            </div>
            <div>
              <label htmlFor="t-sub" className="block text-sm font-medium text-gray-100 mb-1.5">عنوان الطلب</label>
              <input id="t-sub" required minLength={3} maxLength={255} value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="مثال: البوت توقف عن الرد على واتساب" className={fieldClass} />
            </div>
            <div>
              <label htmlFor="t-msg" className="block text-sm font-medium text-gray-100 mb-1.5">التفاصيل</label>
              <textarea id="t-msg" required rows={5} maxLength={10000} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="اشرح ما حدث، ومتى بدأت المشكلة، وما الذي جرّبته." className={`${fieldClass} resize-y`} />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <input ref={fileRef} id="t-files" type="file" multiple className="sr-only" accept=".png,.jpg,.jpeg,.webp,.pdf,.docx,.xlsx,.csv" onChange={(e) => addFiles(e.target.files)} />
                <button type="button" onClick={() => fileRef.current?.click()} className="text-gray-100 border border-white/20 rounded-lg px-4 py-2 text-sm inline-flex items-center gap-2 hover:bg-white/10"><Paperclip className="w-4 h-4" aria-hidden />إرفاق لقطة شاشة أو ملف</button>
                <span className="text-xs text-gray-300">اختياري — حتى {meta?.attachments.max_per_ticket ?? 5} ملفات، 5 ميجابايت للملف.</span>
              </div>
              {files.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {files.map((f, i) => (
                    <li key={`${f.name}-${i}`} className="flex items-center gap-2 text-sm text-gray-100 bg-white/5 rounded-lg px-3 py-1.5">
                      <span className="truncate flex-1" dir="auto">{f.name}</span><span className="text-xs text-gray-300">{formatBytes(f.size)}</span>
                      <button type="button" aria-label={`إزالة ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))} className="p-1 rounded hover:bg-white/10"><X className="w-4 h-4" /></button>
                    </li>
                  ))}
                </ul>
              )}
              {fileError && <p role="alert" className="text-sm text-red-300 mt-1.5">{fileError}</p>}
            </div>
            <div className="flex flex-wrap items-center gap-3 pt-1">
              <button type="submit" disabled={submitting} className="bg-[#C9A84C] text-[#0A0F1C] px-7 py-3 rounded-xl font-bold hover:bg-[#D4B55A] inline-flex items-center gap-2 disabled:opacity-60 w-full md:w-auto justify-center">
                {submitting ? <Loader2 className="w-5 h-5 animate-spin" aria-hidden /> : <Send className="w-5 h-5" aria-hidden />}إرسال التذكرة
              </button>
              {created && (
                <span role="status" className="text-sm text-emerald-300 flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4" aria-hidden />تم الإرسال — رقم التذكرة <span dir="ltr" className="font-mono">{created.reference}</span>
                  {created.failedFiles > 0 && <span className="text-amber-200"> (تعذّر رفع {created.failedFiles} ملف، أضِفه من داخل التذكرة)</span>}
                </span>
              )}
              {submitError && <span role="alert" className="text-sm text-red-300 flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" aria-hidden />{submitError}</span>}
            </div>
          </form>

          <section aria-labelledby="my-tickets" className="bg-[#111827] border border-white/10 rounded-2xl p-5 md:p-6">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-3 mb-4">
              <h2 id="my-tickets" className="text-xl font-bold text-white">تذاكري {total > 0 && <span className="text-sm font-normal text-gray-300">({total})</span>}</h2>
              <div className="flex items-center gap-2">
                <div role="group" aria-label="تصفية الحالة" className="flex gap-1">
                  {FILTERS.map((f) => (
                    <button key={f.id || "all"} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)} className={`px-3 py-1.5 rounded-lg text-xs border ${filter === f.id ? "bg-[#C9A84C] text-[#0A0F1C] border-[#C9A84C] font-bold" : "text-gray-100 border-white/20 hover:bg-white/10"}`}>{f.label}</button>
                  ))}
                </div>
                <button onClick={() => void loadTickets()} disabled={loading} className="p-2 text-gray-200 hover:text-white disabled:opacity-50" aria-label="تحديث قائمة التذاكر"><RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /></button>
              </div>
            </div>
            {listError && (
              <div role="alert" className="flex items-center gap-2 bg-red-500/10 border border-red-500/40 text-red-200 rounded-xl px-4 py-3 text-sm mb-4"><AlertTriangle className="w-4 h-4 shrink-0" />{listError}
                <button onClick={() => void loadTickets()} className="underline ms-auto">إعادة المحاولة</button></div>
            )}
            {loading ? (
              <div className="space-y-3" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="h-16 rounded-xl bg-white/5 animate-pulse" />)}</div>
            ) : tickets.length === 0 && !listError ? (
              <div className="text-center py-10"><Inbox className="w-10 h-10 text-gray-400 mx-auto mb-3" aria-hidden /><p className="text-gray-300 text-sm">{filter ? "لا توجد تذاكر بهذه الحالة." : "لا توجد تذاكر بعد — نتمنى ألّا تحتاج إليها."}</p></div>
            ) : (
              <ul className="space-y-2">
                {tickets.map((t) => {
                  const m = TICKET_STATUS_LABELS[t.status];
                  return (
                    <li key={t.id}>
                      <button onClick={() => setSelected(t.id)} className="w-full text-start border border-white/10 rounded-xl p-4 hover:bg-white/[0.04] focus-visible:ring-2 focus-visible:ring-[#C9A84C] outline-none">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="font-semibold text-gray-100 truncate">{t.subject}</p>
                            <p className="text-sm text-gray-300 mt-1 line-clamp-1">{t.message}</p>
                            <p className="text-xs text-gray-300 mt-2"><span dir="ltr" className="font-mono">{t.reference}</span> · {formatTicketDate(t.created_at)}{t.first_response_at ? " · تم الرد" : ""}</p>
                          </div>
                          <span className={`px-2.5 py-1 rounded-md text-xs font-medium border whitespace-nowrap ${STATUS_TONE[m.tone]}`}>{m.ar}</span>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      </div>

      {selected && <TicketThread ticketId={selected} meta={meta} onClose={() => setSelected(null)} onChanged={() => void loadTickets()} />}
    </div>
  );
}
