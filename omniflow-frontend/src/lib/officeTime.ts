/** Viewing / appointment times are shown in the OFFICE's timezone (settings → sales → viewing hours, default Asia/Riyadh), never in the browser's. */
export const DEFAULT_OFFICE_TZ = "Asia/Riyadh";

export function formatOfficeTime(iso: string | null | undefined, timeZone?: string | null, locale = "ar-SA-u-ca-gregory"): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const opts = { dateStyle: "medium", timeStyle: "short" } as const;
  try {
    return new Intl.DateTimeFormat(locale, { ...opts, timeZone: timeZone || DEFAULT_OFFICE_TZ }).format(d);
  } catch {
    return new Intl.DateTimeFormat(locale, { ...opts, timeZone: DEFAULT_OFFICE_TZ }).format(d);    // an unknown zone name must not break the screen
  }
}
