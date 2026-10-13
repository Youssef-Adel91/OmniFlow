"use client";

import React, { useCallback, useEffect, useState } from "react";
import { MessageCircle } from "lucide-react";
import {
  DEFAULT_SOCIAL_COMMENTS, fetchPreferences, savePreferences, settingsErrorMessage, type CommentMode, type SocialCommentsPrefs,
} from "@/lib/api/settings";
import CommentActivityList from "./CommentActivityList";
import { Field, SaveBar, Toggle, inputClass } from "./common";
import { useSection } from "./useSection";

const MODES: { value: CommentMode; label: string; hint: string }[] = [
  { value: "public_reply", label: "رد علني تحت التعليق", hint: "يجيب المساعد أمام الجميع. لا يُنشر علنًا أي رد فيه سعر أو وعد بعائد؛ يُستبدل بدعوة للمراسلة على الخاص." },
  { value: "private_dm", label: "رسالة خاصة فقط", hint: "رسالة واحدة لصاحب التعليق خلال 7 أيام (قاعدة Meta)." },
  { value: "public_and_private", label: "رد علني قصير + رسالة خاصة بالتفاصيل", hint: "الأنسب للمبيعات: ترحيب علني ثابت ثم التفاصيل على الخاص." },
];

/** "a, b" ⇄ ["a","b"] for the keyword fields (pure; exported for tests). */
export const parseKeywords = (raw: string): string[] => Array.from(new Set(raw.split(/[,،\n]/).map((k) => k.trim()).filter(Boolean)));
export const joinKeywords = (list: string[]): string => list.join("، ");

/** Comment replies on Facebook/Instagram posts. Rendered ONLY when the platform flag is on (backend reports it); otherwise nothing is shown. */
export default function CommentRepliesCard({ onDirty = () => undefined }: { onDirty?: (dirty: boolean) => void }) {
  const [available, setAvailable] = useState(false);
  const [saved, setSaved] = useState<SocialCommentsPrefs | null>(null);

  useEffect(() => {
    let alive = true;
    fetchPreferences()
      .then((p) => { if (!alive) return; setAvailable(Boolean(p.features?.social_comment_replies)); setSaved({ ...DEFAULT_SOCIAL_COMMENTS, ...(p.preferences.social_comments ?? {}) }); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, []);

  const save = useCallback(async (v: SocialCommentsPrefs) => {
    const out = { ...DEFAULT_SOCIAL_COMMENTS, ...(await savePreferences("social_comments", v)) };
    setSaved(out);
    return out;
  }, []);
  const errorText = useCallback((e: unknown) => settingsErrorMessage(e, "تعذّر الحفظ (للمدير فقط)"), []);
  const sec = useSection<SocialCommentsPrefs>(saved, save, onDirty, errorText);

  if (!available || !sec.draft) return null;
  const v = sec.draft;
  const set = (patch: Partial<SocialCommentsPrefs>) => sec.setDraft({ ...v, ...patch });
  const off = !v.enabled;

  return (
    <section aria-label="الرد على التعليقات" className="rounded-xl border border-white/10 p-5 space-y-5" data-testid="comment-replies">
      <div>
        <h3 className="font-bold text-white flex items-center gap-2"><MessageCircle className="w-4 h-4 text-[#C9A84C]" aria-hidden />الرد على التعليقات (فيسبوك وإنستجرام)</h3>
        <p className="text-xs text-gray-300 mt-2 leading-relaxed">يردّ المساعد على التعليقات الرئيسية على منشوراتك. لا يردّ على تعليقات صفحتك نفسها ولا على الردود داخل الخيط، ولا يكرر الرد على التعليق نفسه.</p>
      </div>

      <Toggle checked={v.enabled} onChange={(enabled) => set({ enabled })} label="تفعيل الرد على التعليقات" />

      <fieldset disabled={off} className="space-y-2 disabled:opacity-60">
        <legend className="text-sm text-gray-200 mb-1">طريقة الرد</legend>
        {MODES.map((m) => (
          <label key={m.value} className="flex items-start gap-2 text-sm text-gray-100">
            <input type="radio" name="comment-mode" className="mt-1" checked={v.mode === m.value} onChange={() => set({ mode: m.value })} />
            <span>{m.label}<span className="block text-xs text-gray-300">{m.hint}</span></span>
          </label>
        ))}
      </fieldset>

      <fieldset disabled={off} className="space-y-4 disabled:opacity-60">
        {v.mode !== "private_dm" && (
          <Field label="نص الرد العلني" htmlFor="comment-template" hint="يُستخدم في وضع «رد علني + خاص»، وبديلًا عن أي رد علني يحتوي سعرًا. {name} تُستبدل بالاسم الأول للمعلّق.">
            <textarea id="comment-template" rows={2} maxLength={300} value={v.public_template} onChange={(e) => set({ public_template: e.target.value })} className={inputClass} />
          </Field>
        )}
        <Toggle checked={v.allow_price_in_private} onChange={(allow_price_in_private) => set({ allow_price_in_private })}
          label="السماح بإرسال السعر في الرسالة الخاصة" hint="عند الإيقاف لا يُرسل المساعد أي سعر من خلال التعليقات؛ يدعو العميل للمراسلة فقط." />
        <Toggle checked={v.auto_hide_abusive} onChange={(auto_hide_abusive) => set({ auto_hide_abusive })}
          label="إخفاء التعليقات المسيئة والسبام تلقائيًا" hint="الشتائم واتهامات النصب والروابط الدعائية. التعليق المخفي يبقى ظاهرًا لصاحبه فقط، ويمكنك إظهاره من فيسبوك." />
        <Field label="كلمات تُخفى دائمًا (اختياري)" htmlFor="comment-hide-kw" hint="افصل بينها بفاصلة. تعمل مع خيار الإخفاء التلقائي.">
          <input id="comment-hide-kw" value={joinKeywords(v.hide_keywords)} onChange={(e) => set({ hide_keywords: parseKeywords(e.target.value) })} className={inputClass} />
        </Field>
        <Field label="كلمات يتجاهلها المساعد (اختياري)" htmlFor="comment-ignore-kw" hint="مثل أسماء المنافسين. التعليق الذي يحتوي إحداها لا يُرد عليه ولا يُخفى. التعليقات المكوّنة من إيموجي فقط تُتجاهل دائمًا.">
          <input id="comment-ignore-kw" value={joinKeywords(v.ignore_keywords)} onChange={(e) => set({ ignore_keywords: parseKeywords(e.target.value) })} className={inputClass} />
        </Field>
        <div className="grid sm:grid-cols-2 gap-4">
          <Field label="أقصى عدد ردود في الساعة" htmlFor="comment-max-hour" hint="حماية من الازدحام وتكلفة الذكاء الاصطناعي.">
            <input id="comment-max-hour" type="number" min={1} max={1000} value={v.max_per_hour} onChange={(e) => set({ max_per_hour: Math.min(1000, Math.max(1, Number(e.target.value) || 1)) })} className={inputClass} />
          </Field>
          <div className="pt-6">
            <Toggle checked={v.only_business_hours} onChange={(only_business_hours) => set({ only_business_hours })} label="الرد داخل ساعات العمل فقط" hint="تُضبط الساعات من قسم «ساعات العمل»." />
          </div>
        </div>
      </fieldset>

      <SaveBar dirty={sec.dirty} saving={sec.saving} error={sec.error} savedAt={sec.savedAt} onSave={() => void sec.submit()} onReset={sec.reset} />
      <CommentActivityList />
    </section>
  );
}
