"use client";

/** الإشعارات — which events notify me, in the app and/or by email (per user). */
import React, { useCallback, useEffect, useState } from "react";
import { Bell, Loader2 } from "lucide-react";
import { fetchPrefs, savePrefs, type PrefRow } from "@/lib/api/notifications";
import { LoadError, SaveBar, SectionHeader, SectionSkeleton, cardClass } from "./common";

export default function NotificationsSection({ onDirty }: { onDirty?: (d: boolean) => void }) {
  const [saved, setSaved] = useState<PrefRow[] | null>(null);
  const [draft, setDraft] = useState<PrefRow[]>([]);
  const [emailOk, setEmailOk] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { const r = await fetchPrefs(); setSaved(r.items); setDraft(r.items); setEmailOk(r.email_available); } catch { setError("تعذّر تحميل تفضيلات الإشعارات."); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const dirty = saved !== null && JSON.stringify(saved) !== JSON.stringify(draft);
  useEffect(() => { onDirty?.(dirty); }, [dirty, onDirty]);

  const toggle = (kind: string, field: "in_app" | "email") => setDraft((d) => d.map((r) => (r.kind === kind ? { ...r, [field]: !r[field] } : r)));
  const save = async () => {
    setSaving(true); setSaveError(null);
    try { const r = await savePrefs(draft); setSaved(r.items); setDraft(r.items); setSavedAt(Date.now()); } catch { setSaveError("تعذّر حفظ التفضيلات."); } finally { setSaving(false); }
  };

  if (loading && !saved) return <div className={cardClass}><SectionSkeleton /></div>;
  if (error && !saved) return <LoadError message={error} onRetry={() => void load()} />;

  return (
    <div className={cardClass} data-testid="notifications-section">
      <SectionHeader icon={Bell} title="الإشعارات" description="اختر الأحداث التي تُنبَّه بها داخل التطبيق وعبر البريد. هذه تفضيلاتك أنت فقط." />
      {!emailOk && <p role="status" className="text-sm rounded-xl border border-amber-300/40 bg-amber-500/10 text-amber-100 p-3">البريد الإلكتروني غير مفعّل على المنصة حاليًا، لذا ستصلك الإشعارات داخل التطبيق فقط.</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-gray-300"><th className="text-start py-2">الحدث</th><th className="w-24">داخل التطبيق</th><th className="w-24">البريد</th></tr></thead>
          <tbody>
            {draft.map((r) => (
              <tr key={r.kind} className="border-t border-white/10 text-gray-100">
                <td className="py-3 pe-4"><p className="font-semibold">{r.label}</p><p className="text-xs text-gray-300">{r.description}</p></td>
                <td className="text-center"><input type="checkbox" aria-label={`${r.label} — داخل التطبيق`} checked={r.in_app} onChange={() => toggle(r.kind, "in_app")} className="w-4 h-4" /></td>
                <td className="text-center"><input type="checkbox" aria-label={`${r.label} — البريد`} checked={r.email} disabled={!emailOk} onChange={() => toggle(r.kind, "email")} className="w-4 h-4 disabled:opacity-40" /></td>
              </tr>))}
          </tbody>
        </table>
      </div>
      {saving && <Loader2 className="w-4 h-4 animate-spin text-gray-200" aria-label="جارٍ الحفظ" />}
      <SaveBar dirty={dirty} saving={saving} error={saveError} savedAt={savedAt} onSave={() => void save()} onReset={() => setDraft(saved ?? [])} />
    </div>
  );
}
