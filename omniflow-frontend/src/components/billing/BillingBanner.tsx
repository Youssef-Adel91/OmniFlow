"use client";

/** Subscription state banner shown on every dashboard page. Renders nothing for healthy/exempt tenants and on any error. */
import React, { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Info, Lock } from "lucide-react";
import { bannerFor, fetchBillingStatus, type BannerTone, type BillingStatus } from "@/lib/api/billing";

const TONE: Record<BannerTone, string> = {
  info: "bg-sky-500/10 border-sky-300/40 text-sky-100",
  warn: "bg-amber-500/10 border-amber-300/50 text-amber-100",
  danger: "bg-red-500/10 border-red-300/50 text-red-100",
};
const ICON: Record<BannerTone, React.ElementType> = { info: Info, warn: AlertTriangle, danger: Lock };

export function BillingBanner({ locale }: { locale: string }) {
  const [status, setStatus] = useState<BillingStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => fetchBillingStatus().then((s) => alive && setStatus(s)).catch(() => alive && setStatus(null));
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const b = bannerFor(status);
  if (!b) return null;
  const Icon = ICON[b.tone];
  return (
    <div role={b.tone === "info" ? "status" : "alert"} dir="rtl" data-testid="billing-banner"
      className={`mb-5 flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3 text-sm ${TONE[b.tone]}`}>
      <Icon className="w-4 h-4 shrink-0" aria-hidden />
      <span className="flex-1 min-w-[12rem]">{b.text}</span>
      {b.cta && <Link href={`/${locale}/settings#billing`} className="font-bold underline underline-offset-4">{b.cta}</Link>}
    </div>
  );
}
