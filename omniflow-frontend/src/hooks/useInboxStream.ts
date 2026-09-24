/**
 * hooks/useInboxStream.ts — Real-Time SSE Hook
 * Refreshes authentication on reconnect and reloads missed inbox data.
 */
"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useInboxStore } from "@/store/inboxStore";
import { mapMessage, mapConversationPatch } from "@/lib/api/inbox";
import { createInboxStream, type StreamStatus } from "@/lib/api/inbox-stream";
export type { StreamStatus } from "@/lib/api/inbox-stream";

// ── Config ────────────────────────────────────────────────────────────────────

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000/api/v1";

// ── Hook return type ──────────────────────────────────────────────────────────



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
  const esRef               = useRef<ReturnType<typeof createInboxStream> | null>(null);

  // Pull store actions via stable references (avoid re-render loops)
  const addMessage         = useInboxStore.getState().addMessage;
  const updateConversation = useInboxStore.getState().updateConversation;
  const updateMessageStatus = useInboxStore.getState().updateMessageStatus;
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
    if (!token) throw new Error("Missing authentication token");
    url.searchParams.set("token", token);
    return url.toString();
  }, [getToken]);

  // ── Connect ───────────────────────────────────────────────────────────────
  const subscribe = useCallback((es: EventSource) => {
    // ── Event: new_message ──────────────────────────────────────────────────
    es.addEventListener("new_message", (event: MessageEvent) => {
      try {
        const msg = mapMessage(JSON.parse(event.data as string));
        const state = useInboxStore.getState();
        const duplicate = state.messages[msg.conversationId]?.some((item) => item.id === msg.id);
        addMessage(msg);
        if (!state.conversations.some((item) => item.id === msg.conversationId)) {
          void state.loadConversations();
        }

        // Increment global unread badge if not the active conversation
        const { activeConversationId } = useInboxStore.getState();
        if (!duplicate && msg.senderType === "customer" && msg.conversationId !== activeConversationId) {
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
        const patch = mapConversationPatch(JSON.parse(event.data as string));
        const state = useInboxStore.getState();
        if (state.conversations.some((item) => item.id === patch.id)) {
          updateConversation(patch.id, patch);
        } else {
          void state.loadConversations();
        }
      } catch (e) {
        console.error("[InboxStream] Failed to parse conversation_update:", e);
      }
    });

    // ── Event: message_status_update (item 12) ───────────────────────────────
    // Previously a message's PENDING → QUEUED → SENT (and eventually
    // DELIVERED/READ, once a real Meta status webhook exists) transition was
    // invisible to an already-open inbox until a full reload.
    es.addEventListener("message_status_update", (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data as string);
        const conversationId = payload.conversation_id;
        const messageId = payload.id;
        const deliveryStatus = payload.delivery_status;
        if (conversationId && messageId && deliveryStatus) {
          updateMessageStatus(conversationId, messageId, deliveryStatus);
        }
      } catch (e) {
        console.error("[InboxStream] Failed to parse message_status_update event:", e);
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

  }, [addMessage, updateConversation, setTyping]);

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const stream = createInboxStream({
      getUrl: buildStreamUrl,
      subscribe,
      onStatus: setStatus,
      onOpen: () => {
        const state = useInboxStore.getState();
        void state.loadConversations();
        if (state.activeConversationId) void state.loadMessages(state.activeConversationId, true);
      },
    });
    esRef.current = stream;
    void stream.connect();
    return () => {
      stream.disconnect();
      esRef.current = null;
    };
  }, [buildStreamUrl, subscribe]);

  const reconnect = useCallback(() => { void esRef.current?.connect(); }, []);
  const disconnect = useCallback(() => { esRef.current?.disconnect(); }, []);

  return { status, reconnect, disconnect };
}
