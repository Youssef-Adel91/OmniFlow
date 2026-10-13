"use client";

import React, { useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck } from "lucide-react";
import { fetchFacebookDiagnostics, settingsErrorMessage, type PageDiagnostics } from "@/lib/api/settings";

const fmtDate = (iso: string) => new Intl.DateTimeFormat("ar-SA-u-ca-gregory", { timeZone: "Asia/Riyadh", dateStyle: "medium" }).format(new Date(iso));

/** "ما الناقص؟": asks Meta (read-only) whether the Page token is valid, which permissions are missing and which webhook subscriptions are missing. */
export default function ChannelDiagnostics() {
  const [state, setState] = useState<"idle" | "loading" | PageDiagnostics | { error: string }>("idle");
  const run = async () => {
    setState("loading");
    try { setState(await fetchFacebookDiagnostics()); }
    catch (e) { setState({ error: settingsErrorMessage(e, "تعذّر الفحص (للمدير فقط)") }); }
  };
  const result = typeof state === "object" ? state : null;
  return (
    <div className="space-y-2" data-testid="channel-diagnostics">
      <button onClick={() => void run()} disabled={state === "loading"} className="px-3 py-1.5 text-xs rounded-lg border border-white/20 hover:bg-white/10 disabled:opacity-50 flex items-center gap-1.5">
        {state === "loading" ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden /> : <ShieldCheck className="w-3.5 h-3.5" aria-hidden />}ما الناقص في الربط؟ (صلاحيات واشتراكات)
      </button>
      {result && "error" in result && <p role="alert" className="text-xs text-red-300">{result.error}</p>}
      {result && "issues" in result && (
        <div role="status" className="text-xs space-y-1">
          {result.ok
            ? <p className="text-emerald-300 flex items-center gap-1.5"><CheckCircle2 className="w-3.5 h-3.5" aria-hidden />كل الصلاحيات والاشتراكات المطلوبة موجودة.</p>
            : <ul className="space-y-0.5">{result.issues.map((i) => <li key={i} className="text-amber-200 flex items-start gap-1.5"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden />{i}</li>)}</ul>}
          {result.expires_at && <p className="text-gray-300">ينتهي الرمز في {fmtDate(result.expires_at)}.</p>}
          {!result.ok && !result.needs_reconnect && (result.missing_fields?.length || result.missing_scopes?.length) ? <p className="text-gray-300">للإصلاح: اضغط «إعادة الربط» ووافق على كل الصلاحيات المطلوبة.</p> : null}
        </div>
      )}
    </div>
  );
}
