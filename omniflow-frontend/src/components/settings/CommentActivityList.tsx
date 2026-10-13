"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { commentActionLabel, fetchCommentActivity, type CommentActivityItem } from "@/lib/api/settings";

const when = (iso: string) => new Intl.DateTimeFormat("ar-SA-u-ca-gregory", { timeZone: "Asia/Riyadh", dateStyle: "short", timeStyle: "short" }).format(new Date(iso));
const TONE = { ok: "text-emerald-300", warn: "text-amber-200", muted: "text-gray-300" } as const;

/** The last 20 comments the assistant looked at, and what it did with each one. Kept for 14 days. */
export default function CommentActivityList() {
  const [items, setItems] = useState<CommentActivityItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const load = useCallback(async () => {
    setFailed(false);
    try { setItems(await fetchCommentActivity()); } catch { setFailed(true); setItems([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return (
    <div className="space-y-2" data-testid="comment-activity">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-white">آخر التعليقات</h4>
        <button onClick={() => void load()} className="text-xs text-gray-200 hover:text-white flex items-center gap-1" aria-label="تحديث القائمة"><RefreshCw className="w-3.5 h-3.5" aria-hidden />تحديث</button>
      </div>
      {items === null ? <Loader2 className="w-4 h-4 animate-spin text-gray-300" aria-label="جارٍ التحميل" /> : failed ? <p role="alert" className="text-xs text-red-300">تعذّر تحميل القائمة.</p> : items.length === 0 ? (
        <p className="text-xs text-gray-300">لا توجد تعليقات حديثة بعد. تظهر هنا التعليقات التي عالجها المساعد خلال آخر 14 يومًا.</p>
      ) : (
        <ul className="divide-y divide-white/10 rounded-lg border border-white/10">
          {items.map((it) => {
            const l = commentActionLabel(it.action, it.reason);
            return (
              <li key={`${it.comment_id}-${it.at}`} className="px-3 py-2 text-xs space-y-0.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className={`font-semibold ${TONE[l.tone]}`}>{l.text}</span>
                  <span className="text-gray-300">{it.platform === "instagram" ? "إنستجرام" : "فيسبوك"} · {when(it.at)}</span>
                </div>
                <p className="text-gray-100 break-words">{it.name ? `${it.name}: ` : ""}«{it.preview}»</p>
                {it.public_reply && <p className="text-gray-300">رددنا علنًا: {it.public_reply}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
