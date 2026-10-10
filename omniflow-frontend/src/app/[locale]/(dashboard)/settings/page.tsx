"use client";

/**
 * app/[locale]/(dashboard)/settings/page.tsx — workspace settings (9 sections)
 *
 * Built now (backend-backed, saved per card, with unsaved-change warnings):
 *   1 القنوات والتكاملات · 3 ساعات العمل والتحويل البشري · 4 الذكاء الاصطناعي · 9 الهوية
 * Designed but not built (honest "قريبًا" pages, no fake controls):
 *   2 الفريق والصلاحيات · 5 الإشعارات · 6 الخصوصية والامتثال · 7 الفوترة · 8 المطورون
 *
 * Data: GET /settings, GET /settings/preferences (+ per-section PUT), GET /settings/channels, POST /settings/ai/test.
 * Writes require the admin role; the server answers 403 with an Arabic message for other roles.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  Bell, Bot, Building2, Clock, Code2, CreditCard, PlugZap, RefreshCw, Settings as SettingsIcon, ShieldCheck, Users,
} from "lucide-react";
import { useAuthReady } from "@/hooks/useAuthReady";
import { useTenantStore } from "@/store/tenantStore";
import { fetchPreferences, fetchSettings, settingsErrorMessage, type PreferencesResponse, type TenantSettings } from "@/lib/api/settings";
import AiSection from "@/components/settings/AiSection";
import BillingSection from "@/components/settings/BillingSection";
import NotificationsSection from "@/components/settings/NotificationsSection";
import PrivacySection from "@/components/settings/PrivacySection";
import TeamSection from "@/components/settings/TeamSection";
import ChannelsSection from "@/components/settings/ChannelsSection";
import ComingSoonSection from "@/components/settings/ComingSoonSection";
import HoursHandoffSection from "@/components/settings/HoursHandoffSection";
import IdentitySection from "@/components/settings/IdentitySection";
import { LoadError } from "@/components/settings/common";

type SectionKey = "channels" | "team" | "hours" | "ai" | "notifications" | "privacy" | "billing" | "developers" | "identity";

const SECTIONS: { key: SectionKey; label: string; icon: React.ElementType; soon?: boolean }[] = [
  { key: "channels", label: "القنوات والتكاملات", icon: PlugZap },
  { key: "team", label: "الفريق والصلاحيات", icon: Users },
  { key: "hours", label: "ساعات العمل والتحويل", icon: Clock },
  { key: "ai", label: "الذكاء الاصطناعي", icon: Bot },
  { key: "notifications", label: "الإشعارات", icon: Bell },
  { key: "privacy", label: "الخصوصية والامتثال", icon: ShieldCheck },
  { key: "billing", label: "الفوترة والاستهلاك", icon: CreditCard },
  { key: "developers", label: "المطورون", icon: Code2, soon: true },
  { key: "identity", label: "الهوية", icon: Building2 },
];

// which dirty-flags belong to which section (a section can hold several independently saved cards)
const DIRTY_OF: Record<SectionKey, string[]> = {
  channels: ["display"], hours: ["hours", "handoff"], ai: ["persona", "behavior"], identity: ["name", "identity"],
  team: [], notifications: ["notif"], privacy: [], billing: [], developers: [],
};

const isSection = (v: string): v is SectionKey => SECTIONS.some((s) => s.key === v);

export default function SettingsPage() {
  const routeParams = useParams();
  const locale = (Array.isArray(routeParams?.locale) ? routeParams.locale[0] : routeParams?.locale) ?? "ar";

  const [active, setActive] = useState<SectionKey>("channels");
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [prefs, setPrefs] = useState<PreferencesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const updateTenant = useTenantStore((s) => s.updateTenant);

  const applySettings = useCallback((s: TenantSettings) => {
    setSettings(s);
    updateTenant({ businessName: s.business_name, tier: s.subscription_tier as "economic" | "professional" | "enterprise" });
  }, [updateTenant]);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [s, p] = await Promise.all([fetchSettings(), fetchPreferences()]);
      applySettings(s); setPrefs(p);
    } catch (e) {
      setError(settingsErrorMessage(e, "فشل تحميل الإعدادات"));
    } finally {
      setLoading(false);
    }
  }, [applySettings]);

  const authReady = useAuthReady();
  useEffect(() => { if (authReady) void load(); }, [authReady, load]);

  // deep link: /settings#hours
  useEffect(() => {
    const h = window.location.hash.replace("#", "");
    if (isSection(h)) setActive(h);
  }, []);

  const setDirtyFlag = useCallback((id: string) => (d: boolean) => setDirty((prev) => (prev[id] === d ? prev : { ...prev, [id]: d })), []);
  const flags = useRef<Record<string, (d: boolean) => void>>({});
  const flag = (id: string) => (flags.current[id] ??= setDirtyFlag(id));

  const sectionDirty = useCallback((k: SectionKey) => DIRTY_OF[k].some((id) => dirty[id]), [dirty]);
  const anyDirty = useMemo(() => Object.values(dirty).some(Boolean), [dirty]);

  useEffect(() => {
    if (!anyDirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [anyDirty]);

  const go = (k: SectionKey) => {
    if (k === active) return;
    if (sectionDirty(active) && !window.confirm("لديك تغييرات غير محفوظة في هذا القسم. هل تريد تجاهلها والانتقال؟")) return;
    setActive(k);
    window.history.replaceState(null, "", `#${k}`);
  };

  const p = prefs?.preferences ?? null;
  const patchPrefs = useCallback(<K extends keyof PreferencesResponse["preferences"]>(key: K, value: PreferencesResponse["preferences"][K]) =>
    setPrefs((cur) => (cur ? { ...cur, preferences: { ...cur.preferences, [key]: value }, configured: { ...cur.configured, [key]: true } } : cur)), []);

  return (
    <div className="space-y-6" dir="rtl">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3"><SettingsIcon className="w-8 h-8 text-[#C9A84C]" aria-hidden />الإعدادات</h1>
          <p className="text-gray-300 mt-2 text-sm">قنواتك، ساعات عملك، سلوك المساعد، وهوية شركتك — كل قسم يُحفظ على حدة.</p>
        </div>
        <button onClick={() => { if (anyDirty && !window.confirm("لديك تغييرات غير محفوظة ستُفقد عند التحديث. متابعة؟")) return; void load(); }} disabled={loading}
          className="bg-white/5 border border-white/10 text-white px-4 py-2 rounded-lg flex items-center gap-2 hover:bg-white/10 transition-colors disabled:opacity-50 text-sm">
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} aria-hidden />تحديث
        </button>
      </div>

      {error && <LoadError message={error} onRetry={() => void load()} />}

      <div className="flex flex-col md:flex-row gap-8">
        <nav aria-label="أقسام الإعدادات" className="w-full md:w-64 shrink-0 flex md:flex-col gap-2 overflow-x-auto md:overflow-visible pb-2 md:pb-0">
          {SECTIONS.map(({ key, label, icon: Icon, soon }) => (
            <button key={key} onClick={() => go(key)} aria-current={active === key ? "page" : undefined} data-testid={`nav-${key}`}
              className={`flex items-center gap-3 px-4 py-3 rounded-xl font-medium transition-colors text-right whitespace-nowrap ${
                active === key ? "bg-[#C9A84C]/10 text-[#C9A84C] border border-[#C9A84C]/30" : "text-gray-200 hover:bg-white/5 border border-transparent"}`}>
              <Icon className="w-5 h-5 shrink-0" aria-hidden />
              <span className="flex-1">{label}</span>
              {sectionDirty(key) && <span className="w-2 h-2 rounded-full bg-amber-400 shrink-0" title="تغييرات غير محفوظة" aria-label="تغييرات غير محفوظة" />}
              {soon && <span className="text-[10px] rounded-full border border-white/25 px-2 py-0.5 text-gray-200">قريبًا</span>}
            </button>
          ))}
        </nav>

        <div className="flex-1 min-w-0">
          {active === "channels" && <ChannelsSection locale={locale} settings={settings} onSettings={applySettings} onDirty={flag("display")} />}
          {active === "hours" && (
            <HoursHandoffSection hours={p?.business_hours ?? null} handoff={p?.handoff ?? null}
              onSaved={(s) => { if (s.business_hours) patchPrefs("business_hours", s.business_hours); if (s.handoff) patchPrefs("handoff", s.handoff); }}
              onDirtyHours={flag("hours")} onDirtyHandoff={flag("handoff")} />
          )}
          {active === "ai" && (
            <AiSection locale={locale} settings={settings} behavior={p?.ai_behavior ?? null} onSettings={applySettings} onBehavior={(b) => patchPrefs("ai_behavior", b)}
              onDirtyPersona={flag("persona")} onDirtyBehavior={flag("behavior")} />
          )}
          {active === "identity" && (
            <IdentitySection settings={settings} identity={p?.identity ?? null} currencies={prefs?.currencies ?? []} onSettings={applySettings} onIdentity={(i) => patchPrefs("identity", i)}
              onDirtyName={flag("name")} onDirtyIdentity={flag("identity")} />
          )}
          {active === "team" && <TeamSection />}
          {active === "notifications" && <NotificationsSection onDirty={flag("notif")} />}
          {active === "privacy" && <PrivacySection />}
          {active === "billing" && <BillingSection />}
          {SECTIONS.find((s) => s.key === active)?.soon && <ComingSoonSection sectionKey={active} settings={settings} />}
        </div>
      </div>
    </div>
  );
}
