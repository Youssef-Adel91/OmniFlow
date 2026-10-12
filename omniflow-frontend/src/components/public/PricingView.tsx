import Link from "next/link";
import React from "react";
import { copyFor, type Copy } from "@/lib/public/copy";
import { sar, sarWhole, withVat, yearlySaving, type Catalog, type CatalogPlan } from "@/lib/public/catalog";
import type { PublicLocale } from "@/lib/public/site";

export function planLines(p: CatalogPlan, c: Copy["pricing"]): string[] {
  if (p.contact_sales) return c.features.custom;
  const l = p.limits as Record<string, number | boolean | undefined>;
  const n = (v: number) => v.toLocaleString("en-US");
  const out: string[] = [];
  if (typeof l.messages_per_month === "number") out.push(c.features.replies(n(l.messages_per_month)));
  if (typeof l.seats === "number") out.push(c.features.seats(l.seats));
  if (typeof l.whatsapp_numbers === "number") out.push(c.features.numbers(l.whatsapp_numbers));
  if (typeof l.properties === "number") out.push(c.features.properties(n(l.properties)));
  if (typeof l.contacts === "number") out.push(c.features.contacts(n(l.contacts)));
  out.push(l.campaigns === false ? c.features.noCampaigns : c.features.campaigns);
  return out;
}

/** The public pricing grid: monthly + yearly net prices, "+ VAT", the VAT-inclusive total, limits, top-up packs and add-ons. */
export function PricingView({ catalog, locale }: { catalog: Catalog; locale: PublicLocale }) {
  const c = copyFor(locale).pricing;
  const cur = locale === "ar" ? "ر.س" : "SAR";
  const vat = catalog.vat_rate_bp;
  return (
    <div className="max-w-6xl mx-auto px-4 py-12 space-y-14">
      <header className="text-center space-y-3 max-w-3xl mx-auto">
        <h1 className="text-4xl font-black">{c.title}</h1>
        <p className="text-white/70 leading-8">{c.subtitle}</p>
        <p className="text-[#C9A84C] font-semibold">{c.trialNote}</p>
      </header>

      <section aria-label={c.limitsTitle} className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        {catalog.plans.map((p) => {
          const highlight = p.code === "pro";
          const name = c.names[p.code] ?? p.name_ar;
          const save = yearlySaving(p.monthly_halalas, p.yearly_halalas);
          return (
            <div key={p.code} data-testid={`plan-${p.code}`} className={`rounded-2xl p-6 flex flex-col gap-4 border ${highlight ? "border-[#C9A84C] bg-[#C9A84C]/5" : "border-white/10 bg-white/[0.03]"}`}>
              <div><h2 className="text-xl font-bold">{name}</h2><p className="text-sm text-white/60">{c.blurbs[p.code] ?? ""}</p></div>
              {p.contact_sales ? <p className="text-2xl font-bold text-[#E6C766]">{c.contact}</p> : (
                <div className="space-y-1">
                  <p className="text-3xl font-black text-[#E6C766]" dir="ltr"><span data-testid={`monthly-${p.code}`}>{sarWhole(p.monthly_halalas)}</span> <span className="text-sm font-normal text-white/70">{cur} {c.perMonth}</span></p>
                  <p className="text-xs text-white/60">{c.plusVat} · {c.totalWithVat}: <span dir="ltr">{sar(withVat(p.monthly_halalas, vat))} {cur}</span></p>
                  <p className="text-sm text-white/80" dir="ltr">{sarWhole(p.yearly_halalas)} {cur} {c.perYear} <span className="text-xs text-white/60">{c.plusVat}</span>{save > 0 ? <span className="ms-2 text-emerald-300 text-xs">{c.save} {save}%</span> : null}</p>
                </div>
              )}
              <ul className="space-y-1.5 text-sm text-white/85 flex-1">{planLines(p, c).map((l) => <li key={l} className="flex gap-2"><span aria-hidden className="text-[#C9A84C]">✓</span><span>{l}</span></li>)}</ul>
              {p.contact_sales
                ? <Link href={`/${locale}/sign-in`} className="text-center border border-white/25 rounded-lg py-2.5 font-semibold hover:bg-white/10">{c.contactCta}</Link>
                : <Link href={`/${locale}/sign-up`} className={`text-center rounded-lg py-2.5 font-bold ${highlight ? "bg-[#C9A84C] text-[#0a1128] hover:brightness-110" : "border border-[#C9A84C]/60 text-[#C9A84C] hover:bg-[#C9A84C]/10"}`}>{c.startTrial}</Link>}
            </div>
          );
        })}
      </section>

      <section className="grid gap-8 md:grid-cols-2" aria-label={c.topupsTitle}>
        <div className="rounded-2xl border border-white/10 p-6 space-y-4">
          <h2 className="text-xl font-bold">{c.topupsTitle}</h2><p className="text-sm text-white/70">{c.topupsDesc}</p>
          <ul className="space-y-2">{catalog.topup_packs.map((k) => (
            <li key={k.code} data-testid={`pack-${k.code}`} className="flex justify-between gap-3 border-b border-white/10 pb-2">
              <span>{k.replies.toLocaleString("en-US")} {c.replies}<span className="block text-xs text-white/50">{c.validity}</span></span>
              <span dir="ltr" className="text-[#E6C766] font-bold">{sarWhole(k.price_halalas)} {cur} <span className="text-xs text-white/60 font-normal">{c.plusVat}</span></span>
            </li>))}</ul>
        </div>
        <div className="rounded-2xl border border-white/10 p-6 space-y-4">
          <h2 className="text-xl font-bold">{c.addonsTitle}</h2><p className="text-sm text-white/70">{c.addonsDesc}</p>
          <ul className="space-y-2">{catalog.addons.map((a) => (
            <li key={a.code} className="flex justify-between gap-3 border-b border-white/10 pb-2">
              <span>{a.code === "seat" ? c.seatAddon : c.numberAddon}{!a.available && <span className="ms-2 text-xs rounded-full border border-white/20 px-2 py-0.5 text-white/60">{c.unavailable}</span>}</span>
              <span dir="ltr" className="text-[#E6C766] font-bold">{sarWhole(a.monthly_halalas)} {cur} <span className="text-xs text-white/60 font-normal">{c.perMonth} {c.plusVat}</span></span>
            </li>))}</ul>
        </div>
      </section>

      <section className="max-w-3xl mx-auto rounded-2xl border border-white/10 p-6 space-y-3">
        <h2 className="text-xl font-bold">{c.notesTitle}</h2>
        <ul className="list-disc ps-5 space-y-2 text-white/80 leading-7">{c.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        <p><Link className="text-[#C9A84C] underline underline-offset-4" href={`/${locale}/refund`}>{c.refundLink}</Link></p>
      </section>
    </div>
  );
}
