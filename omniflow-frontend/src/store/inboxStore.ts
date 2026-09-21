/**
 * store/inboxStore.ts — Smart Inbox Zustand Store (Sprint 13)
 *
 * Sprint 13 additions over Sprint 12:
 *   - `leadScore` field on Conversation (AI lead score 0–100)
 *   - `channelFilter` state variable (ChannelFilter | 'ALL')
 *   - `sortBy` state variable ('recent' | 'hot_leads')
 *   - `loadConversations()` now passes channelFilter + sortBy to the API
 *   - `setChannelFilter()` / `setSortBy()` trigger a fresh fetch
 *   - `useFilteredConversations` respects the active sortBy mode client-side
 *
 * Manages the full state of the 3-pane inbox:
 *   - conversations:        sorted list with status, unread count, last message
 *   - activeConversationId: which conversation is open
 *   - messages:             per-conversation message cache (keyed by conv ID)
 *   - typing:               AI/agent typing indicator state
 *   - isLoading / error:    async fetch state for UI loading skeletons
 */
import { create } from "zustand";
import {
  fetchConversations,
  fetchMessages,
  sendMessage       as apiSendMessage,
  takeOverConversation,
  returnConversationToAI,
  type ChannelFilter,
  type SortByOption,
} from "@/lib/api/inbox";

// ── Types ─────────────────────────────────────────────────────────────────────

export type ConversationStatus =
  | "BOT_ACTIVE"
  | "AGENT_ACTIVE"
  | "ESCALATED"
  | "DORMANT"
  | "CLOSED";

export type SenderType   = "customer" | "ai_bot" | "human_agent";
export type MessageType  = "text" | "image" | "audio" | "location" | "interactive";

export interface Conversation {
  id:              string;
  tenantId:        string;
  customerPhone:   string;
  customerName:    string;
  customerAvatar?: string;
  channel:         "whatsapp" | "tiktok" | "instagram" | "x" | "web";
  status:          ConversationStatus;
  lastMessage:     string;
  lastMessageAt:   string;   // ISO string
  unreadCount:     number;
  isAiActive:      boolean;
  // AI Lead Scoring (Sprint 13)
  leadScore:       number;   // 0–100; >75 = Hot Lead 🔥
  // AI extracted context
  intent?:         string;
  budget?:         number;
  lookingIn?:      string;
  propertyType?:   string;
  sentiment?:      "positive" | "neutral" | "negative" | "urgent";
  aiConfidence?:   number;   // 0–1
}

export interface Message {
  id:             string;
  conversationId: string;
  senderType:     SenderType;
  text:           string;
  messageType:    MessageType;
  mediaUrl?:      string | null;   // S3/MinIO presigned URL for audio/image messages
  createdAt:      string;   // ISO string
  isRead:         boolean;
  tier?:          string;   // L0/L1/L2/L3
  model?:         string;   // gemini-flash, etc.
  latencyMs?:     number;
}

// ── Store shape ───────────────────────────────────────────────────────────────

interface InboxState {
  // ── Core data ──────────────────────────────────────────────────────────────
  conversations:        Conversation[];
  activeConversationId: string | null;
  messages:             Record<string, Message[]>;

  // ── UI state ───────────────────────────────────────────────────────────────
  isTyping:             boolean;
  filter:               ConversationStatus | "ALL";
  // Sprint 13: Channel & Sort filters
  channelFilter:        ChannelFilter;
  sortBy:               SortByOption;

  // ── Async state ────────────────────────────────────────────────────────────
  isLoadingConversations: boolean;
  isLoadingMessages:      boolean;
  conversationsError:     string | null;
  messagesError:          Record<string, string | null>; // per conv error

  // ── Derived (selector hooks below are preferred for performance) ──────────
  activeConversation:   Conversation | null;
  activeMessages:       Message[];

  // ── Async thunks ───────────────────────────────────────────────────────────
  loadConversations:  () => Promise<void>;
  loadMessages:       (conversationId: string) => Promise<void>;
  sendAgentMessage:   (conversationId: string, text: string) => Promise<void>;
  requestTakeOver:    (conversationId: string) => Promise<void>;
  requestReturnToAI:  (conversationId: string) => Promise<void>;

  // ── Synchronous actions ────────────────────────────────────────────────────
  setActiveConversation: (id: string) => void;
  addMessage:            (msg: Message) => void;
  takeOverChat:          (conversationId: string) => void;
  returnToAI:            (conversationId: string) => void;
  markAsRead:            (conversationId: string) => void;
  setFilter:             (f: ConversationStatus | "ALL") => void;
  setChannelFilter:      (channel: ChannelFilter) => void;
  setSortBy:             (sort: SortByOption) => void;
  setTyping:             (v: boolean) => void;
  updateConversation:    (id: string, patch: Partial<Conversation>) => void;
}

// ── Store implementation ──────────────────────────────────────────────────────

export const useInboxStore = create<InboxState>()((set, get) => ({
  // ── Initial state ──────────────────────────────────────────────────────────
  conversations:           [],
  activeConversationId:    null,
  messages:                {},
  isTyping:                false,
  filter:                  "ALL",
  channelFilter:           "ALL",
  sortBy:                  "recent",

  isLoadingConversations:  false,
  isLoadingMessages:       false,
  conversationsError:      null,
  messagesError:           {},

  // ── Derived (read at call-time via get()) ──────────────────────────────────
  get activeConversation() {
    const { conversations, activeConversationId } = get();
    return conversations.find((c) => c.id === activeConversationId) ?? null;
  },
  get activeMessages() {
    const { messages, activeConversationId } = get();
    if (!activeConversationId) return [];
    return messages[activeConversationId] ?? [];
  },

  // ── Async thunk: loadConversations ─────────────────────────────────────────
  loadConversations: async () => {
    // Guard: prevent double-loading
    if (get().isLoadingConversations) return;

    const { channelFilter, sortBy } = get();

    set({ isLoadingConversations: true, conversationsError: null });
    try {
      const conversations = await fetchConversations(
        50,
        0,
        channelFilter,
        sortBy,
      );
      set({
        conversations:          conversations,
        isLoadingConversations: false,
      });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to load conversations";
      console.error("[inboxStore] loadConversations error:", err);
      set({
        isLoadingConversations: false,
        conversationsError:     message,
      });
    }
  },

  // ── Async thunk: loadMessages ──────────────────────────────────────────────
  loadMessages: async (conversationId: string) => {
    // Optimistic: if messages are already cached, don't re-fetch
    const cached = get().messages[conversationId];
    if (cached && cached.length > 0) return;

    set({ isLoadingMessages: true });
    try {
      const messages = await fetchMessages(conversationId);
      set((s) => ({
        messages:          { ...s.messages, [conversationId]: messages },
        isLoadingMessages: false,
        messagesError:     { ...s.messagesError, [conversationId]: null },
      }));
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to load messages";
      console.error("[inboxStore] loadMessages error:", err);
      set((s) => ({
        isLoadingMessages: false,
        messagesError:     { ...s.messagesError, [conversationId]: message },
      }));
    }
  },

  // ── Async thunk: sendAgentMessage ──────────────────────────────────────────
  sendAgentMessage: async (conversationId: string, text: string) => {
    try {
      // Optimistic update: add the message immediately with a temp ID
      const tempMsg: Message = {
        id:             `temp-${Date.now()}`,
        conversationId,
        senderType:     "human_agent",
        text,
        messageType:    "text",
        createdAt:      new Date().toISOString(),
        isRead:         true,
      };
      get().addMessage(tempMsg);

      // Persist to backend — replace temp message with confirmed one
      const confirmed = await apiSendMessage(conversationId, text);
      set((s) => {
        const msgs = (s.messages[conversationId] ?? []).filter(
          (m) => m.id !== tempMsg.id,
        );
        return {
          messages: { ...s.messages, [conversationId]: [...msgs, confirmed] },
        };
      });
    } catch (err) {
      console.error("[inboxStore] sendAgentMessage error:", err);
      // Remove the optimistic message on failure
      set((s) => ({
        messages: {
          ...s.messages,
          [conversationId]: (s.messages[conversationId] ?? []).filter(
            (m) => !m.id.startsWith("temp-"),
          ),
        },
      }));
      throw err; // re-throw so the UI can show a toast
    }
  },

  // ── Async thunk: requestTakeOver ───────────────────────────────────────────
  requestTakeOver: async (conversationId: string) => {
    // Optimistic local update
    get().takeOverChat(conversationId);
    try {
      const updated = await takeOverConversation(conversationId);
      get().updateConversation(conversationId, updated);
    } catch (err) {
      console.error("[inboxStore] requestTakeOver error:", err);
      // Rollback
      get().returnToAI(conversationId);
      throw err;
    }
  },

  // ── Async thunk: requestReturnToAI ─────────────────────────────────────────
  requestReturnToAI: async (conversationId: string) => {
    // Optimistic local update
    get().returnToAI(conversationId);
    try {
      const updated = await returnConversationToAI(conversationId);
      get().updateConversation(conversationId, updated);
    } catch (err) {
      console.error("[inboxStore] requestReturnToAI error:", err);
      // Rollback
      get().takeOverChat(conversationId);
      throw err;
    }
  },

  // ── Sync: setActiveConversation ────────────────────────────────────────────
  setActiveConversation: (id) => {
    set({ activeConversationId: id });
    get().markAsRead(id);
    // Load messages lazily on first open
    void get().loadMessages(id);
  },

  // ── Sync: addMessage (called by SSE hook for real-time messages) ───────────
  addMessage: (msg) => {
    set((s) => {
      const prev  = s.messages[msg.conversationId] ?? [];
      // Deduplicate: drop if the same ID already exists (SSE vs REST race)
      if (prev.some((m) => m.id === msg.id)) return s;

      const convs = s.conversations.map((c) =>
        c.id === msg.conversationId
          ? {
              ...c,
              lastMessage:   msg.text.slice(0, 80),
              lastMessageAt: msg.createdAt,
              // Increment unread only if this is not the active conversation
              unreadCount:
                c.id !== s.activeConversationId
                  ? c.unreadCount + 1
                  : c.unreadCount,
            }
          : c,
      );

      return {
        messages:      { ...s.messages, [msg.conversationId]: [...prev, msg] },
        conversations: convs,
      };
    });
  },

  // ── Sync: takeOverChat (local-only, called by requestTakeOver) ─────────────────
  takeOverChat: (conversationId) => {
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === conversationId
          ? { ...c, status: "AGENT_ACTIVE" as ConversationStatus, isAiActive: false }
          : c,
      ),
    }));
  },

  // ── Sync: returnToAI (local-only, called by requestReturnToAI) ────────────────
  returnToAI: (conversationId) => {
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === conversationId
          ? { ...c, status: "BOT_ACTIVE" as ConversationStatus, isAiActive: true }
          : c,
      ),
    }));
  },

  // ── Sync: markAsRead ───────────────────────────────────────────────────────
  markAsRead: (conversationId) => {
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === conversationId ? { ...c, unreadCount: 0 } : c,
      ),
      messages: {
        ...s.messages,
        [conversationId]: (s.messages[conversationId] ?? []).map((m) => ({
          ...m,
          isRead: true,
        })),
      },
    }));
  },

  setFilter:          (filter)   => set({ filter }),
  setTyping:          (isTyping) => set({ isTyping }),

  // Sprint 13: Channel + sort actions — trigger refetch automatically
  setChannelFilter: (channelFilter) => {
    set({ channelFilter, isLoadingConversations: false });
    void get().loadConversations();
  },
  setSortBy: (sortBy) => {
    set({ sortBy, isLoadingConversations: false });
    void get().loadConversations();
  },

  updateConversation: (id, patch) =>
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, ...patch } : c,
      ),
    })),
}));

// ── Selector hooks (stable, avoid re-renders for unrelated state) ─────────────

export const useFilteredConversations = () =>
  useInboxStore((s) => {
    let list = [...s.conversations];

    // Client-side sort fallback (for SSE-updated conversations)
    if (s.sortBy === "hot_leads") {
      list = list.sort((a, b) => b.leadScore - a.leadScore);
    } else {
      list = list.sort(
        (a, b) =>
          new Date(b.lastMessageAt).getTime() -
          new Date(a.lastMessageAt).getTime(),
      );
    }

    if (s.filter === "ALL") return list;
    return list.filter((c) => c.status === s.filter);
  });

export const useActiveConversation = () =>
  useInboxStore((s) =>
    s.conversations.find((c) => c.id === s.activeConversationId) ?? null,
  );

export const useActiveMessages = () =>
  useInboxStore((s) =>
    s.activeConversationId
      ? (s.messages[s.activeConversationId] ?? [])
      : [],
  );

export const useInboxLoadingState = () =>
  useInboxStore((s) => ({
    isLoadingConversations: s.isLoadingConversations,
    isLoadingMessages:      s.isLoadingMessages,
    conversationsError:     s.conversationsError,
  }));

// Sprint 13: exported selectors for the new filter controls
export const useInboxFilters = () =>
  useInboxStore((s) => ({
    channelFilter: s.channelFilter,
    sortBy:        s.sortBy,
    filter:        s.filter,
  }));
