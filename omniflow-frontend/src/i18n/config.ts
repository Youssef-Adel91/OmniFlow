/**
 * i18n/config.ts — re-exports from routing.ts for backward compatibility.
 *
 * The canonical locale config now lives in routing.ts.
 * This file keeps existing imports working.
 */
export { routing, type Locale } from "./routing";

export const locales = ["ar", "en"] as const;
export const defaultLocale = "ar" as const;
export const localePrefix = "always" as const;

export const isRTL = (locale: string): boolean => locale === "ar";
export const localeDir = (locale: string): "rtl" | "ltr" =>
  locale === "ar" ? "rtl" : "ltr";

export const localeMeta: Record<string, { label: string; flag: string; nativeLabel: string }> = {
  ar: { label: "Arabic",  flag: "🇸🇦", nativeLabel: "العربية" },
  en: { label: "English", flag: "🇬🇧", nativeLabel: "English" },
};
