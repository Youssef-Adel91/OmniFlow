"use client";

/** Where the payment page sends the customer back. The webhook usually has already applied the payment; if not, the
 *  server asks the provider directly (GET /billing/transactions/{id}), so a slow webhook never leaves anyone stuck. */
import React, { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { AlertTriangle, CheckCircle2, Clock, Loader2 } from "lucide-react";
import { billingError, fetchTransaction, formatSar, type Txn } from "@/lib/api/billing";

function ReturnInner() {
  const params = useParams();
  const locale = (Array.isArray(params?.locale) ? params.locale[0] : params?.locale) ?? "ar";
  const tx = useSearchParams().get("tx");
  const [txn, setTxn] = useState<Txn | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tries, setTries] = useState(0);

  useEffect(() => {
    if (!tx) return;
    let alive = true;
    fetchTransaction(tx)
      .then((t) => { if (alive) { setTxn(t); if (t.status === "pending" && tries < 6) setTimeout(() => setTries((n) => n + 1), 3000); } })
      .catch((e) => alive && setError(billingError(e, "تعذّر التحقق من حالة الدفع.")));
    return () => { alive = false; };
  }, [tx, tries]);

  let body: React.ReactNode;
  if (!tx) body = <p role="alert" className="text-red-300">رابط غير صالح.</p>;
  else if (error) body = <p role="alert" className="text-red-300 flex items-center gap-2"><AlertTriangle className="w-5 h-5" aria-hidden />{error}</p>;
  else if (!txn) body = <p className="text-gray-100 flex items-center gap-2" role="status"><Loader2 className="w-5 h-5 animate-spin" aria-hidden />جارٍ التحقق من الدفع…</p>;
  else if (txn.status === "paid") body = <p className="text-emerald-300 text-lg flex items-center gap-2" role="status"><CheckCircle2 className="w-6 h-6" aria-hidden />تم الدفع بنجاح ({formatSar(txn.amount_halalas)}). اشتراكك فعّال.</p>;
  else if (txn.status === "pending") body = (
    <p className="text-amber-100 flex items-center gap-2" role="status"><Clock className="w-5 h-5" aria-hidden />
      {tries < 6 ? "ننتظر تأكيد الدفع من المزوّد…" : "لم يصلنا تأكيد الدفع بعد. إن خُصم المبلغ فسيُفعَّل اشتراكك تلقائيًا خلال دقائق، وإلا تواصل مع الدعم."}</p>
  );
  else body = <p className="text-red-300 flex items-center gap-2" role="alert"><AlertTriangle className="w-5 h-5" aria-hidden />لم تكتمل عملية الدفع{txn.failure_reason ? ` (${txn.failure_reason})` : ""}. لم يُخصم أي مبلغ؛ يمكنك المحاولة مرة أخرى.</p>;

  return (
    <div className="max-w-xl mx-auto bg-[#111827] border border-white/10 rounded-2xl p-8 space-y-5" dir="rtl">
      <h1 className="text-2xl font-bold text-white">نتيجة الدفع</h1>
      {body}
      <Link href={`/${locale}/settings#billing`} className="inline-block bg-[#C9A84C] text-[#0A0F1C] px-6 py-2.5 rounded-lg font-bold">العودة إلى الفوترة</Link>
    </div>
  );
}

export default function BillingReturnPage() {
  return <Suspense fallback={<div className="text-gray-100" role="status">جارٍ التحميل…</div>}><ReturnInner /></Suspense>;
}
