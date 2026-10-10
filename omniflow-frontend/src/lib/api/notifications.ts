/** lib/api/notifications.ts — in-app notifications + preferences (backend: routers/notifications.py). */
import { apiClient } from "@/lib/api/client";

export interface AppNotification { notification_id: string; kind: string; title: string; body: string | null; link: string | null; created_at: string | null; read: boolean }
export interface PrefRow { kind: string; label: string; description: string; in_app: boolean; email: boolean }
export interface PrefsResponse { items: PrefRow[]; email_available: boolean }

/** Relative time in Arabic ("قبل 5 دقائق"); falls back to a date after a week. */
export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "الآن";
  const m = Math.floor(s / 60);
  if (m < 60) return m === 1 ? "قبل دقيقة" : m === 2 ? "قبل دقيقتين" : `قبل ${m} ${m <= 10 ? "دقائق" : "دقيقة"}`;
  const h = Math.floor(m / 60);
  if (h < 24) return h === 1 ? "قبل ساعة" : h === 2 ? "قبل ساعتين" : `قبل ${h} ${h <= 10 ? "ساعات" : "ساعة"}`;
  const d = Math.floor(h / 24);
  if (d < 7) return d === 1 ? "أمس" : d === 2 ? "قبل يومين" : `قبل ${d} أيام`;
  return new Date(t).toLocaleDateString("ar-SA");
}

/** A notification link is app-relative ("/settings#billing"); anything else is ignored. */
export function hrefFor(locale: string, link: string | null): string | null {
  return link && link.startsWith("/") && !link.startsWith("//") ? `/${locale}${link}` : null;
}

/** Badge text: caps at 99+. */
export function badgeText(n: number): string { return n > 99 ? "99+" : String(n); }

export async function fetchNotifications(limit = 20): Promise<{ items: AppNotification[]; unread_count: number }> { return (await apiClient.get("/notifications", { params: { limit } })).data; }
export async function fetchUnreadCount(): Promise<number> { return (await apiClient.get<{ unread_count: number }>("/notifications/unread-count")).data.unread_count; }
export async function markRead(id: string): Promise<void> { await apiClient.post(`/notifications/${id}/read`); }
export async function markAllRead(): Promise<void> { await apiClient.post("/notifications/read-all"); }
export async function fetchPrefs(): Promise<PrefsResponse> { return (await apiClient.get<PrefsResponse>("/notifications/prefs")).data; }
export async function savePrefs(items: PrefRow[]): Promise<PrefsResponse> {
  const body = Object.fromEntries(items.map((i) => [i.kind, { in_app: i.in_app, email: i.email }]));
  return (await apiClient.put<PrefsResponse>("/notifications/prefs", body)).data;
}
