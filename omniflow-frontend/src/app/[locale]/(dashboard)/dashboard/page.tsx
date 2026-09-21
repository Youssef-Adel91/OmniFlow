"use client";

/**
 * app/[locale]/(dashboard)/dashboard/page.tsx — Overview KPIs
 *
 * Wired to `lib/api/dashboard.ts` (GET /dashboard/summary). The previous
 * version rendered six hard-coded figures; every number below now comes from
 * the backend, with loading / error / empty states.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  MessageSquare,
  Users,
  Flame,
  FileText,
  Zap,
  AlertTriangle,
  RefreshCw,
} from "lucide-react";
import {
  fetchDashboardSummary,
  type DashboardSummary,
} from "@/lib/api/dashboard";

export default function DashboardOverviewPage() {
  const t = useTranslations("dashboard");
  const m = useTranslations("metrics");

  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await fetchDashboardSummary());
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setError(typeof detail === "string" ? detail : "فشل تحميل مؤشرات الأداء");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const stats = [
    {
      key:   "total_conversations",
      label: m("total_conversations"),
      value: summary?.total_conversations,
      icon:  MessageSquare,
      color: "text-info",
      bg:    "bg-info/10",
    },
    {
      key:   "active_conversations",
      label: m("active_conversations"),
      value: summary?.active_conversations,
      icon:  Zap,
      color: "text-gold-500",
      bg:    "bg-gold-100",
    },
    {
      key:   "total_customers",
      label: m("total_customers"),
      value: summary?.total_customers,
      icon:  Users,
      color: "text-navy-500",
      bg:    "bg-navy-100",
    },
    {
      key:   "hot_leads",
      label: m("hot_leads"),
      value: summary?.hot_leads_count,
      icon:  Flame,
      color: "text-warning",
      bg:    "bg-warning/10",
    },
    {
      key:   "reports_sold",
      label: m("reports_sold"),
      value: summary?.reports_sold_count,
      icon:  FileText,
      color: "text-success",
      bg:    "bg-success/10",
    },
  ];

  const isEmpty =
    !loading &&
    !error &&
    summary != null &&
    stats.every((s) => (s.value ?? 0) === 0);

  return (
    <div className="animate-fade-in">
      {/* Page header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">{t("title")}</h1>
          <p className="page-subtitle">{t("subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void load()}
            disabled={loading}
            className="btn btn-ghost h-8 px-3 text-xs gap-1.5 disabled:opacity-50"
            aria-label="تحديث المؤشرات"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            تحديث
          </button>
          <span className="badge badge-gold">
            <Zap className="w-3 h-3 me-1" />
            AI Active
          </span>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 mb-6 rounded-[var(--radius)] border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger"
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

      {/* KPI Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4 mb-8">
        {stats.map((stat, i) => {
          const Icon = stat.icon;
          return (
            <div
              key={stat.key}
              className="card-hover flex flex-col gap-3 animate-fade-in"
              style={{ animationDelay: `${i * 60}ms` }}
            >
              <div className="flex items-center justify-between">
                <div
                  className={`w-9 h-9 rounded-[var(--radius-sm)] ${stat.bg} flex items-center justify-center`}
                >
                  <Icon className={`w-4.5 h-4.5 ${stat.color}`} />
                </div>
              </div>
              <div>
                {loading ? (
                  <div className="h-8 w-20 skeleton rounded" />
                ) : (
                  <p className="text-2xl font-bold text-[var(--foreground)]">
                    {error ? "—" : (stat.value ?? 0).toLocaleString("ar")}
                  </p>
                )}
                <p className="text-xs text-[var(--muted-foreground)] mt-0.5 leading-snug">
                  {stat.label}
                </p>
              </div>
            </div>
          );
        })}
      </div>

      {/* Empty state — connected, but nothing has happened yet */}
      {isEmpty && (
        <div className="card text-center py-12">
          <MessageSquare className="w-10 h-10 text-[var(--muted-foreground)] mx-auto mb-3" />
          <h2 className="text-base font-semibold mb-1">لا توجد بيانات بعد</h2>
          <p className="text-sm text-[var(--muted-foreground)]">
            بمجرد وصول أول محادثة عبر قنواتك ستظهر المؤشرات هنا تلقائياً.
          </p>
        </div>
      )}
    </div>
  );
}
