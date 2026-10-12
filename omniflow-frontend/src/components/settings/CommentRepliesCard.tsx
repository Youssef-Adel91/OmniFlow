"use client";

import React, { useEffect, useState } from "react";
import { Loader2, MessageCircle } from "lucide-react";
import { fetchPreferences, savePreferences, settingsErrorMessage, type SocialCommentsPrefs } from "@/lib/api/settings";

/** Comment replies on Facebook/Instagram posts. Rendered ONLY when the platform flag is on (backend reports it); otherwise nothing is shown. */
export default function CommentRepliesCard() {
  const [available, setAvailable] = useState(false);
  const [value, setValue] = useState<SocialCommentsPrefs>({ enabled: false, mode: "public_reply" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    fetchPreferences().then((p) => { if (!alive) return; setAvailable(Boolean(p.features?.social_comment_replies)); if (p.preferences.social_comments) setValue(p.preferences.social_comments); }).catch(() => undefined);
    return () => { alive = false; };
  }, []);

  if (!available) return null;
  const save = async (next: SocialCommentsPrefs) => {
    setBusy(true); setMsg(null);
    try { setValue(await savePreferences("social_comments", next)); setMsg({ ok: true, text: "تم الحفظ." }); }
    catch (e) { setMsg({ ok: false, text: settingsErrorMessage(e, "تعذّر الحفظ (للمدير فقط)") }); }
    setBusy(false);
  };
  return (
    <section aria-label="الرد على التعليقات" className="rounded-xl border border-white/10 p-5 space-y-3" data-testid="comment-replies">
      <h3 className="font-bold text-white flex items-center gap-2"><MessageCircle className="w-4 h-4 text-[#C9A84C]" aria-hidden />الرد على التعليقات (فيسبوك وإنستجرام)</h3>
      <p className="text-xs text-gray-300">عند تفعيلها يردّ المساعد على التعليقات الرئيسية على منشوراتك. لا يردّ على تعليقات صفحتك نفسها ولا على الردود داخل الخيط، ولا يكرر الرد على التعليق نفسه، وبحدّ أقصى للردود في الدقيقة.</p>
      <label className="flex items-center gap-3 text-sm text-gray-100">
        <input type="checkbox" role="switch" checked={value.enabled} disabled={busy} onChange={(e) => void save({ ...value, enabled: e.target.checked })} />تفعيل الرد على التعليقات
      </label>
      <fieldset disabled={busy || !value.enabled} className="space-y-1 disabled:opacity-60">
        <legend className="text-sm text-gray-200">طريقة الرد</legend>
        {([["public_reply", "رد علني تحت التعليق"], ["private_dm", "رسالة خاصة لصاحب التعليق (مرة واحدة لكل تعليق، خلال 7 أيام)"]] as const).map(([m, label]) => (
          <label key={m} className="flex items-center gap-2 text-sm text-gray-100"><input type="radio" name="comment-mode" checked={value.mode === m} onChange={() => void save({ ...value, mode: m })} />{label}</label>
        ))}
      </fieldset>
      {busy && <Loader2 className="w-4 h-4 animate-spin text-gray-300" aria-label="جارٍ الحفظ" />}
      {msg && <p role={msg.ok ? "status" : "alert"} className={`text-xs ${msg.ok ? "text-emerald-300" : "text-red-300"}`}>{msg.text}</p>}
    </section>
  );
}
