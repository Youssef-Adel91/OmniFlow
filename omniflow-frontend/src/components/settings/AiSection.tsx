"use client";

/**
 * Settings → الذكاء الاصطناعي: persona + custom instructions (existing), tone / dialect / language / constraints /
 * welcome message / quick replies (new), and a live "try the bot" panel that runs the tenant's real persona
 * (no customer is contacted, nothing is saved).
 */

import React, { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Bot, BookOpen, Loader2, Send } from "lucide-react";
import {
  DIALECT_OPTIONS, LANGUAGE_OPTIONS, TONE_OPTIONS, savePreferences, settingsErrorMessage, testBot, updateAiPersonality,
  type AiBehavior, type TenantSettings,
} from "@/lib/api/settings";
import { Field, SaveBar, SectionHeader, SectionSkeleton, Toggle, cardClass, inputClass } from "./common";
import { useSection } from "./useSection";
import SalesCard from "./SalesCard";

interface PersonaDraft { prompt: string; instructions: string }

export default function AiSection({ locale, settings, behavior, onSettings, onBehavior, onDirtyPersona, onDirtyBehavior }: {
  locale: string; settings: TenantSettings | null; behavior: AiBehavior | null;
  onSettings: (s: TenantSettings) => void; onBehavior: (b: AiBehavior) => void;
  onDirtyPersona: (d: boolean) => void; onDirtyBehavior: (d: boolean) => void;
}) {
  // memoised: useSection resets its draft whenever the saved value's identity changes
  const savedPersona = useMemo<PersonaDraft | null>(
    () => (settings ? { prompt: settings.ai_system_prompt ?? "", instructions: settings.custom_ai_instructions ?? "" } : null),
    [settings],
  );
  const persona = useSection<PersonaDraft>(
    savedPersona,
    async (v) => {
      if (v.prompt.trim().length < 10) throw new Error("يجب أن يكون نص الشخصية 10 أحرف على الأقل.");
      const s = await updateAiPersonality(v.prompt.trim(), v.instructions.trim());
      onSettings(s);
      return { prompt: s.ai_system_prompt ?? "", instructions: s.custom_ai_instructions ?? "" };
    }, onDirtyPersona, (e) => settingsErrorMessage(e, "تعذّر حفظ الشخصية"));

  const beh = useSection<AiBehavior>(behavior, async (v) => { const out = await savePreferences("ai_behavior", v); onBehavior(out); return out; }, onDirtyBehavior, (e) => settingsErrorMessage(e, "تعذّر حفظ سلوك المساعد"));

  const [q, setQ] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<{ reply: string; model: string | null; latency_ms: number; note: string } | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const ask = async () => {
    if (!q.trim() || asking) return;
    setAsking(true); setAskError(null); setAnswer(null);
    try { setAnswer(await testBot(q.trim())); } catch (e) { setAskError(settingsErrorMessage(e, "تعذّرت التجربة")); } finally { setAsking(false); }
  };

  const pd = persona.draft, bd = beh.draft;
  const c = bd?.constraints;
  const setC = (k: keyof NonNullable<typeof c>, v: boolean) => bd && c && beh.setDraft({ ...bd, constraints: { ...c, [k]: v } });

  return (
    <div className="space-y-6">
      <form className={cardClass} onSubmit={(e) => { e.preventDefault(); void persona.submit(); }}>
        <SectionHeader icon={Bot} title="شخصية المساعد" description="نص الشخصية والتعليمات الخاصة يُضافان لبداية كل محادثة جديدة." />
        <Link href={`/${locale}/knowledge`} className="flex items-center justify-between gap-3 bg-[#C9A84C]/10 border border-[#C9A84C]/30 rounded-xl px-4 py-3 hover:bg-[#C9A84C]/15 transition-colors">
          <span className="flex items-start gap-2.5 text-sm text-[#E4D5A5] leading-relaxed"><BookOpen className="w-4 h-4 shrink-0 mt-0.5 text-[#C9A84C]" aria-hidden /><span>هنا تحدد <strong>أسلوب</strong> المساعد. أما <strong>معلومات شركتك</strong> (الخدمات، الأسعار، الأسئلة الشائعة) فمكانها صفحة «معلومات شركتك».</span></span>
          <ArrowLeft className="w-4 h-4 text-[#C9A84C] shrink-0" aria-hidden />
        </Link>
        {!pd ? <SectionSkeleton /> : (
          <>
            <Field label="نص الشخصية (System Prompt)" htmlFor="ai-prompt" hint={`الحد الأدنى 10 أحرف — ${pd.prompt.length.toLocaleString("ar")} / ${(8000).toLocaleString("ar")}`}>
              <textarea id="ai-prompt" rows={9} maxLength={8000} value={pd.prompt} onChange={(e) => persona.setDraft({ ...pd, prompt: e.target.value })} className={inputClass} placeholder="أنت مساعد عقاري محترف لدى {اسم الشركة}…" />
            </Field>
            <Field label="تعليمات خاصة لسلوك المساعد (اختياري)" htmlFor="ai-instr" hint={`${pd.instructions.length.toLocaleString("ar")} / ${(4000).toLocaleString("ar")} — مثال: كيف يتعامل مع اعتراض السعر وكيف يقود المحادثة لموعد معاينة.`}>
              <textarea id="ai-instr" rows={5} maxLength={4000} value={pd.instructions} onChange={(e) => persona.setDraft({ ...pd, instructions: e.target.value })} className={inputClass} />
            </Field>
            <SaveBar dirty={persona.dirty} saving={persona.saving} error={persona.error} savedAt={persona.savedAt} onSave={() => void persona.submit()} onReset={persona.reset} label="حفظ الشخصية" />
          </>
        )}
      </form>

      <div className={cardClass}>
        <SectionHeader icon={Bot} title="أسلوب المساعد وقيوده" description="تُضاف هذه الاختيارات كتعليمات واضحة لشخصية المساعد في كل ردّ. لا يُغيَّر شيء في مساعدك حتى تحفظها لأول مرة." />
        {!bd || !c ? <SectionSkeleton /> : (
          <div className="space-y-5">
            <div className="grid md:grid-cols-3 gap-4">
              <Field label="النبرة" htmlFor="ai-tone"><select id="ai-tone" value={bd.tone} onChange={(e) => beh.setDraft({ ...bd, tone: e.target.value as AiBehavior["tone"] })} className={inputClass}>{TONE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></Field>
              <Field label="اللهجة" htmlFor="ai-dialect"><select id="ai-dialect" value={bd.dialect} onChange={(e) => beh.setDraft({ ...bd, dialect: e.target.value as AiBehavior["dialect"] })} className={inputClass}>{DIALECT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></Field>
              <Field label="لغة الرد" htmlFor="ai-lang"><select id="ai-lang" value={bd.language} onChange={(e) => beh.setDraft({ ...bd, language: e.target.value as AiBehavior["language"] })} className={inputClass}>{LANGUAGE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></Field>
            </div>
            <fieldset className="space-y-3 rounded-xl border border-white/10 p-4">
              <legend className="px-2 text-sm font-semibold text-gray-100">قيود المساعد</legend>
              <Toggle checked={c.no_price_without_inventory} onChange={(v) => setC("no_price_without_inventory", v)} label="لا يذكر سعرًا أو توفّرًا غير موجود في مخزونك" hint="إن لم يجد العقار يقول إنه سيتحقق بدل أن يخترع." />
              <Toggle checked={c.no_legal_or_financial_advice} onChange={(v) => setC("no_legal_or_financial_advice", v)} label="لا يقدّم نصيحة قانونية أو مالية" />
              <Toggle checked={c.no_competitor_mentions} onChange={(v) => setC("no_competitor_mentions", v)} label="لا يذكر المنافسين" />
              <Toggle checked={c.ask_budget_early} onChange={(v) => setC("ask_budget_early", v)} label="يسأل عن الميزانية والموقع مبكرًا" />
            </fieldset>
            <Field label="رسالة الترحيب (اختياري)" htmlFor="ai-welcome" hint="يبدأ بها المساعد مع العميل الجديد بصياغته الخاصة.">
              <textarea id="ai-welcome" rows={2} maxLength={500} value={bd.welcome_message} onChange={(e) => beh.setDraft({ ...bd, welcome_message: e.target.value })} className={inputClass} />
            </Field>
            <Field label="ردود سريعة (سطر لكل رد، حتى 8)" htmlFor="ai-qr" hint="عبارات جاهزة يستخدمها فريقك في الصندوق الوارد.">
              <textarea id="ai-qr" rows={4} value={bd.quick_replies.join("\n")} onChange={(e) => beh.setDraft({ ...bd, quick_replies: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 8) })} className={inputClass} />
            </Field>
            <SaveBar dirty={beh.dirty} saving={beh.saving} error={beh.error} savedAt={beh.savedAt} onSave={() => void beh.submit()} onReset={beh.reset} />
          </div>
        )}
      </div>

      <div className={cardClass} data-testid="bot-test">
        <SectionHeader icon={Send} title="جرّب البوت" description="اسأل المساعد كما لو كنت عميلًا ليرد بشخصيته ومعلومات شركتك الحالية. تجربة داخلية لا تصل لأي عميل ولا تُحفظ، ولا تشمل البحث في العقارات." />
        <p className="text-xs text-amber-200">تنبيه: التجربة تستخدم الشخصية المحفوظة؛ احفظ تعديلاتك أولًا لترى أثرها.</p>
        <div className="flex gap-2">
          <input aria-label="سؤال تجريبي" value={q} maxLength={1000} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void ask()} placeholder="مثال: عندكم فلل في شمال الرياض بميزانية ٣ مليون؟" className={inputClass} />
          <button onClick={() => void ask()} disabled={asking || !q.trim()} className="px-4 py-2 rounded-lg bg-[#C9A84C] text-[#0A0F1C] font-semibold hover:opacity-90 disabled:opacity-50 flex items-center gap-2 shrink-0">
            {asking ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Send className="w-4 h-4" aria-hidden />}إرسال
          </button>
        </div>
        {askError && <p role="alert" className="text-sm text-red-300">{askError}</p>}
        {answer && (
          <div className="rounded-xl border border-white/10 bg-[#0A0F1C] p-4 space-y-2" aria-live="polite">
            <p className="text-sm text-gray-100 whitespace-pre-wrap">{answer.reply}</p>
            <p className="text-xs text-gray-300">{answer.model ? `النموذج: ${answer.model} · ` : ""}{answer.latency_ms.toLocaleString("ar")} م.ث — {answer.note}</p>
          </div>
        )}
      </div>
      <SalesCard />
    </div>
  );
}
