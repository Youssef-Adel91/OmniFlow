/**
 * hooks/useInboxStream.ts — Real-Time SSE Hook
 *
 * Connects to `GET /api/v1/stream/dashboard` (Server-Sent Events).
 *
 * ── SSE + Auth Headers Workaround ────────────────────────────────────────────
 * The native `EventSource` API does NOT support custom headers (Authorization,
 * X-Tenant-ID). We solve this with two complementary strategies:
 *
 *   1. Short-lived token query param:
 *      The backend accepts `?token=<jwt>&tenant_id=<uuid>` on the /stream
 *      endpoint as an alternative to headers (tokens are single-use or very
 *      short-lived via Redis). This is a common pattern for SSE.
 *
 *   2. Fallback to `fetch()` streaming (ReadableStream):
 *      If the EventSource approach proves insufficient, replace with a
 *      `fetch()` call that sends proper headers and parses the SSE protocol
 *      manually. That path is scaffolded but disabled by default.
 *
 * ── Events handled ────────────────────────────────────────────────────────────
 *   `message`              → Generic SSE ping / keepalive (ignored)
 *   `new_message`          → A new chat message arrived; pushed to inboxStore
 *   `conversation_update`  → Conversation status changed (takeover, etc.)
 *   `typing`               → AI typing indicator toggled
 *
 * ── Reconnection ──────────────────────────────────────────────────────────────
 * EventSource has built-in exponential back-off reconnect. We additionally
 * track the connection state and expose it so the UI can show a banner.
 */
"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useInboxStore } from "@/store/inboxStore";
import type { Message, Conversation } from "@/store/inboxStore";

// ── Config ────────────────────────────────────────────────────────────────────

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000/api/v1";

// ── Hook return type ──────────────────────────────────────────────────────────

export type StreamStatus = "connecting" | "open" | "closed" | "error";

export interface UseInboxStreamReturn {
  status:     StreamStatus;
  reconnect:  () => void;
  disconnect: () => void;
}

// ── Hook implementation ───────────────────────────────────────────────────────

/**
 * Mount this hook once in the Inbox page (or a layout that wraps all inbox
 * routes). It will open the SSE connection on mount and close it on unmount.
 *
 * @example
 * const { status } = useInboxStream();
 */
export function useInboxStream(): UseInboxStreamReturn {
  const { getToken } = useAuth();
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const esRef               = useRef<EventSource | null>(null);

  // Pull store actions via stable references (avoid re-render loops)
  const addMessage         = useInboxStore.getState().addMessage;
  const updateConversation = useInboxStore.getState().updateConversation;
  const setTyping          = useInboxStore.getState().setTyping;

  // ── Build authenticated SSE URL ──────────────────────────────────────────
  const buildStreamUrl = useCallback(async (): Promise<string> => {
    let token = "";
    try {
      token = await getToken() ?? "";
    } catch (e) {
      console.error("[InboxStream] Failed to get Clerk token", e);
    }
    
    // NOTE: the legacy `tenant_id` query param used to be read from the
    // localStorage key `omniflow_tenant_id`, written by the old local-JWT
    // login flow. That flow was removed in the Clerk unification — the tenant
    // is now resolved server-side from the Clerk token, so we only send it.
    const url = new URL(`${API_BASE}/stream/dashboard`);
    if (token) url.searchParams.set("token", token);
    return url.toString();
  }, [getToken]);

  // ── Connect ───────────────────────────────────────────────────────────────
  const connect = useCallback(async () => {
    // Cleanup any existing connection first
    esRef.current?.close();

    const url = await buildStreamUrl();
    const es  = new EventSource(url);
    esRef.current = es;

    es.addEventListener("open", () => {
      setStatus("open");
      console.info("[InboxStream] SSE connection established.");
    });

    // ── Event: new_message ──────────────────────────────────────────────────
    es.addEventListener("new_message", (event: MessageEvent) => {
      try {
        const msg: Message = JSON.parse(event.data as string);
        addMessage(msg);

        // Increment global unread badge if not the active conversation
        const { activeConversationId } = useInboxStore.getState();
        if (msg.conversationId !== activeConversationId) {
          // Import is circular-safe because we read from the store directly
          import("@/store/tenantStore").then(({ useTenantStore }) => {
            useTenantStore.getState().incrementUnread();
          });
        }
      } catch (e) {
        console.error("[InboxStream] Failed to parse new_message event:", e);
      }
    });

    // ── Event: conversation_update ──────────────────────────────────────────
    es.addEventListener("conversation_update", (event: MessageEvent) => {
      try {
        const patch: Partial<Conversation> & { id: string } = JSON.parse(
          event.data as string,
        );
        updateConversation(patch.id, patch);
      } catch (e) {
        console.error("[InboxStream] Failed to parse conversation_update:", e);
      }
    });

    // ── Event: typing ──────────────────────────────────────────────────────
    es.addEventListener("typing", (event: MessageEvent) => {
      try {
        const { conversationId, isTyping } = JSON.parse(event.data as string) as {
          conversationId: string;
          isTyping: boolean;
        };
        // Only show the indicator if this is the currently active conversation
        const { activeConversationId } = useInboxStore.getState();
        if (conversationId === activeConversationId) {
          setTyping(isTyping);
          // Auto-clear typing indicator after 5 s in case the backend misses
          // sending a `typing: false` event
          if (isTyping) {
            setTimeout(() => setTyping(false), 5_000);
          }
        }
      } catch (e) {
        console.error("[InboxStream] Failed to parse typing event:", e);
      }
    });

    // ── Error / reconnect ───────────────────────────────────────────────────
    es.addEventListener("error", (err) => {
      console.warn("[InboxStream] SSE error — browser will auto-reconnect:", err);
      setStatus("error");
      // EventSource handles reconnection automatically with exponential back-off.
      // We flip back to "connecting" state for UI feedback.
      if (es.readyState === EventSource.CONNECTING) {
        setStatus("connecting");
      } else if (es.readyState === EventSource.CLOSED) {
        setStatus("closed");
        esRef.current = null;
      }
    });
  }, [buildStreamUrl, addMessage, updateConversation, setTyping]);

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  useEffect(() => {
    connect();
    return () => {
      esRef.current?.close();
      esRef.current = null;
      setStatus("closed");
    };
  }, [connect]);

  const reconnect  = useCallback(() => { connect(); }, [connect]);
  const disconnect = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    setStatus("closed");
  }, []);

  return { status, reconnect, disconnect };
}
