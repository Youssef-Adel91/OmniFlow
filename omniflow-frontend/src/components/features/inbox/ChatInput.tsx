"use client";

import { useState, useRef, useCallback } from "react";
import { cn } from "@/lib/utils";
import {
  useInboxStore,
  useActiveConversation,
} from "@/store/inboxStore";
import {
  Send,
  Zap,
  UserCheck,
  Paperclip,
  Smile,
  ToggleLeft,
  ToggleRight,
  AlertTriangle,
} from "lucide-react";

// ── Takeover toggle ───────────────────────────────────────────────────────────

function TakeoverToggle() {
  const conversation = useActiveConversation();
  // Real API thunks (POST /conversations/{id}/takeover | /return-to-ai).
  // They already apply an optimistic local update and roll it back on failure.
  const requestTakeOver   = useInboxStore((s) => s.requestTakeOver);
  const requestReturnToAI = useInboxStore((s) => s.requestReturnToAI);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  const handleToggle = useCallback(async () => {
    if (!conversation || switching) return;
    const goingToAI = !conversation.isAiActive;
    setSwitching(true);
    setSwitchError(null);
    try {
      if (goingToAI) {
        await requestReturnToAI(conversation.id);
      } else {
        await requestTakeOver(conversation.id);
      }
    } catch {
      setSwitchError(
        goingToAI ? "تعذّر إعادة المحادثة للـ AI" : "تعذّر تسلّم المحادثة",
      );
    } finally {
      setSwitching(false);
    }
  }, [conversation, switching, requestTakeOver, requestReturnToAI]);

  if (!conversation) return null;

  const isHuman = !conversation.isAiActive;

  return (
    <div className={cn(
      "flex items-center gap-2.5 px-4 py-2.5 border-b border-[var(--border)]",
      "transition-colors duration-200",
      isHuman
        ? "bg-warning/8 border-warning/30"
        : "bg-[var(--card)]"
    )}>
      {/* Icon */}
      <div className={cn(
        "w-7 h-7 rounded-full flex items-center justify-center shrink-0",
        isHuman ? "bg-warning/20" : "bg-navy-100"
      )}>
        {isHuman
          ? <UserCheck className="w-3.5 h-3.5 text-warning" />
          : <Zap       className="w-3.5 h-3.5 text-gold-500" strokeWidth={2.5} />
        }
      </div>

      {/* Label */}
      <div className="flex-1 min-w-0">
        <p className={cn(
          "text-xs font-semibold leading-tight",
          isHuman ? "text-warning" : "text-navy-700"
        )}>
          {isHuman ? "أنت تتحكم في المحادثة" : "AI يتحكم في المحادثة"}
        </p>
        <p className="text-2xs text-[var(--muted-foreground)] leading-tight mt-0.5">
          {switchError
            ? switchError
            : isHuman
              ? "ردودك تُرسل مباشرة للعميل"
              : "اضغط 'تسلّم' للرد شخصياً"
          }
        </p>
      </div>

      {/* Toggle button */}
      <button
        onClick={handleToggle}
        disabled={switching}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold",
          "transition-all duration-200 active:scale-95 shrink-0",
          "disabled:opacity-60 disabled:cursor-not-allowed",
          isHuman
            ? "bg-warning/20 text-warning hover:bg-warning/30"
            : "bg-navy-800 text-cream-100 hover:bg-navy-700"
        )}
      >
        {isHuman ? (
          <>
            <ToggleRight className="w-3.5 h-3.5" />
            أعد للـ AI
          </>
        ) : (
          <>
            <ToggleLeft className="w-3.5 h-3.5" />
            تسلّم
          </>
        )}
      </button>
    </div>
  );
}

// ── Escalation warning banner ─────────────────────────────────────────────────

function EscalationBanner() {
  const conversation = useActiveConversation();
  if (conversation?.status !== "ESCALATED") return null;

  return (
    <div className="flex items-start gap-2.5 px-4 py-2.5 bg-danger/8 border-b border-danger/20">
      <AlertTriangle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
      <div>
        <p className="text-xs font-semibold text-danger">طلب تصعيد بشري</p>
        <p className="text-2xs text-[var(--muted-foreground)]">
          العميل طلب التحدث مع وكيل بشري. يُرجى التسلّم من الـ AI.
        </p>
      </div>
    </div>
  );
}

// ── Main ChatInput ────────────────────────────────────────────────────────────

export function ChatInput() {
  const [text, setText]         = useState("");
  const [sending, setSending]   = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const textareaRef             = useRef<HTMLTextAreaElement>(null);

  const conversation    = useActiveConversation();
  // Real API thunk → POST /conversations/{id}/messages (optimistic + rollback)
  const sendAgentMessage = useInboxStore((s) => s.sendAgentMessage);

  const isHuman       = conversation ? !conversation.isAiActive : false;
  const canSend       = text.trim().length > 0 && !sending && !!conversation;

  // Auto-resize textarea
  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`;
    }
  };

  // Send message — persists through the backend (no mocked AI reply anymore;
  // genuine AI replies arrive over the SSE stream in useInboxStream).
  const handleSend = useCallback(async () => {
    if (!canSend || !conversation) return;
    const trimmed = text.trim();
    setSending(true);
    setSendError(null);
    setText("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    try {
      await sendAgentMessage(conversation.id, trimmed);
    } catch {
      // Restore the draft so the agent doesn't lose what they typed
      setText(trimmed);
      setSendError("تعذّر إرسال الرسالة. تحقّق من الاتصال وحاول مجدداً.");
    } finally {
      setSending(false);
    }
  }, [canSend, text, conversation, sendAgentMessage]);

  // Cmd/Ctrl+Enter to send
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSend();
    }
  };

  if (!conversation) return null;

  return (
    <div className="shrink-0 bg-[var(--card)] border-t border-[var(--border)]">
      <EscalationBanner />
      <TakeoverToggle />

      {/* Input row */}
      <div className="flex items-end gap-2 px-4 py-3">
        {/* Attachment */}
        <button
          className="btn-ghost h-9 w-9 p-0 shrink-0 mb-0.5"
          aria-label="إرفاق ملف"
        >
          <Paperclip className="w-4 h-4 text-[var(--muted-foreground)]" />
        </button>

        {/* Text area */}
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder={
              isHuman
                ? "اكتب رداً كوكيل بشري... (Ctrl+Enter للإرسال)"
                : "تسلّم المحادثة أولاً للرد كوكيل بشري..."
            }
            rows={1}
            dir="rtl"
            className={cn(
              "w-full resize-none rounded-[var(--radius)] border px-3.5 py-2.5",
              "text-sm text-[var(--foreground)] placeholder:text-[var(--muted-foreground)]",
              "bg-[var(--input)] focus:outline-none focus:ring-2 focus:ring-[var(--ring)]",
              "transition-all duration-150 scrollbar-hidden leading-relaxed",
              "max-h-[120px]",
              isHuman
                ? "border-warning/40 focus:ring-warning/40"
                : "border-[var(--border)] focus:ring-gold-400/40"
            )}
          />

          {/* Emoji placeholder */}
          <button
            className="absolute top-2.5 start-3 text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition-colors"
            aria-label="إيموجي"
          >
            <Smile className="w-4 h-4" />
          </button>
        </div>

        {/* Send button */}
        <button
          onClick={handleSend}
          disabled={!canSend}
          className={cn(
            "h-10 w-10 rounded-full flex items-center justify-center shrink-0",
            "transition-all duration-200 active:scale-95",
            canSend
              ? isHuman
                ? "bg-warning text-white shadow-sm hover:brightness-105"
                : "bg-navy-800 text-gold-400 shadow-sm hover:bg-navy-700"
              : "bg-cream-200 text-cream-400 cursor-not-allowed"
          )}
          aria-label="إرسال"
        >
          <Send className={cn(
            "w-4 h-4 transition-transform",
            canSend && "group-hover:translate-x-0.5"
          )} />
        </button>
      </div>

      {/* Send error */}
      {sendError && (
        <p role="alert" className="text-center text-2xs text-danger pb-1 px-4">
          {sendError}
        </p>
      )}

      {/* Footer hint */}
      <p className="text-center text-2xs text-[var(--muted-foreground)] pb-2">
        {isHuman
          ? "🟡 أنت في وضع الوكيل البشري"
          : "⚡ AI يعالج المحادثة تلقائياً"
        }
      </p>
    </div>
  );
}
