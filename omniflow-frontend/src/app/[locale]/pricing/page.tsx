import type { Metadata } from "next";
import { PricingView } from "@/components/public/PricingView";
import { PublicShell } from "@/components/public/PublicShell";
import { fetchCatalog } from "@/lib/public/catalog";
import { copyFor } from "@/lib/public/copy";
import { pageMetadata } from "@/lib/public/meta";
import { asLocale } from "@/lib/public/site";

export const revalidate = 300;

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const locale = asLocale((await params).locale);
  const c = copyFor(locale).pricing;
  return pageMetadata(locale, "/pricing", c.metaTitle, c.metaDescription);
}

export default async function PricingPage({ params }: { params: Promise<{ locale: string }> }) {
  const locale = asLocale((await params).locale);
  const catalog = await fetchCatalog();
  return <PublicShell locale={locale}><PricingView catalog={catalog} locale={locale} /></PublicShell>;
}
