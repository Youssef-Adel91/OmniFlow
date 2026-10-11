/** Public-site helpers: the origin used in absolute links / sitemap / Open Graph comes from the environment, never from a hard-coded domain. */

/** Origin of the public website (no trailing slash). PUBLIC_BASE_URL (server) wins; NEXT_PUBLIC_BASE_URL is the browser-visible twin. Empty = relative links only. */
export function siteOrigin(env: Record<string, string | undefined> = process.env): string {
  const raw = (env.PUBLIC_BASE_URL || env.NEXT_PUBLIC_BASE_URL || "").trim();
  if (!raw) return "";
  try {
    const u = new URL(raw);
    return u.origin;
  } catch {
    return "";
  }
}

export function absoluteUrl(path: string, env: Record<string, string | undefined> = process.env): string {
  const origin = siteOrigin(env);
  const p = path.startsWith("/") ? path : `/${path}`;
  return origin ? `${origin}${p}` : p;
}

export const LOCALES = ["ar", "en"] as const;
export type PublicLocale = (typeof LOCALES)[number];
export function asLocale(v: string | undefined): PublicLocale { return v === "en" ? "en" : "ar"; }

/** Public pages that belong in the sitemap (path without locale prefix). */
export const PUBLIC_PATHS = ["", "/pricing", "/privacy", "/terms", "/refund", "/data-deletion"] as const;
/** Everything behind login: never indexed. */
export const PRIVATE_PREFIXES = ["dashboard", "inbox", "settings", "broadcasts", "properties", "customers", "reports", "onboarding", "knowledge", "profile", "support", "billing"];
