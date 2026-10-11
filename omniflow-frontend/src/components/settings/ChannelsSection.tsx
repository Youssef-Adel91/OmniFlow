"use client";

/**
 * Settings → القنوات والتكاملات: WhatsApp + Facebook/Messenger/Instagram status, last inbound message,
 * live health check against Meta, connect / reconnect / disconnect, and the number shown on the contact card.
 * Credentials themselves (WhatsApp token/phone id/WABA id) are only ever written by the onboarding flow.
 */

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Link2, Loader2, MessageSquare, PlugZap, RefreshCw, Share2 as Facebook, Unplug } from "lucide-react";
import { startFacebookConnect, selectFacebookPage, type FacebookPageOption } from "@/lib/api/facebookOauth";
import {
  checkChannel, disconnectFacebook, fetchChannels, settingsErrorMessage, updateSettings,
  type ChannelCheck, type ChannelsStatus, type TenantSettings,
} from "@/lib/api/settings";
import { WhatsappEmbeddedSignup } from "./WhatsappEmbeddedSignup";
import { Field, LoadError, SaveBar, SectionHeader, SectionSkeleton, cardClass, inputClass } from "./common";
import { useSection } from "./useSection";

const fmt = (iso: string | null) => (iso ? new Intl.DateTimeFormat("ar-SA-u-ca-gregory", { timeZone: "Asia/Riyadh", dateStyle: "medium", timeStyle: "short" }).format(new Date(iso)) : "لم تصل أي رسالة بعد");

function StatusPill({ ok, okText, badText }: { ok: boolean; okText: string; badText: string }) {
  return ok
    ? <span className="inline-flex items-center gap-1.5 text-xs rounded-full bg-emerald-500/15 text-emerald-200 border border-emerald-500/40 px-2.5 py-1"><CheckCircle2 className="w-3.5 h-3.5" aria-hidden />{okText}</span>
    : <span className="inline-flex items-center gap-1.5 text-xs rounded-full bg-red-500/15 text-red-200 border border-red-500/40 px-2.5 py-1"><AlertTriangle className="w-3.5 h-3.5" aria-hidden />{badText}</span>;
}

export default function ChannelsSection({ locale, settings, onSettings, onDirty }: {
  locale: string; settings: TenantSettings | null; onSettings: (s: TenantSettings) => void; onDirty: (d: boolean) => void;
}) {
  const [channels, setChannels] = useState<ChannelsStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, ChannelCheck | "loading">>({});
  const [busy, setBusy] = useState<null | "connect" | "disconnect">(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pages, setPages] = useState<{ connectionId: string; options: FacebookPageOption[] } | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try { setChannels(await fetchChannels()); } catch (e) { setLoadError(settingsErrorMessage(e, "تعذّر تحميل حالة القنوات")); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const display = useSection<string>(
    settings ? settings.whatsapp_display_phone_number ?? "" : null,
    async (v) => { const s = await updateSettings({ whatsapp_display_phone_number: v.trim() }); onSettings(s); return s.whatsapp_display_phone_number ?? ""; },
    onDirty, (e) => settingsErrorMessage(e, "تعذّر حفظ الرقم"),
  );

  const runCheck = async (ch: "whatsapp" | "facebook") => {
    setChecks((c) => ({ ...c, [ch]: "loading" }));
    try { const res = await checkChannel(ch); setChecks((c) => ({ ...c, [ch]: res })); }
    catch (e) { setChecks((c) => ({ ...c, [ch]: { ok: false, detail: settingsErrorMessage(e, "تعذّر فحص الاتصال"), checked_at: new Date().toISOString() } })); }
  };

  const connectFacebook = async () => {
    setBusy("connect"); setNotice(null);
    try {
      const r = await startFacebookConnect();
      if (r.status === "success") { setNotice(`رُبطت الصفحة «${r.page_name}»${r.instagram_username ? ` وحساب إنستجرام @${r.instagram_username}` : ""}.`); await load(); }
      else if (r.status === "needs_selection") setPages({ connectionId: r.connection_id, options: r.pages });
      else if (r.status === "error") setNotice(r.message);
    } catch (e) { setNotice(settingsErrorMessage(e, "تعذّر بدء الربط")); }
    finally { setBusy(null); }
  };

  const pickPage = async (id: string) => {
    if (!pages) return;
    setBusy("connect");
    try { const r = await selectFacebookPage(pages.connectionId, id); setPages(null); setNotice(`رُبطت الصفحة «${r.page_name}».`); await load(); }
    catch (e) { setNotice(settingsErrorMessage(e, "تعذّر إتمام الربط")); }
    finally { setBusy(null); }
  };

  const disconnect = async () => {
    if (!window.confirm("فصل صفحة فيسبوك (ماسنجر + إنستجرام) عن هذا الحساب؟ ستتوقف الرسائل الواردة منها عن الوصول إلى صندوق الوارد.")) return;
    setBusy("disconnect"); setNotice(null);
    try { const r = await disconnectFacebook(); setNotice(r.note); await load(); } catch (e) { setNotice(settingsErrorMessage(e, "تعذّر الفصل (للمدير فقط)")); }
    finally { setBusy(null); }
  };

  const wa = channels?.whatsapp, fb = channels?.facebook;
  const CheckResult = ({ ch }: { ch: "whatsapp" | "facebook" }) => {
    const c = checks[ch];
    if (!c) return null;
    if (c === "loading") return <span className="text-xs text-gray-200 flex items-center gap-1.5"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden />جارٍ الفحص…</span>;
    return <span role="status" className={`text-xs flex items-center gap-1.5 ${c.ok ? "text-emerald-300" : "text-red-300"}`}>{c.ok ? <CheckCircle2 className="w-3.5 h-3.5" aria-hidden /> : <AlertTriangle className="w-3.5 h-3.5" aria-hidden />}{c.detail}{c.info?.verified_name ? ` — ${c.info.verified_name}` : c.info?.name ? ` — ${c.info.name}` : ""}</span>;
  };

  return (
    <div className="space-y-6">
      <div className={cardClass}>
        <SectionHeader icon={PlugZap} title="القنوات والتكاملات" description="حالة اتصال كل قناة، وآخر رسالة وصلت منها، وفحص مباشر للاتصال مع Meta." />
        {loadError && <LoadError message={loadError} onRetry={() => void load()} />}
        {notice && <p role="status" className="text-sm text-emerald-200 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-3 py-2">{notice}</p>}
        {!channels && !loadError ? <SectionSkeleton /> : channels && wa && fb ? (
          <div className="space-y-6">
            {/* WhatsApp */}
            <section aria-label="واتساب" className="rounded-xl border border-white/10 p-5 space-y-3" data-testid="channel-whatsapp">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="font-bold text-white flex items-center gap-2"><MessageSquare className="w-4 h-4 text-[#C9A84C]" aria-hidden />واتساب</h3>
                <StatusPill ok={wa.connected} okText="مربوط" badText="غير مربوط" />
              </div>
              <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-gray-300">Phone Number ID</dt><dd dir="ltr" className="text-start">{wa.phone_number_id ?? "—"}</dd>
                <dt className="text-gray-300">WABA ID</dt><dd dir="ltr" className="text-start">{wa.waba_id ?? "—"}</dd>
                <dt className="text-gray-300">رمز الوصول</dt><dd dir="ltr" className="text-start">{wa.token ?? "—"} <span className="text-gray-300" dir="rtl">(مُقنّع)</span></dd>
                <dt className="text-gray-300">آخر رسالة واردة</dt><dd>{fmt(wa.last_inbound_at)}</dd>
              </dl>
              {wa.issues.length > 0 && <ul className="list-disc ps-5 text-xs text-amber-200 space-y-0.5">{wa.issues.map((i) => <li key={i}>{i}</li>)}</ul>}
              <div className="flex flex-wrap items-center gap-3 pt-1">
                <button onClick={() => void runCheck("whatsapp")} disabled={!wa.connected || checks.whatsapp === "loading"} className="px-3 py-1.5 text-xs rounded-lg border border-white/20 hover:bg-white/10 disabled:opacity-50 flex items-center gap-1.5"><RefreshCw className="w-3.5 h-3.5" aria-hidden />فحص الاتصال</button>
                <Link href={`/${locale}/onboarding`} className="px-3 py-1.5 text-xs rounded-lg border border-[#C9A84C]/40 text-[#E4D5A5] hover:bg-[#C9A84C]/10 flex items-center gap-1.5"><Link2 className="w-3.5 h-3.5" aria-hidden />{wa.connected ? "إعادة الربط / تحديث الرمز" : "ربط واتساب"}</Link>
                <WhatsappEmbeddedSignup onConnected={(m) => { setNotice(m); void load(); }} />
                <CheckResult ch="whatsapp" />
              </div>
              <p className="text-xs text-gray-300">رمز الوصول ومعرّف الرقم لا يُعدَّلان من هنا لأسباب أمنية؛ تُحدَّث من صفحة الربط.</p>
            </section>

            {/* Facebook / Messenger / Instagram */}
            <section aria-label="فيسبوك وإنستجرام" className="rounded-xl border border-white/10 p-5 space-y-3" data-testid="channel-facebook">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="font-bold text-white flex items-center gap-2"><Facebook className="w-4 h-4 text-[#C9A84C]" aria-hidden />فيسبوك · ماسنجر · إنستجرام</h3>
                <div className="flex gap-2"><StatusPill ok={fb.messenger} okText="ماسنجر" badText="ماسنجر" /><StatusPill ok={fb.instagram} okText="إنستجرام" badText="إنستجرام" /></div>
              </div>
              <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-gray-300">الصفحة (Page ID)</dt><dd dir="ltr" className="text-start">{fb.page_id ?? "—"}</dd>
                <dt className="text-gray-300">حساب إنستجرام</dt><dd dir="ltr" className="text-start">{fb.instagram_account_id ?? "—"}</dd>
                <dt className="text-gray-300">آخر رسالة واردة</dt><dd>{fmt(fb.last_inbound_at)}</dd>
              </dl>
              {fb.issues.length > 0 && <ul className="list-disc ps-5 text-xs text-amber-200 space-y-0.5">{fb.issues.map((i) => <li key={i}>{i}</li>)}</ul>}
              <div className="flex flex-wrap items-center gap-3 pt-1">
                <button onClick={() => void connectFacebook()} disabled={busy !== null} className="px-3 py-1.5 text-xs rounded-lg bg-[#C9A84C] text-[#0A0F1C] font-semibold hover:opacity-90 disabled:opacity-50 flex items-center gap-1.5">
                  {busy === "connect" ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden /> : <Facebook className="w-3.5 h-3.5" aria-hidden />}{fb.connected ? "إعادة الربط" : "ربط عبر فيسبوك"}
                </button>
                <button onClick={() => void runCheck("facebook")} disabled={!fb.connected || checks.facebook === "loading"} className="px-3 py-1.5 text-xs rounded-lg border border-white/20 hover:bg-white/10 disabled:opacity-50 flex items-center gap-1.5"><RefreshCw className="w-3.5 h-3.5" aria-hidden />فحص الاتصال</button>
                <button onClick={() => void disconnect()} disabled={!fb.connected || busy !== null} className="px-3 py-1.5 text-xs rounded-lg border border-red-500/40 text-red-300 hover:bg-red-500/10 disabled:opacity-50 flex items-center gap-1.5"><Unplug className="w-3.5 h-3.5" aria-hidden />فصل</button>
                <CheckResult ch="facebook" />
              </div>
              {pages && (
                <div role="dialog" aria-label="اختيار الصفحة" className="rounded-lg border border-white/15 p-3 space-y-2">
                  <p className="text-sm text-gray-100">وُجدت أكثر من صفحة؛ اختر الصفحة التي تريد ربطها:</p>
                  {pages.options.map((p) => (
                    <button key={p.page_id} onClick={() => void pickPage(p.page_id)} disabled={busy !== null} className="block w-full text-start rounded-lg border border-white/15 px-3 py-2 text-sm hover:bg-white/10 disabled:opacity-50">
                      {p.page_name}{p.instagram_username ? ` · @${p.instagram_username}` : " · بلا حساب إنستجرام"}
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>
        ) : null}
      </div>

      {/* Contact-card number */}
      <div className={cardClass}>
        <SectionHeader icon={MessageSquare} title="رقم التواصل الظاهر للعملاء" description="الرقم الذي يظهر في بطاقة التعارف (VCard) المرسلة تلقائيًا لكل عميل جديد على واتساب. يختلف عن Phone Number ID التقني." />
        {!settings ? <SectionSkeleton /> : (
          <>
            <Field label="الرقم" htmlFor="display-phone" hint="مثال: 0501234567 أو +966501234567. إن تركته فارغًا تُرسل البطاقة بلا رقم.">
              <input id="display-phone" value={display.draft ?? ""} onChange={(e) => display.setDraft(e.target.value)} dir="ltr" className={inputClass} />
            </Field>
            <SaveBar dirty={display.dirty} saving={display.saving} error={display.error} savedAt={display.savedAt} onSave={() => void display.submit()} onReset={display.reset} />
          </>
        )}
      </div>
    </div>
  );
}
