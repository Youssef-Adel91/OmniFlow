"use client";

/**
 * app/[locale]/(dashboard)/broadcasts/page.tsx — WhatsApp broadcast campaigns
 *
 * Real send path (see lib/api/broadcasts.ts for the contract):
 *   1. audience  — segment + tags + city + channel, with the live reach and WHY customers are left out
 *      (no marketing consent / unsubscribed / objection / no phone). Only consented customers are ever messaged.
 *   2. template  — an APPROVED Meta template listed live from the tenant's WhatsApp Business Account + its variables
 *   3. schedule  — now or a time in Asia/Riyadh, with a send-rate limit
 *   4. confirm   — explicit confirmation of the recipient count (the server refuses if the audience changed)
 * Campaigns list with delivered / read / replies / unsubscribes. If WhatsApp is not connected the composer says so
 * ("اربط واتساب أولًا") and cannot send.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";

const CampaignBars = dynamic(() => import("@/components/broadcasts/CampaignBars"), { ssr: false, loading: () => <div className="h-full w-full rounded bg-white/5 animate-pulse" aria-busy="true" /> });
import {
  AlertTriangle, BarChart3, Ban, CheckCircle2, Clock, Loader2, Megaphone, MessageSquare, RefreshCw, Send, ShieldCheck, Users,
} from "lucide-react";
import { Select } from "@/components/ui/Select";
import {
  BROADCAST_STATUS_LABELS, SEGMENT_OPTIONS, SEND_RATES, SOURCE_OPTIONS, broadcastErrorInfo, cancelBroadcast, createBroadcast,
  fetchBroadcastStats, fetchBroadcasts, fetchReadiness, fetchTemplates, formatRiyadh, previewAudience, renderTemplatePreview,
  riyadhInputToIso, scheduleBroadcast, segmentLabel,
  type AudiencePreview, type Broadcast, type BroadcastStats, type MetaTemplate, type Readiness, type Segment,
} from "@/lib/api/broadcasts";

const field = "w-full bg-[#0A0F1C] border border-white/15 rounded-lg py-2.5 px-3 text-sm text-white placeholder-gray-400 focus:outline-none focus:border-[#C9A84C]/60 transition-colors";
const label = "block text-sm font-medium text-gray-200 mb-1.5";

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3" aria-label={title}>
      <h3 className="flex items-center gap-2 text-base font-bold text-white">
        <span className="w-6 h-6 rounded-full bg-[#C9A84C] text-[#0A0F1C] text-xs font-bold flex items-center justify-center" aria-hidden>{n}</span>{title}
      </h3>
      {children}
    </section>
  );
}

export default function BroadcastsPage() {
  const params = useParams();
  const locale = (params?.locale as string) ?? "ar";

  // ── readiness / templates ─────────────────────────────────────────────────
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [templates, setTemplates] = useState<MetaTemplate[]>([]);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [templatesLoading, setTemplatesLoading] = useState(false);

  // ── composer ──────────────────────────────────────────────────────────────
  const [title, setTitle] = useState("");
  const [segment, setSegment] = useState<Segment>("all");
  const [tags, setTags] = useState("");
  const [city, setCity] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [templateKey, setTemplateKey] = useState("");
  const [vars, setVars] = useState<string[]>([]);
  const [when, setWhen] = useState<"now" | "later">("now");
  const [scheduledAt, setScheduledAt] = useState("");
  const [rate, setRate] = useState(20);
  const [confirmed, setConfirmed] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const [preview, setPreview] = useState<AudiencePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [draftId, setDraftId] = useState<string | null>(null);   // a draft created by a failed attempt is reused on retry
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);

  // ── list ──────────────────────────────────────────────────────────────────
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [statsOpen, setStatsOpen] = useState<string | null>(null);
  const [stats, setStats] = useState<Record<string, BroadcastStats | "loading" | "error">>({});

  const tagList = useMemo(() => tags.split(/[,،;]+/).map((t) => t.trim()).filter(Boolean), [tags]);
  const audienceKey = JSON.stringify({ segment, tagList, city: city.trim(), sources });
  const template = templates.find((t) => `${t.name}|${t.language}` === templateKey) ?? null;

  const loadList = useCallback(async () => {
    setLoading(true); setListError(null);
    try { setBroadcasts((await fetchBroadcasts({ page: 1, page_size: 20 })).items); }
    catch (e) { setListError(broadcastErrorInfo(e, "فشل تحميل الحملات").message); }
    finally { setLoading(false); }
  }, []);

  const loadReadiness = useCallback(async () => {
    setReadinessError(null);
    try {
      const r = await fetchReadiness();
      setReadiness(r);
      if (r.can_list_templates) {
        setTemplatesLoading(true); setTemplatesError(null);
        try { setTemplates(await fetchTemplates()); }
        catch (e) { setTemplatesError(broadcastErrorInfo(e, "تعذّر تحميل القوالب").message); }
        finally { setTemplatesLoading(false); }
      }
    } catch (e) { setReadinessError(broadcastErrorInfo(e, "تعذّر فحص ربط واتساب").message); }
  }, []);

  useEffect(() => { void loadList(); void loadReadiness(); }, [loadList, loadReadiness]);

  // Live reach, debounced.
  useEffect(() => {
    let cancelled = false;
    const t = setTimeout(async () => {
      setPreviewLoading(true); setPreviewError(null);
      try {
        const p = await previewAudience({ segment, tags: tagList, city, sources });
        if (!cancelled) setPreview(p);
      } catch (e) {
        if (!cancelled) { setPreview(null); setPreviewError(broadcastErrorInfo(e, "تعذّر حساب حجم الجمهور").message); }
      } finally { if (!cancelled) setPreviewLoading(false); }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audienceKey]);

  useEffect(() => { setConfirmed(false); }, [audienceKey, templateKey, when, scheduledAt]);
  useEffect(() => { setDraftId(null); }, [audienceKey, templateKey, title, rate, vars]);
  useEffect(() => { setVars(Array.from({ length: template?.variables ?? 0 }, (_, i) => vars[i] ?? (i === 0 ? "{name}" : ""))); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [templateKey]);
  useEffect(() => { if (!formSuccess) return; const t = setTimeout(() => setFormSuccess(null), 8000); return () => clearTimeout(t); }, [formSuccess]);

  const [nowMs, setNowMs] = useState(0);
  useEffect(() => { setNowMs(Date.now()); const t = setInterval(() => setNowMs(Date.now()), 30_000); return () => clearInterval(t); }, []);

  const reach = preview?.recipients_count ?? 0;
  const varsMissing = template ? vars.some((v) => !v.trim()) : false;
  const scheduleInvalid = when === "later" && (!scheduledAt || new Date(riyadhSafe(scheduledAt)).getTime() < nowMs + 60_000);
  const blocker =
    !readiness ? "جارٍ فحص ربط واتساب…"
    : !readiness.whatsapp_connected ? "اربط واتساب أولًا من الإعدادات."
    : !readiness.can_list_templates ? "أدخل معرّف حساب واتساب للأعمال (WABA ID) في الإعدادات لعرض القوالب المعتمدة."
    : !title.trim() ? "أدخل اسم الحملة."
    : !template ? "اختر قالب واتساب معتمدًا."
    : !template.supported ? (template.unsupported_reason ?? "قالب غير مدعوم.")
    : varsMissing ? "املأ كل متغيرات القالب."
    : previewLoading || !preview ? "جارٍ حساب المستلمين…"
    : reach === 0 ? "لا يوجد عملاء مؤهلون (موافقة تسويقية + رقم جوال) ضمن هذا الجمهور."
    : scheduleInvalid ? "اختر موعدًا بعد دقيقة على الأقل من الآن."
    : !confirmed ? "أكّد عدد المستلمين للمتابعة."
    : null;

  function riyadhSafe(v: string): string { try { return riyadhInputToIso(v); } catch { return "invalid"; } }

  const send = async () => {
    if (!template || !preview || submitting) return;
    setSubmitting(true); setFormError(null); setFormSuccess(null);
    try {
      const id = draftId ?? (await createBroadcast({
        title: title.trim(), message_template: template.body, meta_template_id: template.name, template_language: template.language,
        template_params: vars.map((v) => v.trim()), send_rate_per_second: rate,
        target_audience: { segment, tags: tagList, city: city.trim() || null, sources },
      })).id;
      setDraftId(id);
      const scheduled = await scheduleBroadcast(id, {
        scheduledAt: when === "later" ? riyadhInputToIso(scheduledAt) : undefined, confirmedRecipients: reach,
      });
      setBroadcasts((prev) => [scheduled, ...prev.filter((x) => x.id !== scheduled.id)]);
      setDraftId(null);
      setTitle(""); setConfirmed(false); setConfirmOpen(false);
      setFormSuccess(when === "now" ? `جُدولت الحملة للإرسال خلال دقيقة تقريبًا إلى ${reach.toLocaleString("ar")} عميل.` : `جُدولت الحملة (${formatRiyadh(scheduled.scheduled_at)}) إلى ${reach.toLocaleString("ar")} عميل.`);
    } catch (e) {
      const info = broadcastErrorInfo(e, "تعذّر إنشاء الحملة");
      setFormError(info.message);
      setConfirmOpen(false);
      if (info.code === "AUDIENCE_CHANGED") void previewAudience({ segment, tags: tagList, city, sources }).then(setPreview).catch(() => undefined);
    } finally { setSubmitting(false); }
  };

  const handleCancel = async (b: Broadcast) => {
    setBusyId(b.id);
    try { const u = await cancelBroadcast(b.id); setBroadcasts((prev) => prev.map((x) => (x.id === u.id ? u : x))); }
    catch (e) { setListError(broadcastErrorInfo(e, "تعذّر إلغاء الحملة.").message); }
    finally { setBusyId(null); }
  };

  const loadStats = async (b: Broadcast) => {
    setStats((s) => ({ ...s, [b.id]: "loading" }));
    try { const r = await fetchBroadcastStats(b.id); setStats((s) => ({ ...s, [b.id]: r })); }
    catch { setStats((s) => ({ ...s, [b.id]: "error" })); }
  };
  const toggleStats = async (b: Broadcast) => {
    if (statsOpen === b.id) { setStatsOpen(null); return; }
    setStatsOpen(b.id);
    await loadStats(b);
  };

  return (
    <div className="space-y-6" dir="rtl">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3"><Megaphone className="w-8 h-8 text-[#C9A84C]" aria-hidden />حملات الواتساب</h1>
          <p className="text-gray-300 mt-2 text-sm">أرسل رسائل تسويقية بقوالب Meta المعتمدة، فقط للعملاء الذين وافقوا على استلامها.</p>
        </div>
        <button onClick={() => { void loadList(); void loadReadiness(); }} disabled={loading}
          className="bg-white/5 border border-white/10 text-white px-4 py-2 rounded-lg flex items-center gap-2 hover:bg-white/10 transition-colors disabled:opacity-50 text-sm">
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} aria-hidden />تحديث
        </button>
      </div>

      {/* Readiness */}
      {readinessError ? (
        <div role="alert" className="flex items-center justify-between gap-3 bg-red-500/10 border border-red-500/40 text-red-200 rounded-xl px-4 py-3 text-sm">
          <span className="flex items-center gap-2"><AlertTriangle className="w-4 h-4" aria-hidden />{readinessError}</span>
          <button onClick={() => void loadReadiness()} className="text-xs underline">إعادة المحاولة</button>
        </div>
      ) : readiness && !readiness.can_send ? (
        <div role="alert" data-testid="wa-not-ready" className="flex items-start gap-3 bg-amber-500/10 border border-amber-400/50 rounded-xl px-4 py-4">
          <AlertTriangle className="w-5 h-5 text-amber-300 shrink-0 mt-0.5" aria-hidden />
          <div className="text-sm text-amber-100 leading-relaxed">
            <p className="font-bold mb-1">{readiness.whatsapp_connected ? "ينقص إعداد واتساب لإرسال الحملات" : "اربط واتساب أولًا"}</p>
            <ul className="list-disc ps-5">{readiness.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            <Link href={`/${locale}/settings`} className="inline-block mt-2 underline font-semibold">فتح الإعدادات</Link>
            <p className="mt-2 text-amber-200/90">يمكنك تجهيز الحملة الآن، لكن الإرسال والجدولة يبقيان معطّلين حتى يكتمل الربط.</p>
          </div>
        </div>
      ) : null}

      <div className="flex items-start gap-3 bg-emerald-500/5 border border-emerald-500/30 rounded-xl px-4 py-3 text-sm text-gray-200">
        <ShieldCheck className="w-5 h-5 text-emerald-300 shrink-0 mt-0.5" aria-hidden />
        <p>لا تصل الحملات إلا للعملاء الذين <strong>سجّلوا موافقتهم على الرسائل التسويقية</strong> ولم يلغوا الاشتراك، ولديهم رقم جوال. من يرسل «إلغاء الاشتراك» يُستبعد تلقائيًا.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-6">
          {/* Composer */}
          <form className="bg-[#111827] border border-white/10 rounded-2xl p-6 shadow-xl space-y-7" onSubmit={(e) => { e.preventDefault(); if (!blocker) setConfirmOpen(true); }}>
            <Step n={1} title="الجمهور">
              <div>
                <label htmlFor="bc-title" className={label}>اسم الحملة</label>
                <input id="bc-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={255} placeholder="مثال: إطلاق مشروع فلل الياسمين" className={field} />
              </div>
              <div className="grid md:grid-cols-2 gap-4">
                <div>
                  <label htmlFor="bc-seg" className={label}>الشريحة</label>
                  <Select id="bc-seg" value={segment} onChange={(e) => setSegment(e.target.value as Segment)} className={field}>
                    {SEGMENT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </Select>
                  <p className="text-xs text-gray-300 mt-1">{SEGMENT_OPTIONS.find((o) => o.value === segment)?.hint}</p>
                </div>
                <div>
                  <label htmlFor="bc-city" className={label}>المدينة (اختياري)</label>
                  <input id="bc-city" value={city} onChange={(e) => setCity(e.target.value)} maxLength={100} className={field} />
                </div>
                <div>
                  <label htmlFor="bc-tags" className={label}>وسوم (أيٌّ منها، افصل بفاصلة)</label>
                  <input id="bc-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="مستثمر، فيلا" className={field} />
                </div>
                <fieldset>
                  <legend className={label}>القناة / المصدر (اختياري)</legend>
                  <div className="flex flex-wrap gap-x-4 gap-y-1">
                    {SOURCE_OPTIONS.map((o) => (
                      <label key={o.value} className="flex items-center gap-1.5 text-sm text-gray-100 cursor-pointer">
                        <input type="checkbox" checked={sources.includes(o.value)} onChange={(e) => setSources((p) => e.target.checked ? [...p, o.value] : p.filter((x) => x !== o.value))} />{o.label}
                      </label>
                    ))}
                  </div>
                </fieldset>
              </div>

              <div className="rounded-xl border border-white/10 p-4" aria-live="polite" data-testid="reach-box">
                {previewLoading ? (
                  <p className="text-sm text-gray-300 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" aria-hidden />جارٍ حساب المستلمين…</p>
                ) : previewError ? (
                  <p role="alert" className="text-sm text-red-300">{previewError}</p>
                ) : preview ? (
                  <div className="space-y-2">
                    <p className="text-lg font-bold text-[#C9A84C] flex items-center gap-2"><Users className="w-5 h-5" aria-hidden />سيصل إلى {preview.recipients_count.toLocaleString("ar")} عميل</p>
                    {preview.recipients_count === 0 && (
                      <p className="text-sm text-amber-200">
                        لا يوجد عملاء مؤهلون. سجّل موافقة العملاء من <Link href={`/${locale}/customers`} className="underline">صفحة العملاء</Link> (أو استوردهم مع إقرار الموافقة) ثم عد إلى هنا.
                      </p>
                    )}
                    {preview.matching_segment > preview.recipients_count && (
                      <div className="text-xs text-gray-200">
                        <p className="font-semibold mb-0.5">من أصل {preview.matching_segment.toLocaleString("ar")} مطابقين للشريحة استُبعد:</p>
                        <ul className="list-disc ps-5 space-y-0.5">
                          {preview.excluded_no_consent > 0 && <li>{preview.excluded_no_consent.toLocaleString("ar")} بلا موافقة تسويقية</li>}
                          {preview.excluded_opted_out > 0 && <li>{preview.excluded_opted_out.toLocaleString("ar")} ألغوا الاشتراك</li>}
                          {preview.excluded_restricted > 0 && <li>{preview.excluded_restricted.toLocaleString("ar")} اعترضوا على معالجة بياناتهم</li>}
                          {preview.excluded_no_phone > 0 && <li>{preview.excluded_no_phone.toLocaleString("ar")} بلا رقم جوال (قنوات إنستجرام/ماسنجر)</li>}
                        </ul>
                      </div>
                    )}
                    <p className="text-xs text-gray-300">تقدير لحظي؛ يُعاد التحقق من الموافقة لكل عميل لحظة الإرسال.</p>
                  </div>
                ) : null}
              </div>
            </Step>

            <Step n={2} title="قالب الرسالة (من Meta)">
              {templatesLoading ? (
                <p className="text-sm text-gray-300 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" aria-hidden />جارٍ تحميل القوالب المعتمدة…</p>
              ) : templatesError ? (
                <div role="alert" className="text-sm text-red-300 flex items-center justify-between gap-3">
                  <span>{templatesError}</span><button type="button" onClick={() => void loadReadiness()} className="text-xs underline">إعادة المحاولة</button>
                </div>
              ) : !readiness?.can_list_templates ? (
                <p className="text-sm text-gray-300">تظهر القوالب المعتمدة هنا بعد ربط واتساب وإدخال معرّف حساب الأعمال.</p>
              ) : templates.length === 0 ? (
                <p className="text-sm text-amber-200">لا توجد قوالب معتمدة في حسابك. أنشئ قالبًا تسويقيًا في Meta Business Manager وانتظر اعتماده.</p>
              ) : (
                <>
                  <div>
                    <label htmlFor="bc-tpl" className={label}>القالب</label>
                    <Select id="bc-tpl" value={templateKey} onChange={(e) => setTemplateKey(e.target.value)} className={field}>
                      <option value="">— اختر قالبًا —</option>
                      {templates.map((t) => <option key={`${t.name}|${t.language}`} value={`${t.name}|${t.language}`} disabled={!t.supported}>{t.name} ({t.language}){t.supported ? "" : " — غير مدعوم"}</option>)}
                    </Select>
                    {template && !template.supported && <p className="text-xs text-red-300 mt-1">{template.unsupported_reason}</p>}
                  </div>
                  {template && template.variables > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs text-gray-200">متغيرات القالب — استخدم <code dir="ltr">{"{name}"}</code> لاسم العميل و<code dir="ltr">{"{city}"}</code> لمدينته:</p>
                      {vars.map((v, i) => (
                        <div key={i} className="flex items-center gap-2">
                          <span className="text-xs text-gray-300 w-14 shrink-0" dir="ltr">{`{{${i + 1}}}`}</span>
                          <input aria-label={`المتغير ${i + 1}`} value={v} maxLength={1024} onChange={(e) => setVars((p) => p.map((x, j) => (j === i ? e.target.value : x)))} className={field} />
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </Step>

            <Step n={3} title="موعد الإرسال">
              <div className="flex flex-wrap gap-6">
                <label className="flex items-center gap-2 text-sm text-gray-100 cursor-pointer"><input type="radio" name="when" checked={when === "now"} onChange={() => setWhen("now")} />فورًا (خلال دقيقة)</label>
                <label className="flex items-center gap-2 text-sm text-gray-100 cursor-pointer"><input type="radio" name="when" checked={when === "later"} onChange={() => setWhen("later")} />في وقت محدد (توقيت الرياض)</label>
              </div>
              {when === "later" && (
                <div className="grid md:grid-cols-2 gap-4">
                  <div>
                    <label htmlFor="bc-when" className={label}>التاريخ والوقت (بتوقيت الرياض)</label>
                    <input id="bc-when" type="datetime-local" value={scheduledAt} onChange={(e) => setScheduledAt(e.target.value)} className={field} dir="ltr" />
                    {scheduledAt && !scheduleInvalid && <p className="text-xs text-gray-300 mt-1">{formatRiyadh(riyadhSafe(scheduledAt))}</p>}
                  </div>
                </div>
              )}
              <div className="max-w-xs">
                <label htmlFor="bc-rate" className={label}>حد معدل الإرسال</label>
                <Select id="bc-rate" value={String(rate)} onChange={(e) => setRate(Number(e.target.value))} className={field}>
                  {SEND_RATES.map((r) => <option key={r} value={r}>{r} رسالة في الثانية</option>)}
                </Select>
                <p className="text-xs text-gray-300 mt-1">الحملات الكبيرة تُرسل بالتدريج بهذا المعدل.</p>
              </div>
            </Step>

            <Step n={4} title="المراجعة والتأكيد">
              <label className="flex items-start gap-2 text-sm text-gray-100 cursor-pointer">
                <input type="checkbox" className="mt-1" checked={confirmed} disabled={!preview || reach === 0} onChange={(e) => setConfirmed(e.target.checked)} />
                <span>أؤكد أنني راجعت الجمهور والقالب وأن الإرسال سيتم إلى <strong>{reach.toLocaleString("ar")}</strong> عميل وافقوا على الرسائل التسويقية.</span>
              </label>
              {formError && <p role="alert" className="text-sm text-red-300 flex items-start gap-1.5"><AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden />{formError}</p>}
              {formSuccess && <p role="status" className="text-sm text-emerald-300 flex items-center gap-1.5"><CheckCircle2 className="w-4 h-4" aria-hidden />{formSuccess}</p>}
              {blocker && <p className="text-xs text-amber-200" data-testid="send-blocker">{blocker}</p>}
              <button type="submit" disabled={!!blocker || submitting}
                className="w-full bg-[#C9A84C] text-[#0A0F1C] py-3 rounded-xl font-bold hover:bg-[#D4B55A] transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed">
                {submitting ? <Loader2 className="w-5 h-5 animate-spin" aria-hidden /> : when === "now" ? <Send className="w-5 h-5" aria-hidden /> : <Clock className="w-5 h-5" aria-hidden />}
                {when === "now" ? "إرسال الحملة" : "جدولة الحملة"}
              </button>
            </Step>
          </form>

          {/* Campaigns list */}
          <div className="bg-[#111827] border border-white/5 rounded-2xl p-6 shadow-xl">
            <h2 className="text-xl font-bold text-white mb-4 border-b border-white/5 pb-4">الحملات</h2>
            {listError && (
              <div role="alert" className="flex items-center justify-between gap-3 bg-red-500/10 border border-red-500/40 text-red-200 rounded-xl px-4 py-3 text-sm mb-4">
                <span className="flex items-center gap-2"><AlertTriangle className="w-4 h-4" aria-hidden />{listError}</span>
                <button onClick={() => void loadList()} className="text-xs underline">إعادة المحاولة</button>
              </div>
            )}
            {loading ? (
              <div className="space-y-3" aria-busy="true">{Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-20 rounded-xl bg-white/5 animate-pulse" />)}</div>
            ) : broadcasts.length === 0 ? (
              <div className="text-center py-12">
                <Megaphone className="w-10 h-10 text-gray-500 mx-auto mb-3" aria-hidden />
                <p className="text-gray-200 text-sm">لم تُنشئ أي حملة بعد.</p>
                <p className="text-gray-300 text-xs mt-1">جهّز أول حملة من النموذج أعلاه.</p>
              </div>
            ) : (
              <ul className="space-y-3">
                {broadcasts.map((b) => {
                  const meta = BROADCAST_STATUS_LABELS[b.status] ?? { ar: b.status, color: "#B4B8CC" };
                  const st = stats[b.id];
                  return (
                    <li key={b.id} className="border border-white/10 rounded-xl p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="font-semibold text-gray-100 truncate">{b.title}</p>
                          <p className="text-xs text-gray-300 mt-1">قالب: <span dir="ltr">{b.meta_template_id ?? "—"}</span></p>
                          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-xs text-gray-300">
                            <span className="flex items-center gap-1"><Users className="w-3 h-3" aria-hidden />{segmentLabel(b.target_audience)}</span>
                            {b.scheduled_at && <span className="flex items-center gap-1"><Clock className="w-3 h-3" aria-hidden />{formatRiyadh(b.scheduled_at)}</span>}
                            {b.recipients_count != null && <span>{b.recipients_count.toLocaleString("ar")} مستلم</span>}
                          </div>
                          {b.last_error && <p className="text-xs text-red-300 mt-2 break-words" dir="auto">{b.last_error}</p>}
                        </div>
                        <div className="flex flex-col items-end gap-2 shrink-0">
                          <span className="px-2.5 py-1 rounded-md text-xs font-medium border whitespace-nowrap" style={{ color: meta.color, background: `${meta.color}18`, borderColor: `${meta.color}55` }}>{meta.ar}</span>
                          {(b.status === "scheduled" || b.status === "draft") && (
                            <button onClick={() => void handleCancel(b)} disabled={busyId === b.id} className="text-xs text-gray-200 hover:text-red-300 flex items-center gap-1 disabled:opacity-50">
                              {busyId === b.id ? <Loader2 className="w-3 h-3 animate-spin" aria-hidden /> : <Ban className="w-3 h-3" aria-hidden />}إلغاء
                            </button>
                          )}
                          {["sending", "completed", "sent", "failed"].includes(b.status) && (
                            <button onClick={() => void toggleStats(b)} aria-expanded={statsOpen === b.id} className="text-xs text-[#C9A84C] hover:underline flex items-center gap-1"><BarChart3 className="w-3 h-3" aria-hidden />الإحصاءات</button>
                          )}
                        </div>
                      </div>
                      {statsOpen === b.id && (
                        <div className="mt-4 border-t border-white/10 pt-4" data-testid="stats-panel">
                          {st === "loading" || !st ? <div className="h-24 rounded-lg bg-white/5 animate-pulse" aria-busy="true" />
                            : st === "error" ? <p role="alert" className="text-sm text-red-300">تعذّر تحميل الإحصاءات. <button onClick={() => void loadStats(b)} className="underline">إعادة المحاولة</button></p>
                            : <StatsPanel s={st} />}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        {/* Live preview */}
        <div className="lg:col-span-1">
          <div className="bg-[#111827] border border-white/10 rounded-2xl p-6 shadow-xl sticky top-24">
            <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2"><MessageSquare className="w-5 h-5 text-gray-300" aria-hidden />معاينة الرسالة</h2>
            <div className="bg-[#0A0F1C]/80 border border-white/5 rounded-2xl p-4 min-h-[260px] flex flex-col">
              <div className="mt-auto">
                <div className="bg-[#1f2c34] rounded-2xl rounded-tr-none p-3 shadow-md inline-block max-w-[92%] border border-[#2a3942]">
                  <p className="text-[#e9edef] text-sm leading-relaxed whitespace-pre-wrap">
                    {template ? renderTemplatePreview(template.body, vars) : "اختر قالبًا لرؤية المعاينة هنا…"}
                  </p>
                  <span className="text-[11px] text-[#aebac1] flex justify-end items-center gap-1 mt-1">الآن <CheckCircle2 className="w-3.5 h-3.5 text-[#53bdeb]" aria-hidden /></span>
                </div>
              </div>
            </div>
            <p className="text-xs text-gray-300 mt-3">المعاينة بأسماء تجريبية؛ يُستبدل <code dir="ltr">{"{name}"}</code> باسم كل عميل عند الإرسال.</p>
          </div>
        </div>
      </div>

      {/* Final confirmation */}
      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" role="alertdialog" aria-modal="true" aria-labelledby="bc-confirm-title">
          <div className="w-full max-w-md rounded-2xl border border-white/15 bg-[#111827] p-6 space-y-4 text-sm text-gray-100">
            <h3 id="bc-confirm-title" className="text-lg font-bold text-white">تأكيد الإرسال</h3>
            <ul className="space-y-1.5 text-gray-200">
              <li>الحملة: <strong>{title.trim()}</strong></li>
              <li>المستلمون: <strong>{reach.toLocaleString("ar")}</strong> عميل ({segmentLabel({ segment, tags: tagList, city: city.trim() || null, sources })})</li>
              <li>القالب: <strong dir="ltr">{template?.name}</strong></li>
              <li>الموعد: <strong>{when === "now" ? "خلال دقيقة تقريبًا" : formatRiyadh(scheduledAt ? riyadhSafe(scheduledAt) : null)}</strong></li>
            </ul>
            <p className="text-amber-200 text-xs">لا يمكن سحب رسائل أُرسلت. يمكن إلغاء الحملة فقط قبل بدء إرسالها.</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmOpen(false)} disabled={submitting} className="px-4 py-2 rounded-lg border border-white/15 hover:bg-white/10 disabled:opacity-50">رجوع</button>
              <button onClick={() => void send()} disabled={submitting} className="px-4 py-2 rounded-lg bg-[#C9A84C] text-[#0A0F1C] font-bold hover:opacity-90 disabled:opacity-50 flex items-center gap-2">
                {submitting && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}تأكيد وإرسال
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatsPanel({ s }: { s: BroadcastStats }) {
  const bars = [
    { name: "أُرسلت", value: s.sent, color: "#6DB3F2" }, { name: "وصلت", value: s.delivered, color: "#6FCF73" },
    { name: "قُرئت", value: s.read, color: "#C9A84C" }, { name: "ردود", value: s.replies, color: "#B48CF2" },
    { name: "إلغاء اشتراك", value: s.unsubscribes, color: "#F2A56D" }, { name: "فشلت", value: s.failed, color: "#F28B8B" },
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 md:grid-cols-6 gap-2 text-center text-xs">
        {bars.map((b) => (
          <div key={b.name} className="rounded-lg border border-white/10 p-2">
            <div className="text-lg font-bold" style={{ color: b.color }}>{b.value.toLocaleString("ar")}</div>
            <div className="text-gray-200">{b.name}</div>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-300">
        نسبة الوصول {s.rates.delivered_pct}٪ · نسبة القراءة {s.rates.read_pct}٪ من الواصلة · الردود خلال {s.reply_window_hours} ساعة {s.rates.reply_pct}٪ · إلغاء الاشتراك {s.rates.unsubscribe_pct}٪
      </p>
      <div className="h-44" role="img" aria-label="رسم بياني لأرقام الحملة">
        <CampaignBars bars={bars} />
      </div>
      {s.top_errors.length > 0 && (
        <details className="text-xs"><summary className="cursor-pointer font-semibold text-gray-100">أكثر أسباب الفشل</summary>
          <ul className="mt-1 list-disc ps-5 text-gray-200">{s.top_errors.map((e) => <li key={e.error} dir="auto">{e.error || "غير معروف"} — {e.count}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
