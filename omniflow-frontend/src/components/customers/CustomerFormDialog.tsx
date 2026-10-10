"use client";

/**
 * components/customers/CustomerFormDialog.tsx — add / edit a customer (manual CRM entry)
 *
 * Add: phone (Saudi numbers are normalised server-side: 0501234567 → +966501234567), name, email, city,
 * tags, notes, VIP and — only when the customer really agreed — marketing consent (PDPL).
 * Edit: same fields; the phone can only be attached when the customer has none (Instagram/Messenger
 * customers). A phone owned by another customer comes back as 409 with that customer's id so the
 * caller can open it (and merge the two).
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { createCustomer, customerErrorInfo, updateCustomer, type Customer } from "@/lib/api/customers";

interface Props {
  open: boolean;
  /** null = add a new customer */
  customer: Customer | null;
  onClose: () => void;
  onSaved: (c: Customer) => void;
  /** a duplicate phone: let the page open the existing customer */
  onOpenExisting?: (customerId: string) => void;
}

const input = "w-full rounded-[10px] border border-white/15 bg-[#0B1121] px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:border-[#C9A84C]/60";

export default function CustomerFormDialog({ open, customer, onClose, onSaved, onOpenExisting }: Props) {
  const editing = customer !== null;
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [city, setCity] = useState("");
  const [tags, setTags] = useState("");
  const [notes, setNotes] = useState("");
  const [vip, setVip] = useState(false);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; existingId?: string } | null>(null);
  const ref = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (!open) return;
    setPhone(customer?.phone_e164 ?? ""); setName(customer?.display_name ?? ""); setEmail(customer?.email ?? "");
    setCity(customer?.city ?? ""); setTags((customer?.tags ?? []).join("، ")); setNotes(customer?.notes ?? "");
    setVip(customer?.is_vip ?? false); setConsent(false); setError(null); setBusy(false);
    ref.current?.focus();
  }, [open, customer]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const tagList = tags.split(/[,،;]+/).map((t) => t.trim()).filter(Boolean);
  const canAttachPhone = !editing || !customer?.phone_e164;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing && !phone.trim()) { setError({ message: "رقم الجوال مطلوب" }); return; }
    setBusy(true); setError(null);
    try {
      let saved: Customer;
      if (editing && customer) {
        saved = await updateCustomer(customer.id, {
          display_name: name.trim(), email: email.trim(), city: city.trim(), tags: tagList, notes: notes.trim(), is_vip: vip,
          ...(canAttachPhone && phone.trim() ? { phone: phone.trim() } : {}),
        });
      } else {
        saved = await createCustomer({
          phone: phone.trim(), display_name: name.trim() || undefined, email: email.trim() || undefined, city: city.trim() || undefined,
          tags: tagList, notes: notes.trim() || undefined, is_vip: vip, marketing_opt_in: consent,
        });
      }
      onSaved(saved);
      onClose();
    } catch (err) {
      const info = customerErrorInfo(err, "تعذّر حفظ العميل");
      setError({ message: info.message, existingId: info.code === "DUPLICATE_PHONE" ? info.existingId : undefined });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60" dir="rtl"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <form ref={ref} tabIndex={-1} onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="customer-form-title"
        className="w-full max-w-lg max-h-[92vh] overflow-y-auto rounded-2xl border border-white/10 bg-[#111827] text-gray-100 shadow-2xl outline-none">
        <header className="flex items-center justify-between px-5 py-4 border-b border-white/10">
          <h2 id="customer-form-title" className="text-base font-bold">{editing ? "تعديل العميل" : "إضافة عميل"}</h2>
          <button type="button" onClick={onClose} disabled={busy} aria-label="إغلاق" className="p-1.5 rounded-lg hover:bg-white/10 disabled:opacity-40"><X className="w-4 h-4" /></button>
        </header>

        <div className="px-5 py-4 space-y-3">
          {error && (
            <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden />
              <span>
                {error.message}
                {error.existingId && onOpenExisting && (
                  <button type="button" className="ms-2 underline" onClick={() => { onClose(); onOpenExisting(error.existingId!); }}>فتح العميل الموجود</button>
                )}
              </span>
            </div>
          )}

          <label className="block text-xs space-y-1">
            <span>رقم الجوال {!editing && <span className="text-red-300" title="مطلوب">*</span>}</span>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} dir="ltr" inputMode="tel" autoComplete="off" maxLength={30}
              disabled={!canAttachPhone} placeholder="0501234567 أو +966501234567" className={`${input} text-start disabled:opacity-60`} />
            {!canAttachPhone && <span className="text-gray-400">الرقم مرتبط بالعميل ولا يُعدَّل من هنا.</span>}
            {editing && canAttachPhone && <span className="text-gray-400">هذا العميل بلا رقم جوال (قناة إنستجرام/ماسنجر). أضف رقمه إن عرفته؛ إن كان لعميل آخر ستُعرض عليك إمكانية الدمج.</span>}
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-xs space-y-1"><span>الاسم</span>
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} className={input} /></label>
            <label className="block text-xs space-y-1"><span>المدينة</span>
              <input value={city} onChange={(e) => setCity(e.target.value)} maxLength={100} className={input} /></label>
          </div>
          <label className="block text-xs space-y-1"><span>البريد الإلكتروني</span>
            <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" dir="ltr" maxLength={254} className={`${input} text-start`} /></label>
          <label className="block text-xs space-y-1"><span>الوسوم (افصل بفاصلة)</span>
            <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="مستثمر، فيلا، جدة" className={input} /></label>
          <label className="block text-xs space-y-1"><span>ملاحظات</span>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} maxLength={2000} className={input} /></label>
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={vip} onChange={(e) => setVip(e.target.checked)} />عميل VIP
          </label>

          {!editing && (
            <label className="flex items-start gap-2 rounded-lg border border-white/10 p-3 text-xs cursor-pointer">
              <input type="checkbox" className="mt-0.5" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span><strong className="block">العميل وافق على استلام الرسائل التسويقية</strong>
                <span className="text-gray-400">لا تُحدّد هذا الخيار إلا إذا وافق العميل فعلًا (نظام حماية البيانات الشخصية PDPL). بدونه لن يستلم العميل أي حملات، لكن تبقى محادثاته الاعتيادية معه.</span></span>
            </label>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 px-5 py-3 border-t border-white/10">
          <button type="button" onClick={onClose} disabled={busy} className="px-4 py-2 text-sm rounded-lg border border-white/15 hover:bg-white/10 disabled:opacity-50">إلغاء</button>
          <button type="submit" disabled={busy} className="px-4 py-2 text-sm rounded-lg bg-[#C9A84C] text-[#0A0F1C] font-semibold hover:opacity-90 disabled:opacity-50 flex items-center gap-2">
            {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}{editing ? "حفظ" : "إضافة"}
          </button>
        </footer>
      </form>
    </div>
  );
}
