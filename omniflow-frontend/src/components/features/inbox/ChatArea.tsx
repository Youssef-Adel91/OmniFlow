"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { useActiveMessages, useActiveConversation, useHasMoreMessages } from "@/store/inboxStore";
import { useInboxStore } from "@/store/inboxStore";
import type { Message } from "@/store/inboxStore";
import {
  Zap,
  User,
  Headphones,
  Check,
  CheckCheck,
  Clock,
  AlertCircle,
  Bot,
  MessageSquare,
} from "lucide-react";

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Delivery-status ticks for OUR outbound messages (item 12), driven by the
 * real `deliveryStatus` field that already flowed from the backend but was
 * never rendered — DELIVERED/READ will simply start appearing once the Meta
 * status webhook (a separate, unbuilt piece of infra) starts setting them;
 * this component doesn't need to change for that.
 */
function DeliveryStatusIcon({ status }: { status?: string }) {
  switch (status) {
    case "PENDING":
    case "QUEUED":
      return <Clock className="w-3 h-3 text-[var(--muted-foreground)]" />;
    case "SENT":
      return <Check className="w-3 h-3 text-[var(--muted-foreground)]" />;
    case "DELIVERED":
      return <CheckCheck className="w-3 h-3 text-[var(--muted-foreground)]" />;
    case "READ":
      return <CheckCheck className="w-3 h-3 text-info" />;
    case "FAILED":
      return <AlertCircle className="w-3 h-3 text-danger" />;
    default:
      return null;
  }
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("ar-SA", {
    hour:   "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

// ── Audio Player ──────────────────────────────────────────────────────────────

/**
 * Sleek HTML5 audio player styled for the dark luxury inbox theme.
 *
 * Wraps the native <audio controls> with:
 *   - A "🎙 رسالة صوتية" pill header so users know it's a voice note
 *   - A transcript text block below (collapsible if long)
 *   - Gold accent border to distinguish AI voice notes visually
 *
 * The <audio> element uses the browser's native controls which honour
 * the system media session and work correctly with WhatsApp-sourced MP3s.
 */
function AudioPlayer({
  src,
  transcript,
  isCustomer,
}: {
  src: string;
  transcript?: string;
  isCustomer: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-xl p-3 mt-1",
        isCustomer
          ? "bg-cream-200/60 border border-cream-400/40"
          : "bg-navy-900/60 border border-gold-500/30"
      )}
    >
      {/* Voice note pill header */}
      <div className="flex items-center gap-1.5">
        <span className="text-base" aria-hidden="true">🎙</span>
        <span
          className={cn(
            "text-2xs font-semibold tracking-wide uppercase",
            isCustomer ? "text-navy-600" : "text-gold-400"
          )}
        >
          رسالة صوتية
        </span>
      </div>

      {/* Native HTML5 audio player */}
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio
        controls
        src={src}
        preload="metadata"
        className="w-full max-w-[280px]"
        style={{
          // Override browser defaults to fit the luxury theme
          accentColor: isCustomer ? "#2d4d8c" : "#C9A84C",
          colorScheme: isCustomer ? "light" : "dark",
          borderRadius: "8px",
          height: "36px",
        }}
        aria-label="تشغيل الرسالة الصوتية"
      />

      {/* Transcript text — show below the player if present */}
      {transcript && transcript.trim() && (
        <p
          className={cn(
            "text-xs leading-relaxed mt-1 opacity-90 font-medium",
            isCustomer ? "text-navy-800" : "text-cream-100"
          )}
        >
          {transcript}
        </p>
      )}
    </div>
  );
}

// ── Message bubble ────────────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: Message }) {
  const isCustomer = msg.senderType === "customer";
  const isAI       = msg.senderType === "ai_bot";
  const isHuman    = msg.senderType === "human_agent";
  const isAudio    = msg.messageType === "audio" && Boolean(msg.mediaUrl);

  return (
    <div className={cn(
      "flex gap-2.5 animate-fade-in w-full",
      isCustomer ? "flex-row" : "flex-row-reverse"
    )}>
      {/* Avatar icon */}
      <div className={cn(
        "w-7 h-7 rounded-full shrink-0 mt-1 flex items-center justify-center",
        isCustomer && "bg-cream-300",
        isAI       && "bg-gradient-to-br from-navy-700 to-navy-900 shadow-sm",
        isHuman    && "bg-gold-500"
      )}>
        {isCustomer && <User     className="w-3.5 h-3.5 text-navy-500" />}
        {isAI       && <Zap      className="w-3.5 h-3.5 text-gold-400" strokeWidth={2.5} />}
        {isHuman    && <Headphones className="w-3.5 h-3.5 text-navy-900" />}
      </div>

      {/* Bubble */}
      <div className={cn(
        "max-w-[72%] flex flex-col gap-1",
        isCustomer ? "items-start" : "items-end"
      )}>
        {/* Sender label */}
        <span className="text-2xs text-[var(--muted-foreground)] px-1">
          {isCustomer && "العميل"}
          {isAI       && "OmniFlow AI"}
          {isHuman    && "الوكيل البشري"}
        </span>

        {/* Text bubble */}
        <div className={cn(
          "rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap",
          // Customer — cream/warm background, navy text, rounded end removed
          isCustomer && [
            "bg-cream-200 text-navy-900 border border-cream-400/40 shadow-sm",
            "rounded-ss-sm",   // remove top-start radius (top-right in RTL -> tail on right)
          ],
          // AI — deep navy with gold shimmer border
          isAI && [
            "bg-navy-800 text-cream-100",
            "rounded-se-sm",   // remove top-end radius (top-left in RTL -> tail on left)
            "border border-gold-500/20 shadow-sm shadow-gold-500/5",
            // Gold border intensified for voice-note messages
            isAudio && "border-gold-500/50 shadow-gold-500/10",
          ],
          // Human — solid navy (distinguishable from AI by slightly lighter shade)
          isHuman && [
            "bg-navy-700 text-cream-100 border border-navy-500/30 shadow-sm",
            "rounded-se-sm",
          ]
        )}>
          {/* AI badge inside bubble */}
          {isAI && (
            <div className="flex items-center gap-1.5 mb-1.5 opacity-60">
              <Bot className="w-3 h-3 text-gold-400" />
              <span className="text-2xs text-gold-300 font-medium">
                {msg.tier ?? "AI"} · {msg.model ?? ""}
              </span>
            </div>
          )}

          {/* ── Audio player (voice note) ────────────────────────────── */}
          {isAudio && msg.mediaUrl ? (
            <AudioPlayer
              src={msg.mediaUrl}
              transcript={msg.text}
              isCustomer={isCustomer}
            />
          ) : (
            // Plain text message
            msg.text
          )}
        </div>

        {/* Timestamp + read receipt */}
        <div className={cn(
          "flex items-center gap-1.5 px-1",
          isCustomer ? "flex-row" : "flex-row-reverse"
        )}>
          <span className="text-2xs text-[var(--muted-foreground)]">
            {formatTime(msg.createdAt)}
          </span>
          {isAI && msg.latencyMs && (
            <span className="text-2xs text-[var(--muted-foreground)] flex items-center gap-0.5">
              <Clock className="w-2.5 h-2.5" />
              {msg.latencyMs}ms
            </span>
          )}
          {!isCustomer && <DeliveryStatusIcon status={msg.deliveryStatus} />}
        </div>
      </div>
    </div>
  );
}

// ── Typing indicator ──────────────────────────────────────────────────────────

function TypingIndicator() {
  return (
    <div className="flex gap-2.5 animate-fade-in w-full flex-row-reverse">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-navy-700 to-navy-900 flex items-center justify-center shrink-0 mt-1">
        <Zap className="w-3.5 h-3.5 text-gold-400" strokeWidth={2.5} />
      </div>
      <div className="max-w-[72%] flex flex-col gap-1 items-end">
        <div className="bg-navy-800 border border-gold-500/20 shadow-sm shadow-gold-500/5 rounded-2xl rounded-se-sm px-4 py-3 flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="w-1.5 h-1.5 rounded-full bg-gold-400 animate-bounce"
            style={{ animationDelay: `${i * 150}ms`, animationDuration: "0.8s" }}
          />
        ))}
      </div>
      </div>
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center p-8">
      <div className="w-16 h-16 rounded-full bg-cream-200 flex items-center justify-center mb-4">
        <MessageSquare className="w-8 h-8 text-cream-400" />
      </div>
      <h3 className="text-base font-semibold text-[var(--foreground)] mb-1">
        اختر محادثة
      </h3>
      <p className="text-sm text-[var(--muted-foreground)] max-w-xs">
        اختر محادثة من القائمة على اليمين للبدء في مراجعتها والرد عليها
      </p>
    </div>
  );
}

// ── Date separator ────────────────────────────────────────────────────────────

function DateSeparator({ date }: { date: string }) {
  return (
    <div className="flex items-center gap-3 my-4">
      <div className="flex-1 border-t border-[var(--border)]" />
      <span className="text-2xs text-[var(--muted-foreground)] px-2 py-0.5 bg-[var(--background)] rounded-full border border-[var(--border)]">
        {new Date(date).toLocaleDateString("ar-SA", { weekday: "long", day: "numeric", month: "long" })}
      </span>
      <div className="flex-1 border-t border-[var(--border)]" />
    </div>
  );
}

// ── Chat Area ─────────────────────────────────────────────────────────────────

export function ChatArea() {
  const messages         = useActiveMessages();
  const conversation     = useActiveConversation();
  const isTyping         = useInboxStore((s) => s.isTyping);
  const hasMoreMessages  = useHasMoreMessages();
  const isLoadingOlder   = useInboxStore((s) => s.isLoadingOlderMessages);
  const loadOlderMessages = useInboxStore((s) => s.loadOlderMessages);
  const bottomRef        = useRef<HTMLDivElement>(null);
  const scrollRef        = useRef<HTMLDivElement>(null);
  const prevOldestId     = useRef<string | null>(null);
  const pendingScrollAdjust = useRef(false);

  // Auto-scroll to bottom only when a message is appended at the end (normal
  // chat flow) — NOT when "load older" prepends messages at the start, which
  // would otherwise yank the view away from what the agent was reading.
  useEffect(() => {
    const oldestId = messages[0]?.id ?? null;
    if (pendingScrollAdjust.current) {
      // Restore scroll position after older messages were prepended, instead
      // of jumping to the bottom (see loadOlder() below).
      pendingScrollAdjust.current = false;
    } else if (oldestId === prevOldestId.current || prevOldestId.current === null) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
    prevOldestId.current = oldestId;
  }, [messages, isTyping]);

  async function loadOlder() {
    const el = scrollRef.current;
    const prevScrollHeight = el?.scrollHeight ?? 0;
    pendingScrollAdjust.current = true;
    await loadOlderMessages(conversation!.id);
    // Prepending changes scrollHeight; hold the viewport on the same
    // messages instead of snapping to the top or bottom.
    requestAnimationFrame(() => {
      if (el) el.scrollTop += el.scrollHeight - prevScrollHeight;
    });
  }

  if (!conversation) return <EmptyState />;

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-[var(--background)]">

      {/* ── Conversation header ─────────────────────────────────────────── */}
      <div className="shrink-0 flex items-center gap-3 px-5 h-14 border-b border-[var(--border)] bg-[var(--card)]">
        {/* Avatar */}
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-navy-600 to-navy-800 flex items-center justify-center shrink-0">
          <span className="text-xs font-bold text-cream-100">
            {conversation.customerName.split(" ").map((w) => w[0]).slice(0, 2).join("")}
          </span>
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-[var(--foreground)] truncate">
            {conversation.customerName}
          </p>
          <p className="text-2xs text-[var(--muted-foreground)]">
            {conversation.customerPhone}
          </p>
        </div>

        {/* Status chip */}
        <div className={cn(
          "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-2xs font-medium",
          conversation.isAiActive
            ? "bg-navy-100 text-navy-700"
            : "bg-warning/15 text-warning"
        )}>
          {conversation.isAiActive
            ? <><Zap className="w-3 h-3 text-gold-500" /> يُدار بالذكاء الاصطناعي</>
            : <><Headphones className="w-3 h-3" /> وكيل بشري</>
          }
        </div>
      </div>

      {/* ── Messages area ───────────────────────────────────────────────── */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-5 space-y-4 scrollbar-hidden" dir="rtl">

        {/* Load older messages (item 12) — a plain button rather than
            scroll-triggered infinite scroll: simpler, and scroll-position
            preservation is handled explicitly in loadOlder() above. */}
        {hasMoreMessages && (
          <div className="flex justify-center pb-2">
            <button
              onClick={loadOlder}
              disabled={isLoadingOlder}
              className="text-2xs text-[var(--muted-foreground)] hover:text-[var(--foreground)] px-3 py-1.5 rounded-full border border-[var(--border)] disabled:opacity-50"
            >
              {isLoadingOlder ? "جارٍ التحميل..." : "تحميل رسائل أقدم"}
            </button>
          </div>
        )}

        {/* Date separator at top */}
        {messages.length > 0 && (
          <DateSeparator date={messages[0].createdAt} />
        )}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} />
        ))}

        {/* Typing indicator */}
        {isTyping && <TypingIndicator />}

        {/* Scroll anchor */}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
