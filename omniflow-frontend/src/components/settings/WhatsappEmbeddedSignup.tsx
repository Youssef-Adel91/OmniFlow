"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, MessageSquare } from "lucide-react";
import { completeSignup, fetchSignupConfig, parseSignupMessage, type SignupConfig } from "@/lib/api/onboarding";
import { settingsErrorMessage } from "@/lib/api/settings";

type FbSdk = {
  init: (o: Record<string, unknown>) => void;
  login: (cb: (r: { authResponse?: { code?: string } }) => void, o: Record<string, unknown>) => void;
};
declare global { interface Window { FB?: FbSdk; fbAsyncInit?: () => void } }

function loadSdk(cfg: SignupConfig): Promise<FbSdk> {
  return new Promise((resolve, reject) => {
    const ready = () => { window.FB!.init({ appId: cfg.app_id, autoLogAppEvents: true, xfbml: false, version: cfg.graph_version }); resolve(window.FB!); };
    if (window.FB) return ready();
    window.fbAsyncInit = ready;
    const s = document.createElement("script");
    s.src = "https://connect.facebook.net/en_US/sdk.js"; s.async = true; s.defer = true; s.crossOrigin = "anonymous";
    s.onerror = () => reject(new Error("sdk"));
    document.body.appendChild(s);
  });
}

/** "Connect WhatsApp with Meta" button. Renders NOTHING unless the backend flag is on and the app is configured. */
export function WhatsappEmbeddedSignup({ onConnected }: { onConnected: (message: string) => void }) {
  const [cfg, setCfg] = useState<SignupConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ code?: string; ids?: { waba_id: string; phone_number_id: string } }>({});

  useEffect(() => { let alive = true; void fetchSignupConfig().then((c) => { if (alive) setCfg(c); }); return () => { alive = false; }; }, []);

  const finish = async () => {
    const { code, ids } = pending.current;
    if (!code || !ids) return;
    pending.current = {};
    try {
      const r = await completeSignup({ code, ...ids });
      onConnected(`رُبط رقم واتساب${r.display_phone_number ? ` ${r.display_phone_number}` : ""} بنجاح.`);
    } catch (e) { setError(settingsErrorMessage(e, "تعذّر إكمال الربط مع Meta")); }
    setBusy(false);
  };

  useEffect(() => {
    if (!cfg) return;
    const onMessage = (ev: MessageEvent) => {
      const parsed = parseSignupMessage(ev.origin, ev.data);
      if (!parsed) return;
      if ("cancelled" in parsed) { setBusy(false); return; }
      pending.current.ids = parsed; void finish();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg]);

  if (!cfg) return null;
  const start = async () => {
    setError(null); setBusy(true); pending.current = {};
    try {
      const fb = await loadSdk(cfg);
      fb.login((r) => {
        const code = r.authResponse?.code;
        if (!code) { setBusy(false); return; }
        pending.current.code = code; void finish();
      }, { config_id: cfg.config_id, response_type: "code", override_default_response_type: true, extras: { setup: {}, sessionInfoVersion: "3" } });
    } catch { setError("تعذّر تحميل أداة الربط من Meta. تحقق من الاتصال ومانع الإعلانات."); setBusy(false); }
  };
  return (
    <div className="space-y-2">
      <button type="button" onClick={() => void start()} disabled={busy} className="px-3 py-1.5 text-xs rounded-lg bg-[#C9A84C] text-[#0A0F1C] font-semibold hover:opacity-90 disabled:opacity-50 flex items-center gap-1.5">
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden /> : <MessageSquare className="w-3.5 h-3.5" aria-hidden />}ربط واتساب عبر Meta
      </button>
      {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
    </div>
  );
}
