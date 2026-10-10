"use client";

/** Settings → الهوية: business name (+ read-only FAL), logo, default language, time zone, currency. */

import React, { useRef, useState } from "react";
import { Building2, ImageIcon, Loader2, Upload } from "lucide-react";
import {
  CURRENCY_LABELS, TIMEZONES, savePreferences, settingsErrorMessage, updateSettings, uploadLogo,
  type IdentityPrefs, type TenantSettings,
} from "@/lib/api/settings";
import { Field, SaveBar, SectionHeader, SectionSkeleton, cardClass, inputClass, readOnlyClass } from "./common";
import { useSection } from "./useSection";

export default function IdentitySection({ settings, identity, currencies, onSettings, onIdentity, onDirtyName, onDirtyIdentity }: {
  settings: TenantSettings | null; identity: IdentityPrefs | null; currencies: string[];
  onSettings: (s: TenantSettings) => void; onIdentity: (i: IdentityPrefs) => void;
  onDirtyName: (d: boolean) => void; onDirtyIdentity: (d: boolean) => void;
}) {
  const name = useSection<string>(settings ? settings.business_name : null, async (v) => { const s = await updateSettings({ business_name: v.trim() }); onSettings(s); return s.business_name; }, onDirtyName, (e) => settingsErrorMessage(e, "تعذّر حفظ الاسم"));
  const ident = useSection<IdentityPrefs>(identity, async (v) => { const out = await savePreferences("identity", v); onIdentity(out); return out; }, onDirtyIdentity, (e) => settingsErrorMessage(e, "تعذّر حفظ الهوية"));

  const fileRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [logoOk, setLogoOk] = useState(false);

  const pick = (f: File | undefined) => {
    setLogoOk(false); setLogoError(null);
    if (!f) return;
    if (f.size > 5 * 1024 * 1024) { setLogoError("حجم الشعار يتجاوز 5 ميجابايت."); return; }
    setFile(f);
    const r = new FileReader();
    r.onload = () => setPreview(r.result as string);
    r.readAsDataURL(f);
  };
  const upload = async () => {
    if (!file) return;
    setUploading(true); setLogoError(null);
    try { const r = await uploadLogo(file); setPreview(r.logo_url); setFile(null); setLogoOk(true); if (settings) onSettings({ ...settings, logo_url: r.logo_url }); }
    catch (e) { setLogoError(settingsErrorMessage(e, "فشل رفع الشعار (للمدير فقط)")); }
    finally { setUploading(false); }
  };
  const shown = preview ?? settings?.logo_url ?? null;
  const id = ident.draft;

  return (
    <div className="space-y-6">
      <div className={cardClass}>
        <SectionHeader icon={Building2} title="هوية الشركة" description="الاسم الذي يُستخدم في المحادثات والتقارير وبطاقة التعارف." />
        {!settings || name.draft === null ? <SectionSkeleton /> : (
          <>
            <div className="grid md:grid-cols-2 gap-4">
              <Field label="اسم الشركة" htmlFor="biz-name"><input id="biz-name" value={name.draft} minLength={2} maxLength={255} onChange={(e) => name.setDraft(e.target.value)} className={inputClass} /></Field>
              <Field label="رقم رخصة فال (FAL)" hint="للقراءة فقط — يُدار عبر الدعم."><input value={settings.fal_license_number ?? "—"} readOnly className={readOnlyClass} dir="ltr" /></Field>
            </div>
            <SaveBar dirty={name.dirty && name.draft.trim().length >= 2} saving={name.saving} error={name.error} savedAt={name.savedAt} onSave={() => void name.submit()} onReset={name.reset} />
          </>
        )}
      </div>

      <div className={cardClass}>
        <SectionHeader icon={ImageIcon} title="الشعار" description="يظهر في التقارير والواجهات المرسلة للعملاء. PNG أو JPEG أو WebP أو SVG حتى 5 ميجابايت." />
        <div className="flex flex-col sm:flex-row items-center gap-5">
          {shown ? /* eslint-disable-next-line @next/next/no-img-element */ <img src={shown} alt="شعار الشركة" className="w-32 h-32 object-contain rounded-2xl border border-white/10 bg-white/5 p-2" />
            : <div className="w-32 h-32 rounded-2xl border border-dashed border-white/20 flex items-center justify-center text-xs text-gray-300">لا يوجد شعار</div>}
          <div className="space-y-2">
            <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" className="hidden" aria-label="اختيار ملف الشعار" onChange={(e) => pick(e.target.files?.[0])} />
            <div className="flex gap-2">
              <button type="button" onClick={() => fileRef.current?.click()} className="px-3 py-2 text-sm rounded-lg border border-white/20 hover:bg-white/10">اختيار صورة</button>
              <button type="button" onClick={() => void upload()} disabled={!file || uploading} className="px-3 py-2 text-sm rounded-lg bg-[#C9A84C] text-[#0A0F1C] font-semibold disabled:opacity-50 flex items-center gap-2">
                {uploading ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Upload className="w-4 h-4" aria-hidden />}رفع الشعار
              </button>
            </div>
            {logoError && <p role="alert" className="text-sm text-red-300">{logoError}</p>}
            {logoOk && <p role="status" className="text-sm text-emerald-300">تم رفع الشعار.</p>}
          </div>
        </div>
      </div>

      <div className={cardClass}>
        <SectionHeader icon={Building2} title="اللغة والمنطقة الزمنية والعملة" description="الإعدادات الافتراضية لحسابك." />
        {!id ? <SectionSkeleton /> : (
          <>
            <div className="grid md:grid-cols-3 gap-4">
              <Field label="اللغة الافتراضية" htmlFor="id-lang" hint="يعتمد عليها المساعد عندما تكون لغة الرد «بلغة العميل» ولا تتضح لغته."><select id="id-lang" value={id.language} onChange={(e) => ident.setDraft({ ...id, language: e.target.value as IdentityPrefs["language"] })} className={inputClass}><option value="ar">العربية</option><option value="en">English</option></select></Field>
              <Field label="المنطقة الزمنية" htmlFor="id-tz" hint="الافتراضية لساعات العمل."><select id="id-tz" value={id.timezone} onChange={(e) => ident.setDraft({ ...id, timezone: e.target.value })} className={inputClass}>{TIMEZONES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></Field>
              <Field label="العملة" htmlFor="id-cur" hint="يذكر المساعد الأسعار بهذه العملة."><select id="id-cur" value={id.currency} onChange={(e) => ident.setDraft({ ...id, currency: e.target.value })} className={inputClass}>{currencies.map((c) => <option key={c} value={c}>{CURRENCY_LABELS[c] ?? c}</option>)}</select></Field>
            </div>
            <SaveBar dirty={ident.dirty} saving={ident.saving} error={ident.error} savedAt={ident.savedAt} onSave={() => void ident.submit()} onReset={ident.reset} />
          </>
        )}
      </div>
    </div>
  );
}
