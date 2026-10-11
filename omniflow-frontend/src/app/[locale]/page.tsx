/**
 * Public landing page. Server component; copy lives in lib/public/copy.ts (ar/en). It states only what the product does today:
 * no invented statistics, customers, awards or "first in the market" claims.
 */
import type { Metadata } from "next";
import Link from "next/link";
import { PublicShell } from "@/components/public/PublicShell";
import { copyFor } from "@/lib/public/copy";
import { pageMetadata } from "@/lib/public/meta";
import { asLocale } from "@/lib/public/site";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const locale = asLocale((await params).locale);
  const c = copyFor(locale);
  return pageMetadata(locale, "", `OmniFlow — ${c.hero.title}`, c.hero.subtitle);
}

export default async function LandingPage({ params }: { params: Promise<{ locale: string }> }) {
  const locale = asLocale((await params).locale);
  const c = copyFor(locale);
  const primary = "inline-block text-center rounded-xl bg-[#C9A84C] px-7 py-3.5 font-bold text-[#0a1128] hover:brightness-110";
  const secondary = "inline-block text-center rounded-xl border border-white/25 px-7 py-3.5 font-semibold hover:bg-white/10";
  return (
    <PublicShell locale={locale}>
      <section className="max-w-4xl mx-auto px-4 pt-20 pb-16 text-center space-y-6">
        <p className="inline-block rounded-full border border-[#C9A84C]/40 px-4 py-1 text-sm text-[#E6C766]">{c.hero.badge}</p>
        <h1 className="text-3xl sm:text-5xl font-black leading-tight">{c.hero.title}</h1>
        <p className="text-lg text-white/75 leading-8 max-w-3xl mx-auto">{c.hero.subtitle}</p>
        <div className="flex flex-col sm:flex-row gap-3 justify-center pt-2">
          <Link href={`/${locale}/sign-up`} className={primary}>{c.hero.ctaPrimary}</Link>
          <Link href={`/${locale}/pricing`} className={secondary}>{c.hero.ctaSecondary}</Link>
        </div>
        <p className="text-sm text-white/60">{c.hero.note}</p>
      </section>

      <section id="features" className="max-w-6xl mx-auto px-4 py-14 space-y-8 scroll-mt-20">
        <div className="text-center space-y-2"><h2 className="text-3xl font-bold">{c.features.title}</h2><p className="text-white/70">{c.features.intro}</p></div>
        <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {c.features.items.map((f) => (
            <li key={f.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 space-y-2">
              <h3 className="font-bold text-[#E6C766]">{f.title}</h3><p className="text-sm text-white/75 leading-7">{f.desc}</p>
            </li>
          ))}
        </ul>
      </section>

      <section id="how" className="max-w-4xl mx-auto px-4 py-14 space-y-8 scroll-mt-20">
        <h2 className="text-3xl font-bold text-center">{c.how.title}</h2>
        <ol className="space-y-4">
          {c.how.steps.map((s, i) => (
            <li key={s.title} className="flex gap-4 rounded-2xl border border-white/10 p-5">
              <span aria-hidden className="shrink-0 w-9 h-9 rounded-full bg-[#C9A84C] text-[#0a1128] font-black grid place-items-center">{i + 1}</span>
              <div><h3 className="font-bold">{s.title}</h3><p className="text-sm text-white/75 leading-7 mt-1">{s.desc}</p></div>
            </li>
          ))}
        </ol>
      </section>

      <section id="faq" className="max-w-3xl mx-auto px-4 py-14 space-y-6 scroll-mt-20">
        <h2 className="text-3xl font-bold text-center">{c.faq.title}</h2>
        <div className="space-y-3">
          {c.faq.items.map((f) => (
            <details key={f.q} className="rounded-xl border border-white/10 p-4 group">
              <summary className="cursor-pointer font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#C9A84C] rounded">{f.q}</summary>
              <p className="mt-3 text-white/75 leading-7">{f.a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="max-w-3xl mx-auto px-4 py-14 text-center space-y-4">
        <h2 className="text-3xl font-bold">{c.cta.title}</h2><p className="text-white/70">{c.cta.desc}</p>
        <Link href={`/${locale}/sign-up`} className={primary}>{c.cta.button}</Link>
      </section>
    </PublicShell>
  );
}
