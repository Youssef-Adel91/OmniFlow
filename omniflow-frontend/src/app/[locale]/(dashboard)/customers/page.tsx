"use client";

/**
 * app/[locale]/(dashboard)/customers/page.tsx — CRM customers list
 *
 * Wired to the real backend via `lib/api/customers.ts`:
 *   GET   /customers        (paginated: items / total / page / page_size)
 *   PATCH /customers/{id}   (display_name, is_vip, is_processing_restricted)
 *
 * States follow the pattern used by properties/page.tsx and
 * inbox/ConversationList.tsx: loading skeleton → error banner → empty state.
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  Users,
  Search,
  Phone,
  Calendar,
  Star,
  Flame,
  Sun,
  Snowflake,
  ShieldOff,
  AlertTriangle,
  RefreshCw,
  Loader2,
  MailCheck,
  Contact,
} from "lucide-react";
import {
  fetchCustomers,
  updateCustomer,
  type Customer,
  type CustomerPage,
  type LeadTier,
} from "@/lib/api/customers";

const PAGE_SIZE = 20;

const TIER_STYLE: Record<LeadTier, { label: string; icon: typeof Flame; className: string }> = {
  hot:  { label: "ساخن",   icon: Flame,     className: "bg-red-500/10 text-red-400 border-red-500/20" },
  warm: { label: "دافئ",   icon: Sun,       className: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  cold: { label: "بارد",   icon: Snowflake, className: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
};

// ── Helpers ────────────────────────────────────────────────────────────────

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "؟";
  return (parts[0][0] ?? "") + (parts[1]?.[0] ?? "");
}

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "—";
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 60) return "الآن";
  if (diff < 3600) return `قبل ${Math.floor(diff / 60)} دقيقة`;
  if (diff < 86400) return `قبل ${Math.floor(diff / 3600)} ساعة`;
  return `منذ ${Math.floor(diff / 86400)} يوم`;
}

// ── Skeleton ───────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <tr className="border-b border-white/5">
      {Array.from({ length: 7 }).map((_, i) => (
        <td key={i} className="py-4 px-6">
          <div
            className="h-4 rounded bg-white/5 animate-pulse"
            style={{ width: i === 0 ? "160px" : i === 3 ? "180px" : "90px" }}
          />
        </td>
      ))}
    </tr>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function CustomersPage() {
  const [data, setData]       = useState<CustomerPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [page, setPage]       = useState(1);
  const [search, setSearch]   = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);

  // Debounce the search box (350 ms) so we don't hammer the API
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchCustomers({
        page,
        page_size: PAGE_SIZE,
        search: debouncedSearch || undefined,
        sort: "lead_score",
      });
      setData(result);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setError(typeof detail === "string" ? detail : "فشل تحميل قائمة العملاء");
    } finally {
      setLoading(false);
    }
  }, [page, debouncedSearch]);

  useEffect(() => {
    void load();
  }, [load]);

  // ── VIP toggle (PATCH /customers/{id}) ───────────────────────────────────
  const handleToggleVip = async (customer: Customer) => {
    setSavingId(customer.id);
    // Optimistic update
    setData((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((c) =>
              c.id === customer.id ? { ...c, is_vip: !c.is_vip } : c,
            ),
          }
        : prev,
    );
    try {
      const updated = await updateCustomer(customer.id, { is_vip: !customer.is_vip });
      setData((prev) =>
        prev
          ? { ...prev, items: prev.items.map((c) => (c.id === updated.id ? updated : c)) }
          : prev,
      );
    } catch {
      // Roll back
      setData((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((c) =>
                c.id === customer.id ? { ...c, is_vip: customer.is_vip } : c,
              ),
            }
          : prev,
      );
      setError("تعذّر تحديث حالة العميل. حاول مجدداً.");
    } finally {
      setSavingId(null);
    }
  };

  const items      = data?.items ?? [];
  const total      = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / (data?.page_size || PAGE_SIZE)));
  const rangeStart = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd   = Math.min(page * PAGE_SIZE, total);

  return (
    <div className="space-y-6" dir="rtl">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <Users className="w-8 h-8 text-[#C9A84C]" />
            العملاء والفرص البيعية
          </h1>
          <p className="text-gray-400 mt-2 text-sm">
            مرتّبون تلقائياً من الأقرب للشراء إلى الأبرد، بناءً على سلوك المحادثة وسجل التفاعل.
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

      {/* Search & Stats */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-3">
          <div className="relative">
            <Search className="absolute right-4 top-3.5 w-5 h-5 text-gray-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="ابحث بالاسم أو رقم الهاتف..."
              className="w-full bg-[#111827] border border-white/10 rounded-xl py-3 pr-12 pl-4 text-white placeholder-gray-500 focus:outline-none focus:border-[#C9A84C]/50 transition-colors"
            />
          </div>
        </div>
        <div className="bg-[#111827] border border-[#C9A84C]/20 rounded-xl p-3 flex items-center justify-between">
          <span className="text-sm text-gray-400 font-medium">إجمالي العملاء</span>
          <span className="text-2xl font-bold text-[#C9A84C]">
            {loading && !data ? "…" : total.toLocaleString("ar")}
          </span>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 bg-red-500/10 border border-red-500/40 text-red-300 rounded-xl px-4 py-3 text-sm"
        >
          <span className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            {error}
          </span>
          <button
            onClick={() => void load()}
            className="flex items-center gap-1 text-xs hover:opacity-80"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            إعادة المحاولة
          </button>
        </div>
      )}

      {/* Table */}
      <div className="bg-[#111827] border border-white/5 rounded-2xl overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-right">
            <thead>
              <tr className="border-b border-white/10 bg-white/5 text-gray-400 text-sm">
                <th className="py-4 px-6 font-medium w-10">#</th>
                <th className="py-4 px-6 font-medium">اسم العميل</th>
                <th className="py-4 px-6 font-medium">رقم الهاتف</th>
                <th className="py-4 px-6 font-medium">احتمالية الشراء</th>
                <th className="py-4 px-6 font-medium">حالة بطاقة التواصل</th>
                <th className="py-4 px-6 font-medium">آخر تفاعل</th>
                <th className="py-4 px-6 font-medium text-center">VIP</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="text-center py-20 px-6">
                    <Users className="w-12 h-12 text-gray-600 mx-auto mb-4" />
                    <h3 className="text-lg font-semibold text-gray-200 mb-1">
                      {debouncedSearch ? "لا توجد نتائج مطابقة" : "لا يوجد عملاء بعد"}
                    </h3>
                    <p className="text-sm text-gray-500">
                      {debouncedSearch
                        ? "جرّب كلمة بحث أخرى أو امسح البحث."
                        : "سيظهر العملاء هنا تلقائياً بمجرد بدء محادثات عبر قنواتك."}
                    </p>
                  </td>
                </tr>
              ) : (
                items.map((customer, index) => {
                  const name  = customer.display_name || customer.whatsapp_profile_name || "عميل غير معروف";
                  const score = customer.lead_score;
                  const tier  = TIER_STYLE[customer.lead_tier] ?? TIER_STYLE.cold;
                  const TierIcon = tier.icon;
                  const rank  = (page - 1) * PAGE_SIZE + index + 1;
                  const vcardOpened = Boolean(customer.vcard_opened_at);
                  const vcardSaved  = customer.vcard_state === "STATE_CONTACT_SAVED_VERIFIED";
                  return (
                    <tr
                      key={customer.id}
                      className="border-b border-white/5 hover:bg-white/[0.02] transition-colors group"
                    >
                      <td className="py-4 px-6 text-gray-500 text-sm font-medium">{rank}</td>
                      <td className="py-4 px-6">
                        <div className="flex items-center gap-3">
                          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-[#C9A84C] to-yellow-700 flex items-center justify-center text-[#0A0F1C] font-bold text-sm shrink-0">
                            {initials(name)}
                          </div>
                          <div className="min-w-0">
                            <span className="font-semibold text-gray-100 block truncate">
                              {name}
                            </span>
                            {customer.is_processing_restricted && (
                              <span className="inline-flex items-center gap-1 text-[11px] text-orange-400 mt-0.5">
                                <ShieldOff className="w-3 h-3" />
                                معالجة البيانات مقيّدة
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="py-4 px-6 text-gray-300" dir="ltr">
                        <div className="flex items-center justify-end gap-2">
                          {customer.unified_phone ?? "—"}
                          <Phone className="w-4 h-4 text-gray-500" />
                        </div>
                      </td>
                      <td className="py-4 px-6">
                        <div className="flex items-center gap-2.5">
                          <div className="w-16 h-1.5 rounded-full bg-white/10 overflow-hidden shrink-0">
                            <div
                              className={`h-full rounded-full ${
                                customer.lead_tier === "hot" ? "bg-red-500"
                                : customer.lead_tier === "warm" ? "bg-amber-500"
                                : "bg-blue-500"
                              }`}
                              style={{ width: `${score}%` }}
                            />
                          </div>
                          <span
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border shrink-0 ${tier.className}`}
                          >
                            <TierIcon className="w-3.5 h-3.5" /> {tier.label} · {score}%
                          </span>
                        </div>
                      </td>
                      <td className="py-4 px-6 text-sm">
                        {vcardOpened ? (
                          <span className="inline-flex items-center gap-1.5 text-emerald-400">
                            <MailCheck className="w-4 h-4" /> تم فتح البطاقة
                          </span>
                        ) : vcardSaved ? (
                          <span className="inline-flex items-center gap-1.5 text-[#C9A84C]">
                            <Contact className="w-4 h-4" /> تم حفظ الرقم
                          </span>
                        ) : (
                          <span className="text-gray-500">لم تُحفظ بعد</span>
                        )}
                      </td>
                      <td className="py-4 px-6 text-gray-400 text-sm">
                        <div className="flex items-center gap-2">
                          <Calendar className="w-4 h-4" />
                          {timeAgo(customer.last_interaction_at)}
                        </div>
                      </td>
                      <td className="py-4 px-6 text-center">
                        <button
                          onClick={() => void handleToggleVip(customer)}
                          disabled={savingId === customer.id}
                          aria-pressed={customer.is_vip}
                          aria-label={customer.is_vip ? "إلغاء تمييز VIP" : "تمييز كعميل VIP"}
                          className={`p-2 rounded-lg transition-colors disabled:opacity-50 ${
                            customer.is_vip
                              ? "text-[#C9A84C] bg-[#C9A84C]/10"
                              : "text-gray-500 hover:text-[#C9A84C]"
                          }`}
                        >
                          {savingId === customer.id ? (
                            <Loader2 className="w-5 h-5 animate-spin" />
                          ) : (
                            <Star
                              className="w-5 h-5"
                              fill={customer.is_vip ? "currentColor" : "none"}
                            />
                          )}
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="p-4 border-t border-white/5 flex items-center justify-between text-sm text-gray-400">
          <span>
            {total === 0
              ? "لا توجد سجلات"
              : `عرض ${rangeStart} إلى ${rangeEnd} من ${total.toLocaleString("ar")} عميل`}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
              className="px-3 py-1 bg-white/5 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              السابق
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || loading}
              className="px-3 py-1 bg-white/5 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              التالي
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
