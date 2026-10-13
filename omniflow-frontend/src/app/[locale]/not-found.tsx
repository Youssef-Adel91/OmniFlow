import Link from "next/link";
import { getLocale } from "next-intl/server";
import { FALLBACK_COPY, asFallbackLocale } from "@/components/system/messages";

export default async function LocaleNotFound() {
  const locale = asFallbackLocale(await getLocale());
  const c = FALLBACK_COPY[locale];
  return (
    <main dir={locale === "ar" ? "rtl" : "ltr"} className="min-h-screen grid place-items-center bg-[#0a1128] text-white px-4 [&_h1]:text-inherit">
      <section className="max-w-md w-full text-center space-y-4">
        <p aria-hidden className="text-6xl font-black text-[#C9A84C]">404</p>
        <h1 className="text-2xl font-bold">{c.notFoundTitle}</h1>
        <p className="leading-7 text-white/80">{c.notFoundBody}</p>
        <Link href={`/${locale}`} className="inline-flex min-h-11 items-center px-6 rounded-lg bg-[#C9A84C] text-[#0a1128] font-bold hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#C9A84C]">{c.home}</Link>
      </section>
    </main>
  );
}
