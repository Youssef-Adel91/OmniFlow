"use client";

import React, { useEffect, useState } from "react";
import { Loader2, Handshake } from "lucide-react";
import { fetchPreferences, savePreferences, settingsErrorMessage, type SalesPrefs } from "@/lib/api/settings";

const DAYS: [string, string][] = [["sun", "الأحد"], ["mon", "الإثنين"], ["tue", "الثلاثاء"], ["wed", "الأربعاء"], ["thu", "الخميس"], ["fri", "الجمعة"], ["sat", "السبت"]];
const input = "bg-[#0A0F1C] border border-white/15 rounded-lg py-1.5 px-2 text-sm text-white focus:outline-none focus:border-[#C9A84C]/60";

function windowOf(p: SalesPrefs): { from: string; to: string; days: string[] } {
  const days = Object.keys(p.viewing_hours?.days ?? {});
  const first = days.length ? p.viewing_hours.days[days[0]]?.[0] : undefined;
  return { from: first?.[0] ?? "10:00", to: first?.[1] ?? "18:00", days };
}

/** Sales-closing assistant settings (admin). Rendered ONLY when the platform flag FEATURE_SALES_CLOSING is on; otherwise nothing is shown. */
export default function SalesCard() {
  const [available, setAvailable] = useState(false);
  const [value, setValue] = useState<SalesPrefs | null>(null);
  const [win, setWin] = useState({ from: "10:00", to: "18:00", days: ["sun", "mon", "tue", "wed", "thu"] as string[] });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    fetchPreferences().then((p) => {
      if (!alive) return;
      setAvailable(Boolean(p.features?.sales_closing));
      if (p.preferences.sales) { setValue(p.preferences.sales); setWin(windowOf(p.preferences.sales)); }
    }).catch(() => undefined);
    return () => { alive = false; };
  }, []);

  if (!available || !value) return null;
  const set = <K extends keyof SalesPrefs>(k: K, v: SalesPrefs[K]) => setValue({ ...value, [k]: v });
  const num = (k: keyof SalesPrefs, label: string, min: number, max: number) => (
    <label className="flex items-center justify-between gap-3 text-sm text-gray-100">{label}
      <input type="number" className={`${input} w-24`} min={min} max={max} value={value[k] as number} onChange={(e) => set(k, Number(e.target.value) as never)} /></label>
  );
  const save = async () => {
    setBusy(true); setMsg(null);
    const days: Record<string, string[][]> = {};
    win.days.forEach((d) => { days[d] = [[win.from, win.to]]; });
    try {
      const saved = await savePreferences("sales", { ...value, viewing_hours: { timezone: value.viewing_hours?.timezone ?? "Asia/Riyadh", days } });
      if (saved) setValue(saved);
      setMsg({ ok: true, text: "تم الحفظ." });
    } catch (e) { setMsg({ ok: false, text: settingsErrorMessage(e, "تعذّر الحفظ (للمدير فقط)") }); }
    setBusy(false);
  };
  const delays = value.followup_delays_hours;
  return (
    <section aria-label="مساعد إغلاق الصفقات" className="rounded-xl border border-white/10 p-5 space-y-4" data-testid="sales-settings">
      <h3 className="font-bold text-white flex items-center gap-2"><Handshake className="w-4 h-4 text-[#C9A84C]" aria-hidden />مساعد إغلاق الصفقات</h3>
      <p className="text-xs text-gray-300">يسأل العميل أسئلة تأهيل قليلة، يقترح عقارات مطابقة من مخزونك المعتمد فقط، يحجز المعاينات، ويحوّل للموظف عند التفاوض على السعر أو الشكوى. لا يمنح خصومات ولا يغلق صفقة بنفسه.</p>
      <label className="flex items-center gap-3 text-sm text-gray-100"><input type="checkbox" role="switch" checked={value.enabled} onChange={(e) => set("enabled", e.target.checked)} />تفعيل المساعد</label>
      <fieldset disabled={!value.enabled} className="space-y-3 disabled:opacity-60">
        <label className="flex items-center gap-3 text-sm text-gray-100"><input type="checkbox" checked={value.ask_questions} onChange={(e) => set("ask_questions", e.target.checked)} />طرح أسئلة التأهيل (سؤال واحد في كل مرة)</label>
        {value.ask_questions && num("max_questions", "أقصى عدد أسئلة لكل محادثة", 0, 8)}
        <label className="flex items-center gap-3 text-sm text-gray-100"><input type="checkbox" checked={value.booking_enabled} onChange={(e) => set("booking_enabled", e.target.checked)} />حجز المعاينات داخل المحادثة</label>
        {value.booking_enabled && (
          <div className="space-y-3 ps-6">
            <div role="group" aria-label="أيام المعاينة" className="flex flex-wrap gap-3">
              {DAYS.map(([k, l]) => (
                <label key={k} className="flex items-center gap-1 text-sm text-gray-100"><input type="checkbox" checked={win.days.includes(k)} onChange={(e) => setWin({ ...win, days: e.target.checked ? [...win.days, k] : win.days.filter((d) => d !== k) })} />{l}</label>
              ))}
            </div>
            <div className="flex items-center gap-2 text-sm text-gray-100">من <input aria-label="بداية الدوام" type="time" className={input} value={win.from} onChange={(e) => setWin({ ...win, from: e.target.value })} /> إلى <input aria-label="نهاية الدوام" type="time" className={input} value={win.to} onChange={(e) => setWin({ ...win, to: e.target.value })} /></div>
            {num("slot_minutes", "مدة المعاينة (دقيقة)", 15, 240)}
            {num("min_notice_hours", "أقل مهلة قبل الموعد (ساعة)", 0, 72)}
            {num("days_ahead", "عدد الأيام المعروضة", 1, 14)}
            {num("max_parallel_viewings", "معاينات متزامنة (عدد الموظفين)", 1, 20)}
            {num("reminder_hours_before", "تذكير العميل قبل الموعد بـ (ساعة)", 1, 48)}
            <label className="block text-sm text-gray-100">موقع اللقاء (يُرسل مع تأكيد الموعد)
              <input className={`${input} mt-1 w-full`} maxLength={300} value={value.viewing_location_note} onChange={(e) => set("viewing_location_note", e.target.value)} /></label>
          </div>
        )}
        <label className="flex items-center gap-3 text-sm text-gray-100"><input type="checkbox" checked={value.followup_enabled} onChange={(e) => set("followup_enabled", e.target.checked)} />متابعة تلقائية للعميل الذي سكت (حتى 3 رسائل، لا تُرسل لمن طلب الإيقاف)</label>
        {value.followup_enabled && (
          <div className="space-y-2 ps-6">
            <div className="flex flex-wrap items-center gap-2 text-sm text-gray-100">بعد
              {delays.map((h, i) => <input key={i} aria-label={`مهلة المتابعة ${i + 1} بالساعات`} type="number" min={1} max={720} className={`${input} w-20`} value={h} onChange={(e) => set("followup_delays_hours", delays.map((x, j) => (j === i ? Number(e.target.value) : x)))} />)}
              ساعة من آخر ردّ
            </div>
            <label className="block text-sm text-gray-100">اسم قالب واتساب المعتمد للمتابعة بعد انتهاء نافذة 24 ساعة (اختياري — بدونه تُتخطى المتابعة خارج النافذة)
              <input className={`${input} mt-1 w-full`} maxLength={100} value={value.whatsapp_followup_template} onChange={(e) => set("whatsapp_followup_template", e.target.value)} /></label>
          </div>
        )}
      </fieldset>
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => void save()} disabled={busy} className="rounded-lg bg-[#C9A84C] px-4 py-2 text-sm font-semibold text-[#0A0F1C] disabled:opacity-60">حفظ</button>
        {busy && <Loader2 className="w-4 h-4 animate-spin text-gray-300" aria-label="جارٍ الحفظ" />}
        {msg && <p role={msg.ok ? "status" : "alert"} className={`text-xs ${msg.ok ? "text-emerald-300" : "text-red-300"}`}>{msg.text}</p>}
      </div>
    </section>
  );
}
