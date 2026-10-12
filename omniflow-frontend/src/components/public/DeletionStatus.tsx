"use client";

import React, { useCallback, useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/api/client";
import type { Copy } from "@/lib/public/copy";

interface Result { status: string; requested_at: string; completed_at: string | null }
type State = { kind: "idle" } | { kind: "loading" } | { kind: "ok"; data: Result } | { kind: "error"; message: string };

/** Public lookup of a data-deletion request by its confirmation code (the code is the only credential). */
export function DeletionStatus({ copy, initialCode, locale }: { copy: Copy["status"]; initialCode?: string; locale: string }) {
  const [code, setCode] = useState(initialCode ?? "");
  const [state, setState] = useState<State>({ kind: "idle" });

  const check = useCallback(async (value: string) => {
    const c = value.trim();
    if (!/^[A-Za-z0-9]{8,32}$/.test(c)) { setState({ kind: "error", message: copy.invalid }); return; }
    setState({ kind: "loading" });
    try {
      const res = await fetch(`${API_BASE_URL}/meta/data-deletion/status/${encodeURIComponent(c)}`, { cache: "no-store" });
      if (res.status === 404) { setState({ kind: "error", message: copy.notFound }); return; }
      if (!res.ok) throw new Error(String(res.status));
      setState({ kind: "ok", data: (await res.json()) as Result });
    } catch { setState({ kind: "error", message: copy.error }); }
  }, [copy]);

  useEffect(() => { if (initialCode) void check(initialCode); }, [initialCode, check]);

  const fmt = (iso: string) => new Intl.DateTimeFormat(locale === "ar" ? "ar-SA" : "en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Riyadh" }).format(new Date(iso));
  return (
    <div className="space-y-4">
      <form onSubmit={(e) => { e.preventDefault(); void check(code); }} className="flex flex-col sm:flex-row gap-3">
        <label className="flex-1">
          <span className="block text-sm text-gray-200 mb-1.5">{copy.codeLabel}</span>
          <input value={code} onChange={(e) => setCode(e.target.value)} dir="ltr" autoComplete="off" inputMode="text" maxLength={32}
            className="w-full bg-[#0A0F1C] border border-white/20 rounded-lg py-2.5 px-3 text-white focus:outline-none focus:border-[#C9A84C]" />
        </label>
        <button type="submit" disabled={state.kind === "loading"} className="self-end bg-[#C9A84C] text-[#0a1128] font-bold rounded-lg px-6 py-2.5 disabled:opacity-60">
          {state.kind === "loading" ? copy.checking : copy.check}
        </button>
      </form>
      <div aria-live="polite" data-testid="deletion-result">
        {state.kind === "error" && <p role="alert" className="text-red-300">{state.message}</p>}
        {state.kind === "ok" && (
          <div className="rounded-xl border border-white/15 p-4 space-y-1" data-status={state.data.status}>
            <p className="font-bold text-white">{copy.states[state.data.status] ?? state.data.status}</p>
            <p className="text-sm text-gray-300">{copy.requestedAt}: {fmt(state.data.requested_at)}</p>
            {state.data.completed_at && <p className="text-sm text-gray-300">{copy.completedAt}: {fmt(state.data.completed_at)}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
