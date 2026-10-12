"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** Switches the current public page to the other locale (/ar/x <-> /en/x). */
export function LangSwitch({ locale, label }: { locale: string; label: string }) {
  const pathname = usePathname() || `/${locale}`;
  const other = locale === "ar" ? "en" : "ar";
  const target = pathname.replace(/^\/(ar|en)(?=\/|$)/, `/${other}`);
  return (
    <Link href={target === pathname ? `/${other}` : target} hrefLang={other} lang={other} className="text-sm text-white/70 hover:text-white px-3 py-2 rounded-lg border border-white/15">
      {label}
    </Link>
  );
}
