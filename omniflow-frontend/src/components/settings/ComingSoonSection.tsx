"use client";

/** Honest placeholders for sections that are designed but not built yet: no controls that pretend to work. */

import React from "react";
import { Hourglass } from "lucide-react";
import { TIER_LABELS, type TenantSettings } from "@/lib/api/settings";
import { SectionHeader, cardClass } from "./common";

export const COMING_SOON: Record<string, { title: string; description: string; planned: string[] }> = {
  team: { title: "الفريق والصلاحيات", description: "دعوة الموظفين وإدارة أدوارهم.", planned: ["دعوة موظف بالبريد", "أدوار: مالك / مدير / وكيل / مشاهد", "تعطيل حساب موظف", "سجل دخول المستخدمين"] },
  notifications: { title: "الإشعارات", description: "متى ومن يُنبَّه.", planned: ["تنبيه التصعيد إلى موظف", "تنبيه العميل الساخن", "قنوات التنبيه: بريد / Push / واتساب"] },
  privacy: { title: "الخصوصية والامتثال (PDPL)", description: "سياسة البيانات وحقوق العملاء.", planned: ["سياسة الاحتفاظ بالبيانات", "تصدير أو حذف بيانات عميل بطلبه", "عارض سجل التدقيق"] },
  billing: { title: "الفوترة والاستهلاك", description: "الباقة والحدود.", planned: ["الرسائل والتوكنز المستهلكة", "حدود الباقة وتنبيهات الاقتراب منها", "الفواتير"] },
  developers: { title: "المطورون", description: "التكامل البرمجي.", planned: ["مفاتيح API", "Webhooks صادرة"] },
};

export default function ComingSoonSection({ sectionKey, settings }: { sectionKey: string; settings: TenantSettings | null }) {
  const s = COMING_SOON[sectionKey];
  if (!s) return null;
  return (
    <div className={cardClass} data-testid={`soon-${sectionKey}`}>
      <SectionHeader icon={Hourglass} title={s.title} description={s.description} />
      <p className="inline-block text-xs rounded-full border border-[#C9A84C]/50 text-[#E4D5A5] px-3 py-1">قريبًا — هذا القسم غير متاح بعد</p>
      <ul className="list-disc ps-5 text-sm text-gray-200 space-y-1">{s.planned.map((p) => <li key={p}>{p}</li>)}</ul>
      {sectionKey === "billing" && settings && (
        <div className="rounded-xl border border-white/10 p-4 text-sm text-gray-100" data-testid="billing-current">
          <p>الباقة الحالية: <strong className="text-[#C9A84C]">{TIER_LABELS[settings.subscription_tier] ?? settings.subscription_tier}</strong></p>
          <p className="text-xs text-gray-300 mt-1">للتغيير أو الاستفسار عن الفوترة تواصل مع الدعم.</p>
        </div>
      )}
      <p className="text-xs text-gray-300">لا نعرض هنا خيارات غير فعّالة. أخبرنا من صفحة الدعم بما تحتاجه أولًا.</p>
    </div>
  );
}
