import type { Metadata } from "next";
import { absoluteUrl, type PublicLocale } from "@/lib/public/site";

/** Title/description/canonical/hreflang/Open Graph for a public page (`path` has no locale prefix, e.g. "/pricing"). */
export function pageMetadata(locale: PublicLocale, path: string, title: string, description: string): Metadata {
  const url = absoluteUrl(`/${locale}${path}`);
  return {
    title,
    description,
    alternates: { canonical: url, languages: { ar: absoluteUrl(`/ar${path}`), en: absoluteUrl(`/en${path}`) } },
    openGraph: { title, description, url, siteName: "OmniFlow", locale: locale === "ar" ? "ar_SA" : "en_US", type: "website" },
    twitter: { card: "summary", title, description },
  };
}
