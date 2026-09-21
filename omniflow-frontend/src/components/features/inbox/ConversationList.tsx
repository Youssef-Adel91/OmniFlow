"use client";

/**
 * ConversationList.tsx — Unified Smart Inbox Sidebar (Sprint 13)
 *
 * Sprint 13 additions:
 *   1. Control Bar with Channel Isolation Dropdown Filter.
 *   2. Sorting Toggle: "الأحدث (Recent)" / "الأقرب للإغلاق (Hot Leads 🔥)".
 *   3. Conversation cards display channel icon + Hot Lead badge (score > 75).
 *   4. Strict RTL layout support (dir="rtl", logical CSS props).
 */

import { useMemo, useState, useRef, useEffect } from "react";
import { cn } from "@/lib/utils";
import {
  useInboxStore,
  useFilteredConversations,
  useInboxLoadingState,
  useInboxFilters,
  type Conversation,
  type ConversationStatus,
} from "@/store/inboxStore";
import type { ChannelFilter, SortByOption } from "@/lib/api/inbox";
import {
  MessageSquare,
  Bot,
  UserCheck,
  AlertTriangle,
  Moon,
  Search,
  ChevronDown,
  Flame,
  Clock,
  Globe,
  CheckCheck,
} from "lucide-react";

// ── Channel definitions ───────────────────────────────────────────────────────

// SVG icons for social channels (inline, no extra dep)
const WhatsAppIcon = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/>
    <path d="M12 0C5.373 0 0 5.373 0 12c0 2.123.554 4.118 1.522 5.847L0 24l6.341-1.499A11.96 11.96 0 0012 24c6.627 0 12-5.373 12-12S18.627 0 12 0zm0 21.818a9.797 9.797 0 01-5.017-1.377l-.358-.214-3.766.89.938-3.664-.234-.374A9.787 9.787 0 012.182 12C2.182 6.57 6.569 2.182 12 2.182 17.43 2.182 21.818 6.57 21.818 12c0 5.43-4.388 9.818-9.818 9.818z"/>
  </svg>
);

const InstagramIcon = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
    <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/>
  </svg>
);

const TikTokIcon = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
    <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/>
  </svg>
);

const XIcon = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
    <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.744l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
  </svg>
);

// ── Channel config ─────────────────────────────────────────────────────────────

interface ChannelConfig {
  value:  ChannelFilter;
  label:  string;
  icon:   React.ElementType | (({ className }: { className?: string }) => React.ReactElement);
  color:  string;
  bg:     string;
}

const CHANNEL_CONFIGS: ChannelConfig[] = [
  { value: "ALL",       label: "الكل",      icon: MessageSquare, color: "text-[var(--muted-foreground)]", bg: "bg-cream-300" },
  { value: "whatsapp",  label: "WhatsApp",  icon: WhatsAppIcon,  color: "text-emerald-600",               bg: "bg-emerald-50" },
  { value: "instagram", label: "Instagram", icon: InstagramIcon, color: "text-pink-600",                  bg: "bg-pink-50"    },
  { value: "tiktok",    label: "TikTok",    icon: TikTokIcon,    color: "text-slate-900",                  bg: "bg-slate-100"  },
  { value: "x",         label: "X",         icon: XIcon,         color: "text-slate-800",                  bg: "bg-slate-50"   },
  { value: "web",       label: "Web",       icon: Globe,         color: "text-blue-600",                  bg: "bg-blue-50"    },
];

const CHANNEL_MAP = Object.fromEntries(CHANNEL_CONFIGS.map((c) => [c.value, c])) as Record<ChannelFilter, ChannelConfig>;

// ── Sort options ──────────────────────────────────────────────────────────────

const SORT_OPTIONS: { value: SortByOption; label: string; icon: React.ElementType }[] = [
  { value: "recent",    label: "الأحدث",                icon: Clock },
  { value: "hot_leads", label: "الأقرب للإغلاق 🔥",     icon: Flame },
];

// ── Status config ─────────────────────────────────────────────────────────────

const STATUS_FILTERS: { value: ConversationStatus | "ALL"; label: string }[] = [
  { value: "ALL",          label: "الكل"       },
  { value: "BOT_ACTIVE",   label: "AI نشط"     },
  { value: "ESCALATED",    label: "تصعيد"      },
  { value: "AGENT_ACTIVE", label: "وكيل بشري"  },
  { value: "DORMANT",      label: "غير نشط"    },
];

const STATUS_META: Record<ConversationStatus, { icon: React.ElementType; label: string; className: string }> = {
  BOT_ACTIVE:   { icon: Bot,           label: "AI نشط",    className: "badge-navy"                                        },
  AGENT_ACTIVE: { icon: UserCheck,     label: "وكيل بشري", className: "bg-warning/15 text-warning ring-warning/30 badge"  },
  ESCALATED:    { icon: AlertTriangle, label: "تصعيد",     className: "badge-danger"                                      },
  DORMANT:      { icon: Moon,          label: "غير نشط",   className: "badge bg-cream-300 text-navy-400 ring-cream-400"   },
  CLOSED:       { icon: CheckCheck,    label: "مغلق",      className: "badge bg-cream-300 text-navy-400 ring-cream-400"   },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60)    return "الآن";
  if (diff < 3600)  return `${Math.floor(diff / 60)}د`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}س`;
  return `${Math.floor(diff / 86400)}ي`;
}

function initials(name: string): string {
  return name.split(" ").map((w) => w[0]).slice(0, 2).join("");
}

const AVATAR_COLORS = [
  "from-blue-500 to-blue-700",
  "from-emerald-500 to-teal-700",
  "from-violet-500 to-purple-700",
  "from-orange-400 to-rose-600",
  "from-cyan-500 to-blue-600",
];
function avatarColor(phone: string): string {
  const hash = phone.split("").reduce((a, c) => a + c.charCodeAt(0), 0);
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

// ── Lead Score Badge ──────────────────────────────────────────────────────────

function LeadScoreBadge({ score }: { score: number }) {
  if (score <= 75) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-2xs font-bold",
        "bg-gradient-to-r from-orange-500 to-red-500 text-white shadow-sm",
        "animate-pulse",
      )}
      title={`Lead Score: ${score}`}
      aria-label={`نقطة عميل ساخن: ${score}`}
    >
      🔥 {score}
    </span>
  );
}

// ── Channel Badge (inline in card) ────────────────────────────────────────────

function ChannelBadge({ channel }: { channel: string }) {
  const cfg = CHANNEL_MAP[channel as ChannelFilter] ?? CHANNEL_MAP["web"];
  const Icon = cfg.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center w-4 h-4 rounded-full",
        cfg.bg,
        cfg.color,
      )}
      title={cfg.label}
      aria-label={cfg.label}
    >
      <Icon className="w-2.5 h-2.5" />
    </span>
  );
}

// ── Skeleton row ──────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <div className="flex items-start gap-3 px-4 py-3.5 border-b border-[var(--border)]">
      <div className="w-10 h-10 rounded-full bg-cream-200 shrink-0 animate-pulse" />
      <div className="flex-1 min-w-0 space-y-2 pt-0.5">
        <div className="flex items-center justify-between gap-2">
          <div className="h-3 w-28 rounded-full bg-cream-200 animate-pulse" />
          <div className="h-2.5 w-8 rounded-full bg-cream-200 animate-pulse" />
        </div>
        <div className="h-2.5 w-40 rounded-full bg-cream-200 animate-pulse" />
        <div className="h-4 w-16 rounded-full bg-cream-200 animate-pulse" />
      </div>
    </div>
  );
}

const SKELETON_COUNT = 5;

// ── Channel Dropdown ──────────────────────────────────────────────────────────

function ChannelDropdown({
  value,
  onChange,
}: {
  value: ChannelFilter;
  onChange: (v: ChannelFilter) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const current = CHANNEL_MAP[value] ?? CHANNEL_MAP["ALL"];
  const Icon = current.icon;

  return (
    <div className="relative" ref={ref}>
      <button
        id="inbox-channel-filter"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium",
          "border border-[var(--border)] bg-[var(--card)]",
          "hover:bg-cream-200 transition-all duration-150",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold-500/50",
          current.color,
        )}
      >
        <Icon className="w-3.5 h-3.5 shrink-0" />
        <span className="truncate max-w-[4.5rem]">{current.label}</span>
        <ChevronDown className={cn("w-3 h-3 shrink-0 text-[var(--muted-foreground)] transition-transform duration-200", open && "rotate-180")} />
      </button>

      {open && (
        <ul
          role="listbox"
          className={cn(
            "absolute end-0 top-full mt-1 z-50 w-40",
            "bg-[var(--card)] border border-[var(--border)] rounded-xl shadow-card-lg",
            "overflow-hidden animate-fade-in",
          )}
        >
          {CHANNEL_CONFIGS.map((ch) => {
            const CIcon = ch.icon;
            return (
              <li key={ch.value}>
                <button
                  role="option"
                  aria-selected={value === ch.value}
                  onClick={() => { onChange(ch.value); setOpen(false); }}
                  className={cn(
                    "w-full flex items-center gap-2.5 px-3 py-2 text-xs text-start",
                    "hover:bg-cream-100 transition-colors duration-100",
                    value === ch.value
                      ? "bg-gold-50 text-gold-700 font-semibold"
                      : "text-[var(--foreground)]",
                  )}
                >
                  <span className={cn("flex items-center justify-center w-5 h-5 rounded-full shrink-0", ch.bg, ch.color)}>
                    <CIcon className="w-3 h-3" />
                  </span>
                  {ch.label}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ── Sort Toggle ───────────────────────────────────────────────────────────────

function SortToggle({
  value,
  onChange,
}: {
  value: SortByOption;
  onChange: (v: SortByOption) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const current = SORT_OPTIONS.find((s) => s.value === value) ?? SORT_OPTIONS[0];
  const Icon = current.icon;

  return (
    <div className="relative" ref={ref}>
      <button
        id="inbox-sort-filter"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium",
          "border border-[var(--border)] bg-[var(--card)]",
          "hover:bg-cream-200 transition-all duration-150",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold-500/50",
          value === "hot_leads"
            ? "text-orange-600 border-orange-300 bg-orange-50"
            : "text-[var(--muted-foreground)]",
        )}
      >
        <Icon className="w-3.5 h-3.5 shrink-0" />
        <ChevronDown className={cn("w-3 h-3 shrink-0 transition-transform duration-200", open && "rotate-180")} />
      </button>

      {open && (
        <ul
          role="listbox"
          className={cn(
            "absolute end-0 top-full mt-1 z-50 w-52",
            "bg-[var(--card)] border border-[var(--border)] rounded-xl shadow-card-lg",
            "overflow-hidden animate-fade-in",
          )}
        >
          {SORT_OPTIONS.map((opt) => {
            const OIcon = opt.icon;
            return (
              <li key={opt.value}>
                <button
                  role="option"
                  aria-selected={value === opt.value}
                  onClick={() => { onChange(opt.value); setOpen(false); }}
                  className={cn(
                    "w-full flex items-center gap-2.5 px-3 py-2.5 text-xs text-start",
                    "hover:bg-cream-100 transition-colors duration-100",
                    value === opt.value
                      ? "bg-gold-50 text-gold-700 font-semibold"
                      : "text-[var(--foreground)]",
                  )}
                >
                  <OIcon className="w-3.5 h-3.5 shrink-0" />
                  {opt.label}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ── Conversation Row ──────────────────────────────────────────────────────────

function ConversationRow({ conv, isActive }: { conv: Conversation; isActive: boolean }) {
  const setActive = useInboxStore((s) => s.setActiveConversation);
  const statusKey = (conv.status?.toUpperCase() ?? "DORMANT") as ConversationStatus;
  const meta = STATUS_META[statusKey] ?? STATUS_META.DORMANT;
  const Icon = meta.icon;

  return (
    <button
      onClick={() => setActive(conv.id)}
      id={`conv-${conv.id}`}
      className={cn(
        "w-full flex items-start gap-3 px-4 py-3.5 text-start",
        "border-b border-[var(--border)] transition-all duration-150",
        "hover:bg-cream-100 focus-visible:outline-none",
        isActive
          ? "bg-gold-50 border-s-2 border-s-gold-500 hover:bg-gold-50"
          : "bg-transparent",
      )}
    >
      {/* Avatar */}
      <div className={cn(
        "relative w-10 h-10 rounded-full shrink-0",
        "bg-gradient-to-br flex items-center justify-center",
        avatarColor(conv.customerPhone),
      )}>
        <span className="text-xs font-bold text-white select-none">
          {initials(conv.customerName)}
        </span>
        {/* Channel indicator dot */}
        <span className={cn(
          "absolute -bottom-0.5 -end-0.5 w-4 h-4 rounded-full",
          "border-2 border-[var(--card)]",
          "flex items-center justify-center overflow-hidden",
          CHANNEL_MAP[conv.channel as ChannelFilter]?.bg ?? "bg-cream-200",
        )}>
          <ChannelBadge channel={conv.channel} />
        </span>
        {/* Online pulse for active AI convs */}
        {conv.isAiActive && (
          <span className="absolute top-0 end-0 w-2.5 h-2.5 rounded-full bg-info border-2 border-[var(--card)]" />
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {/* Name row */}
        <div className="flex items-center justify-between gap-2 mb-0.5">
          <span className={cn(
            "text-sm truncate",
            conv.unreadCount > 0 ? "font-semibold text-[var(--foreground)]" : "font-medium text-[var(--foreground)]",
          )}>
            {conv.customerName}
          </span>
          <span className="text-2xs text-[var(--muted-foreground)] shrink-0">
            {timeAgo(conv.lastMessageAt)}
          </span>
        </div>

        {/* Last message */}
        <p className={cn(
          "text-xs truncate mb-1.5",
          conv.unreadCount > 0 ? "text-[var(--foreground)]" : "text-[var(--muted-foreground)]",
        )}>
          {conv.lastMessage}
        </p>

        {/* Footer: status badge + lead score + unread */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className={cn("text-2xs flex items-center gap-1 shrink-0", meta.className, "!px-1.5 !py-0")}>
              <Icon className="w-2.5 h-2.5" />
              {meta.label}
            </span>
            {/* 🔥 Hot Lead badge — shown only for score > 75 */}
            <LeadScoreBadge score={conv.leadScore ?? 0} />
          </div>

          {conv.unreadCount > 0 && (
            <span className="min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-gold-500 text-navy-900 text-2xs font-bold flex items-center justify-center shrink-0">
              {conv.unreadCount}
            </span>
          )}
        </div>
      </div>
    </button>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export function ConversationList() {
  const activeId        = useInboxStore((s) => s.activeConversationId);
  const filter          = useInboxStore((s) => s.filter);
  const setFilter       = useInboxStore((s) => s.setFilter);
  const setChannelFilter = useInboxStore((s) => s.setChannelFilter);
  const setSortBy       = useInboxStore((s) => s.setSortBy);
  const { channelFilter, sortBy } = useInboxFilters();
  const convs           = useFilteredConversations();

  const { isLoadingConversations, conversationsError } = useInboxLoadingState();

  const totalUnread = useInboxStore((s) =>
    s.conversations.reduce((n, c) => n + c.unreadCount, 0),
  );

  return (
    <div className="flex flex-col h-full bg-[var(--card)] border-e border-[var(--border)]" dir="rtl">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="px-4 pt-5 pb-3 border-b border-[var(--border)] shrink-0">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <MessageSquare className="w-4 h-4 text-[var(--accent)]" />
            <h2 className="text-sm font-semibold text-[var(--foreground)]">
              صندوق الرسائل
            </h2>
            {totalUnread > 0 && (
              <span className="badge badge-gold text-2xs">
                {totalUnread}
              </span>
            )}
          </div>
        </div>

        {/* Search */}
        <div className="relative mb-3">
          <Search className="absolute top-1/2 -translate-y-1/2 start-3 w-3.5 h-3.5 text-[var(--muted-foreground)]" />
          <input
            type="text"
            placeholder="بحث..."
            className="input h-8 text-xs ps-9"
            dir="rtl"
            aria-label="بحث في المحادثات"
          />
        </div>

        {/* ── Control Bar: Channel Filter + Sort Toggle ─────────────────── */}
        <div
          id="inbox-control-bar"
          className={cn(
            "flex items-center gap-2 p-2 rounded-xl",
            "bg-cream-100 border border-[var(--border)]",
          )}
        >
          {/* Channel Dropdown */}
          <div className="flex-1 flex items-center gap-1.5">
            <span className="text-2xs text-[var(--muted-foreground)] shrink-0 font-medium">القناة:</span>
            <ChannelDropdown
              value={channelFilter}
              onChange={(v) => setChannelFilter(v)}
            />
          </div>

          {/* Sort Toggle */}
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-2xs text-[var(--muted-foreground)] font-medium">ترتيب:</span>
            <SortToggle value={sortBy} onChange={(v) => setSortBy(v)} />
          </div>
        </div>
      </div>

      {/* ── Status Filter Tabs ──────────────────────────────────────────── */}
      <div className="flex gap-1 px-3 py-2 border-b border-[var(--border)] shrink-0 overflow-x-auto scrollbar-hidden">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            id={`status-filter-${f.value.toLowerCase()}`}
            onClick={() => setFilter(f.value)}
            className={cn(
              "shrink-0 px-2.5 py-1 rounded-full text-2xs font-medium transition-all duration-150",
              filter === f.value
                ? "bg-navy-800 text-cream-100"
                : "text-[var(--muted-foreground)] hover:bg-cream-200",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* ── Conversation List ───────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto scrollbar-hidden" role="list" aria-label="قائمة المحادثات">

        {/* Loading skeleton */}
        {isLoadingConversations && (
          <>
            {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
              <SkeletonRow key={i} />
            ))}
          </>
        )}

        {/* Error state */}
        {!isLoadingConversations && conversationsError && (
          <div className="flex flex-col items-center justify-center h-32 text-center px-4">
            <AlertTriangle className="w-6 h-6 text-danger mb-2" />
            <p className="text-xs text-danger font-medium">فشل تحميل المحادثات</p>
            <p className="text-2xs text-[var(--muted-foreground)] mt-0.5">تحقق من الاتصال وأعد المحاولة</p>
          </div>
        )}

        {/* Empty state (data loaded but list is empty) */}
        {!isLoadingConversations && !conversationsError && convs.length === 0 && (
          <div className="flex flex-col items-center justify-center h-32 text-center px-4">
            <MessageSquare className="w-8 h-8 text-cream-400 mb-2" />
            <p className="text-sm text-[var(--muted-foreground)]">لا توجد محادثات</p>
            {channelFilter !== "ALL" && (
              <p className="text-2xs text-[var(--muted-foreground)] mt-1">
                لا توجد محادثات على {CHANNEL_MAP[channelFilter]?.label}
              </p>
            )}
          </div>
        )}

        {/* Conversation rows */}
        {!isLoadingConversations &&
          convs.map((conv) => (
            <ConversationRow
              key={conv.id}
              conv={conv}
              isActive={conv.id === activeId}
            />
          ))}
      </div>

      {/* ── Footer: hot leads count ─────────────────────────────────────── */}
      {sortBy === "hot_leads" && !isLoadingConversations && convs.length > 0 && (
        <div className="shrink-0 px-4 py-2 border-t border-[var(--border)] bg-orange-50/50">
          <p className="text-2xs text-orange-600 font-medium text-center">
            🔥 {convs.filter((c) => (c.leadScore ?? 0) > 75).length} عملاء ساخنون من أصل {convs.length}
          </p>
        </div>
      )}
    </div>
  );
}
