"use client";

import React, { useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Send } from "lucide-react";
import { sendWhatsappTestMessage, settingsErrorMessage, type WhatsappTestResult } from "@/lib/api/settings";
import { inputClass } from "./common";

/** Sends ONE real message from the connected number to a number the admin types (their own phone), to prove the connection end to end. */
export default function WhatsappTestMessage() {
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<WhatsappTestResult | { ok: false; detail: string } | null>(null);
  const send = async () => {
    setBusy(true); setRes(null);
    try { setRes(await sendWhatsappTestMessage(to.trim())); }
    catch (e) { setRes({ ok: false, detail: settingsErrorMessage(e, "تعذّر الإرسال (للمدير فقط)") }); }
    finally { setBusy(false); }
  };
  return (
    <div className="space-y-2" data-testid="wa-test-message">
      <label htmlFor="wa-test-to" className="text-xs text-gray-200 block">اختبار الإرسال: أرسل رسالة تجريبية حقيقية إلى جوالك</label>
      <div className="flex flex-wrap items-center gap-2">
        <input id="wa-test-to" value={to} onChange={(e) => setTo(e.target.value)} dir="ltr" inputMode="tel" placeholder="+9665xxxxxxxx" className={`${inputClass} max-w-[14rem]`} />
        <button onClick={() => void send()} disabled={busy || to.trim().length < 5} className="px-3 py-1.5 text-xs rounded-lg border border-white/20 hover:bg-white/10 disabled:opacity-50 flex items-center gap-1.5">
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden /> : <Send className="w-3.5 h-3.5" aria-hidden />}إرسال تجربة
        </button>
      </div>
      {res && <p role="status" className={`text-xs flex items-center gap-1.5 ${res.ok ? "text-emerald-300" : "text-red-300"}`}>{res.ok ? <CheckCircle2 className="w-3.5 h-3.5" aria-hidden /> : <AlertTriangle className="w-3.5 h-3.5" aria-hidden />}{res.detail}</p>}
      <p className="text-xs text-gray-300">تُرسل رسالة واحدة فقط (حد أقصى 5 في الساعة) وتُسجَّل في سجل التدقيق دون رقمك كاملًا.</p>
    </div>
  );
}
