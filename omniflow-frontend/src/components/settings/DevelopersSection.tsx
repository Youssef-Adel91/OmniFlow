"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Check, Code2, Copy, KeyRound, Loader2, Send, Trash2, Webhook } from "lucide-react";
import {
  EVENT_LABELS, createApiKey, createWebhook, deleteWebhook, deliveryLabel, fetchDeliveries, fetchOverview, revokeApiKey, rotateSecret, testWebhook, updateWebhook,
  type DeliveryRow, type DevOverview,
} from "@/lib/api/developers";
import { settingsErrorMessage } from "@/lib/api/settings";
import { LoadError, SectionHeader, SectionSkeleton, cardClass, inputClass } from "./common";

const fmt = (iso: string | null) => (iso ? new Intl.DateTimeFormat("ar-SA", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Riyadh" }).format(new Date(iso)) : "—");

/** A secret/key that is shown exactly once, with a copy button. */
function OneTime({ label, value, onDone }: { label: string; value: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div role="alert" className="rounded-xl border border-amber-300/50 bg-amber-500/10 p-4 space-y-2" data-testid="one-time-secret">
      <p className="text-sm text-amber-100 font-semibold">{label} — انسخه الآن، لن يُعرض مرة أخرى.</p>
      <div className="flex gap-2 items-center">
        <code dir="ltr" className="flex-1 break-all rounded-lg bg-black/40 px-3 py-2 text-xs text-white">{value}</code>
        <button type="button" onClick={() => { void navigator.clipboard?.writeText(value).then(() => setCopied(true)); }} className="px-3 py-2 rounded-lg border border-white/25 text-xs flex items-center gap-1.5 hover:bg-white/10">
          {copied ? <Check className="w-3.5 h-3.5" aria-hidden /> : <Copy className="w-3.5 h-3.5" aria-hidden />}{copied ? "نُسخ" : "نسخ"}
        </button>
      </div>
      <button type="button" onClick={onDone} className="text-xs underline text-amber-100">تم، أخفِ</button>
    </div>
  );
}

export default function DevelopersSection() {
  const [data, setData] = useState<DevOverview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [secret, setSecret] = useState<{ label: string; value: string } | null>(null);
  const [keyName, setKeyName] = useState("");
  const [hook, setHook] = useState({ url: "", description: "", events: [] as string[] });
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<{ id: string; rows: DeliveryRow[] } | null>(null);

  const load = useCallback(async () => {
    try { setData(await fetchOverview()); setLoadError(null); } catch (e) { setLoadError(settingsErrorMessage(e, "تعذّر تحميل إعدادات المطورين")); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const run = async (fn: () => Promise<void>, fallback: string) => {
    setBusy(true); setNotice(null);
    try { await fn(); await load(); } catch (e) { setNotice(settingsErrorMessage(e, fallback)); }
    setBusy(false);
  };

  return (
    <div className="space-y-6">
      <div className={cardClass}>
        <SectionHeader icon={Code2} title="المطورون" description="مفاتيح API للقراءة فقط (العملاء والمحادثات والعقارات) وWebhooks صادرة موقّعة. التوثيق الكامل في docs/api.md." />
        {loadError && <LoadError message={loadError} onRetry={() => void load()} />}
        {notice && <p role="alert" className="text-sm text-red-300">{notice}</p>}
        {secret && <OneTime label={secret.label} value={secret.value} onDone={() => setSecret(null)} />}
        {!data && !loadError ? <SectionSkeleton /> : data && (
          <>
            <section aria-label="مفاتيح API" className="space-y-3">
              <h3 className="font-bold text-white flex items-center gap-2"><KeyRound className="w-4 h-4 text-[#C9A84C]" aria-hidden />مفاتيح API (قراءة فقط)</h3>
              <p className="text-xs text-gray-300">الاستخدام: آخر 24 ساعة {data.usage.last_24h} طلب · آخر 7 أيام {data.usage.last_7d} · الحد {data.limits.rate_limit_per_minute} طلب/دقيقة لكل مفتاح.</p>
              <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (!keyName.trim()) return; void run(async () => { const k = await createApiKey(keyName.trim()); setSecret({ label: "مفتاح API", value: k.key }); setKeyName(""); }, "تعذّر إنشاء المفتاح (للمدير فقط)"); }}>
                <label className="flex-1 min-w-[12rem]"><span className="sr-only">اسم المفتاح</span><input className={inputClass} placeholder="اسم المفتاح (مثل: نظام المحاسبة)" value={keyName} onChange={(e) => setKeyName(e.target.value)} maxLength={80} /></label>
                <button type="submit" disabled={busy || !keyName.trim()} className="px-4 py-2 rounded-lg bg-[#C9A84C] text-[#0A0F1C] font-semibold text-sm disabled:opacity-50">إنشاء مفتاح</button>
              </form>
              {data.api_keys.length === 0 ? <p className="text-sm text-gray-300 border border-dashed border-white/15 rounded-xl p-4">لا توجد مفاتيح بعد. أنشئ مفتاحًا لقراءة بياناتك من أنظمتك.</p> : (
                <ul className="divide-y divide-white/10 border border-white/10 rounded-xl">
                  {data.api_keys.map((k) => (
                    <li key={k.key_id} className="p-3 flex flex-wrap items-center justify-between gap-2 text-sm">
                      <span><span className="text-white font-medium">{k.name}</span> <code dir="ltr" className="text-xs text-gray-300">{k.prefix}…</code>
                        <span className="block text-xs text-gray-300">أُنشئ {fmt(k.created_at)} · آخر استخدام {fmt(k.last_used_at)}{k.revoked_at ? ` · أُلغي ${fmt(k.revoked_at)}` : ""}</span></span>
                      {k.revoked_at ? <span className="text-xs text-gray-300 border border-white/20 rounded-full px-2 py-0.5">مُلغى</span> :
                        <button type="button" disabled={busy} onClick={() => { if (window.confirm("إلغاء المفتاح؟ ستتوقف الأنظمة التي تستخدمه فورًا.")) void run(() => revokeApiKey(k.key_id), "تعذّر الإلغاء"); }} className="px-3 py-1.5 text-xs rounded-lg border border-red-400/40 text-red-200 hover:bg-red-500/10 flex items-center gap-1.5"><Trash2 className="w-3.5 h-3.5" aria-hidden />إلغاء</button>}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-label="Webhooks" className="space-y-3 pt-2">
              <h3 className="font-bold text-white flex items-center gap-2"><Webhook className="w-4 h-4 text-[#C9A84C]" aria-hidden />Webhooks صادرة</h3>
              <form className="space-y-3 rounded-xl border border-white/10 p-4" onSubmit={(e) => { e.preventDefault(); void run(async () => { const w = await createWebhook({ url: hook.url.trim(), events: hook.events, description: hook.description.trim() || undefined }); setSecret({ label: "سر التوقيع", value: w.secret }); setHook({ url: "", description: "", events: [] }); }, "تعذّر إضافة الـ webhook"); }}>
                <label className="block"><span className="text-sm text-gray-200">عنوان الاستقبال (https)</span><input dir="ltr" className={inputClass} placeholder="https://example.com/omniflow-hook" value={hook.url} onChange={(e) => setHook({ ...hook, url: e.target.value })} /></label>
                <fieldset><legend className="text-sm text-gray-200 mb-1">الأحداث</legend>
                  <div className="flex flex-wrap gap-3">{data.events.map((ev) => (
                    <label key={ev} className="flex items-center gap-2 text-sm text-gray-100"><input type="checkbox" checked={hook.events.includes(ev)} onChange={(e) => setHook({ ...hook, events: e.target.checked ? [...hook.events, ev] : hook.events.filter((x) => x !== ev) })} />{EVENT_LABELS[ev] ?? ev}</label>))}</div></fieldset>
                <button type="submit" disabled={busy || !hook.url.trim() || hook.events.length === 0} className="px-4 py-2 rounded-lg bg-[#C9A84C] text-[#0A0F1C] font-semibold text-sm disabled:opacity-50">إضافة</button>
              </form>
              {data.webhooks.length === 0 ? <p className="text-sm text-gray-300 border border-dashed border-white/15 rounded-xl p-4">لا توجد webhooks. أضف عنوانًا لتصلك الأحداث فور حدوثها.</p> : (
                <ul className="space-y-3">{data.webhooks.map((w) => (
                  <li key={w.endpoint_id} className="rounded-xl border border-white/10 p-4 space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <code dir="ltr" className="text-sm text-white break-all">{w.url}</code>
                      <span className={`text-xs rounded-full px-2 py-0.5 border ${w.active ? "border-emerald-400/40 text-emerald-300" : "border-red-400/40 text-red-300"}`}>{w.active ? "مفعّل" : `متوقف${w.disabled_reason ? ` (${w.disabled_reason})` : ""}`}</span>
                    </div>
                    <p className="text-xs text-gray-300">{w.events.map((e) => EVENT_LABELS[e] ?? e).join(" · ")}{w.consecutive_failures ? ` · إخفاقات متتالية: ${w.consecutive_failures}` : ""}</p>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" disabled={busy} onClick={() => void run(async () => { const d = await testWebhook(w.endpoint_id); setNotice(`اختبار: ${deliveryLabel(d)}`); }, "تعذّر إرسال الاختبار")} className="px-3 py-1.5 text-xs rounded-lg border border-white/20 hover:bg-white/10 flex items-center gap-1.5"><Send className="w-3.5 h-3.5" aria-hidden />إرسال اختبار</button>
                      <button type="button" disabled={busy} onClick={() => void run(async () => setLog({ id: w.endpoint_id, rows: await fetchDeliveries(w.endpoint_id) }), "تعذّر تحميل السجل")} className="px-3 py-1.5 text-xs rounded-lg border border-white/20 hover:bg-white/10">سجل التسليم</button>
                      <button type="button" disabled={busy} onClick={() => void run(async () => { await updateWebhook(w.endpoint_id, { active: !w.active }); }, "تعذّر التحديث")} className="px-3 py-1.5 text-xs rounded-lg border border-white/20 hover:bg-white/10">{w.active ? "إيقاف" : "تفعيل"}</button>
                      <button type="button" disabled={busy} onClick={() => { if (window.confirm("إصدار سر جديد؟ السر القديم يتوقف فورًا.")) void run(async () => { const r = await rotateSecret(w.endpoint_id); setSecret({ label: "السر الجديد", value: r.secret }); }, "تعذّر التدوير"); }} className="px-3 py-1.5 text-xs rounded-lg border border-white/20 hover:bg-white/10">تدوير السر</button>
                      <button type="button" disabled={busy} onClick={() => { if (window.confirm("حذف الـ webhook وسجله؟")) void run(() => deleteWebhook(w.endpoint_id), "تعذّر الحذف"); }} className="px-3 py-1.5 text-xs rounded-lg border border-red-400/40 text-red-200 hover:bg-red-500/10 flex items-center gap-1.5"><Trash2 className="w-3.5 h-3.5" aria-hidden />حذف</button>
                    </div>
                    {log?.id === w.endpoint_id && (log.rows.length === 0 ? <p className="text-xs text-gray-300">لا توجد عمليات تسليم بعد.</p> : (
                      <table className="w-full text-xs"><caption className="sr-only">سجل التسليم</caption><thead><tr className="text-gray-300 text-start"><th scope="col" className="text-start py-1">الحدث</th><th scope="col" className="text-start">النتيجة</th><th scope="col" className="text-start">الوقت</th></tr></thead>
                        <tbody>{log.rows.map((d) => <tr key={d.delivery_id} className="border-t border-white/10"><td className="py-1">{EVENT_LABELS[d.event_type] ?? d.event_type}</td><td>{deliveryLabel(d)}</td><td>{fmt(d.created_at)}</td></tr>)}</tbody></table>))}
                  </li>))}</ul>
              )}
              {busy && <Loader2 className="w-4 h-4 animate-spin text-gray-300" aria-label="جارٍ التنفيذ" />}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
