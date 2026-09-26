"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  LayoutDashboard,
  MessageSquare,
  Building2,
  Users,
  Megaphone,
  FileText,
  BookOpen,
  Settings,
  HelpCircle,
  ChevronLeft,
  ChevronRight,
  Zap,
  LogOut,
  Loader2,
} from "lucide-react";
import { useClerk, useUser } from "@clerk/nextjs";
import { cn } from "@/lib/utils";
import { useSidebar, useUnreadCount, useTenant, useTenantStore } from "@/store/tenantStore";
import type { Locale } from "@/i18n/config";

// ── Nav item definition ───────────────────────────────────────────────────────

interface NavItem {
  key:   string;
  href:  string;
  icon:  React.ElementType;
  badge?: "unread" | "beta";
}

const PRIMARY_NAV: NavItem[] = [
  { key: "overview",   href: "/dashboard",    icon: LayoutDashboard  },
  { key: "inbox",      href: "/inbox",        icon: MessageSquare, badge: "unread" },
  { key: "properties", href: "/properties",   icon: Building2        },
  { key: "customers",  href: "/customers",    icon: Users            },
  { key: "knowledge",  href: "/knowledge",    icon: BookOpen         },
  { key: "broadcasts", href: "/broadcasts",   icon: Megaphone, badge: "beta" },
  { key: "reports",    href: "/reports",      icon: FileText         },
];

const SECONDARY_NAV: NavItem[] = [
  { key: "settings", href: "/settings", icon: Settings },
  { key: "support",  href: "/support",  icon: HelpCircle },
];

// ── Component ─────────────────────────────────────────────────────────────────

interface SidebarProps {
  locale: Locale;
}

export function Sidebar({ locale }: SidebarProps) {
  const t          = useTranslations("nav");
  const pathname   = usePathname();
  const { collapsed, toggle } = useSidebar();
  const unread     = useUnreadCount();
  const tenant     = useTenant();
  const isRTL      = locale === "ar";
  // ── Auth: Clerk is the single source of truth ────────────────────────────
  const { signOut }  = useClerk();
  const { user }     = useUser();
  const resetTenant  = useTenantStore((s) => s.resetTenantState);
  const [loggingOut, setLoggingOut] = useState(false);

  const displayName = user?.fullName ?? user?.username ?? "";
  const displayEmail = user?.primaryEmailAddress?.emailAddress ?? "";

  // Strip locale prefix for active matching
  const cleanPath  = pathname.replace(`/${locale}`, "") || "/";

  const isActive = (href: string) =>
    href === "/"
      ? cleanPath === "/" || cleanPath === "/dashboard"
      : cleanPath.startsWith(href);

  return (
    <aside
      className={cn(
        "fixed inset-y-0 z-30 flex flex-col bg-[var(--sidebar)] transition-all duration-300 ease-in-out",
        collapsed ? "w-[4.5rem]" : "w-64",
        isRTL ? "right-0 border-l border-l-navy-700" : "left-0 border-r border-r-navy-700"
      )}
    >
      {/* ── Logo / Brand ────────────────────────────────────────────────── */}
      <div className={cn(
        "flex items-center gap-3 px-4 h-16 border-b border-navy-700 shrink-0",
        collapsed && "justify-center px-0"
      )}>
        {/* Logo mark */}
        <div className="relative shrink-0">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-gold-400 to-gold-600 flex items-center justify-center shadow-gold">
            <Zap className="w-5 h-5 text-navy-900" strokeWidth={2.5} />
          </div>
          {/* Online indicator */}
          <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-success border-2 border-navy-800" />
        </div>

        {!collapsed && (
          <div className="min-w-0 animate-fade-in">
            <p className="text-sm font-bold text-cream-200 tracking-wide truncate">
              OmniFlow <span className="text-gradient-gold">AI</span>
            </p>
            {tenant && (
              <p className="text-2xs text-navy-300 truncate mt-0.5">
                {tenant.businessName}
              </p>
            )}
          </div>
        )}
      </div>

      {/* ── Primary Navigation ──────────────────────────────────────────── */}
      <nav className="flex-1 overflow-y-auto scrollbar-hidden sidebar-scroll px-2 py-4 space-y-0.5">
        {PRIMARY_NAV.map((item) => {
          const active = isActive(item.href);
          const Icon   = item.icon;

          return (
            <Link
              key={item.key}
              href={`/${locale}${item.href}`}
              className={cn(
                "nav-item group relative",
                active && "nav-item-active",
                collapsed && "justify-center px-0 py-3"
              )}
              title={collapsed ? t(item.key as any) : undefined}
            >
              {/* Icon */}
              <Icon
                className={cn(
                  "shrink-0 transition-colors",
                  collapsed ? "w-5 h-5" : "w-4.5 h-4.5",
                  active ? "text-gold-400" : "text-navy-300 group-hover:text-cream-200"
                )}
                strokeWidth={active ? 2.5 : 2}
              />

              {/* Label */}
              {!collapsed && (
                <span className="flex-1 truncate animate-fade-in">
                  {t(item.key as any)}
                </span>
              )}

              {/* Badge */}
              {item.badge === "unread" && unread > 0 && (
                <span className={cn(
                  "shrink-0 rounded-full text-2xs font-bold leading-none px-1.5 py-0.5",
                  "bg-gold-500 text-navy-900",
                  collapsed && "absolute top-1 right-1 min-w-[1rem] text-center"
                )}>
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
              {item.badge === "beta" && !collapsed && (
                <span className="badge badge-gold text-2xs py-0 px-1.5">
                  Beta
                </span>
              )}

              {/* Tooltip for collapsed mode */}
              {collapsed && (
                <div className={cn(
                  "absolute z-50 hidden group-hover:flex",
                  "items-center px-3 py-1.5 rounded-[var(--radius-sm)]",
                  "bg-navy-900 text-cream-100 text-xs whitespace-nowrap shadow-lg",
                  "pointer-events-none",
                  isRTL ? "right-full mr-3" : "left-full ml-3"
                )}>
                  {t(item.key as any)}
                </div>
              )}
            </Link>
          );
        })}

        {/* Divider */}
        <div className="my-3 border-t border-navy-700" />

        {/* Secondary nav */}
        {SECONDARY_NAV.map((item) => {
          const active = isActive(item.href);
          const Icon   = item.icon;

          return (
            <Link
              key={item.key}
              href={`/${locale}${item.href}`}
              className={cn(
                "nav-item group relative",
                active && "nav-item-active",
                collapsed && "justify-center px-0 py-3"
              )}
              title={collapsed ? t(item.key as any) : undefined}
            >
              <Icon
                className={cn(
                  "shrink-0 transition-colors",
                  collapsed ? "w-5 h-5" : "w-4.5 h-4.5",
                  active ? "text-gold-400" : "text-navy-300 group-hover:text-cream-200"
                )}
                strokeWidth={active ? 2.5 : 2}
              />
              {!collapsed && (
                <span className="flex-1 truncate animate-fade-in">
                  {t(item.key as any)}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <div className="shrink-0 px-2 pb-4 space-y-1">

        {/* User card — data comes from Clerk */}
        {!collapsed && (displayName || displayEmail) && (
          <div className="mx-1 px-3 py-2.5 rounded-[var(--radius-sm)] bg-navy-700/60 mb-2">
            {displayName && (
              <p className="text-xs font-semibold text-cream-200 truncate">{displayName}</p>
            )}
            {displayEmail && (
              <p className="text-2xs text-navy-300 truncate mt-0.5">{displayEmail}</p>
            )}
          </div>
        )}

        {/* Logout button — Clerk sign-out (clears the Clerk session cookie) */}
        <button
          id="sidebar-logout-btn"
          onClick={async () => {
            setLoggingOut(true);
            try {
              // Drop cached tenant metadata before the session goes away
              resetTenant();
              await signOut({ redirectUrl: `/${locale}/sign-in` });
            } finally {
              setLoggingOut(false);
            }
          }}
          disabled={loggingOut}
          aria-label={isRTL ? "تسجيل الخروج" : "Sign out"}
          className={cn(
            "w-full flex items-center gap-2 px-3 py-2 rounded-[var(--radius-sm)]",
            "text-navy-300 hover:text-danger hover:bg-danger/10",
            "transition-colors duration-150 text-xs font-medium",
            "disabled:opacity-50 disabled:cursor-not-allowed",
            collapsed && "justify-center"
          )}
          title={collapsed ? (isRTL ? "تسجيل الخروج" : "Sign out") : undefined}
        >
          {loggingOut
            ? <Loader2 className="w-4 h-4 animate-spin" />
            : <LogOut className="w-4 h-4 shrink-0" />
          }
          {!collapsed && (
            <span className="animate-fade-in">
              {isRTL ? "تسجيل الخروج" : "Sign out"}
            </span>
          )}
        </button>

        {/* Collapse toggle */}
        <button
          onClick={toggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={cn(
            "w-full flex items-center gap-2 px-3 py-2 rounded-[var(--radius-sm)]",
            "text-navy-300 hover:text-cream-100 hover:bg-navy-700",
            "transition-colors duration-150 text-xs font-medium",
            collapsed && "justify-center"
          )}
        >
          {isRTL ? (
            collapsed ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />
          ) : (
            collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />
          )}
          {!collapsed && <span className="animate-fade-in">طيّ القائمة</span>}
        </button>

        {/* Tier badge */}
        {!collapsed && (
          <div className="mt-2 mx-1 px-3 py-2 rounded-[var(--radius-sm)] bg-navy-700 animate-fade-in">
            <p className="text-2xs text-navy-300">الباقة الحالية</p>
            <p className="text-xs font-semibold text-gradient-gold capitalize mt-0.5">
              {tenant?.tier ?? "professional"}
            </p>
          </div>
        )}
      </div>
    </aside>
  );
}
