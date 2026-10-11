import type { Metadata } from "next";
import { Doc } from "@/components/public/Doc";
import { PublicShell } from "@/components/public/PublicShell";
import { copyFor } from "@/lib/public/copy";
import { pageMetadata } from "@/lib/public/meta";
import { asLocale } from "@/lib/public/site";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const locale = asLocale((await params).locale);
  const c = copyFor(locale).privacy;
  return pageMetadata(locale, "/privacy", c.metaTitle, c.title);
}

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const locale = asLocale((await params).locale);
  const copy = copyFor(locale);
  const c = copy.privacy;
  return <PublicShell locale={locale}><Doc title={c.title} draft={copy.legalDraft} sections={c.sections} /></PublicShell>;
}
