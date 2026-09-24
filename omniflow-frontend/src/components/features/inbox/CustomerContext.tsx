"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { cn, formatSAR } from "@/lib/utils";
import { useActiveConversation } from "@/store/inboxStore";
import {
  fetchRecommendations,
  addConversationNote,
  scheduleAppointment,
  type PropertyRecommendation,
} from "@/lib/api/inbox";
import {
  Brain,
  Target,
  Wallet,
  MapPin,
  Home,
  TrendingUp,
  AlertCircle,
  Smile,
  Meh,
  Frown,
  Zap,
  Phone,
  Calendar,
  ChevronRight,
  Building2,
} from "lucide-react";

// ── Confidence bar ────────────────────────────────────────────────────────────

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? "bg-success" : pct >= 60 ? "bg-warning" : "bg-danger";

  return (
    <div className="space-y-1">
      <div className="flex justify-between items-center">
        <span className="text-2xs text-[var(--muted-foreground)]">ثقة AI</span>
        <span className={cn(
          "text-2xs font-bold",
          pct >= 80 ? "text-success" : pct >= 60 ? "text-warning" : "text-danger"
        )}>
          {pct}%
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-cream-300 overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all duration-500", color)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ── Sentiment indicator ───────────────────────────────────────────────────────

function SentimentBadge({ sentiment }: { sentiment: string }) {
  const config = {
    positive: { icon: Smile,       label: "إيجابي",  className: "text-success bg-success/10" },
    neutral:  { icon: Meh,         label: "محايد",   className: "text-warning bg-warning/10" },
    negative: { icon: Frown,       label: "سلبي",    className: "text-danger  bg-danger/10"  },
    urgent:   { icon: AlertCircle, label: "عاجل",    className: "text-danger  bg-danger/10 animate-pulse-gold" },
  };
  const c = config[sentiment as keyof typeof config] ?? config.neutral;
  const Icon = c.icon;

  return (
    <span className={cn(
      "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
      c.className
    )}>
      <Icon className="w-3 h-3" />
      {c.label}
    </span>
  );
}

// ── Info row ──────────────────────────────────────────────────────────────────

function InfoRow({
  icon: Icon,
  label,
  value,
  valueClass,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 border-b border-[var(--border)] last:border-0">
      <div className="w-7 h-7 rounded-[var(--radius-sm)] bg-cream-200 flex items-center justify-center shrink-0 mt-0.5">
        <Icon className="w-3.5 h-3.5 text-navy-600" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-2xs text-[var(--muted-foreground)] mb-0.5">{label}</p>
        <p className={cn("text-sm font-medium text-[var(--foreground)] truncate", valueClass)}>
          {value}
        </p>
      </div>
    </div>
  );
}

// ── Property suggestion card ──────────────────────────────────────────────────

function PropertySuggestion({
  title,
  price,
  area,
  district,
  score,
}: {
  title: string;
  price: number;
  area: number;
  district: string;
  score: number;
}) {
  return (
    <div className="rounded-[var(--radius-sm)] border border-[var(--border)] p-3 hover:border-gold-400 hover:shadow-card transition-all duration-150 cursor-pointer group">
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <p className="text-xs font-semibold text-[var(--foreground)] leading-snug flex-1">
          {title}
        </p>
        <span className="shrink-0 text-2xs font-bold text-success bg-success/10 px-1.5 py-0.5 rounded-full">
          {Math.round(score * 100)}%
        </span>
      </div>
      <p className="text-sm font-bold text-gold-600 mb-1">
        {formatSAR(price)}
      </p>
      <div className="flex items-center gap-3 text-2xs text-[var(--muted-foreground)]">
        <span className="flex items-center gap-0.5">
          <Home className="w-3 h-3" />
          {area} م²
        </span>
        <span className="flex items-center gap-0.5">
          <MapPin className="w-3 h-3" />
          {district}
        </span>
      </div>
      <div className="mt-2 flex items-center justify-end opacity-0 group-hover:opacity-100 transition-opacity">
        <span className="text-2xs text-gold-600 flex items-center gap-0.5">
          عرض التفاصيل <ChevronRight className="w-3 h-3" />
        </span>
      </div>
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyContext() {
  return (
    <div className="flex flex-col items-center justify-center h-48 text-center px-4">
      <Brain className="w-8 h-8 text-cream-400 mb-2" />
      <p className="text-sm text-[var(--muted-foreground)]">
        اختر محادثة لعرض السياق
      </p>
    </div>
  );
}

// ── Quick actions ────────────────────────────────────────────────────────────
//
// "Reports" links to the existing reports page (read-only) rather than a
// fake on-demand generation — real paid-report generation depends on payment
// integration, which doesn't exist yet. "Notes" and "Appointments" are real,
// minimal features: an internal note and a structured date/location record,
// not full calendar sync (see IMPLEMENTATION_STATUS.md for the scope-cut).

function QuickActions({ conversationId }: { conversationId: string }) {
  const params = useParams<{ locale: string }>();
  const locale = params?.locale ?? "ar";

  const [openForm, setOpenForm] = useState<"note" | "appointment" | null>(null);
  const [noteText, setNoteText] = useState("");
  const [apptDate, setApptDate] = useState("");
  const [apptLocation, setApptLocation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  async function submitNote() {
    if (!noteText.trim()) return;
    setSubmitting(true);
    setFeedback(null);
    try {
      await addConversationNote(conversationId, noteText.trim(), "warning");
      setNoteText("");
      setOpenForm(null);
      setFeedback("تمت إضافة الملاحظة.");
    } catch {
      setFeedback("تعذّرت إضافة الملاحظة، حاول مرة أخرى.");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitAppointment() {
    if (!apptDate) return;
    setSubmitting(true);
    setFeedback(null);
    try {
      const iso = new Date(apptDate).toISOString();
      await scheduleAppointment(conversationId, iso, apptLocation.trim() || undefined);
      setApptDate("");
      setApptLocation("");
      setOpenForm(null);
      setFeedback("تمت جدولة الموعد.");
    } catch {
      setFeedback("تعذّرت جدولة الموعد — تأكد من أن التاريخ في المستقبل.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-1.5">
      <a
        href={`/${locale}/reports`}
        className="w-full btn-outline text-xs py-2 flex items-center justify-start gap-2"
      >
        <TrendingUp className="w-3.5 h-3.5 text-success" />
        عرض تقارير العميل
      </a>

      <button
        onClick={() => setOpenForm(openForm === "appointment" ? null : "appointment")}
        className="w-full btn-outline text-xs py-2 flex items-center justify-start gap-2"
      >
        <Calendar className="w-3.5 h-3.5 text-info" />
        جدولة موعد زيارة
      </button>
      {openForm === "appointment" && (
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] p-2 space-y-1.5">
          <input
            type="datetime-local"
            value={apptDate}
            onChange={(e) => setApptDate(e.target.value)}
            className="w-full text-xs rounded border border-[var(--border)] px-2 py-1 bg-[var(--background)]"
          />
          <input
            type="text"
            value={apptLocation}
            onChange={(e) => setApptLocation(e.target.value)}
            placeholder="ملاحظة الموقع (اختياري)"
            className="w-full text-xs rounded border border-[var(--border)] px-2 py-1 bg-[var(--background)]"
          />
          <button
            onClick={submitAppointment}
            disabled={submitting || !apptDate}
            className="w-full btn-primary text-xs py-1.5 disabled:opacity-50"
          >
            تأكيد الموعد
          </button>
        </div>
      )}

      <button
        onClick={() => setOpenForm(openForm === "note" ? null : "note")}
        className="w-full btn-outline text-xs py-2 flex items-center justify-start gap-2 text-danger border-danger/30 hover:bg-danger/5"
      >
        <AlertCircle className="w-3.5 h-3.5" />
        إضافة ملاحظة تحذير
      </button>
      {openForm === "note" && (
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] p-2 space-y-1.5">
          <textarea
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            placeholder="اكتب الملاحظة..."
            rows={3}
            className="w-full text-xs rounded border border-[var(--border)] px-2 py-1 bg-[var(--background)] resize-none"
          />
          <button
            onClick={submitNote}
            disabled={submitting || !noteText.trim()}
            className="w-full btn-primary text-xs py-1.5 disabled:opacity-50"
          >
            حفظ الملاحظة
          </button>
        </div>
      )}

      {feedback && <p className="text-2xs text-[var(--muted-foreground)]">{feedback}</p>}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function CustomerContext() {
  const conv = useActiveConversation();
  const [recommendations, setRecommendations] = useState<PropertyRecommendation[]>([]);

  // Hooks must run unconditionally (before the `!conv` early return below).
  // When there's no active conversation, EmptyContext renders instead and
  // stale recommendations are simply never shown — no need to reset them.
  useEffect(() => {
    if (!conv) return;
    let cancelled = false;
    fetchRecommendations(conv.id)
      .then((recs) => { if (!cancelled) setRecommendations(recs); })
      .catch(() => { if (!cancelled) setRecommendations([]); });
    return () => { cancelled = true; };
    // Depend on the id, not `conv` itself: the store returns a new object
    // reference on every unrelated update to the active conversation (e.g. a
    // new chat message), which would otherwise refetch recommendations on
    // every incoming message instead of only when the conversation changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conv?.id]);

  if (!conv) return (
    <div className="h-full bg-[var(--card)] border-s border-[var(--border)] flex flex-col">
      <div className="px-4 pt-5 pb-3 border-b border-[var(--border)]">
        <h2 className="text-sm font-semibold text-[var(--foreground)] flex items-center gap-2">
          <Brain className="w-4 h-4 text-[var(--accent)]" />
          سياق العميل
        </h2>
      </div>
      <EmptyContext />
    </div>
  );

  return (
    <div className="h-full bg-[var(--card)] border-s border-[var(--border)] flex flex-col overflow-hidden">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="px-4 pt-5 pb-3 border-b border-[var(--border)] shrink-0">
        <h2 className="text-sm font-semibold text-[var(--foreground)] flex items-center gap-2">
          <Brain className="w-4 h-4 text-[var(--accent)]" />
          سياق العميل
        </h2>
        <p className="text-2xs text-[var(--muted-foreground)] mt-0.5">
          معلومات العميل المتاحة لهذه المحادثة
        </p>
      </div>

      {/* ── Scrollable content ──────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto scrollbar-hidden px-4 py-4 space-y-5">

        {/* ── Customer CRM info ───────────────────────────────────────── */}
        <section>
          <h3 className="text-2xs font-semibold text-[var(--muted-foreground)] uppercase tracking-wider mb-2">
            معلومات العميل
          </h3>
          <div className="bg-[var(--background)] rounded-[var(--radius-sm)] border border-[var(--border)] px-2">
            <InfoRow icon={Phone}    label="رقم الهاتف"  value={conv.customerPhone} />
            <InfoRow icon={Calendar} label="آخر تواصل"   value={new Date(conv.lastMessageAt).toLocaleDateString("ar-SA")} />
          </div>
        </section>

        {/* ── AI Extracted Intent ─────────────────────────────────────── */}
        <section>
          <h3 className="text-2xs font-semibold text-[var(--muted-foreground)] uppercase tracking-wider mb-2">
            تحليل AI
          </h3>
          <div className="bg-[var(--background)] rounded-[var(--radius-sm)] border border-[var(--border)] px-2">
            {conv.intent && (
              <InfoRow icon={Target}  label="النية"       value={conv.intent}
                valueClass="text-info font-semibold" />
            )}
            {conv.budget && (
              <InfoRow icon={Wallet}  label="الميزانية"   value={formatSAR(conv.budget)} />
            )}
            {conv.lookingIn && (
              <InfoRow icon={MapPin}  label="الموقع المفضل" value={conv.lookingIn} />
            )}
            {conv.propertyType && (
              <InfoRow icon={Building2} label="نوع العقار"  value={conv.propertyType} />
            )}
          </div>
        </section>

        {/* ── Sentiment + Confidence ──────────────────────────────────── */}
        <section>
          <h3 className="text-2xs font-semibold text-[var(--muted-foreground)] uppercase tracking-wider mb-2">
            حالة العميل
          </h3>
          <div className="bg-[var(--background)] rounded-[var(--radius-sm)] border border-[var(--border)] p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs text-[var(--muted-foreground)]">المشاعر</span>
              {conv.sentiment && <SentimentBadge sentiment={conv.sentiment} />}
            </div>
            {conv.aiConfidence !== undefined && (
              <ConfidenceBar value={conv.aiConfidence} />
            )}
          </div>
        </section>

        {/* ── RAG Property Recommendations ───────────────────────────── */}
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-2xs font-semibold text-[var(--muted-foreground)] uppercase tracking-wider">
              عقارات مقترحة
            </h3>
            {recommendations.length > 0 && <span className="badge badge-gold text-2xs flex items-center gap-0.5">
              <Zap className="w-2.5 h-2.5" />
              RAG
            </span>}
          </div>
          <div className="space-y-2">
            {recommendations.length === 0 && (
              <p className="text-xs text-[var(--muted-foreground)]">
                لا توجد اقتراحات متاحة لهذه المحادثة.
              </p>
            )}
            {recommendations.map((rec, i) => (
              <PropertySuggestion key={i} {...rec} />
            ))}
          </div>
        </section>

        {/* ── Quick actions ───────────────────────────────────────────── */}
        <section>
          <h3 className="text-2xs font-semibold text-[var(--muted-foreground)] uppercase tracking-wider mb-2">
            إجراءات سريعة
          </h3>
          <QuickActions conversationId={conv.id} />
        </section>
      </div>
    </div>
  );
}
