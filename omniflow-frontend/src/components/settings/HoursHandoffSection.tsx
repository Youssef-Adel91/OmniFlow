"use client";

/**
 * Settings → ساعات العمل والتحويل البشري. Two independently saved cards:
 *  • business hours (timezone, per-day shifts, out-of-hours message with {next_open})
 *  • human handoff (explicit-request keywords, low-confidence escalation, round-robin assignment, messages, SLA)
 * What they change at runtime is stated under each card (the same wording as the backend docs).
 */

import React from "react";
import { Clock, Headset, Plus, Trash2 } from "lucide-react";
import {
  DAY_KEYS, DAY_LABELS, TIMEZONES, parseKeywords, savePreferences, settingsErrorMessage, summarizeHours, validateHours,
  type BusinessHours, type DayKey, type HandoffPrefs,
} from "@/lib/api/settings";
import { Field, SaveBar, SectionHeader, SectionSkeleton, Toggle, cardClass, inputClass } from "./common";
import { useSection } from "./useSection";

export default function HoursHandoffSection({ hours, handoff, onSaved, onDirtyHours, onDirtyHandoff }: {
  hours: BusinessHours | null; handoff: HandoffPrefs | null;
  onSaved: (s: { business_hours?: BusinessHours; handoff?: HandoffPrefs }) => void;
  onDirtyHours: (d: boolean) => void; onDirtyHandoff: (d: boolean) => void;
}) {
  const h = useSection<BusinessHours>(hours, async (v) => { const out = await savePreferences("business_hours", v); onSaved({ business_hours: out }); return out; }, onDirtyHours, (e) => settingsErrorMessage(e, "تعذّر حفظ ساعات العمل"));
  const f = useSection<HandoffPrefs>(handoff, async (v) => { const out = await savePreferences("handoff", v); onSaved({ handoff: out }); return out; }, onDirtyHandoff, (e) => settingsErrorMessage(e, "تعذّر حفظ قواعد التحويل"));

  const hd = h.draft, fd = f.draft;
  const hoursErrors = hd ? validateHours(hd) : [];
  const setDay = (day: DayKey, rows: [string, string][]) => hd && h.setDraft({ ...hd, days: { ...hd.days, [day]: rows } });

  return (
    <div className="space-y-6">
      <div className={cardClass}>
        <SectionHeader icon={Clock} title="ساعات العمل" description="متى يتوفر فريقك للرد. تُستخدم لتحديد الرسالة التي يستلمها العميل عند تحويله لموظف خارج أوقات الدوام." />
        {!hd ? <SectionSkeleton /> : (
          <div className="space-y-5">
            <Toggle checked={hd.enabled} onChange={(v) => h.setDraft({ ...hd, enabled: v })} label="تفعيل ساعات العمل" hint="عند الإيقاف يعتبر النظام أنكم متاحون دائمًا." />
            <fieldset disabled={!hd.enabled} className="space-y-4 disabled:opacity-60">
              <Field label="المنطقة الزمنية" htmlFor="hours-tz">
                <select id="hours-tz" value={hd.timezone} onChange={(e) => h.setDraft({ ...hd, timezone: e.target.value })} className={inputClass}>
                  {TIMEZONES.some((t) => t.value === hd.timezone) ? null : <option value={hd.timezone}>{hd.timezone}</option>}
                  {TIMEZONES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </Field>
              <div className="space-y-2" role="group" aria-label="أيام وفترات الدوام">
                {DAY_KEYS.map((day) => {
                  const rows = hd.days[day] ?? [];
                  return (
                    <div key={day} className="flex flex-wrap items-center gap-3 rounded-lg border border-white/10 px-3 py-2" data-testid={`day-${day}`}>
                      <span className="w-20 text-sm text-gray-100">{DAY_LABELS[day]}</span>
                      {rows.length === 0 && <span className="text-sm text-gray-300">مغلق</span>}
                      {rows.map(([s, e], i) => (
                        <span key={i} className="flex items-center gap-1.5">
                          <input type="time" aria-label={`${DAY_LABELS[day]} من`} value={s} dir="ltr" className={`${inputClass} w-28`} onChange={(ev) => setDay(day, rows.map((r, j) => (j === i ? [ev.target.value, r[1]] : r)) as [string, string][])} />
                          <span className="text-gray-300">–</span>
                          <input type="time" aria-label={`${DAY_LABELS[day]} إلى`} value={e} dir="ltr" className={`${inputClass} w-28`} onChange={(ev) => setDay(day, rows.map((r, j) => (j === i ? [r[0], ev.target.value] : r)) as [string, string][])} />
                          <button type="button" aria-label={`حذف فترة ${DAY_LABELS[day]}`} onClick={() => setDay(day, rows.filter((_, j) => j !== i))} className="p-1.5 rounded hover:bg-white/10 text-gray-200"><Trash2 className="w-4 h-4" aria-hidden /></button>
                        </span>
                      ))}
                      {rows.length < 4 && <button type="button" onClick={() => setDay(day, [...rows, rows.length ? ["16:00", "20:00"] : ["09:00", "17:00"]])} className="ms-auto text-xs text-[#C9A84C] flex items-center gap-1 hover:underline"><Plus className="w-3.5 h-3.5" aria-hidden />فترة</button>}
                    </div>
                  );
                })}
              </div>
              <p className="text-xs text-gray-300" data-testid="hours-summary">{summarizeHours(hd)}</p>
              {hoursErrors.length > 0 && <ul role="alert" className="list-disc ps-5 text-sm text-red-300">{hoursErrors.map((e) => <li key={e}>{e}</li>)}</ul>}
              <Field label="رسالة خارج أوقات الدوام" htmlFor="hours-msg" hint={<>تُرسل للعميل عند تحويله لموظف خارج الدوام. استخدم <code dir="ltr">{"{next_open}"}</code> لعبارة موعد الفتح القادم (مثال: «غدًا الساعة 09:00»).</>}>
                <textarea id="hours-msg" rows={3} maxLength={1000} value={hd.out_of_hours_message} onChange={(e) => h.setDraft({ ...hd, out_of_hours_message: e.target.value })} className={inputClass} />
              </Field>
            </fieldset>
            <SaveBar dirty={h.dirty} saving={h.saving} error={h.error ?? (h.dirty && hoursErrors.length ? "صحّح الأخطاء أعلاه قبل الحفظ" : null)} savedAt={h.savedAt} onSave={() => (hoursErrors.length ? undefined : void h.submit())} onReset={h.reset} />
          </div>
        )}
      </div>

      <div className={cardClass}>
        <SectionHeader icon={Headset} title="التحويل إلى موظف" description="متى يتوقف المساعد ويُحوَّل العميل إلى فريقك، ومن يستلم المحادثة، وما الذي يُقال للعميل." />
        {!fd ? <SectionSkeleton /> : (
          <div className="space-y-5">
            <Toggle checked={fd.auto_escalate} onChange={(v) => f.setDraft({ ...fd, auto_escalate: v })} label="التحويل التلقائي إلى موظف"
              hint="عند الإيقاف يبقى المساعد يتولى كل المحادثات ولا يُحوَّل أحد تلقائيًا (السلوك القديم)." />
            <fieldset disabled={!fd.auto_escalate} className="space-y-5 disabled:opacity-60">
              <Field label="عبارات طلب موظف (كلمة أو عبارة في كل سطر)" htmlFor="handoff-kw" hint="إذا تضمنت رسالة العميل إحدى هذه العبارات كاملةً (كلمات كاملة، لا جزء من كلمة) يُحوَّل لموظف فورًا. اترك القائمة ضيقة وواضحة حتى لا تُحوَّل أسئلة عادية.">
                <textarea id="handoff-kw" rows={5} value={fd.keywords.join("\n")} onChange={(e) => f.setDraft({ ...fd, keywords: parseKeywords(e.target.value) })} className={inputClass} />
                <span className="text-xs text-gray-300">{fd.keywords.length} / 30</span>
              </Field>
              <Toggle checked={fd.on_low_confidence} onChange={(v) => f.setDraft({ ...fd, on_low_confidence: v })} label="تحويل عند ضعف ثقة المساعد" hint="عندما لا يفهم المساعد الطلب بثقة كافية يُحوَّل لموظف بدل أن يخمّن." />
              <Toggle checked={fd.round_robin} onChange={(v) => f.setDraft({ ...fd, round_robin: v })} label="توزيع المحادثات على الموظفين (Round-robin)" hint="تُسنَد المحادثة المحوّلة لأقل الموظفين النشطين انشغالًا؛ وإن لم يوجد موظفون فللمدير." />
              <Field label="رسالة التحويل (أثناء الدوام)" htmlFor="handoff-msg">
                <textarea id="handoff-msg" rows={2} maxLength={1000} value={fd.handoff_message} onChange={(e) => f.setDraft({ ...fd, handoff_message: e.target.value })} className={inputClass} />
              </Field>
            </fieldset>
            <Field label="مهلة الرد المستهدفة (SLA) بالدقائق" htmlFor="handoff-sla" hint="تُستخدم في لوحة التحكم لعدّ المحادثات التي تجاوزت زمن الرد المستهدف.">
              <input id="handoff-sla" type="number" min={1} max={1440} value={fd.sla_minutes} onChange={(e) => f.setDraft({ ...fd, sla_minutes: Math.min(1440, Math.max(1, Number(e.target.value) || 1)) })} className={`${inputClass} max-w-[10rem]`} dir="ltr" />
            </Field>
            <SaveBar dirty={f.dirty} saving={f.saving} error={f.error} savedAt={f.savedAt} onSave={() => void f.submit()} onReset={f.reset} />
          </div>
        )}
      </div>
    </div>
  );
}
