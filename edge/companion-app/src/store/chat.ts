/** Chat 状态 store —— 提到 Zustand 是为了**跨 tab 切换不丢消息**。
 *
 * 之前 useChat 用 useState 放在 ChatTab 组件里,React unmount 时 state 销毁,
 * 用户切到"控制台"再切回"对话"看不到之前的对话。
 *
 * 现在 store 是单例,跨组件 mount/unmount 持久。
 * Week 3 持久化时(写 state.db),从这个 store 钩 listener 即可。
 */

import { create } from "zustand";
import type { ChatMessage } from "../types/chat";

interface ChatState {
  /** 当前对话所有消息 */
  messages: ChatMessage[];
  /** 是否在流式输出中 */
  isStreaming: boolean;
  /** 当前正在流式输出的 assistant 消息 id（用来判断在哪里 append delta、显示光标） */
  streamingId: string | null;
  /** 选中的模型 id */
  model: string;
  /** 写入 ~/.hermes/state.db 的 session id (Plan C Week 2 持久化)
   *  null = 还没创建 (lazy create on first send) */
  persistedSessionId: string | null;

  // ── actions ──
  setMessages: (msgs: ChatMessage[]) => void;
  addMessage: (msg: ChatMessage) => void;
  updateMessage: (id: string, update: Partial<ChatMessage>) => void;
  appendToMessage: (id: string, delta: string) => void;
  setIsStreaming: (v: boolean) => void;
  setStreamingId: (id: string | null) => void;
  setModel: (m: string) => void;
  setPersistedSessionId: (id: string | null) => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isStreaming: false,
  streamingId: null,
  model: "catfish-private-main",
  persistedSessionId: null,

  setMessages: (messages) => set({ messages }),
  addMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg] })),
  updateMessage: (id, update) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, ...update } : m,
      ),
    })),
  appendToMessage: (id, delta) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, content: m.content + delta } : m,
      ),
    })),
  setIsStreaming: (v) => set({ isStreaming: v }),
  setStreamingId: (id) => set({ streamingId: id }),
  setModel: (model) => set({ model }),
  setPersistedSessionId: (persistedSessionId) =>
    set({ persistedSessionId }),
  reset: () =>
    set({
      messages: [],
      isStreaming: false,
      streamingId: null,
      persistedSessionId: null,
    }),
}));
