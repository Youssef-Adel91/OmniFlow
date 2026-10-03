/** Shared formatting + labels for the analytics dashboard (Arabic, RTL). */

// Gregorian calendar explicitly: plain "ar-SA" defaults to Hijri (e.g. "٢٠ ربيع الأول").
const AR = "ar-SA-u-ca-gregory";

export const fmtNumber = (n: number | null | undefined) =>
  n == null ? "—" : new Intl.NumberFormat(AR).format(n);

export const fmtPercent = (n: number | null | undefined) =>
  n == null ? "—" : `${new Intl.NumberFormat(AR, { maximumFractionDigits: 1 }).format(n)}٪`;

export const fmtSar = (n: number | null | undefined) =>
  n == null
    ? "—"
    : new Intl.NumberFormat(AR, { style: "currency", currency: "SAR", maximumFractionDigits: 0 }).format(n);

/** 45 -> "٤٥ ث", 300 -> "٥ د", 5400 -> "١ س ٣٠ د". */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  const n = (v: number) => new Intl.NumberFormat(AR).format(v);
  if (s < 60) return `${n(s)} ث`;
  if (s < 3600) return `${n(Math.round(s / 60))} د`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return m ? `${n(h)} س ${n(m)} د` : `${n(h)} س`;
}

export const fmtDay = (iso: string) =>
  new Intl.DateTimeFormat(AR, { day: "numeric", month: "short" }).format(new Date(`${iso}T00:00:00`));

export const CHANNEL_LABELS: Record<string, string> = {
  whatsapp: "واتساب",
  instagram: "إنستجرام",
  messenger: "ماسنجر",
  tiktok: "تيك توك",
  x: "إكس",
  snapchat: "سناب شات",
  web: "الويب",
};

/** CSS-var chart colours so charts follow the light/dark theme. */
export const CHANNEL_COLORS: Record<string, string> = {
  whatsapp: "var(--chart-5)",
  instagram: "var(--chart-3)",
  messenger: "var(--chart-2)",
  tiktok: "var(--chart-4)",
  x: "var(--chart-axis)",
  snapchat: "var(--chart-1)",
  web: "var(--chart-1)",
};

export const STATUS_LABELS: Record<string, string> = {
  ai_active: "يُدار بالذكاء الاصطناعي",
  human_active: "وكيل بشري نشط",
  escalated: "تصعيد بشري",
  closed: "مغلقة",
  dormant: "غير نشطة",
};

/** Postgres dow order: row 0 = Sunday. */
export const WEEKDAYS = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"];

export const PROPERTY_STATUS_LABELS: Record<string, string> = {
  PENDING_VERIFICATION: "بانتظار التحقق",
  VERIFIED_ACTIVE: "نشط ومُتحقق",
  VERIFICATION_FAILED: "فشل التحقق",
  SUSPENDED: "موقوف",
  SOLD: "مباع",
  RENTED: "مؤجَّر",
  WITHDRAWN: "مسحوب",
};

export const PROPERTY_TYPE_LABELS: Record<string, string> = {
  apartment: "شقة",
  villa: "فيلا",
  land: "أرض",
  commercial: "تجاري",
  daily_rental: "إيجار يومي",
  office: "مكتب",
  warehouse: "مستودع",
};
