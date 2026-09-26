/**
 * app/[locale]/layout.tsx — Root Locale Layout
 *
 * Provides the shared <html> and <body> tags for ALL routes under [locale]
 * (both the (auth) and (dashboard) route groups).
 *
 * Route params are asynchronous in Next.js 16.
 */
import type { Metadata } from "next";
import { getMessages } from "next-intl/server";
import { NextIntlClientProvider } from "next-intl";
import { routing, type Locale } from "@/i18n/routing";
import { AuthHydrator } from "@/components/auth/AuthHydrator";
import { ClerkProvider } from "@clerk/nextjs";
import "../globals.css";

// ── Metadata ──────────────────────────────────────────────────────────────────

export const metadata: Metadata = {
  title: {
    template: "%s | OmniFlow AI",
    default: "OmniFlow AI — منصة إدارة العقارات",
  },
  description: "منصة إدارة العقارات والمحادثات بالذكاء الاصطناعي",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function localeDir(locale: Locale): "rtl" | "ltr" {
  return locale === "ar" ? "rtl" : "ltr";
}

// ── Layout ────────────────────────────────────────────────────────────────────

interface RootLocaleLayoutProps {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}

export default async function RootLocaleLayout({
  children,
  params,
}: RootLocaleLayoutProps) {
  const locale = (await params).locale as Locale;

  // Validate — fall back to default if somehow invalid
  const safeLocale = (routing.locales as readonly string[]).includes(locale)
    ? locale
    : routing.defaultLocale;

  const dir = localeDir(safeLocale);
  const messages = await getMessages();

  return (
    <html
      lang={safeLocale}
      dir={dir}
      className="h-full"
      suppressHydrationWarning
    >
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Kufi+Arabic:wght@300;400;500;600;700&display=swap"
          rel="stylesheet"
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.ico" />
      </head>

      <body
        className={
          safeLocale === "ar"
            ? "h-full antialiased font-arabic"
            : "h-full antialiased font-sans"
        }
      >
        <ClerkProvider>
          {/*
            AuthHydrator triggers the deferred Zustand persist rehydration.
            Must be OUTSIDE NextIntlClientProvider so it fires on every route.
          */}
          <AuthHydrator />
          <NextIntlClientProvider locale={safeLocale} messages={messages}>
            {children}
          </NextIntlClientProvider>
        </ClerkProvider>
      </body>
    </html>
  );
}
