/** Copy for the error / not-found / loading fallbacks. Plain data so it can be used from server and client components alike. */
export type FallbackLocale = "ar" | "en";

export const FALLBACK_COPY: Record<FallbackLocale, {
  errorTitle: string; errorBody: string; retry: string; home: string; reference: string;
  notFoundTitle: string; notFoundBody: string; loading: string; dashboard: string;
}> = {
  ar: {
    errorTitle: "حدث خطأ غير متوقع",
    errorBody: "تعذّر عرض هذه الصفحة. بياناتك آمنة؛ جرّب إعادة المحاولة، وإن استمرت المشكلة تواصل مع الدعم وزوّدنا بالرمز أدناه.",
    retry: "إعادة المحاولة",
    home: "العودة إلى الرئيسية",
    reference: "رمز الخطأ",
    notFoundTitle: "الصفحة غير موجودة",
    notFoundBody: "الرابط الذي فتحته غير صحيح أو لم يعد متاحًا.",
    loading: "جارٍ التحميل…",
    dashboard: "لوحة التحكم",
  },
  en: {
    errorTitle: "Something went wrong",
    errorBody: "This page could not be shown. Your data is safe; try again, and if it keeps happening contact support and quote the reference below.",
    retry: "Try again",
    home: "Back to home",
    reference: "Error reference",
    notFoundTitle: "Page not found",
    notFoundBody: "The link you opened is wrong or no longer available.",
    loading: "Loading…",
    dashboard: "Dashboard",
  },
};

export function asFallbackLocale(value: string | string[] | undefined | null): FallbackLocale {
  const v = Array.isArray(value) ? value[0] : value;
  return v === "en" ? "en" : "ar";
}
