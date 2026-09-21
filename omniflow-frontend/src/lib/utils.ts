import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes safely (handles conflicts like px-2 px-4 → px-4) */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format SAR price with Arabic-friendly notation */
export function formatSAR(amount: number, locale: string = "ar"): string {
  return new Intl.NumberFormat(locale === "ar" ? "ar-SA" : "en-SA", {
    style:    "currency",
    currency: "SAR",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(amount);
}

/** Format a date string relative to now (e.g., "منذ 5 دقائق") */
export function formatRelativeTime(date: string | Date, locale: string = "ar"): string {
  const rtf    = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const diffMs = new Date(date).getTime() - Date.now();
  const diffS  = Math.round(diffMs / 1000);
  const diffM  = Math.round(diffS  / 60);
  const diffH  = Math.round(diffM  / 60);
  const diffD  = Math.round(diffH  / 24);

  if (Math.abs(diffS) < 60)  return rtf.format(diffS, "second");
  if (Math.abs(diffM) < 60)  return rtf.format(diffM, "minute");
  if (Math.abs(diffH) < 24)  return rtf.format(diffH, "hour");
  return rtf.format(diffD, "day");
}

/** Truncate text to N characters with ellipsis */
export function truncate(text: string, n: number): string {
  return text.length > n ? text.slice(0, n) + "..." : text;
}
