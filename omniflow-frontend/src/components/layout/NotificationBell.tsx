"use client";

/** TopNav bell: real unread count + the latest notifications (polled every 60 s while the tab is visible). */
import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bell, CheckCheck, Loader2 } from "lucide-react";
import { badgeText, fetchNotifications, hrefFor, markAllRead, markRead, timeAgo, type AppNotification } from "@/lib/api/notifications";

export function NotificationBell({ locale }: { locale: string }) {
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<AppNotification[] | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { const r = await fetchNotifications(15); setItems(r.items); setUnread(r.unread_count); setError(false); } catch { setError(true); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => { if (document.visibilityState === "visible") void load(); }, 60_000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  const open_ = (n: AppNotification) => {
    if (!n.read) { setItems((l) => l?.map((x) => (x.notification_id === n.notification_id ? { ...x, read: true } : x)) ?? l); setUnread((u) => Math.max(0, u - 1)); void markRead(n.notification_id).catch(() => undefined); }
    setOpen(false);
  };

  return (
    <div className="relative" ref={box}>
      <button type="button" className="relative btn btn-ghost h-9 w-9 p-0" aria-label={unread > 0 ? `الإشعارات — ${unread} غير مقروء` : "الإشعارات"}
        aria-expanded={open} aria-haspopup="true" onClick={() => { setOpen((o) => !o); if (!open) void load(); }}>
        <Bell className="w-4.5 h-4.5 text-[var(--muted-foreground)]" aria-hidden />
        {unread > 0 && (
          <span className="absolute -top-0.5 -end-0.5 min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-[#C9A84C] text-[#0A0F1C] text-[10px] font-bold flex items-center justify-center" data-testid="bell-badge">{badgeText(unread)}</span>
        )}
      </button>
      {open && (
        <div role="region" aria-label="الإشعارات" dir="rtl" className="absolute end-0 mt-2 w-80 max-w-[90vw] rounded-xl border border-[var(--border)] bg-[var(--menu-bg)] shadow-xl z-50">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--border)]">
            <h2 className="text-sm font-bold text-[var(--foreground)]">الإشعارات</h2>
            <div className="flex items-center gap-2">
              {loading && <Loader2 className="w-3.5 h-3.5 animate-spin text-[var(--muted-foreground)]" aria-label="جارٍ التحديث" />}
              {unread > 0 && <button onClick={() => { setItems((l) => l?.map((x) => ({ ...x, read: true })) ?? l); setUnread(0); void markAllRead().catch(() => undefined); }} className="text-xs underline text-[var(--foreground)] flex items-center gap-1"><CheckCheck className="w-3.5 h-3.5" aria-hidden />تعليم الكل كمقروء</button>}
            </div>
          </div>
          <div className="max-h-96 overflow-y-auto">
            {error && <p role="alert" className="p-4 text-sm text-red-400">تعذّر تحميل الإشعارات. <button onClick={() => void load()} className="underline">إعادة المحاولة</button></p>}
            {!error && items === null && <div className="p-4 space-y-2" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="h-12 rounded bg-[var(--menu-hover)] animate-pulse" />)}</div>}
            {!error && items?.length === 0 && <p className="p-6 text-sm text-center text-[var(--muted-foreground)]">لا توجد إشعارات.</p>}
            <ul>
              {items?.map((n) => {
                const href = hrefFor(locale, n.link);
                const inner = (
                  <>
                    <span className="flex items-start gap-2">
                      {!n.read && <span className="mt-1.5 w-2 h-2 rounded-full bg-[#C9A84C] shrink-0" aria-label="غير مقروء" />}
                      <span className="min-w-0">
                        <span className={`block text-sm ${n.read ? "text-[var(--muted-foreground)]" : "font-semibold text-[var(--foreground)]"}`}>{n.title}</span>
                        {n.body && <span className="block text-xs text-[var(--muted-foreground)] mt-0.5 line-clamp-2">{n.body}</span>}
                        <span className="block text-[11px] text-[var(--muted-foreground)] mt-1">{timeAgo(n.created_at)}</span>
                      </span>
                    </span>
                  </>
                );
                return (
                  <li key={n.notification_id} className="border-b border-[var(--border)] last:border-0">
                    {href ? <Link href={href} onClick={() => open_(n)} className="block px-4 py-3 hover:bg-[var(--menu-hover)]">{inner}</Link>
                      : <button type="button" onClick={() => open_(n)} className="block w-full text-start px-4 py-3 hover:bg-[var(--menu-hover)]">{inner}</button>}
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
