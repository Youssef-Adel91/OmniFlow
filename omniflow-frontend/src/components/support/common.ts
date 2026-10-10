/** Shared bits of the support screens. Contrast: every text colour is ≥ gray-300 on the #111827/#0A0F1C surfaces (AA). */
export const STATUS_TONE = {
  gold: "text-amber-200 bg-amber-400/15 border-amber-300/40",
  blue: "text-sky-200 bg-sky-400/15 border-sky-300/40",
  green: "text-emerald-200 bg-emerald-400/15 border-emerald-300/40",
} as const;

export function formatTicketDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("ar-SA", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(d);
}

export const fieldClass =
  "w-full bg-[#0A0F1C] border border-white/15 rounded-lg py-2.5 px-3 text-sm text-white placeholder-gray-400 focus:outline-none focus:border-[#C9A84C]/60";
