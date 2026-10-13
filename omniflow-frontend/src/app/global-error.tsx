"use client";

import { useEffect } from "react";
import { FALLBACK_COPY } from "@/components/system/messages";

/** Last resort: the root layout itself failed. Renders its own <html>/<body> (the root layout is gone) with inline styles only. */
export default function GlobalError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => { console.error("[OmniFlow] global error", { digest: error.digest }); }, [error]);
  const ar = FALLBACK_COPY.ar, en = FALLBACK_COPY.en;
  return (
    <html lang="ar" dir="rtl">
      <body style={{ margin: 0, minHeight: "100vh", display: "grid", placeItems: "center", background: "#0a1128", color: "#fff", fontFamily: "system-ui, sans-serif", padding: 16 }}>
        <main role="alert" style={{ maxWidth: 420, textAlign: "center", lineHeight: 1.8 }}>
          <h1 style={{ fontSize: 24 }}>{ar.errorTitle}</h1>
          <p>{ar.errorBody}</p>
          <p dir="ltr" style={{ opacity: 0.85 }}>{en.errorTitle}. {en.errorBody}</p>
          {error.digest && <p style={{ fontSize: 12, opacity: 0.7 }}>{ar.reference}: <code dir="ltr">{error.digest}</code></p>}
          <button type="button" onClick={() => retry()} style={{ minHeight: 44, padding: "0 24px", borderRadius: 8, border: 0, background: "#C9A84C", color: "#0a1128", fontWeight: 700, cursor: "pointer" }}>
            {ar.retry} / {en.retry}
          </button>
        </main>
      </body>
    </html>
  );
}
