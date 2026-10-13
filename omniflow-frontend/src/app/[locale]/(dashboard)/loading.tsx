import { FALLBACK_COPY } from "@/components/system/messages";

/** Shown while a dashboard page streams in. Locale-agnostic skeleton with an accessible busy label in both languages. */
export default function DashboardLoading() {
  return (
    <div role="status" aria-busy="true" aria-live="polite" className="space-y-4 animate-pulse p-2">
      <span className="sr-only">{FALLBACK_COPY.ar.loading} / {FALLBACK_COPY.en.loading}</span>
      <div className="h-8 w-56 rounded-lg bg-black/10" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <div key={i} className="h-28 rounded-xl bg-black/10" />)}</div>
      <div className="h-72 rounded-xl bg-black/10" />
    </div>
  );
}
