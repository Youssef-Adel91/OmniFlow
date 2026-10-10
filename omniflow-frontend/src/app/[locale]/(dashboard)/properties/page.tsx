"use client";

/**
 * app/[locale]/(dashboard)/properties/page.tsx
 *
 * Property Listings management page for the OmniFlow dashboard.
 *
 * Features:
 *   - Luxury Dark Navy/Gold theme — RTL Arabic-first layout
 *   - Data table with columns: Type, Status, Price, City/District, Area, Actions
 *   - Status badge with color coding (Gold = active, Gray = pending, Red = suspended)
 *   - Qdrant vector sync indicator (show if qdrant_point_id is set)
 *   - Add/Edit via PropertyFormSheet slide-over
 *   - Inline delete with confirmation dialog
 *   - Pagination controls
 *   - Loading skeleton + empty state
 *   - Fully accessible with ARIA labels
 */

import React, { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import { PropertyFormSheet } from "@/components/properties/PropertyFormSheet";
import ImportWizard from "@/components/properties/ImportWizard";
import { ACTIVE_STATUSES, fetchImport, importErrorMessage, saveBlob, type ImportJob } from "@/lib/api/propertyImport";
import {
  listProperties,
  deleteProperty,
  bulkDeleteProperties,
  bulkSetPropertyStatus,
  exportPropertiesCsv,
  reindexProperties,
  type BulkScope,
  type PropertySort,
  type PropertyListing,
  type PropertyListingPage,
  type ListingStatus,
  type PropertyType,
  PROPERTY_TYPE_LABELS,
  LISTING_STATUS_LABELS,
} from "@/lib/api/properties";
import { Select } from "@/components/ui/Select";

// ══════════════════════════════════════════════════════════════════════════════
// Colour palette
// ══════════════════════════════════════════════════════════════════════════════
const NAVY     = "#0B1121";
const NAVY_800 = "#0D1526";
const NAVY_700 = "#111827";
const NAVY_600 = "#1A2540";
const NAVY_500 = "#243058";
const GOLD     = "#C9A84C";
const GOLD_DIM = "#A07C30";
const TEXT     = "#E2E8F0";
const TEXT_DIM = "#8B8FA8";
const BORDER   = "rgba(201, 168, 76, 0.18)";
const RED      = "#E05252";
const GREEN    = "#4CAF50";

// ══════════════════════════════════════════════════════════════════════════════
// Sub-components
// ══════════════════════════════════════════════════════════════════════════════

function StatusBadge({ status }: { status: ListingStatus }) {
  const { ar, color } = LISTING_STATUS_LABELS[status] ?? {
    ar: status, color: TEXT_DIM,
  };
  return (
    <span style={{
      display: "inline-flex",
      alignItems: "center",
      gap: "6px",
      padding: "4px 10px",
      borderRadius: "20px",
      fontSize: "12px",
      fontWeight: 600,
      color,
      background: `${color}18`,
      border: `1px solid ${color}40`,
      whiteSpace: "nowrap",
    }}>
      <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: color, flexShrink: 0 }} />
      {ar}
    </span>
  );
}

function VectorBadge({ synced }: { synced: boolean }) {
  return (
    <span
      title={synced ? "مفهرس في Qdrant" : "لم يتم الفهرسة بعد"}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "4px",
        padding: "3px 8px",
        borderRadius: "12px",
        fontSize: "11px",
        fontWeight: 600,
        color: synced ? GREEN : TEXT_DIM,
        background: synced ? `${GREEN}15` : `${TEXT_DIM}15`,
        border: `1px solid ${synced ? GREEN : TEXT_DIM}30`,
      }}
    >
      {synced ? "⚡ مفهرس" : "○ غير مفهرس"}
    </span>
  );
}

function SkeletonRow() {
  return (
    <tr>
      {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
        <td key={i} style={{ padding: "16px 20px" }}>
          <div style={{
            height: "16px",
            borderRadius: "8px",
            background: `linear-gradient(90deg, ${NAVY_500} 25%, ${NAVY_600} 50%, ${NAVY_500} 75%)`,
            backgroundSize: "200% 100%",
            animation: "shimmer 1.5s infinite",
            width: i === 1 ? "80px" : i === 4 ? "120px" : "60px",
          }} />
        </td>
      ))}
    </tr>
  );
}

function EmptyState({ onAdd, onImport, filtered, onClear }: {
  onAdd: () => void; onImport: () => void; filtered: boolean; onClear: () => void;
}) {
  return (
    <tr>
      <td colSpan={8} style={{ textAlign: "center", padding: "72px 24px" }}>
        <div style={{ fontSize: "52px", marginBottom: "16px" }}>{filtered ? "🔍" : "🏠"}</div>
        <h3 style={{ margin: "0 0 8px", color: TEXT, fontSize: "18px", fontWeight: 600 }}>
          {filtered ? "لا توجد عقارات مطابقة للبحث" : "لا توجد عقارات مضافة بعد"}
        </h3>
        <p style={{ margin: "0 0 24px", color: TEXT_DIM, fontSize: "14px" }}>
          {filtered
            ? "جرّب تعديل كلمات البحث أو الفلاتر."
            : "أضف أول عقار، أو استورد مخزونك كاملًا من ملف Excel/CSV دفعة واحدة."}
        </p>
        {filtered ? (
          <button onClick={onClear} style={btnGhost}>✕ مسح البحث والفلاتر</button>
        ) : (
          <div style={{ display: "flex", gap: "12px", justifyContent: "center", flexWrap: "wrap" }}>
            <button onClick={onAdd} style={btnGold}>+ إضافة عقار جديد</button>
            <button onClick={onImport} style={btnGhost}>⬆ استيراد من ملف</button>
          </div>
        )}
      </td>
    </tr>
  );
}

function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
  isDeleting,
  title = "تأكيد حذف العقار",
  message = "سيتم حذف العقار نهائياً من قاعدة البيانات ومن فهرس Qdrant. هذا الإجراء لا يمكن التراجع عنه.",
  confirmLabel = "🗑️ نعم، احذف العقار",
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  isDeleting: boolean;
  title?: string;
  message?: string;
  confirmLabel?: string;
}) {
  if (!open) return null;
  return (
    <>
      <div
        onClick={onCancel}
        style={{
          position: "fixed", inset: 0,
          background: "rgba(0,0,0,0.65)",
          backdropFilter: "blur(4px)",
          zIndex: 60,
        }}
      />
      <div dir="rtl" style={{
        position: "fixed",
        top: "50%", left: "50%",
        transform: "translate(-50%,-50%)",
        background: NAVY_700,
        border: `1px solid ${RED}40`,
        borderRadius: "16px",
        padding: "32px",
        width: "min(420px, 90vw)",
        zIndex: 61,
        textAlign: "center",
        boxShadow: "0 20px 60px rgba(0,0,0,0.7)",
      }}>
        <div style={{ fontSize: "42px", marginBottom: "16px" }}>🗑️</div>
        <h3 style={{ margin: "0 0 8px", color: TEXT, fontSize: "18px", fontWeight: 700 }}>
          {title}
        </h3>
        <p style={{ margin: "0 0 28px", color: TEXT_DIM, fontSize: "14px", lineHeight: "1.6" }}>
          {message}
        </p>
        <div style={{ display: "flex", gap: "12px", justifyContent: "center" }}>
          <button onClick={onCancel} disabled={isDeleting} style={btnGhost}>
            إلغاء
          </button>
          <button onClick={onConfirm} disabled={isDeleting} style={{
            ...btnDanger,
            ...(isDeleting ? { opacity: 0.5, cursor: "not-allowed" } : {}),
          }}>
            {isDeleting ? "⏳ جارٍ التنفيذ..." : confirmLabel}
          </button>
        </div>
      </div>
    </>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Main Page
// ══════════════════════════════════════════════════════════════════════════════

export default function PropertiesPage() {
  const params  = useParams();
  const locale  = (params?.locale as string) ?? "ar";
  const isRtl   = locale === "ar";

  // ── State ──────────────────────────────────────────────────────────────────
  const [page, setPage]                     = useState(1);
  const [data, setData]                     = useState<PropertyListingPage | null>(null);
  const [loading, setLoading]               = useState(true);
  const [fetchError, setFetchError]         = useState<string | null>(null);
  const [sheetOpen, setSheetOpen]           = useState(false);
  const [editTarget, setEditTarget]         = useState<PropertyListing | null>(null);
  const [deleteTarget, setDeleteTarget]     = useState<PropertyListing | null>(null);
  const [isDeleting, setIsDeleting]         = useState(false);
  const [statusFilter, setStatusFilter]     = useState<ListingStatus | "">("");
  const [typeFilter, setTypeFilter]         = useState<PropertyType | "">("");
  const [searchInput, setSearchInput]       = useState("");
  const [search, setSearch]                 = useState("");
  const [city, setCity]                     = useState("");
  const [priceMin, setPriceMin]             = useState("");
  const [priceMax, setPriceMax]             = useState("");
  const [sort, setSort]                     = useState<PropertySort>("newest");
  const [importOpen, setImportOpen]         = useState(false);
  const [selected, setSelected]             = useState<Set<string>>(new Set());
  const [bulkStatus, setBulkStatus]         = useState<ListingStatus | "">("");
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkBusy, setBulkBusy]             = useState(false);
  const [notice, setNotice]                 = useState<string | null>(null);
  const [exporting, setExporting]           = useState(false);
  const [indexedFilter, setIndexedFilter]   = useState<"" | "true" | "false">("");
  const [allMatching, setAllMatching]       = useState(false);      // "select all N matching the filter"
  const [unindexed, setUnindexed]           = useState<number | null>(null);
  const [reindexJob, setReindexJob]         = useState<ImportJob | null>(null);

  const filterParams = {
    status:        statusFilter || undefined,
    property_type: (typeFilter as PropertyType) || undefined,
    city:          city || undefined,
    search:        search || undefined,
    price_min:     priceMin !== "" && Number.isFinite(Number(priceMin)) ? Number(priceMin) : undefined,
    price_max:     priceMax !== "" && Number.isFinite(Number(priceMax)) ? Number(priceMax) : undefined,
    indexed:       indexedFilter === "" ? undefined : indexedFilter === "true",
    sort,
  };
  const hasFilters = Boolean(statusFilter || typeFilter || city || search || priceMin || priceMax || indexedFilter);
  // The filters (without sort) that "all matching" actions send to the server.
  const { sort: _sort, ...matchFilters } = filterParams;
  const filterKey = JSON.stringify(filterParams);

  // ── Fetch ──────────────────────────────────────────────────────────────────
  const fetchListings = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const result = await listProperties({ page, limit: 15, ...filterParams });
      setData(result);
      setSelected(new Set());
      setAllMatching(false);
      listProperties({ limit: 1, indexed: false }).then((r) => setUnindexed(r.total)).catch(() => undefined);
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? err?.message ?? "فشل تحميل العقارات";
      setFetchError(typeof msg === "string" ? msg : "فشل تحميل العقارات");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, filterKey]);

  useEffect(() => { void fetchListings(); }, [fetchListings]);

  // Debounce the search box (and reset to page 1 when the term changes).
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch((prev) => {
        const next = searchInput.trim();
        if (next !== prev) setPage(1);
        return next;
      });
    }, 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  const clearFilters = () => {
    setStatusFilter(""); setTypeFilter(""); setCity(""); setPriceMin(""); setPriceMax(""); setIndexedFilter("");
    setSearchInput(""); setSearch(""); setPage(1);
  };

  const toggleOne = (id: string) => {
    setAllMatching(false);
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  };
  const allOnPageSelected = (data?.items.length ?? 0) > 0 && (data?.items ?? []).every((i) => selected.has(i.listing_id));
  const toggleAll = () => {
    setAllMatching(false);
    setSelected(allOnPageSelected ? new Set() : new Set((data?.items ?? []).map((i) => i.listing_id)));
  };
  const MATCH_CAP = 20000;
  const canSelectAllMatching = allOnPageSelected && !allMatching && (data?.total ?? 0) > (data?.items.length ?? 0);
  const selectionCount = allMatching ? (data?.total ?? 0) : selected.size;
  const scope = (): BulkScope => (allMatching ? { filters: matchFilters } : { ids: [...selected] });

  // Poll the background re-index job; refresh the list when it ends.
  useEffect(() => {
    if (!reindexJob || !ACTIVE_STATUSES.includes(reindexJob.status)) return;
    const id = reindexJob.import_id;
    const t = setInterval(async () => {
      try {
        const j = await fetchImport(id);
        setReindexJob(j);
        if (!ACTIVE_STATUSES.includes(j.status)) {
          setNotice(j.index_failed > 0
            ? `انتهت إعادة الفهرسة: فُهرس ${j.indexed} وفشل ${j.index_failed}.`
            : `اكتملت إعادة الفهرسة: فُهرس ${j.indexed} عقارًا.`);
          void fetchListings();
        }
      } catch { /* transient */ }
    }, 1500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reindexJob]);

  const startReindex = async (sc: BulkScope) => {
    setBulkBusy(true); setFetchError(null);
    try {
      const r = await reindexProperties(sc);
      if (!r.job) setNotice("لا توجد عقارات لإعادة فهرستها.");
      else setReindexJob(r.job);
    } catch (err) {
      setFetchError(importErrorMessage(err, "تعذّر بدء إعادة الفهرسة"));
    } finally { setBulkBusy(false); }
  };

  const handleBulkStatus = async () => {
    if (!bulkStatus || selectionCount === 0) return;
    setBulkBusy(true); setFetchError(null);
    try {
      const r = await bulkSetPropertyStatus(scope(), bulkStatus);
      setNotice(`تم تحديث حالة ${r.updated ?? 0} عقارًا${r.not_found ? ` (${r.not_found} غير موجود)` : ""}${r.reindex_job_id ? " — تجري إعادة الفهرسة في الخلفية." : "."}`);
      setBulkStatus("");
      if (r.reindex_job_id) fetchImport(r.reindex_job_id).then(setReindexJob).catch(() => undefined);
      await fetchListings();
    } catch (err) {
      setFetchError(importErrorMessage(err, "فشل تحديث الحالة"));
    } finally { setBulkBusy(false); }
  };

  const handleBulkDelete = async () => {
    setBulkBusy(true); setFetchError(null);
    try {
      const r = await bulkDeleteProperties([...selected]);
      setNotice(`تم حذف ${r.deleted ?? 0} عقارًا.`);
      setBulkDeleteOpen(false);
      await fetchListings();
    } catch (err: any) {
      const msg = err?.response?.data?.detail;
      setFetchError(typeof msg === "string" ? msg : "فشل الحذف الجماعي");
      setBulkDeleteOpen(false);
    } finally { setBulkBusy(false); }
  };

  const handleExport = async () => {
    setExporting(true); setFetchError(null);
    try {
      saveBlob(await exportPropertiesCsv(filterParams), `omniflow-properties-${new Date().toISOString().slice(0, 10)}.csv`);
    } catch (err: any) {
      setFetchError("تعذّر تصدير العقارات");
    } finally { setExporting(false); }
  };

  // ── Handlers ───────────────────────────────────────────────────────────────
  const handleAddClick = () => {
    setEditTarget(null);
    setSheetOpen(true);
  };

  const handleEditClick = (listing: PropertyListing) => {
    setEditTarget(listing);
    setSheetOpen(true);
  };

  const handleFormSuccess = (listing: PropertyListing) => {
    // Optimistic: re-fetch the page to show updated data
    fetchListings();
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await deleteProperty(deleteTarget.listing_id);
      setDeleteTarget(null);
      fetchListings();
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? err?.message ?? "فشل الحذف";
      setFetchError(typeof msg === "string" ? msg : "فشل الحذف");
      setDeleteTarget(null);
    } finally {
      setIsDeleting(false);
    }
  };

  // ── Formatters ─────────────────────────────────────────────────────────────
  const formatPrice = (price: number | null) =>
    price != null
      ? new Intl.NumberFormat("ar-SA", { style: "currency", currency: "SAR", maximumFractionDigits: 0 }).format(price)
      : "—";

  const formatArea = (area: number | null) =>
    area != null ? `${area.toLocaleString("ar")} م²` : "—";

  const items = data?.items ?? [];

  return (
    <div dir={isRtl ? "rtl" : "ltr"} style={styles.page}>

      {/* ── Global animation keyframes ─────────────────────────────────── */}
      <style>{`
        @keyframes shimmer {
          0%   { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
        @keyframes fadeInUp {
          from { opacity: 0; transform: translateY(16px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to   { transform: translateX(0); }
        }
        button:hover:not(:disabled) { opacity: 0.88; }
        tr:hover td { background: rgba(201,168,76,0.04) !important; }
      `}</style>

      {/* ── Page Header ────────────────────────────────────────────────── */}
      <div style={styles.pageHeader}>
        <div>
          <h1 style={styles.pageTitle}>🏠 إدارة العقارات</h1>
          <p style={styles.pageSubtitle}>
            {data
              ? `${data.total.toLocaleString("ar")} عقار — ${data.pages} صفحة`
              : "إدارة المخزون العقاري وفهرسة RAG"}
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <button id="export-properties-btn" onClick={handleExport} disabled={exporting || !data?.total} style={btnGhost}>
            {exporting ? "⏳ جارٍ التصدير…" : "⬇ تصدير CSV"}
          </button>
          {(unindexed ?? 0) > 0 && (
            <button id="reindex-unindexed-btn" onClick={() => void startReindex({ filters: { indexed: false } })}
              disabled={bulkBusy || (reindexJob != null && ACTIVE_STATUSES.includes(reindexJob.status))} style={btnGhost}
              title="العقارات غير المفهرسة لا يجدها المساعد في البحث">
              🔄 فهرسة غير المفهرس ({(unindexed ?? 0).toLocaleString("ar")})
            </button>
          )}
          <button id="import-properties-btn" onClick={() => setImportOpen(true)} style={btnGhost}>
            ⬆ استيراد
          </button>
          <button id="add-property-btn" onClick={handleAddClick} style={btnGold}>
            + إضافة عقار
          </button>
        </div>
      </div>

      {notice && (
        <div role="status" style={{ ...styles.errorBanner, color: GREEN, background: `${GREEN}12`, border: `1px solid ${GREEN}40` }}>
          ✓ {notice}
          <button onClick={() => setNotice(null)} aria-label="إغلاق" style={{ ...btnGhost, fontSize: "12px", padding: "2px 10px" }}>✕</button>
        </div>
      )}

      {reindexJob && (
        <div role="status" aria-live="polite" data-testid="reindex-banner" style={{ ...styles.errorBanner, color: TEXT, background: `${GOLD}10`, border: `1px solid ${BORDER}`, flexDirection: "column", alignItems: "stretch", gap: "8px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
            <span>
              🔄 {ACTIVE_STATUSES.includes(reindexJob.status) ? "جارٍ إعادة الفهرسة" : reindexJob.status === "completed" ? "اكتملت إعادة الفهرسة" : "توقفت إعادة الفهرسة"}
              {" — "}{reindexJob.indexed.toLocaleString("ar")} / {reindexJob.total_rows.toLocaleString("ar")}
              {reindexJob.index_failed > 0 && ` (فشل ${reindexJob.index_failed.toLocaleString("ar")})`}
            </span>
            {!ACTIVE_STATUSES.includes(reindexJob.status) && (
              <button onClick={() => setReindexJob(null)} aria-label="إغلاق" style={{ ...btnGhost, fontSize: "12px", padding: "2px 10px" }}>✕</button>
            )}
          </div>
          <div role="progressbar" aria-valuenow={Math.round(reindexJob.percent)} aria-valuemin={0} aria-valuemax={100} style={{ height: "8px", borderRadius: "6px", background: NAVY_500, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${reindexJob.percent}%`, background: GOLD, transition: "width .4s" }} />
          </div>
          {(reindexJob.index_error || reindexJob.error_message) && (
            <span dir="ltr" style={{ color: RED, fontSize: "12px", wordBreak: "break-word" }}>{reindexJob.index_error ?? reindexJob.error_message}</span>
          )}
        </div>
      )}

      {/* ── Filter Bar ─────────────────────────────────────────────────── */}
      <div style={styles.filterBar}>
        <input
          id="property-search"
          type="search"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="بحث: رقم الإعلان، المدينة، الحي، الوصف…"
          aria-label="بحث في العقارات"
          maxLength={100}
          style={{ ...styles.filterSelect, minWidth: "260px", flex: "1 1 260px" }}
        />
        <span style={styles.filterLabel}>تصفية:</span>
        <Select
          id="status-filter"
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value as any); setPage(1); }}
          style={styles.filterSelect}
        >
          <option value="">كل الحالات</option>
          {(Object.entries(LISTING_STATUS_LABELS) as [ListingStatus, { ar: string }][]).map(
            ([val, { ar }]) => <option key={val} value={val}>{ar}</option>
          )}
        </Select>
        <Select
          id="type-filter"
          value={typeFilter}
          onChange={(e) => { setTypeFilter(e.target.value as any); setPage(1); }}
          style={styles.filterSelect}
        >
          <option value="">كل الأنواع</option>
          {(Object.entries(PROPERTY_TYPE_LABELS) as [PropertyType, { ar: string }][]).map(
            ([val, { ar }]) => <option key={val} value={val}>{ar}</option>
          )}
        </Select>
        <input
          id="city-filter" value={city} maxLength={100} placeholder="المدينة" aria-label="المدينة"
          onChange={(e) => { setCity(e.target.value); setPage(1); }}
          style={{ ...styles.filterSelect, minWidth: "110px", width: "130px" }}
        />
        <input
          id="price-min" type="number" min={0} inputMode="numeric" value={priceMin} placeholder="السعر من" aria-label="السعر من"
          onChange={(e) => { setPriceMin(e.target.value); setPage(1); }}
          style={{ ...styles.filterSelect, minWidth: "90px", width: "110px" }} dir="ltr"
        />
        <input
          id="price-max" type="number" min={0} inputMode="numeric" value={priceMax} placeholder="السعر إلى" aria-label="السعر إلى"
          onChange={(e) => { setPriceMax(e.target.value); setPage(1); }}
          style={{ ...styles.filterSelect, minWidth: "90px", width: "110px" }} dir="ltr"
        />
        <Select
          id="indexed-filter" value={indexedFilter} aria-label="حالة الفهرسة" style={styles.filterSelect}
          onChange={(e) => { setIndexedFilter(e.target.value as "" | "true" | "false"); setPage(1); }}
        >
          <option value="">كل حالات الفهرسة</option>
          <option value="true">مفهرس</option>
          <option value="false">غير مفهرس</option>
        </Select>
        <Select
          id="sort-select" value={sort} aria-label="الترتيب" style={styles.filterSelect}
          onChange={(e) => { setSort(e.target.value as PropertySort); setPage(1); }}
        >
          <option value="newest">الأحدث أولًا</option>
          <option value="oldest">الأقدم أولًا</option>
          <option value="price_asc">السعر: الأقل أولًا</option>
          <option value="price_desc">السعر: الأعلى أولًا</option>
          <option value="area_desc">المساحة: الأكبر أولًا</option>
        </Select>
        {hasFilters && (
          <button onClick={clearFilters} style={btnGhost}>
            ✕ مسح الفلاتر
          </button>
        )}
      </div>

      {/* ── Error Banner ───────────────────────────────────────────────── */}
      {fetchError && (
        <div role="alert" style={styles.errorBanner}>
          ⚠️ {fetchError}
          <button onClick={fetchListings} style={{ ...btnGhost, fontSize: "12px", padding: "4px 12px" }}>
            إعادة المحاولة
          </button>
        </div>
      )}

      {selected.size > 0 && (
        <div role="region" aria-label="إجراءات جماعية" style={{ ...styles.filterBar, borderColor: GOLD }}>
          <strong style={{ color: GOLD }}>{selectionCount.toLocaleString("ar")} محدد{allMatching ? " (كل المطابق للفلتر)" : ""}</strong>
          {canSelectAllMatching && (
            data!.total <= MATCH_CAP ? (
              <button id="select-all-matching" onClick={() => setAllMatching(true)} style={btnGhost}>
                تحديد كل الـ {data!.total.toLocaleString("ar")} عقارًا المطابقة للفلتر
              </button>
            ) : (
              <span style={{ color: TEXT_DIM, fontSize: "12px" }}>النتائج تتجاوز {MATCH_CAP.toLocaleString("ar")} — ضيّق الفلاتر لتحديدها كلها.</span>
            )
          )}
          <Select
            id="bulk-status" value={bulkStatus} aria-label="تغيير الحالة" style={styles.filterSelect}
            onChange={(e) => setBulkStatus(e.target.value as ListingStatus | "")}
          >
            <option value="">تغيير الحالة إلى…</option>
            {(Object.entries(LISTING_STATUS_LABELS) as [ListingStatus, { ar: string }][]).map(
              ([val, { ar }]) => <option key={val} value={val}>{ar}</option>
            )}
          </Select>
          <button onClick={handleBulkStatus} disabled={!bulkStatus || bulkBusy} style={btnGhost}>تطبيق</button>
          <button id="bulk-reindex" onClick={() => void startReindex(scope())} disabled={bulkBusy} style={btnGhost}>🔄 إعادة فهرسة</button>
          <button onClick={() => setBulkDeleteOpen(true)} disabled={bulkBusy || allMatching} style={btnDanger}
            title={allMatching ? "الحذف الجماعي متاح للعقارات المحددة صراحةً فقط" : undefined}>🗑️ حذف المحدد</button>
          <button onClick={() => { setSelected(new Set()); setAllMatching(false); }} style={btnGhost}>إلغاء التحديد</button>
        </div>
      )}

      {/* ── Table Card ─────────────────────────────────────────────────── */}
      <div style={styles.card}>
        <div style={{ overflowX: "auto" }}>
          <table style={styles.table} role="table" aria-label="قائمة العقارات">
            <thead>
              <tr>
                <th style={{ ...styles.th, width: "40px" }}>
                  <input type="checkbox" checked={allOnPageSelected} onChange={toggleAll} aria-label="تحديد كل عقارات الصفحة" />
                </th>
                {["نوع العقار", "الحالة", "السعر", "الموقع", "المساحة", "فهرس RAG", "الإجراءات"].map((h) => (
                  <th key={h} style={styles.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
              ) : items.length === 0 ? (
                <EmptyState onAdd={handleAddClick} onImport={() => setImportOpen(true)} filtered={hasFilters} onClear={clearFilters} />
              ) : (
                items.map((listing, idx) => (
                  <tr
                    key={listing.listing_id}
                    style={{
                      animation: `fadeInUp 0.3s ease both`,
                      animationDelay: `${idx * 40}ms`,
                    }}
                  >
                    <td style={styles.td}>
                      <input
                        type="checkbox" checked={selected.has(listing.listing_id)} onChange={() => toggleOne(listing.listing_id)}
                        aria-label={`تحديد ${listing.rega_ad_number}`}
                      />
                    </td>

                    {/* Type */}
                    <td style={styles.td}>
                      <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                        <span style={{ color: TEXT, fontWeight: 600, fontSize: "14px" }}>
                          {PROPERTY_TYPE_LABELS[listing.property_type]?.ar ?? listing.property_type}
                        </span>
                        <span style={{ color: TEXT_DIM, fontSize: "11px" }}>
                          {listing.rega_ad_number}
                        </span>
                      </div>
                    </td>

                    {/* Status */}
                    <td style={styles.td}>
                      <StatusBadge status={listing.status} />
                    </td>

                    {/* Price */}
                    <td style={styles.td}>
                      <span style={{ color: GOLD, fontWeight: 700, fontSize: "15px", fontVariantNumeric: "tabular-nums" }}>
                        {formatPrice(listing.price)}
                      </span>
                    </td>

                    {/* Location */}
                    <td style={styles.td}>
                      <div style={{ color: TEXT, fontSize: "13px" }}>
                        {listing.city ?? "—"}
                        {listing.district && (
                          <span style={{ color: TEXT_DIM, fontSize: "12px" }}> / {listing.district}</span>
                        )}
                      </div>
                    </td>

                    {/* Area */}
                    <td style={styles.td}>
                      <span style={{ color: TEXT, fontSize: "13px" }}>
                        {formatArea(listing.area_sqm)}
                        {listing.bedrooms != null && (
                          <span style={{ color: TEXT_DIM, fontSize: "11px" }}>
                            {" "}· {listing.bedrooms} غرف
                          </span>
                        )}
                      </span>
                    </td>

                    {/* Qdrant sync */}
                    <td style={styles.td}>
                      <VectorBadge synced={Boolean(listing.qdrant_point_id)} />
                    </td>

                    {/* Actions */}
                    <td style={{ ...styles.td, whiteSpace: "nowrap" }}>
                      <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
                        <button
                          id={`edit-property-${listing.listing_id}`}
                          onClick={() => handleEditClick(listing)}
                          aria-label={`تعديل ${listing.rega_ad_number}`}
                          style={btnActionEdit}
                        >
                          ✏️ تعديل
                        </button>
                        <button
                          id={`reindex-property-${listing.listing_id}`}
                          onClick={() => void startReindex({ ids: [listing.listing_id] })}
                          disabled={bulkBusy}
                          aria-label={`إعادة فهرسة ${listing.rega_ad_number}`}
                          title="إعادة فهرسة هذا العقار"
                          style={btnActionEdit}
                        >
                          🔄
                        </button>
                        <button
                          id={`delete-property-${listing.listing_id}`}
                          onClick={() => setDeleteTarget(listing)}
                          aria-label={`حذف ${listing.rega_ad_number}`}
                          style={btnActionDelete}
                        >
                          🗑️
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* ── Pagination ─────────────────────────────────────────────── */}
        {data && data.pages > 1 && (
          <div style={styles.pagination}>
            <button
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
              style={{ ...btnGhost, ...(page <= 1 ? { opacity: 0.35, cursor: "not-allowed" } : {}) }}
            >
              ▶ السابق
            </button>
            <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
              {Array.from({ length: Math.min(data.pages, 7) }, (_, i) => {
                const pageNum = i + 1;
                return (
                  <button
                    key={pageNum}
                    onClick={() => setPage(pageNum)}
                    style={{
                      width: "36px", height: "36px",
                      borderRadius: "8px",
                      border: `1px solid ${page === pageNum ? GOLD : BORDER}`,
                      background: page === pageNum ? `${GOLD}20` : "transparent",
                      color: page === pageNum ? GOLD : TEXT_DIM,
                      fontSize: "13px",
                      fontWeight: page === pageNum ? 700 : 400,
                      cursor: "pointer",
                      transition: "all 0.15s ease",
                      fontFamily: "inherit",
                    }}
                  >
                    {pageNum}
                  </button>
                );
              })}
            </div>
            <button
              disabled={page >= data.pages}
              onClick={() => setPage((p) => p + 1)}
              style={{ ...btnGhost, ...(page >= data.pages ? { opacity: 0.35, cursor: "not-allowed" } : {}) }}
            >
              التالي ◀
            </button>
          </div>
        )}
      </div>

      {/* ── Slide-over Form ────────────────────────────────────────────── */}
      <PropertyFormSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        onSuccess={handleFormSuccess}
        initialData={editTarget}
      />

      <ImportWizard open={importOpen} onClose={() => setImportOpen(false)} onDone={() => { setNotice("انتهى الاستيراد — حدّثنا القائمة."); void fetchListings(); }} />

      <ConfirmDialog
        open={bulkDeleteOpen}
        onCancel={() => setBulkDeleteOpen(false)}
        onConfirm={handleBulkDelete}
        isDeleting={bulkBusy}
        title={`حذف ${selected.size.toLocaleString("ar")} عقارًا`}
        message="سيتم حذف العقارات المحددة نهائياً من قاعدة البيانات ومن فهرس Qdrant. هذا الإجراء لا يمكن التراجع عنه."
        confirmLabel="🗑️ نعم، احذف المحدد"
      />

      {/* ── Delete Confirm Dialog ───────────────────────────────────────── */}
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        isDeleting={isDeleting}
      />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Styles
// ══════════════════════════════════════════════════════════════════════════════

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    background: NAVY,
    padding: "32px 28px",
    fontFamily: "'Cairo', 'Segoe UI', system-ui, sans-serif",
    color: TEXT,
  },
  pageHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: "24px",
    gap: "16px",
    flexWrap: "wrap" as const,
  },
  pageTitle: {
    margin: 0,
    fontSize: "26px",
    fontWeight: 800,
    background: `linear-gradient(135deg, ${GOLD}, #F0D080)`,
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
    backgroundClip: "text",
  },
  pageSubtitle: {
    margin: "6px 0 0",
    color: TEXT_DIM,
    fontSize: "14px",
  },
  filterBar: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    marginBottom: "20px",
    flexWrap: "wrap" as const,
  },
  filterLabel: {
    color: TEXT_DIM,
    fontSize: "13px",
    fontWeight: 600,
  },
  filterSelect: {
    background: NAVY_600,
    border: `1px solid ${BORDER}`,
    borderRadius: "10px",
    padding: "8px 14px",
    color: TEXT,
    fontSize: "13px",
    outline: "none",
    cursor: "pointer",
    fontFamily: "inherit",
    minWidth: "130px",
  },
  errorBanner: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    background: "rgba(224, 82, 82, 0.12)",
    border: `1px solid ${RED}50`,
    borderRadius: "12px",
    padding: "12px 16px",
    color: RED,
    fontSize: "13px",
    marginBottom: "20px",
    gap: "12px",
  },
  card: {
    background: NAVY_800,
    border: `1px solid ${BORDER}`,
    borderRadius: "16px",
    overflow: "hidden",
    boxShadow: "0 4px 24px rgba(0,0,0,0.3)",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
  },
  th: {
    padding: "14px 20px",
    textAlign: "right" as const,
    fontSize: "12px",
    fontWeight: 600,
    color: GOLD,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    borderBottom: `1px solid ${BORDER}`,
    background: NAVY_700,
    whiteSpace: "nowrap" as const,
  },
  td: {
    padding: "14px 20px",
    borderBottom: `1px solid ${BORDER}20`,
    verticalAlign: "middle" as const,
    transition: "background 0.1s ease",
  },
  pagination: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "12px",
    padding: "20px",
    borderTop: `1px solid ${BORDER}`,
  },
};

// ── Shared button styles ───────────────────────────────────────────────────

const btnGold: React.CSSProperties = {
  background: `linear-gradient(135deg, ${GOLD}, ${GOLD_DIM})`,
  border: "none",
  borderRadius: "12px",
  padding: "10px 22px",
  color: NAVY,
  fontSize: "14px",
  fontWeight: 700,
  cursor: "pointer",
  transition: "opacity 0.15s ease, transform 0.1s ease",
  fontFamily: "inherit",
  whiteSpace: "nowrap",
};

const btnGhost: React.CSSProperties = {
  background: "transparent",
  border: `1px solid ${BORDER}`,
  borderRadius: "10px",
  padding: "8px 18px",
  color: TEXT_DIM,
  fontSize: "13px",
  cursor: "pointer",
  transition: "opacity 0.15s ease",
  fontFamily: "inherit",
};

const btnDanger: React.CSSProperties = {
  background: `${RED}15`,
  border: `1px solid ${RED}50`,
  borderRadius: "10px",
  padding: "10px 24px",
  color: RED,
  fontSize: "14px",
  fontWeight: 600,
  cursor: "pointer",
  transition: "opacity 0.15s ease",
  fontFamily: "inherit",
};

const btnActionEdit: React.CSSProperties = {
  background: `${GOLD}15`,
  border: `1px solid ${GOLD}40`,
  borderRadius: "8px",
  padding: "6px 14px",
  color: GOLD,
  fontSize: "12px",
  fontWeight: 600,
  cursor: "pointer",
  transition: "opacity 0.15s ease",
  fontFamily: "inherit",
};

const btnActionDelete: React.CSSProperties = {
  background: `${RED}12`,
  border: `1px solid ${RED}35`,
  borderRadius: "8px",
  padding: "6px 10px",
  color: RED,
  fontSize: "14px",
  cursor: "pointer",
  transition: "opacity 0.15s ease",
  fontFamily: "inherit",
};
