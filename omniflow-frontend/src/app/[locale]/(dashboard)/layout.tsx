/**
 * app/[locale]/(dashboard)/layout.tsx — Dashboard Shell Layout
 *
 * Wraps all authenticated dashboard pages with the sidebar + top navigation.
 * The <html>, <body>, and NextIntlClientProvider are provided by the parent
 * app/[locale]/layout.tsx — this layout only adds the dashboard chrome.
 */
import type { Metadata } from "next";
import { routing, type Locale } from "@/i18n/routing";
import { Sidebar }    from "@/components/layout/Sidebar";
import { TopNav }     from "@/components/layout/TopNav";
import { AuthSyncer } from "@/components/auth/AuthSyncer";
import { DashboardAuth } from "@/components/auth/DashboardAuth";
import { cn }         from "@/lib/utils";

// ── Metadata ──────────────────────────────────────────────────────────────────

export const metadata: Metadata = {
  title: {
    template: "%s | OmniFlow AI",
    default: "OmniFlow AI — لوحة التحكم",
  },
  description: "منصة إدارة العقارات والمحادثات بالذكاء الاصطناعي",
  robots: { index: false }, // B2B dashboard — don't index
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface DashboardLayoutProps {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}

// ── Layout ────────────────────────────────────────────────────────────────────

export default async function DashboardLayout({
  children,
  params,
}: DashboardLayoutProps) {
  const locale = (await params).locale as Locale;
  const safeLocale = (routing.locales as readonly string[]).includes(locale)
    ? locale
    : routing.defaultLocale;

  return (
    <DashboardAuth>
      <AuthSyncer />
      <DashboardShell locale={safeLocale}>{children}</DashboardShell>
    </DashboardAuth>
  );
}

// ── Shell (client boundary inside server layout) ──────────────────────────────

function DashboardShell({
  children,
  locale,
}: {
  children: React.ReactNode;
  locale: Locale;
}) {
  const isRTL = locale === "ar";

  return (
    <div className="flex h-full bg-[var(--background)]">
      {/* Fixed sidebar */}
      <Sidebar locale={locale} />

      {/* Main area — offset by sidebar width via padding */}
      <div
        className={cn(
          "flex-1 flex flex-col min-h-full transition-all duration-300",
          isRTL ? "pr-64 pl-0" : "pl-64 pr-0",
          "data-[collapsed=true]:ps-[4.5rem]"
        )}
      >
        {/* Fixed top navigation */}
        <TopNav locale={locale} />

        {/* Scrollable page content */}
        <main
          className="flex-1 overflow-y-auto mt-16 p-6 lg:p-8"
          id="main-content"
          role="main"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
