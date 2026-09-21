"use client";

/**
 * app/[locale]/(dashboard)/broadcasts/page.tsx — WhatsApp broadcast campaigns
 *
 * Wired to `lib/api/broadcasts.ts`:
 *   GET  /broadcasts                — campaign list
 *   POST /broadcasts                — create a campaign (draft)
 *   POST /broadcasts/{id}/schedule  — send now / schedule for later
 *   POST /broadcasts/{id}/cancel    — cancel a scheduled campaign
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  Megaphone,
  Users,
  MessageSquare,
  Send,
  CheckCircle2,
  Clock,
  AlertTriangle,
  RefreshCw,
  Loader2,
  Ban,
  Construction,
} from "lucide-react";
import {
  fetchBroadcasts,
  createBroadcast,
  scheduleBroadcast,
  cancelBroadcast,
  previewAudience,
  BROADCAST_STATUS_LABELS,
  TARGET_AUDIENCE_OPTIONS,
  type Broadcast,
} from "@/lib/api/broadcasts";

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("ar-SA", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

export default function BroadcastsPage() {
  // ── Form ─────────────────────────────────────────────────────────────────
  const [title, setTitle]     = useState("");
  const [target, setTarget]   = useState<string>(TARGET_AUDIENCE_OPTIONS[1].value);
  const [messageTemplate, setMessageTemplate] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");

  // تقدير حجم الجمهور — يُحدَّث عند تغيير الشريحة المستهدفة.
  const [audienceCount, setAudienceCount]     = useState<number | null>(null);
  const [audienceLoading, setAudienceLoading] = useState(false);
  const [audienceError, setAudienceError]     = useState(false);

  const [submitting, setSubmitting]   = useState(false);
  const [formError, setFormError]     = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);

  // ── List ─────────────────────────────────────────────────────────────────
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([]);
  const [loading, setLoading]       = useState(true);
  const [listError, setListError]   = useState<string | null>(null);
  const [busyId, setBusyId]         = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const result = await fetchBroadcasts({ page: 1, page_size: 20 });
      setBroadcasts(result.items);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setListError(typeof detail === "string" ? detail : "فشل تحميل الحملات");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // كل ما تتغير الشريحة، نسأل الباك-إند عن عدد المستلمين المتوقّع.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setAudienceLoading(true);
      setAudienceError(false);
      try {
        const count = await previewAudience(target);
        if (!cancelled) setAudienceCount(count);
      } catch {
        if (!cancelled) {
          setAudienceCount(null);
          setAudienceError(true);
        }
      } finally {
        if (!cancelled) setAudienceLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [target]);

  useEffect(() => {
    if (!formSuccess) return;
    const t = setTimeout(() => setFormSuccess(null), 5000);
    return () => clearTimeout(t);
  }, [formSuccess]);

  /**
   * Create the campaign. `sendNow = true` immediately calls the schedule
   * endpoint without a timestamp so the backend dispatches right away.
   */
  const handleCreate = async (sendNow: boolean) => {
    if (submitting) return;

    const trimmedTitle = title.trim();
    const trimmedMessage = messageTemplate.trim();
    if (!trimmedTitle || !trimmedMessage) {
      setFormError("يرجى إدخال اسم الحملة ونص الرسالة.");
      return;
    }
    if (!sendNow && !scheduledAt) {
      setFormError("يرجى اختيار موعد الجدولة.");
      return;
    }

    setSubmitting(true);
    setFormError(null);
    setFormSuccess(null);

    // datetime-local gives a local wall-clock string — convert it to ISO/UTC.
    // "إرسال الآن" ليس له timestamp من المستخدم، لكن الباك-إند يرفض scheduled_at
    // في الماضي أو الحاضر (POST /schedule يتطلب وقت مستقبلي)، فبنبعت +30 ثانية
    // من الآن عشان يعدي التحقق. الإرسال الفعلي بيتم من الـ worker لما يوصل الوقت ده.
    const isoScheduledAt = sendNow
      ? new Date(Date.now() + 30_000).toISOString()
      : scheduledAt
        ? new Date(scheduledAt).toISOString()
        : undefined;

    try {
      const created = await createBroadcast({
        title: trimmedTitle,
        message_template: trimmedMessage,
        target_audience: target,
        scheduled_at: isoScheduledAt ?? null,
      });

      const finalized = await scheduleBroadcast(created.id, isoScheduledAt);

      setBroadcasts((prev) => [finalized, ...prev]);
      setTitle("");
      setMessageTemplate("");
      setScheduledAt("");
      setFormSuccess(
        sendNow
          ? "تم تسجيل الحملة وجدولتها للإرسال الفوري (لن تصل للعملاء حتى اكتمال ربط واتساب)."
          : "تمت جدولة الحملة بنجاح (لن تصل للعملاء حتى اكتمال ربط واتساب).",
      );
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setFormError(typeof detail === "string" ? detail : "تعذّر إنشاء الحملة");
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async (broadcast: Broadcast) => {
    setBusyId(broadcast.id);
    try {
      const updated = await cancelBroadcast(broadcast.id);
      setBroadcasts((prev) => prev.map((b) => (b.id === updated.id ? updated : b)));
    } catch {
      setListError("تعذّر إلغاء الحملة.");
    } finally {
      setBusyId(null);
    }
  };

  const fieldClass =
    "w-full bg-[#0A0F1C] border border-white/10 rounded-lg py-3 px-4 text-white placeholder-gray-600 focus:outline-none focus:border-[#C9A84C]/50 transition-colors";

  return (
    <div className="space-y-8" dir="rtl">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <Megaphone className="w-8 h-8 text-[#C9A84C]" />
            حملات إعادة الاستهداف (Broadcasts)
          </h1>
          <p className="text-gray-400 mt-2 text-sm">
            أرسل رسائل تسويقية عبر الواتساب للعملاء باستخدام قوالب Meta المعتمدة.
          </p>
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="bg-white/5 border border-white/10 text-white px-4 py-2 rounded-lg flex items-center gap-2 hover:bg-white/10 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          تحديث
        </button>
      </div>

      {/* ── تنبيه صادق: الإرسال الفعلي لسه قيد التطوير ─────────────────── */}
      <div
        role="status"
        className="flex items-start gap-3 bg-amber-500/10 border border-amber-500/40 rounded-xl px-4 py-4"
      >
        <Construction className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
        <div className="text-sm text-amber-200 leading-relaxed">
          <p className="font-bold mb-1">تنبيه مهم: الإرسال الفعلي عبر واتساب قيد التطوير.</p>
          <p>
            الحملات التي تنشئها هنا يتم <strong>تسجيلها وجدولتها فقط</strong> — ولا تصل
            رسائلها إلى العملاء بعد. لا تعتبر إنشاء الحملة إرسالاً فعلياً. سنُعلمك فور
            اكتمال ربط الإرسال بقوالب واتساب المعتمدة من Meta.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Campaign Form */}
        <div className="lg:col-span-2 space-y-6">
          <div className="bg-[#111827] border border-white/10 rounded-2xl p-6 shadow-xl">
            <h2 className="text-xl font-bold text-white mb-6 border-b border-white/5 pb-4">
              إعداد الحملة
            </h2>

            <form
              className="space-y-5"
              onSubmit={(e) => {
                e.preventDefault();
                void handleCreate(true);
              }}
            >
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">اسم الحملة</label>
                <input
                  type="text"
                  required
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="مثال: إطلاق مشروع فلل الياسمين"
                  className={fieldClass}
                />
              </div>

              <div>
                <div className="flex items-center justify-between gap-3 mb-2">
                  <label className="block text-sm font-medium text-gray-300">
                    الشريحة المستهدفة
                  </label>
                  {audienceLoading ? (
                    <span className="text-xs text-gray-500 flex items-center gap-1.5">
                      <Loader2 className="w-3 h-3 animate-spin" />
                      جارٍ حساب حجم الجمهور…
                    </span>
                  ) : audienceCount != null ? (
                    <span className="text-xs font-semibold text-[#C9A84C] bg-[#C9A84C]/10 border border-[#C9A84C]/30 rounded-md px-2.5 py-1 flex items-center gap-1.5">
                      <Users className="w-3 h-3" />
                      تقريباً {audienceCount.toLocaleString("ar")} عميل
                    </span>
                  ) : audienceError ? (
                    <span className="text-xs text-gray-500">
                      تعذّر حساب حجم الجمهور
                    </span>
                  ) : null}
                </div>
                <select
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  className={fieldClass}
                >
                  {TARGET_AUDIENCE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-2 flex items-center gap-1">
                  <Users className="w-3.5 h-3.5" /> الرقم أعلاه تقدير لحظي لعدد العملاء
                  المطابقين للشريحة، وقد يتغير عند لحظة الإرسال الفعلي.
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  نص الرسالة / اسم قالب Meta
                </label>
                <textarea
                  rows={5}
                  required
                  value={messageTemplate}
                  onChange={(e) => setMessageTemplate(e.target.value)}
                  placeholder="مرحباً {{1}}، يسعدنا إبلاغك بإطلاق مشروعنا الجديد..."
                  className={`${fieldClass} resize-none`}
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  موعد الجدولة (اختياري)
                </label>
                <input
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(e) => setScheduledAt(e.target.value)}
                  className={fieldClass}
                  dir="ltr"
                />
              </div>

              {formError && (
                <p role="alert" className="text-sm text-red-400 flex items-center gap-1.5">
                  <AlertTriangle className="w-4 h-4" /> {formError}
                </p>
              )}
              {formSuccess && (
                <p className="text-sm text-green-400 flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4" /> {formSuccess}
                </p>
              )}

              <div className="mt-8 flex items-center gap-4 border-t border-white/5 pt-6">
                <button
                  type="submit"
                  disabled={submitting}
                  className="flex-1 bg-[#C9A84C] text-[#0A0F1C] py-3.5 rounded-xl font-bold hover:bg-[#D4B55A] transition-all flex items-center justify-center gap-2 shadow-lg shadow-[#C9A84C]/10 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {submitting ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <Send className="w-5 h-5" />
                  )}
                  إرسال الحملة الآن
                </button>
                <button
                  type="button"
                  disabled={submitting || !scheduledAt}
                  onClick={() => void handleCreate(false)}
                  className="flex-1 bg-white/5 text-white py-3.5 rounded-xl font-semibold hover:bg-white/10 transition-colors flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Clock className="w-5 h-5" />
                  جدولة لوقت لاحق
                </button>
              </div>
            </form>
          </div>

          {/* Campaigns list */}
          <div className="bg-[#111827] border border-white/5 rounded-2xl p-6 shadow-xl">
            <h2 className="text-xl font-bold text-white mb-4 border-b border-white/5 pb-4">
              الحملات السابقة
            </h2>

            {listError && (
              <div
                role="alert"
                className="flex items-center justify-between gap-3 bg-red-500/10 border border-red-500/40 text-red-300 rounded-xl px-4 py-3 text-sm mb-4"
              >
                <span className="flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4" />
                  {listError}
                </span>
                <button onClick={() => void load()} className="text-xs hover:opacity-80">
                  إعادة المحاولة
                </button>
              </div>
            )}

            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="h-20 rounded-xl bg-white/5 animate-pulse" />
                ))}
              </div>
            ) : broadcasts.length === 0 ? (
              <div className="text-center py-12">
                <Megaphone className="w-10 h-10 text-gray-600 mx-auto mb-3" />
                <p className="text-gray-400 text-sm">لم تُنشئ أي حملة بعد.</p>
                <p className="text-gray-600 text-xs mt-1">
                  استخدم النموذج أعلاه لإطلاق أول حملة إعادة استهداف.
                </p>
              </div>
            ) : (
              <ul className="space-y-3">
                {broadcasts.map((b) => {
                  const meta =
                    BROADCAST_STATUS_LABELS[b.status] ?? { ar: b.status, color: "#8B8FA8" };
                  const canCancel = b.status === "scheduled" || b.status === "draft";
                  return (
                    <li
                      key={b.id}
                      className="border border-white/10 rounded-xl p-4 hover:bg-white/[0.02] transition-colors"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="font-semibold text-gray-100 truncate">{b.title}</p>
                          <p className="text-sm text-gray-400 mt-1 line-clamp-2">
                            {b.message_template}
                          </p>
                          <div className="flex items-center gap-3 mt-2 text-xs text-gray-600">
                            <span className="flex items-center gap-1">
                              <Users className="w-3 h-3" />
                              {TARGET_AUDIENCE_OPTIONS.find((o) => o.value === b.target_audience)
                                ?.label ?? b.target_audience}
                            </span>
                            {b.scheduled_at && (
                              <span className="flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                {formatDate(b.scheduled_at)}
                              </span>
                            )}
                            {b.recipients_count != null && (
                              <span>{b.recipients_count.toLocaleString("ar")} مستلم</span>
                            )}
                          </div>
                        </div>
                        <div className="flex flex-col items-end gap-2 shrink-0">
                          <span
                            className="px-2.5 py-1 rounded-md text-xs font-medium border whitespace-nowrap"
                            style={{
                              color: meta.color,
                              background: `${meta.color}18`,
                              borderColor: `${meta.color}40`,
                            }}
                          >
                            {meta.ar}
                          </span>
                          {canCancel && (
                            <button
                              onClick={() => void handleCancel(b)}
                              disabled={busyId === b.id}
                              className="text-xs text-gray-400 hover:text-red-400 transition-colors flex items-center gap-1 disabled:opacity-50"
                            >
                              {busyId === b.id ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : (
                                <Ban className="w-3 h-3" />
                              )}
                              إلغاء
                            </button>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        {/* Live Preview */}
        <div className="lg:col-span-1">
          <div className="bg-[#111827] border border-white/10 rounded-2xl p-6 shadow-xl sticky top-24">
            <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
              <MessageSquare className="w-5 h-5 text-gray-400" />
              معاينة الرسالة (WhatsApp)
            </h2>

            <div className="bg-[#0A0F1C]/80 border border-white/5 rounded-2xl p-4 min-h-[300px] flex flex-col relative overflow-hidden">
              <div
                className="absolute inset-0 opacity-[0.03]"
                style={{
                  backgroundImage: "radial-gradient(#C9A84C 1px, transparent 1px)",
                  backgroundSize: "20px 20px",
                }}
              />
              <div className="relative z-10 mt-auto">
                <div className="bg-[#1f2c34] rounded-2xl rounded-tr-none p-3 mb-2 shadow-md inline-block max-w-[90%] border border-[#2a3942]">
                  <p className="text-[#e9edef] text-sm leading-relaxed mb-2 whitespace-pre-wrap">
                    {messageTemplate.trim() || "اكتب نص الرسالة لرؤية المعاينة هنا..."}
                  </p>
                  <span className="text-[11px] text-[#8696a0] flex justify-end items-center gap-1 mt-1">
                    الآن <CheckCircle2 className="w-3.5 h-3.5 text-[#53bdeb]" />
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
