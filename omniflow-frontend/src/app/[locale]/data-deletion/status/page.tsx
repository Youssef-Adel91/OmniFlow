import type { Metadata } from "next";
import Link from "next/link";
import { DeletionStatus } from "@/components/public/DeletionStatus";
import { PublicShell } from "@/components/public/PublicShell";
import { copyFor } from "@/lib/public/copy";
import { pageMetadata } from "@/lib/public/meta";
import { asLocale } from "@/lib/public/site";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const locale = asLocale((await params).locale);
  return { ...pageMetadata(locale, "/data-deletion/status", copyFor(locale).status.title, copyFor(locale).status.title), robots: { index: false, follow: true } };
}

export default async function Page({ params, searchParams }: { params: Promise<{ locale: string }>; searchParams: Promise<{ code?: string }> }) {
  const locale = asLocale((await params).locale);
  const { code } = await searchParams;
  const c = copyFor(locale);
  return (
    <PublicShell locale={locale}>
      <section className="max-w-xl mx-auto px-4 py-12 space-y-6">
        <h1 className="text-3xl font-bold">{c.status.title}</h1>
        <DeletionStatus copy={c.status} initialCode={typeof code === "string" ? code : undefined} locale={locale} />
        <p><Link className="text-[#C9A84C] underline underline-offset-4" href={`/${locale}/data-deletion`}>{c.deletion.backToInstructions}</Link></p>
      </section>
    </PublicShell>
  );
}
