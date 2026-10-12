import Link from "next/link";
import React from "react";
import { copyFor } from "@/lib/public/copy";
import type { PublicLocale } from "@/lib/public/site";
import { LangSwitch } from "./LangSwitch";

/** Header + footer shared by every public (logged-out) page. Server component; keyboard-friendly (skip link, landmarks, focus rings). */
export function PublicShell({ locale, children }: { locale: PublicLocale; children: React.ReactNode }) {
  const c = copyFor(locale);
  const dir = locale === "ar" ? "rtl" : "ltr";
  const link = "text-sm text-white/70 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#C9A84C] rounded";
  return (
    <div dir={dir} lang={locale} className="min-h-screen bg-[#0a1128] text-white antialiased">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:start-2 focus:z-[60] focus:bg-[#C9A84C] focus:text-[#0a1128] focus:px-4 focus:py-2 focus:rounded-lg">
        {locale === "ar" ? "تخطَّ إلى المحتوى" : "Skip to content"}
      </a>
      <header className="sticky top-0 z-50 border-b border-[#C9A84C]/15 bg-[#0a1128]/90 backdrop-blur">
        <div className="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between gap-3">
          <Link href={`/${locale}`} className="text-lg font-bold tracking-tight"><span className="text-[#C9A84C]">Omni</span>Flow</Link>
          <nav aria-label={c.nav.menuLabel} className="hidden md:flex items-center gap-6">
            <Link href={`/${locale}#features`} className={link}>{c.nav.features}</Link>
            <Link href={`/${locale}#how`} className={link}>{c.nav.how}</Link>
            <Link href={`/${locale}/pricing`} className={link}>{c.nav.pricing}</Link>
            <Link href={`/${locale}#faq`} className={link}>{c.nav.faq}</Link>
          </nav>
          <div className="flex items-center gap-2">
            <LangSwitch locale={locale} label={c.nav.lang} />
            <Link href={`/${locale}/sign-in`} className="hidden sm:inline-block text-sm text-white/80 hover:text-white px-3 py-2">{c.nav.signIn}</Link>
            <Link href={`/${locale}/sign-up`} className="text-sm font-semibold text-[#0a1128] bg-[#C9A84C] hover:brightness-110 px-4 py-2 rounded-lg">{c.nav.start}</Link>
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1}>{children}</main>
      <footer className="border-t border-white/10 mt-20">
        <div className="max-w-6xl mx-auto px-4 py-10 grid gap-8 sm:grid-cols-3 text-sm text-white/70">
          <div><p className="text-base font-bold text-white"><span className="text-[#C9A84C]">Omni</span>Flow</p><p className="mt-2">{c.footer.draftNote}</p></div>
          <nav aria-label={c.footer.product}><p className="font-semibold text-white mb-3">{c.footer.product}</p>
            <ul className="space-y-2"><li><Link className="hover:text-white" href={`/${locale}/pricing`}>{c.footer.pricing}</Link></li>
              <li><Link className="hover:text-white" href={`/${locale}#features`}>{c.nav.features}</Link></li><li><Link className="hover:text-white" href={`/${locale}#faq`}>{c.nav.faq}</Link></li></ul></nav>
          <nav aria-label={c.footer.legal}><p className="font-semibold text-white mb-3">{c.footer.legal}</p>
            <ul className="space-y-2"><li><Link className="hover:text-white" href={`/${locale}/privacy`}>{c.footer.privacy}</Link></li><li><Link className="hover:text-white" href={`/${locale}/terms`}>{c.footer.terms}</Link></li>
              <li><Link className="hover:text-white" href={`/${locale}/refund`}>{c.footer.refund}</Link></li><li><Link className="hover:text-white" href={`/${locale}/data-deletion`}>{c.footer.deletion}</Link></li></ul></nav>
        </div>
        <p className="text-center text-xs text-white/50 pb-8">© {new Date().getFullYear()} OmniFlow. {c.footer.rights}</p>
      </footer>
    </div>
  );
}
