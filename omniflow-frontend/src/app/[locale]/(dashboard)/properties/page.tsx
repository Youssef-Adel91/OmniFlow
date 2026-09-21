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
import {
  listProperties,
  deleteProperty,
  type PropertyListing,
  type PropertyListingPage,
  type ListingStatus,
  type PropertyType,
  PROPERTY_TYPE_LABELS,
  LISTING_STATUS_LABELS,
} from "@/lib/api/properties";

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
      {[1, 2, 3, 4, 5, 6, 7].map((i) => (
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

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <tr>
      <td colSpan={7} style={{ textAlign: "center", padding: "72px 24px" }}>
        <div style={{ fontSize: "52px", marginBottom: "16px" }}>🏠</div>
        <h3 style={{ margin: "0 0 8px", color: TEXT, fontSize: "18px", fontWeight: 600 }}>
          لا توجد عقارات مضافة بعد
        </h3>
        <p style={{ margin: "0 0 24px", color: TEXT_DIM, fontSize: "14px" }}>
          أضف أول عقار لبدء بناء مخزونك العقاري وفهرسته في قاعدة RAG
        </p>
        <button onClick={onAdd} style={btnGold}>
          + إضافة عقار جديد
        </button>
      </td>
    </tr>
  );
}

function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
  isDeleting,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  isDeleting: boolean;
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
          تأكيد حذف العقار
        </h3>
        <p style={{ margin: "0 0 28px", color: TEXT_DIM, fontSize: "14px", lineHeight: "1.6" }}>
          سيتم حذف العقار نهائياً من قاعدة البيانات ومن فهرس Qdrant.
          هذا الإجراء لا يمكن التراجع عنه.
        </p>
        <div style={{ display: "flex", gap: "12px", justifyContent: "center" }}>
          <button onClick={onCancel} disabled={isDeleting} style={btnGhost}>
            إلغاء
          </button>
          <button onClick={onConfirm} disabled={isDeleting} style={{
            ...btnDanger,
            ...(isDeleting ? { opacity: 0.5, cursor: "not-allowed" } : {}),
          }}>
            {isDeleting ? "⏳ جارٍ الحذف..." : "🗑️ نعم، احذف العقار"}
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

  // ── Fetch ──────────────────────────────────────────────────────────────────
  const fetchListings = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const result = await listProperties({
        page,
        limit: 15,
        status:        statusFilter || undefined,
        property_type: (typeFilter as PropertyType) || undefined,
      });
      setData(result);
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? err?.message ?? "فشل تحميل العقارات";
      setFetchError(typeof msg === "string" ? msg : "فشل تحميل العقارات");
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter, typeFilter]);

  useEffect(() => { fetchListings(); }, [fetchListings]);

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
        <button id="add-property-btn" onClick={handleAddClick} style={btnGold}>
          + إضافة عقار
        </button>
      </div>

      {/* ── Filter Bar ─────────────────────────────────────────────────── */}
      <div style={styles.filterBar}>
        <span style={styles.filterLabel}>تصفية:</span>
        <select
          id="status-filter"
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value as any); setPage(1); }}
          style={styles.filterSelect}
        >
          <option value="">كل الحالات</option>
          {(Object.entries(LISTING_STATUS_LABELS) as [ListingStatus, { ar: string }][]).map(
            ([val, { ar }]) => <option key={val} value={val}>{ar}</option>
          )}
        </select>
        <select
          id="type-filter"
          value={typeFilter}
          onChange={(e) => { setTypeFilter(e.target.value as any); setPage(1); }}
          style={styles.filterSelect}
        >
          <option value="">كل الأنواع</option>
          {(Object.entries(PROPERTY_TYPE_LABELS) as [PropertyType, { ar: string }][]).map(
            ([val, { ar }]) => <option key={val} value={val}>{ar}</option>
          )}
        </select>
        {(statusFilter || typeFilter) && (
          <button
            onClick={() => { setStatusFilter(""); setTypeFilter(""); setPage(1); }}
            style={btnGhost}
          >
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

      {/* ── Table Card ─────────────────────────────────────────────────── */}
      <div style={styles.card}>
        <div style={{ overflowX: "auto" }}>
          <table style={styles.table} role="table" aria-label="قائمة العقارات">
            <thead>
              <tr>
                {["نوع العقار", "الحالة", "السعر", "الموقع", "المساحة", "فهرس RAG", "الإجراءات"].map((h) => (
                  <th key={h} style={styles.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
              ) : items.length === 0 ? (
                <EmptyState onAdd={handleAddClick} />
              ) : (
                items.map((listing, idx) => (
                  <tr
                    key={listing.listing_id}
                    style={{
                      animation: `fadeInUp 0.3s ease both`,
                      animationDelay: `${idx * 40}ms`,
                    }}
                  >
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
