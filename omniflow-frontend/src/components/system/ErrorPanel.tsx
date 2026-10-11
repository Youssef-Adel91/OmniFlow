"use client";

import Link from "next/link";
import { useEffect } from "react";
import { FALLBACK_COPY, type FallbackLocale } from "./messages";

/** Shared fallback UI for error boundaries. `tone="page"` fills the screen (dark, public look); `tone="inline"` sits inside the dashboard layout. */
export function ErrorPanel({ error, retry, locale, tone }: { error: Error & { digest?: string }; retry: () => void; locale: FallbackLocale; tone: "page" | "inline" }) {
  const c = FALLBACK_COPY[locale];
  useEffect(() => {
    // Log the technical error for developers only; the screen shows just the digest (never a stack or server detail).
    console.error("[OmniFlow] route error", { digest: error.digest, message: error.message });
  }, [error]);
  const dir = locale === "ar" ? "rtl" : "ltr";
  const wrap = tone === "page" ? "min-h-screen grid place-items-center bg-[#0a1128] text-white px-4 [&_h1]:text-inherit" : "grid place-items-center py-16 px-4";
  const card = tone === "page" ? "border-white/15 bg-white/[0.04]" : "border-[var(--border)] bg-[var(--card)] text-[var(--card-foreground)]";
  return (
    <div dir={dir} lang={locale} className={wrap}>
      <section role="alert" aria-labelledby="err-title" className={`max-w-md w-full rounded-2xl border p-8 text-center space-y-4 ${card}`}>
        <h1 id="err-title" className="text-2xl font-bold">{c.errorTitle}</h1>
        <p className="leading-7 opacity-90">{c.errorBody}</p>
        {error.digest && <p className="text-xs opacity-70">{c.reference}: <code dir="ltr">{error.digest}</code></p>}
        <div className="flex flex-col sm:flex-row gap-3 justify-center pt-2">
          <button type="button" onClick={() => retry()} className="min-h-11 px-6 rounded-lg bg-[#C9A84C] text-[#0a1128] font-bold hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#C9A84C]">{c.retry}</button>
          <Link href={`/${locale}`} className="min-h-11 px-6 inline-flex items-center justify-center rounded-lg border border-current/40 hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#C9A84C]">{c.home}</Link>
        </div>
      </section>
    </div>
  );
}
