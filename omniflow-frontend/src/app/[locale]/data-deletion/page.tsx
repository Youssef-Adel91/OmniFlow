import type { Metadata } from "next";
import Link from "next/link";
import { Doc } from "@/components/public/Doc";
import { PublicShell } from "@/components/public/PublicShell";
import { copyFor } from "@/lib/public/copy";
import { pageMetadata } from "@/lib/public/meta";
import { asLocale } from "@/lib/public/site";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const locale = asLocale((await params).locale);
  const c = copyFor(locale).deletion;
  return pageMetadata(locale, "/data-deletion", c.metaTitle, c.title);
}

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const locale = asLocale((await params).locale);
  const c = copyFor(locale).deletion;
  return (
    <PublicShell locale={locale}>
      <Doc title={c.title} draft={c.draft} sections={c.sections}>
        <p><Link className="text-[#C9A84C] underline underline-offset-4" href={`/${locale}/data-deletion/status`}>{c.statusTitle}</Link></p>
      </Doc>
    </PublicShell>
  );
}
