/**
 * app/[locale]/(dashboard)/inbox/layout.tsx
 *
 * Provides the <title> and <meta name="description"> for the Smart Inbox route.
 *
 * WHY THIS FILE EXISTS:
 *   Next.js 13+ does not allow `export const metadata` inside a file marked
 *   `"use client"`. Since inbox/page.tsx must be a client component (it runs
 *   hooks — useEffect, useInboxStream), the metadata object lives here in a
 *   server-side layout instead.
 *
 *   This layout is a thin pass-through — it renders `{children}` with no
 *   extra DOM wrapper so the parent (dashboard) layout's padding / grid is
 *   completely unaffected.
 */
import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title:       "Smart Inbox | OmniFlow AI — صندوق الرسائل الذكي",
  description:
    "Manage and respond to real-time customer conversations across WhatsApp and " +
    "all channels. AI-powered inbox with live SSE updates.",
  robots: { index: false, follow: false }, // dashboard pages are private
};

export default function InboxLayout({ children }: { children: ReactNode }) {
  // Thin pass-through — no wrapper element to avoid layout shift.
  return <>{children}</>;
}
