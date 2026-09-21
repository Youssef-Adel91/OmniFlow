"use client";

/**
 * app/[locale]/(dashboard)/inbox/page.tsx — Smart Inbox Page (Sprint 12)
 *
 * Sprint 12 additions over Sprint 11:
 *   1. `useEffect` triggers `loadConversations()` on mount.
 *   2. `useInboxStream()` opens the SSE connection to /api/v1/stream/dashboard
 *      and wires real-time events into the Zustand store automatically.
 *   3. Connection status banner shown if SSE is not open.
 *   4. Loading skeleton shown while conversations are fetching.
 *   5. Error banner shown if the initial fetch fails.
 *
 * The page is now a client component (`"use client"`) so it can run hooks.
 * Metadata is exported from a separate `metadata.ts` in the same directory
 * (Next.js 13+ allows a collocated metadata file alongside a client page).
 */

import { useEffect } from "react";
import { ConversationList } from "@/components/features/inbox/ConversationList";
import { ChatArea }         from "@/components/features/inbox/ChatArea";
import { ChatInput }        from "@/components/features/inbox/ChatInput";
import { CustomerContext }  from "@/components/features/inbox/CustomerContext";
import { useInboxStore, useInboxLoadingState } from "@/store/inboxStore";
import { useInboxStream }   from "@/hooks/useInboxStream";
import { Wifi, WifiOff, AlertTriangle, RefreshCw } from "lucide-react";

// ── SSE Status Banner ─────────────────────────────────────────────────────────

function StreamStatusBanner({
  status,
  reconnect,
}: {
  status: "connecting" | "open" | "closed" | "error";
  reconnect: () => void;
}) {
  if (status === "open") return null; // healthy — nothing to show

  const map = {
    connecting: {
      icon:    <Wifi className="w-3.5 h-3.5 animate-pulse" />,
      text:    "جارٍ الاتصال بالبث المباشر…",
      classes: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    },
    error: {
      icon:    <AlertTriangle className="w-3.5 h-3.5" />,
      text:    "خطأ في الاتصال — إعادة المحاولة…",
      classes: "bg-warning/10 text-warning border-warning/20",
    },
    closed: {
      icon:    <WifiOff className="w-3.5 h-3.5" />,
      text:    "انتهى البث المباشر",
      classes: "bg-error/10 text-error border-error/20",
    },
  } as const;

  const cfg = map[status as keyof typeof map];

  return (
    <div
      className={`
        shrink-0 flex items-center justify-between gap-2
        px-4 py-1.5 text-xs font-medium border-b
        ${cfg.classes}
      `}
    >
      <span className="flex items-center gap-1.5">
        {cfg.icon}
        {cfg.text}
      </span>
      {status === "closed" && (
        <button
          onClick={reconnect}
          className="flex items-center gap-1 hover:opacity-80 transition-opacity"
          aria-label="إعادة الاتصال"
        >
          <RefreshCw className="w-3 h-3" />
          إعادة الاتصال
        </button>
      )}
    </div>
  );
}

// ── Error Banner ──────────────────────────────────────────────────────────────

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="shrink-0 flex items-center justify-between gap-2 px-4 py-2 bg-error/10 text-error border-b border-error/20 text-xs font-medium">
      <span className="flex items-center gap-1.5">
        <AlertTriangle className="w-3.5 h-3.5" />
        {message}
      </span>
      <button
        onClick={onRetry}
        className="flex items-center gap-1 hover:opacity-80 transition-opacity"
      >
        <RefreshCw className="w-3 h-3" />
        إعادة المحاولة
      </button>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function InboxPage() {
  const loadConversations = useInboxStore((s) => s.loadConversations);
  const { conversationsError, isLoadingConversations } = useInboxLoadingState();

  // ── 1. Initial data fetch ────────────────────────────────────────────────
  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  // ── 2. Live SSE stream ───────────────────────────────────────────────────
  const { status: streamStatus, reconnect } = useInboxStream();

  return (
    /*
     * Full-height 3-pane layout.
     *
     * Height = 100vh − 64px header − 0px (no page padding in inbox).
     * The parent layout sets mt-16 for the header, so we use h-[calc(100vh-4rem)].
     * We also negate the parent's p-6/p-8 by extending with negative margins.
     *
     * Grid columns:
     *   Left (RTL right)  = Conversation list  (w-72 = 288px, fixed)
     *   Center            = Chat area           (flex-1, grows)
     *   Right (RTL left)  = Customer context    (w-72 = 288px, fixed)
     */
    <div
      className="
        flex flex-col h-[calc(100vh-4rem)]
        -mx-6 lg:-mx-8 -mt-6 lg:-mt-8
        overflow-hidden
      "
    >
      {/* ── Status banners (SSE + API errors) ─────────────────────────────── */}
      <StreamStatusBanner status={streamStatus} reconnect={reconnect} />
      {conversationsError && !isLoadingConversations && (
        <ErrorBanner
          message={`تعذّر تحميل المحادثات: ${conversationsError}`}
          onRetry={() => void loadConversations()}
        />
      )}

      {/* ── 3-pane inbox layout ────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Pane 1: Conversation List (start side) */}
        <div className="w-72 shrink-0 flex flex-col overflow-hidden">
          <ConversationList />
        </div>

        {/* Pane 2: Chat Area + Input */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          <ChatArea />
          <ChatInput />
        </div>

        {/* Pane 3: Customer Context (end side, hidden on small screens) */}
        <div className="w-72 shrink-0 overflow-hidden hidden xl:flex flex-col">
          <CustomerContext />
        </div>
      </div>
    </div>
  );
}

/*
 * ── Test Bot Simulator (REMOVED) ─────────────────────────────────────────────
 *
 * The floating "محاكي البوت" widget was a demo-only tool that POSTed to a
 * hard-coded `http://localhost:8000/api/v1/chat/simulate-mock` endpoint. That
 * URL is unreachable (and insecure: mixed-content over HTTPS) in production and
 * bypassed the authenticated apiClient entirely, so the widget and its trigger
 * button were removed from this production page. Use the real inbox against a
 * staging backend for manual testing instead.
 */
