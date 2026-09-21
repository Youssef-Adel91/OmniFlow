"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  Bell,
  Search,
  Globe,
  ChevronDown,
  Check,
} from "lucide-react";
import { UserButton, SignedIn, SignedOut, SignInButton } from "@clerk/nextjs";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { cn } from "@/lib/utils";
import { useTenantStore, useUnreadCount } from "@/store/tenantStore";
import { localeMeta, type Locale } from "@/i18n/config";

interface TopNavProps {
  locale: Locale;
}

export function TopNav({ locale }: TopNavProps) {
  const t          = useTranslations("header");
  const router     = useRouter();
  const unread     = useUnreadCount();
  const setLocale  = useTenantStore((s) => s.setLocale);

  // NOTE: sign-out is handled by Clerk's <UserButton /> below (and by the
  // Sidebar's logout button) — there is no local session to clear.

  const handleLocaleSwitch = (newLocale: Locale) => {
    setLocale(newLocale);
    // Swap locale prefix in current URL
    const currentPath = window.location.pathname;
    const newPath = currentPath.replace(`/${locale}`, `/${newLocale}`);
    router.push(newPath);
  };

  return (
    <header className="fixed top-0 inset-x-0 z-20 h-16 bg-[var(--header)] border-b border-[var(--border)] flex items-center px-4 gap-4 shadow-card">

      {/* ── Search bar ──────────────────────────────────────────────────── */}
      <div className="flex-1 max-w-md">
        <div className="relative">
          <Search className={cn(
            "absolute top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--muted-foreground)]",
            locale === "ar" ? "right-3" : "left-3"
          )} />
          <input
            type="text"
            placeholder={t("search")}
            className={cn(
              "input text-sm h-9",
              locale === "ar" ? "pr-9 text-right" : "pl-9"
            )}
            dir={locale === "ar" ? "rtl" : "ltr"}
          />
        </div>
      </div>

      {/* ── Right controls ───────────────────────────────────────────────── */}
      <div className="flex items-center gap-1 ms-auto">

        {/* Language Switcher */}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button
              className={cn(
                "btn btn-ghost h-9 px-2.5 text-sm gap-1.5",
                "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              )}
              aria-label={t("language")}
            >
              <Globe className="w-4 h-4" />
              <span className="font-medium text-xs hidden sm:block">
                {localeMeta[locale].nativeLabel}
              </span>
              <ChevronDown className="w-3 h-3 opacity-60" />
            </button>
          </DropdownMenu.Trigger>

          <DropdownMenu.Portal>
            <DropdownMenu.Content
              className={cn(
                "z-50 min-w-[140px] overflow-hidden rounded-[var(--radius)]",
                "bg-[var(--card)] border border-[var(--border)] shadow-card-md",
                "animate-fade-in p-1"
              )}
              align="end"
              sideOffset={8}
            >
              {(["ar", "en"] as Locale[]).map((loc) => {
                const meta = localeMeta[loc];
                return (
                  <DropdownMenu.Item
                    key={loc}
                    onSelect={() => handleLocaleSwitch(loc)}
                    className={cn(
                      "flex items-center gap-2.5 px-3 py-2 rounded-[var(--radius-sm)]",
                      "text-sm cursor-pointer outline-none",
                      "hover:bg-[var(--input)] transition-colors duration-100",
                      locale === loc && "text-[var(--accent)] font-medium"
                    )}
                  >
                    <span>{meta.flag}</span>
                    <span className="flex-1">{meta.nativeLabel}</span>
                    {locale === loc && <Check className="w-3.5 h-3.5 text-[var(--accent)]" />}
                  </DropdownMenu.Item>
                );
              })}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>

        {/* Notification Bell */}
        <button
          className="relative btn btn-ghost h-9 w-9 p-0"
          aria-label={t("notifications")}
        >
          <Bell className="w-4.5 h-4.5 text-[var(--muted-foreground)]" />
          {unread > 0 && (
            <span className={cn(
              "absolute top-1 w-2 h-2 rounded-full bg-gold-500 border-2 border-[var(--header)]",
              locale === "ar" ? "left-1" : "right-1"
            )} />
          )}
        </button>

        {/* Profile Dropdown */}
        <SignedIn>
          <UserButton />
        </SignedIn>
        <SignedOut>
          <SignInButton>
            <button className="btn bg-[var(--accent)] text-white hover:bg-[var(--accent)]/90 h-9 px-4 rounded-[var(--radius-sm)] text-sm font-medium transition-colors">
              تسجيل الدخول
            </button>
          </SignInButton>
        </SignedOut>
      </div>
    </header>
  );
}
