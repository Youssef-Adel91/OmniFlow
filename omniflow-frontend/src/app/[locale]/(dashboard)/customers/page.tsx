"use client";

/**
 * app/[locale]/(dashboard)/customers/page.tsx — CRM customers
 *
 * Real data from /customers (see lib/api/customers.ts): ranked by purchase likelihood, filterable by
 * channel/source, tag, city, consent and archive state, with manual add/edit, bulk import (shared wizard),
 * CSV export, a detail drawer (conversations, appointments, reports, activity, merge, archive) and the PDPL
 * marketing-consent flag on every row.
 *
 * Identity model shown to the user: "الجوال" is the real phone; Instagram/Messenger customers have a channel
 * id instead of a phone until one is added (and can then be merged with their WhatsApp identity).
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, Calendar, Contact, Download, Flame, Loader2, MailCheck, Phone, Plus, RefreshCw, Search, ShieldCheck,
  ShieldOff, Snowflake, Star, Sun, Upload, Users,
} from "lucide-react";
import dynamic from "next/dynamic";
const ImportWizard = dynamic(() => import("@/components/properties/ImportWizard"), { ssr: false });
const CustomerFormDialog = dynamic(() => import("@/components/customers/CustomerFormDialog"), { ssr: false });
const CustomerDetailDrawer = dynamic(() => import("@/components/customers/CustomerDetailDrawer"), { ssr: false });
import { Select } from "@/components/ui/Select";
import { saveBlob } from "@/lib/api/propertyImport";
import {
  SOURCE_LABELS, customerErrorInfo, customersImportAdapter, exportCustomersCsv, fetchCustomers, updateCustomer,
  type Customer, type CustomerPage, type LeadTier,
} from "@/lib/api/customers";

const PAGE_SIZE = 20;

const TIER_STYLE: Record<LeadTier, { label: string; icon: typeof Flame; className: string }> = {
  hot:  { label: "ساخن",   icon: Flame,     className: "bg-red-500/10 text-red-300 border-red-500/30" },
  warm: { label: "دافئ",   icon: Sun,       className: "bg-amber-500/10 text-amber-300 border-amber-500/30" },
  cold: { label: "بارد",   icon: Snowflake, className: "bg-blue-500/10 text-blue-300 border-blue-500/30" },
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

const field = "bg-[#111827] border border-white/10 rounded-xl py-2.5 px-3 text-sm text-white placeholder-gray-400 focus:outline-none focus:border-[#C9A84C]/50 transition-colors";
const ghostBtn = "bg-white/5 border border-white/10 text-white px-4 py-2 rounded-lg flex items-center gap-2 hover:bg-white/10 transition-colors disabled:opacity-50 text-sm";

function SkeletonRow() {
  return (
    <tr className="border-b border-white/5" aria-hidden>
      {Array.from({ length: 8 }).map((_, i) => (
        <td key={i} className="py-4 px-4"><div className="h-4 rounded bg-white/5 animate-pulse" style={{ width: i === 0 ? "160px" : "90px" }} /></td>
      ))}
    </tr>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function CustomersPage() {
  const [data, setData]       = useState<CustomerPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [notice, setNotice]   = useState<string | null>(null);
  const [page, setPage]       = useState(1);
  const [search, setSearch]   = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [source, setSource]   = useState("");
  const [tag, setTag]         = useState("");
  const [debouncedTag, setDebouncedTag] = useState("");
  const [consent, setConsent] = useState<"" | "true" | "false">("");
  const [archived, setArchived] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Customer | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => { setDebouncedSearch(search.trim()); setDebouncedTag(tag.trim()); setPage(1); }, 350);
    return () => clearTimeout(t);
  }, [search, tag]);

  const filters = {
    search: debouncedSearch || undefined,
    source: source || undefined,
    tag: debouncedTag || undefined,
    opt_in: consent === "" ? undefined : consent === "true",
    archived,
  };
  const filterKey = JSON.stringify(filters);
  const hasFilters = Boolean(debouncedSearch || source || debouncedTag || consent || archived);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchCustomers({ page, page_size: PAGE_SIZE, sort: "lead_score", ...JSON.parse(filterKey) }));
    } catch (err) {
      setError(customerErrorInfo(err, "فشل تحميل قائمة العملاء").message);
    } finally {
      setLoading(false);
    }
  }, [page, filterKey]);

  useEffect(() => { void load(); }, [load]);

  const clearFilters = () => { setSearch(""); setTag(""); setSource(""); setConsent(""); setArchived(false); setPage(1); };

  const handleToggleVip = async (customer: Customer) => {
    setSavingId(customer.id);
    setData((prev) => prev ? { ...prev, items: prev.items.map((c) => c.id === customer.id ? { ...c, is_vip: !c.is_vip } : c) } : prev);
    try {
      const updated = await updateCustomer(customer.id, { is_vip: !customer.is_vip });
      setData((prev) => prev ? { ...prev, items: prev.items.map((c) => (c.id === updated.id ? { ...c, ...updated, lead_score: c.lead_score, lead_tier: c.lead_tier } : c)) } : prev);
    } catch (err) {
      setData((prev) => prev ? { ...prev, items: prev.items.map((c) => c.id === customer.id ? { ...c, is_vip: customer.is_vip } : c) } : prev);
      setError(customerErrorInfo(err, "تعذّر تحديث حالة العميل. حاول مجدداً.").message);
    } finally {
      setSavingId(null);
    }
  };

  const handleExport = async () => {
    setExporting(true); setError(null);
    try {
      saveBlob(await exportCustomersCsv({ ...filters }), `omniflow-customers-${new Date().toISOString().slice(0, 10)}.csv`);
    } catch { setError("تعذّر تصدير العملاء"); }
    finally { setExporting(false); }
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
            <Users className="w-8 h-8 text-[#C9A84C]" aria-hidden />
            العملاء والفرص البيعية
          </h1>
          <p className="text-gray-300 mt-2 text-sm">
            مرتّبون من الأقرب للشراء إلى الأبرد. العميل يُنشأ تلقائيًا عند أول رسالة، أو تضيفه يدويًا أو تستورده من ملف.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => void load()} disabled={loading} className={ghostBtn}>
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} aria-hidden />تحديث
          </button>
          <button onClick={() => void handleExport()} disabled={exporting || total === 0} className={ghostBtn}>
            {exporting ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Download className="w-4 h-4" aria-hidden />}تصدير CSV
          </button>
          <button id="import-customers-btn" onClick={() => setImportOpen(true)} className={ghostBtn}><Upload className="w-4 h-4" aria-hidden />استيراد</button>
          <button id="add-customer-btn" onClick={() => { setEditTarget(null); setFormOpen(true); }}
            className="bg-[#C9A84C] text-[#0A0F1C] font-semibold px-4 py-2 rounded-lg flex items-center gap-2 hover:opacity-90 text-sm">
            <Plus className="w-4 h-4" aria-hidden />إضافة عميل
          </button>
        </div>
      </div>

      {notice && (
        <div role="status" className="flex items-center justify-between gap-3 bg-emerald-500/10 border border-emerald-500/40 text-emerald-200 rounded-xl px-4 py-3 text-sm">
          <span>✓ {notice}</span>
          <button onClick={() => setNotice(null)} aria-label="إغلاق" className="text-xs underline">إغلاق</button>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[16rem]">
          <Search className="absolute right-3 top-3 w-5 h-5 text-gray-400" aria-hidden />
          <input id="customer-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={100}
            placeholder="ابحث بالاسم أو الجوال أو البريد أو المدينة…" aria-label="بحث في العملاء" className={`${field} w-full pr-11`} />
        </div>
        <Select id="source-filter" value={source} onChange={(e) => { setSource(e.target.value); setPage(1); }} aria-label="المصدر / القناة" className={field}>
          <option value="">كل المصادر</option>
          {Object.entries(SOURCE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
        <input value={tag} onChange={(e) => setTag(e.target.value)} maxLength={40} placeholder="وسم" aria-label="وسم" className={`${field} w-32`} />
        <Select id="consent-filter" value={consent} onChange={(e) => { setConsent(e.target.value as "" | "true" | "false"); setPage(1); }} aria-label="الموافقة التسويقية" className={field}>
          <option value="">كل حالات الموافقة</option>
          <option value="true">موافق على التسويق</option>
          <option value="false">غير موافق</option>
        </Select>
        <label className="flex items-center gap-2 text-sm text-gray-200 cursor-pointer">
          <input type="checkbox" checked={archived} onChange={(e) => { setArchived(e.target.checked); setPage(1); }} />المؤرشفون فقط
        </label>
        {hasFilters && <button onClick={clearFilters} className="text-sm text-gray-300 underline">مسح الفلاتر</button>}
        <div className="bg-[#111827] border border-[#C9A84C]/20 rounded-xl px-4 py-2 flex items-center gap-3 ms-auto">
          <span className="text-sm text-gray-300">{hasFilters ? "المطابقون" : "إجمالي العملاء"}</span>
          <span className="text-xl font-bold text-[#C9A84C]">{loading && !data ? "…" : total.toLocaleString("ar")}</span>
        </div>
      </div>

      {error && (
        <div role="alert" className="flex items-center justify-between gap-3 bg-red-500/10 border border-red-500/40 text-red-200 rounded-xl px-4 py-3 text-sm">
          <span className="flex items-center gap-2"><AlertTriangle className="w-4 h-4" aria-hidden />{error}</span>
          <button onClick={() => void load()} className="flex items-center gap-1 text-xs hover:opacity-80"><RefreshCw className="w-3.5 h-3.5" aria-hidden />إعادة المحاولة</button>
        </div>
      )}

      {/* Table */}
      <div className="bg-[#111827] border border-white/5 rounded-2xl overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-right" aria-label="قائمة العملاء">
            <thead>
              <tr className="border-b border-white/10 bg-white/5 text-gray-300 text-sm">
                <th className="py-3 px-4 font-medium">العميل</th>
                <th className="py-3 px-4 font-medium">الجوال</th>
                <th className="py-3 px-4 font-medium">المصدر والوسوم</th>
                <th className="py-3 px-4 font-medium">الموافقة التسويقية</th>
                <th className="py-3 px-4 font-medium">احتمالية الشراء</th>
                <th className="py-3 px-4 font-medium">بطاقة التواصل</th>
                <th className="py-3 px-4 font-medium">آخر تفاعل</th>
                <th className="py-3 px-4 font-medium text-center">VIP</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={8} className="text-center py-20 px-6">
                    <Users className="w-12 h-12 text-gray-500 mx-auto mb-4" aria-hidden />
                    <h3 className="text-lg font-semibold text-gray-100 mb-1">{hasFilters ? "لا توجد نتائج مطابقة" : "لا يوجد عملاء بعد"}</h3>
                    <p className="text-sm text-gray-300 mb-4">
                      {hasFilters ? "جرّب تغيير الفلاتر أو امسحها." : "سيظهر العملاء تلقائيًا بمجرد بدء محادثات عبر قنواتك، ويمكنك إضافة عملائك الحاليين الآن."}
                    </p>
                    {hasFilters ? (
                      <button onClick={clearFilters} className={`${ghostBtn} mx-auto`}>مسح الفلاتر</button>
                    ) : (
                      <div className="flex justify-center gap-2">
                        <button onClick={() => { setEditTarget(null); setFormOpen(true); }} className="bg-[#C9A84C] text-[#0A0F1C] font-semibold px-4 py-2 rounded-lg text-sm">إضافة عميل</button>
                        <button onClick={() => setImportOpen(true)} className={ghostBtn}>استيراد من ملف</button>
                      </div>
                    )}
                  </td>
                </tr>
              ) : (
                items.map((customer) => {
                  const name = customer.display_name || customer.whatsapp_profile_name || "عميل غير معروف";
                  const tier = TIER_STYLE[customer.lead_tier] ?? TIER_STYLE.cold;
                  const TierIcon = tier.icon;
                  const vcardOpened = Boolean(customer.vcard_opened_at);
                  const vcardSaved = customer.vcard_state === "STATE_CONTACT_SAVED_VERIFIED";
                  const channelId = Object.values(customer.channel_user_ids)[0];
                  return (
                    <tr key={customer.id} className={`border-b border-white/5 hover:bg-white/[0.03] transition-colors ${customer.archived_at ? "opacity-70" : ""}`}>
                      <td className="py-3 px-4">
                        <button onClick={() => setDetailId(customer.id)} className="flex items-center gap-3 text-right w-full" aria-label={`فتح ملف ${name}`}>
                          <span className="w-10 h-10 rounded-full bg-gradient-to-br from-[#C9A84C] to-yellow-700 flex items-center justify-center text-[#0A0F1C] font-bold text-sm shrink-0">{initials(name)}</span>
                          <span className="min-w-0">
                            <span className="font-semibold text-gray-100 block truncate hover:underline">{name}</span>
                            {customer.city && <span className="text-xs text-gray-300 block truncate">{customer.city}</span>}
                            {customer.is_processing_restricted && (
                              <span className="inline-flex items-center gap-1 text-[11px] text-orange-300 mt-0.5"><ShieldOff className="w-3 h-3" aria-hidden />معالجة البيانات مقيّدة</span>
                            )}
                          </span>
                        </button>
                      </td>
                      <td className="py-3 px-4 text-gray-200 text-sm" dir="ltr">
                        {customer.phone_e164 ? (
                          <span className="flex items-center justify-end gap-2">{customer.phone_e164}<Phone className="w-4 h-4 text-gray-400" aria-hidden /></span>
                        ) : (
                          <span className="text-gray-300 text-xs" dir="rtl" title={channelId ? `معرّف القناة: ${channelId}` : undefined}>بلا رقم (معرّف قناة)</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-xs">
                        <span className="inline-block rounded-md bg-white/10 px-2 py-0.5 text-gray-100">{SOURCE_LABELS[customer.source] ?? customer.source}</span>
                        {customer.tags.slice(0, 3).map((t) => <span key={t} className="inline-block rounded-md border border-white/15 px-2 py-0.5 ms-1 text-gray-200">{t}</span>)}
                        {customer.tags.length > 3 && <span className="text-gray-300 ms-1">+{customer.tags.length - 3}</span>}
                      </td>
                      <td className="py-3 px-4 text-xs">
                        {customer.marketing_opt_in ? (
                          <span className="inline-flex items-center gap-1.5 text-emerald-300"><ShieldCheck className="w-4 h-4" aria-hidden />موافق</span>
                        ) : customer.opt_out_at ? (
                          <span className="inline-flex items-center gap-1.5 text-orange-300"><ShieldOff className="w-4 h-4" aria-hidden />ألغى الاشتراك</span>
                        ) : (
                          <span className="text-gray-300">غير موافق</span>
                        )}
                      </td>
                      <td className="py-3 px-4">
                        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border ${tier.className}`}>
                          <TierIcon className="w-3.5 h-3.5" aria-hidden /> {tier.label} · {customer.lead_score}%
                        </span>
                      </td>
                      <td className="py-3 px-4 text-sm">
                        {vcardOpened ? (
                          <span className="inline-flex items-center gap-1.5 text-emerald-300"><MailCheck className="w-4 h-4" aria-hidden /> تم فتح البطاقة</span>
                        ) : vcardSaved ? (
                          <span className="inline-flex items-center gap-1.5 text-[#C9A84C]"><Contact className="w-4 h-4" aria-hidden /> تم حفظ الرقم</span>
                        ) : (
                          <span className="text-gray-300">لم تُحفظ بعد</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-gray-300 text-sm">
                        <span className="flex items-center gap-2"><Calendar className="w-4 h-4" aria-hidden />{timeAgo(customer.last_interaction_at)}</span>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <button onClick={() => void handleToggleVip(customer)} disabled={savingId === customer.id} aria-pressed={customer.is_vip}
                          aria-label={customer.is_vip ? "إلغاء تمييز VIP" : "تمييز كعميل VIP"}
                          className={`p-2 rounded-lg transition-colors disabled:opacity-50 ${customer.is_vip ? "text-[#C9A84C] bg-[#C9A84C]/10" : "text-gray-300 hover:text-[#C9A84C]"}`}>
                          {savingId === customer.id ? <Loader2 className="w-5 h-5 animate-spin" aria-hidden /> : <Star className="w-5 h-5" fill={customer.is_vip ? "currentColor" : "none"} aria-hidden />}
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="p-4 border-t border-white/5 flex items-center justify-between text-sm text-gray-300">
          <span>{total === 0 ? "لا توجد سجلات" : `عرض ${rangeStart} إلى ${rangeEnd} من ${total.toLocaleString("ar")} عميل`}</span>
          <div className="flex gap-2">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || loading} className="px-3 py-1 bg-white/5 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed">السابق</button>
            <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages || loading} className="px-3 py-1 bg-white/5 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed">التالي</button>
          </div>
        </div>
      </div>

      <CustomerFormDialog
        open={formOpen} customer={editTarget} onClose={() => setFormOpen(false)}
        onSaved={(c) => { setNotice(editTarget ? "تم حفظ التعديلات." : `أُضيف العميل ${c.display_name || c.phone_e164 || ""}.`); void load(); if (editTarget) setDetailId(c.id); }}
        onOpenExisting={(id) => setDetailId(id)}
      />
      <CustomerDetailDrawer
        customerId={detailId} onClose={() => setDetailId(null)} onChanged={() => void load()}
        onEdit={(c) => { setEditTarget(c); setFormOpen(true); }}
      />
      <ImportWizard adapter={customersImportAdapter} open={importOpen} onClose={() => setImportOpen(false)}
        onDone={() => { setNotice("انتهى الاستيراد — حدّثنا القائمة."); void load(); }} />
    </div>
  );
}
