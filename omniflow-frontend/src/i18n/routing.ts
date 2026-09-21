/**
 * i18n/routing.ts — next-intl v3 routing configuration
 *
 * Single source of truth for locale settings.
 * Used by: middleware.ts, request.ts, and any createNavigation() calls.
 */
import { defineRouting } from "next-intl/routing";

export const routing = defineRouting({
  locales: ["ar", "en"],
  defaultLocale: "ar",
  localePrefix: "always",
});

export type Locale = (typeof routing.locales)[number];
