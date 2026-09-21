"use client";

/**
 * app/[locale]/(dashboard)/reports/page.tsx — Paid reports
 *
 * Wired to the real backend via `lib/api/reports.ts` (GET /reports,
 * GET /reports/{id}) plus `lib/api/dashboard.ts` for the headline counters.
 * The previous version rendered hard-coded percentages and a fake CSS chart.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  FileText,
  Download,
  AlertTriangle,
  RefreshCw,
  Users,
  Flame,
  Loader2,
  FileDown,
  TrendingUp,
  Calendar,
  XCircle,
} from "lucide-react";
import {
  fetchReports,
  fetchReport,
  fetchAllReports,
  fetchRevenueAnalytics,
  REPORT_TYPE_LABELS,
  REPORT_STATUS_LABELS,
  type Report,
  type ReportPage,
  type RevenuePoint,
} from "@/lib/api/reports";
import { fetchDashboardSummary, type DashboardSummary } from "@/lib/api/dashboard";

const PAGE_SIZE = 20;

// ── Helpers ────────────────────────────────────────────────────────────────

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("ar-SA", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(d);
}

function formatPrice(price: number | null | undefined, currency = "SAR"): string {
  if (price == null) return "—";
  return new Intl.NumberFormat("ar-SA", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(price);
}

/** Short Arabic label for a "YYYY-MM" bucket coming from the analytics API. */
function formatMonthLabel(month: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(month);
  if (!match) return month || "—";
  const d = new Date(Number(match[1]), Number(match[2]) - 1, 1);
  if (Number.isNaN(d.getTime())) return month;
  return new Intl.DateTimeFormat("ar-SA", { month: "short", year: "2-digit" }).format(d);
}

function compactNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}

// ── CSV export helpers (client-side, no backend endpoint needed) ───────────

function csvCell(value: unknown): string {
  const str = value == null ? "" : String(value);
  // Escape quotes and wrap any cell containing a separator/newline.
  const escaped = str.replace(/"/g, '""');
  return /[",\n\r]/.test(str) ? `"${escaped}"` : escaped;
}

function reportsToCsv(reports: Report[]): string {
  const headers = [
    "معرّف التقرير",
    "نوع التقرير",
    "العنوان",
    "العميل",
    "معرّف العميل",
    "الحالة",
    "السعر",
    "العملة",
    "تاريخ الإنشاء",
    "رابط التحميل",
  ];

  const rows = reports.map((r) => [
    r.id,
    REPORT_TYPE_LABELS[r.report_type] ?? r.report_type,
    r.title ?? "",
    r.customer_name ?? "",
    r.customer_id ?? "",
    REPORT_STATUS_LABELS[r.status ?? "pending"]?.ar ?? r.status ?? "",
    r.price ?? "",
    r.currency ?? "",
    r.created_at ?? "",
    r.s3_url ?? "",
  ]);

  return [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
}

function downloadCsv(filename: string, csv: string): void {
  // UTF-8 BOM so Excel renders the Arabic headers correctly.
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// ── Revenue chart ──────────────────────────────────────────────────────────
//
// `recharts` is not a dependency of this project (checked package.json), so the
// chart is a lightweight CSS/flexbox bar chart — no new bundle weight.

function RevenueChart({
  points,
  loading,
  error,
}: {
  points: RevenuePoint[];
  loading: boolean;
  error: string | null;
}) {
  const max = Math.max(1, ...points.map((p) => p.total_revenue));
  const totalRevenue = points.reduce((sum, p) => sum + p.total_revenue, 0);

  return (
    <div className="bg-[#111827] border border-white/10 rounded-2xl p-6 shadow-xl">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-6">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <TrendingUp className="w-5 h-5 text-[#C9A84C]" />
          الإيرادات الشهرية
        </h2>
        {!loading && !error && points.length > 0 && (
          <span className="text-sm text-gray-400">
            الإجمالي:{" "}
            <span className="text-[#C9A84C] font-bold">
              {formatPrice(totalRevenue)}
            </span>
          </span>
        )}
      </div>

      {loading ? (
        <div className="h-52 flex items-end gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="flex-1 rounded-t-lg bg-white/5 animate-pulse"
              style={{ height: `${30 + ((i * 17) % 60)}%` }}
            />
          ))}
        </div>
      ) : error ? (
        <div className="h-52 flex flex-col items-center justify-center text-center">
          <AlertTriangle className="w-8 h-8 text-gray-600 mb-2" />
          <p className="text-sm text-gray-400">{error}</p>
        </div>
      ) : points.length === 0 ? (
        <div className="h-52 flex flex-col items-center justify-center text-center">
          <BarChart3 className="w-10 h-10 text-gray-600 mb-3" />
          <p className="text-sm text-gray-400">لا توجد إيرادات مسجّلة بعد.</p>
          <p className="text-xs text-gray-600 mt-1">
            سيظهر الرسم البياني بمجرد بيع أول تقرير مدفوع.
          </p>
        </div>
      ) : (
        <div className="h-56 flex items-end justify-between gap-2 sm:gap-3" dir="ltr">
          {points.map((point) => {
            const heightPct = Math.max(4, (point.total_revenue / max) * 100);
            return (
              <div
                key={point.month}
                className="flex-1 flex flex-col items-center justify-end h-full min-w-0 group"
                title={`${formatMonthLabel(point.month)} — ${formatPrice(
                  point.total_revenue,
                )} (${point.count} تقرير)`}
              >
                <span className="text-[10px] text-gray-400 mb-1 whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity">
                  {compactNumber(point.total_revenue)}
                </span>
                <div
                  className="w-full rounded-t-lg bg-gradient-to-t from-[#C9A84C]/40 to-[#C9A84C] transition-all duration-300 hover:from-[#C9A84C]/60 hover:to-[#E0C46A]"
                  style={{ height: `${heightPct}%` }}
                />
                <span className="text-[10px] text-gray-500 mt-2 whitespace-nowrap truncate max-w-full">
                  {formatMonthLabel(point.month)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── KPI card ───────────────────────────────────────────────────────────────

function KpiCard({
  icon: Icon,
  label,
  value,
  loading,
  accent = "#C9A84C",
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  loading: boolean;
  accent?: string;
}) {
  return (
    <div className="bg-[#111827] border border-white/10 rounded-2xl p-6 hover:border-white/20 transition-colors">
      <div
        className="w-10 h-10 rounded-lg flex items-center justify-center mb-4"
        style={{ background: `${accent}1A` }}
      >
        <Icon className="w-5 h-5" style={{ color: accent }} />
      </div>
      <h3 className="text-gray-400 font-medium mb-1">{label}</h3>
      {loading ? (
        <div className="h-8 w-24 rounded bg-white/5 animate-pulse" />
      ) : (
        <span className="text-3xl font-bold text-white">{value}</span>
      )}
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function ReportsPage() {
  const [data, setData]       = useState<ReportPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [page, setPage]       = useState(1);
  const [typeFilter, setTypeFilter] = useState("");
  const [dateFrom, setDateFrom]     = useState("");
  const [dateTo, setDateTo]         = useState("");

  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);

  const [revenue, setRevenue]               = useState<RevenuePoint[]>([]);
  const [revenueLoading, setRevenueLoading] = useState(true);
  const [revenueError, setRevenueError]     = useState<string | null>(null);

  const [exporting, setExporting]     = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchReports({
        page,
        page_size: PAGE_SIZE,
        report_type: typeFilter || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });
      setData(result);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setError(typeof detail === "string" ? detail : "فشل تحميل التقارير");
    } finally {
      setLoading(false);
    }
  }, [page, typeFilter, dateFrom, dateTo]);

  useEffect(() => {
    void load();
  }, [load]);

  // Headline counters come from the dashboard summary endpoint; a failure here
  // must not block the reports table, so it is handled independently.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setSummaryLoading(true);
      try {
        const s = await fetchDashboardSummary();
        if (!cancelled) setSummary(s);
      } catch {
        if (!cancelled) setSummary(null);
      } finally {
        if (!cancelled) setSummaryLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Client-side date guard. The backend is expected to honour `date_from` /
   * `date_to`, but this keeps the UI honest if it silently ignores them.
   */
  const inDateRange = useCallback(
    (iso: string | null | undefined): boolean => {
      if (!dateFrom && !dateTo) return true;
      if (!iso) return false;
      const t = new Date(iso).getTime();
      if (Number.isNaN(t)) return false;
      if (dateFrom && t < new Date(`${dateFrom}T00:00:00`).getTime()) return false;
      if (dateTo && t > new Date(`${dateTo}T23:59:59`).getTime()) return false;
      return true;
    },
    [dateFrom, dateTo],
  );

  // Monthly revenue for the chart — independent of the table so a failure
  // here only blanks the chart.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setRevenueLoading(true);
      setRevenueError(null);
      try {
        const result = await fetchRevenueAnalytics("monthly");
        if (!cancelled) setRevenue(result.items);
      } catch (err: any) {
        if (!cancelled) {
          const detail = err?.response?.data?.detail ?? err?.message;
          setRevenueError(
            typeof detail === "string" ? detail : "تعذّر تحميل بيانات الإيرادات",
          );
        }
      } finally {
        if (!cancelled) setRevenueLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Export every report matching the current filters to a CSV file.
   * Fully client-side: the pages are walked via `fetchAllReports` and the rows
   * are serialised in the browser — no dedicated backend endpoint required.
   */
  const handleExportCsv = async () => {
    if (exporting) return;
    setExporting(true);
    setExportError(null);
    try {
      const all = await fetchAllReports({
        report_type: typeFilter || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });

      // Defensive: if the backend ignores the date params, apply them here too
      // so the exported file always matches what the filters promise.
      const filtered = all.filter((r) => inDateRange(r.created_at));

      if (filtered.length === 0) {
        setExportError("لا توجد تقارير مطابقة للتصدير.");
        return;
      }

      const stamp = new Date().toISOString().slice(0, 10);
      downloadCsv(`omniflow-reports-${stamp}.csv`, reportsToCsv(filtered));
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setExportError(typeof detail === "string" ? detail : "تعذّر تصدير التقارير");
    } finally {
      setExporting(false);
    }
  };

  /**
   * Open the report PDF. The list response already carries `s3_url`, but the
   * presigned link may be stale, so we re-fetch the single report to get a
   * fresh URL before opening it.
   */
  const handleDownload = async (report: Report) => {
    setDownloadingId(report.id);
    try {
      const fresh = await fetchReport(report.id);
      const url = fresh.s3_url ?? report.s3_url;
      if (url) {
        window.open(url, "_blank", "noopener,noreferrer");
      } else {
        setError("رابط التقرير غير متاح بعد. حاول لاحقاً.");
      }
    } catch {
      if (report.s3_url) {
        window.open(report.s3_url, "_blank", "noopener,noreferrer");
      } else {
        setError("تعذّر فتح التقرير.");
      }
    } finally {
      setDownloadingId(null);
    }
  };

  const items      = (data?.items ?? []).filter((r) => inDateRange(r.created_at));
  const total      = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / (data?.page_size || PAGE_SIZE)));

  const reportTypes = useMemo(
    () => Object.entries(REPORT_TYPE_LABELS),
    [],
  );

  return (
    <div className="space-y-8" dir="rtl">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <BarChart3 className="w-8 h-8 text-[#C9A84C]" />
            التقارير المدفوعة
          </h1>
          <p className="text-gray-400 mt-2 text-sm">
            التقارير العقارية التي تم إنشاؤها وبيعها لعملائك.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => void handleExportCsv()}
            disabled={exporting}
            className="bg-[#C9A84C]/15 border border-[#C9A84C]/40 text-[#C9A84C] px-4 py-2 rounded-lg flex items-center gap-2 hover:bg-[#C9A84C]/25 transition-colors disabled:opacity-50 font-semibold"
          >
            {exporting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <FileDown className="w-4 h-4" />
            )}
            تصدير CSV
          </button>
          <button
            onClick={() => void load()}
            disabled={loading}
            className="bg-white/5 border border-white/10 text-white px-4 py-2 rounded-lg flex items-center gap-2 hover:bg-white/10 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            تحديث
          </button>
        </div>
      </div>

      {/* KPI Cards — real values from GET /dashboard/summary + GET /reports */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <KpiCard
          icon={FileText}
          label="التقارير المُباعة"
          value={(summary?.reports_sold_count ?? total).toLocaleString("ar")}
          loading={summaryLoading && loading}
        />
        <KpiCard
          icon={Users}
          label="إجمالي العملاء"
          value={(summary?.total_customers ?? 0).toLocaleString("ar")}
          loading={summaryLoading}
          accent="#52A0E0"
        />
        <KpiCard
          icon={Flame}
          label="العملاء الساخنون"
          value={(summary?.hot_leads_count ?? 0).toLocaleString("ar")}
          loading={summaryLoading}
          accent="#E05252"
        />
      </div>

      {/* Monthly revenue chart */}
      <RevenueChart points={revenue} loading={revenueLoading} error={revenueError} />

      {/* Filters */}
      <div className="flex items-end gap-4 flex-wrap bg-[#111827] border border-white/10 rounded-2xl p-4">
        <div>
          <label className="block text-xs text-gray-500 mb-1.5">نوع التقرير</label>
          <select
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value);
              setPage(1);
            }}
            className="bg-[#0A0F1C] border border-white/10 rounded-lg py-2 px-4 text-white text-sm focus:outline-none focus:border-[#C9A84C]/50"
          >
            <option value="">كل الأنواع</option>
            {reportTypes.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs text-gray-500 mb-1.5 flex items-center gap-1">
            <Calendar className="w-3 h-3" /> من تاريخ
          </label>
          <input
            type="date"
            value={dateFrom}
            max={dateTo || undefined}
            onChange={(e) => {
              setDateFrom(e.target.value);
              setPage(1);
            }}
            dir="ltr"
            className="bg-[#0A0F1C] border border-white/10 rounded-lg py-2 px-4 text-white text-sm focus:outline-none focus:border-[#C9A84C]/50"
          />
        </div>

        <div>
          <label className="text-xs text-gray-500 mb-1.5 flex items-center gap-1">
            <Calendar className="w-3 h-3" /> إلى تاريخ
          </label>
          <input
            type="date"
            value={dateTo}
            min={dateFrom || undefined}
            onChange={(e) => {
              setDateTo(e.target.value);
              setPage(1);
            }}
            dir="ltr"
            className="bg-[#0A0F1C] border border-white/10 rounded-lg py-2 px-4 text-white text-sm focus:outline-none focus:border-[#C9A84C]/50"
          />
        </div>

        {(typeFilter || dateFrom || dateTo) && (
          <button
            onClick={() => {
              setTypeFilter("");
              setDateFrom("");
              setDateTo("");
              setPage(1);
            }}
            className="text-xs text-gray-400 hover:text-white transition-colors flex items-center gap-1 py-2.5"
          >
            <XCircle className="w-3.5 h-3.5" />
            مسح الفلاتر
          </button>
        )}
      </div>

      {/* Export feedback */}
      {exportError && (
        <p role="alert" className="text-sm text-red-400 flex items-center gap-1.5">
          <AlertTriangle className="w-4 h-4" /> {exportError}
        </p>
      )}

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

      {/* Reports table */}
      <div className="bg-[#111827] border border-white/5 rounded-2xl overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-right">
            <thead>
              <tr className="border-b border-white/10 bg-white/5 text-gray-400 text-sm">
                <th className="py-4 px-6 font-medium">نوع التقرير</th>
                <th className="py-4 px-6 font-medium">العميل</th>
                <th className="py-4 px-6 font-medium">الحالة</th>
                <th className="py-4 px-6 font-medium">السعر</th>
                <th className="py-4 px-6 font-medium">تاريخ الإنشاء</th>
                <th className="py-4 px-6 font-medium text-center">تحميل</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/5">
                    {Array.from({ length: 6 }).map((__, j) => (
                      <td key={j} className="py-4 px-6">
                        <div className="h-4 w-24 rounded bg-white/5 animate-pulse" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="text-center py-20 px-6">
                    <FileText className="w-12 h-12 text-gray-600 mx-auto mb-4" />
                    <h3 className="text-lg font-semibold text-gray-200 mb-1">
                      لا توجد تقارير بعد
                    </h3>
                    <p className="text-sm text-gray-500">
                      ستظهر التقارير هنا بمجرد أن يطلب أحد عملائك تقريراً مدفوعاً.
                    </p>
                  </td>
                </tr>
              ) : (
                items.map((report) => {
                  const statusMeta =
                    REPORT_STATUS_LABELS[report.status ?? "pending"] ?? {
                      ar: report.status ?? "—",
                      color: "#8B8FA8",
                    };
                  return (
                    <tr
                      key={report.id}
                      className="border-b border-white/5 hover:bg-white/[0.02] transition-colors"
                    >
                      <td className="py-4 px-6">
                        <span className="font-semibold text-gray-100">
                          {REPORT_TYPE_LABELS[report.report_type] ?? report.report_type}
                        </span>
                        {report.title && (
                          <span className="block text-xs text-gray-500 mt-0.5 truncate max-w-[220px]">
                            {report.title}
                          </span>
                        )}
                      </td>
                      <td className="py-4 px-6 text-gray-300 text-sm">
                        {report.customer_name ?? report.customer_id ?? "—"}
                      </td>
                      <td className="py-4 px-6">
                        <span
                          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border"
                          style={{
                            color: statusMeta.color,
                            background: `${statusMeta.color}18`,
                            borderColor: `${statusMeta.color}40`,
                          }}
                        >
                          {statusMeta.ar}
                        </span>
                      </td>
                      <td className="py-4 px-6 text-[#C9A84C] font-bold">
                        {formatPrice(report.price, report.currency ?? "SAR")}
                      </td>
                      <td className="py-4 px-6 text-gray-400 text-sm">
                        {formatDate(report.created_at)}
                      </td>
                      <td className="py-4 px-6 text-center">
                        <button
                          onClick={() => void handleDownload(report)}
                          disabled={downloadingId === report.id}
                          aria-label="تحميل التقرير"
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#C9A84C]/15 border border-[#C9A84C]/40 text-[#C9A84C] text-xs font-semibold hover:bg-[#C9A84C]/25 transition-colors disabled:opacity-50"
                        >
                          {downloadingId === report.id ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <Download className="w-3.5 h-3.5" />
                          )}
                          تحميل
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
        {totalPages > 1 && (
          <div className="p-4 border-t border-white/5 flex items-center justify-between text-sm text-gray-400">
            <span>
              صفحة {page} من {totalPages}
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
        )}
      </div>
    </div>
  );
}
