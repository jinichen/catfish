/** Chat 状态 store —— 提到 Zustand 是为了**跨 tab 切换不丢消息**。
 *
 * 之前 useChat 用 useState 放在 ChatTab 组件里,React unmount 时 state 销毁,
 * 用户切到"控制台"再切回"对话"看不到之前的对话。
 *
 * 现在 store 是单例,跨组件 mount/unmount 持久。
 * Week 3 持久化 (state.db 持久 + sidebar 切换 + resume 历史) 钩这个 store。
 */

import { create } from "zustand";
import type { ChatMessage, ToolCall } from "../types/chat";
import type { SessionDetail, SessionMessage } from "../types/session";

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
  /** BL-CONTEXT-COUNTER (5/13 借鉴 Hermes 0.13): 最近一轮 LLM 完成时的
   *  prompt_tokens, 给状态栏 context counter 用 (xxK / 128K, 80% 黄, 95% 红).
   *  reset / 切 session 时清零, 每次 onDone 时更新. */
  lastPromptTokens: number | null;

  // ── actions ──
  setMessages: (msgs: ChatMessage[]) => void;
  addMessage: (msg: ChatMessage) => void;
  updateMessage: (id: string, update: Partial<ChatMessage>) => void;
  appendToMessage: (id: string, delta: string) => void;
  setIsStreaming: (v: boolean) => void;
  setStreamingId: (id: string | null) => void;
  setModel: (m: string) => void;
  setPersistedSessionId: (id: string | null) => void;
  setLastPromptTokens: (n: number | null) => void;
  /**
   * 把一个历史会话 (从 sessions_get 拿到的 SessionDetail) 灌进 store, 用于 resume。
   * - 把 DB 里的 SessionMessage[] 映射成 ChatMessage[]
   * - 设 persistedSessionId 让后续 send 顺着同一个 session 续写
   * - reset streaming 状态 (历史一定不在 streaming 中)
   * - model 也跟随 session 的 model (但不强制, 调用方可以再 setModel)
   */
  loadSession: (detail: SessionDetail) => void;
  reset: () => void;
}

/** SessionMessage (DB 行) -> ChatMessage (UI 运行时) 映射。
 *  - tool_calls JSON 反序列化成 ToolCall[], status 一律 "done" (历史已完成)
 *  - 不解析 tool 角色消息的 result 字段, 直接当 content 显示
 */
function dbMessageToChat(m: SessionMessage): ChatMessage {
  let toolCalls: ToolCall[] | undefined;
  if (m.toolCalls) {
    try {
      const parsed = JSON.parse(m.toolCalls);
      if (Array.isArray(parsed)) {
        toolCalls = parsed.map((tc, i) => ({
          id: String(tc.id ?? `historical-${m.id}-${i}`),
          name: String(tc.function?.name ?? tc.name ?? "(unknown)"),
          args: safeParseArgs(tc.function?.arguments ?? tc.arguments),
          status: "done" as const,
        }));
      }
    } catch {
      // 解析坏了不致命, 历史记录里有畸形 tool_calls 就忽略
    }
  }
  // ChatRole 是 union, 兜底成 "assistant" 避免 string 不被接受
  const role = (["user", "assistant", "system", "tool"] as const).includes(
    m.role as never,
  )
    ? (m.role as ChatMessage["role"])
    : "assistant";
  return {
    id: `db-${m.id}`,
    role,
    content: m.content,
    tool_calls: toolCalls,
    tool_call_id: m.toolCallId,
    ts: m.timestamp,
    status: "done",
  };
}

function safeParseArgs(raw: unknown): Record<string, unknown> {
  if (raw == null) return {};
  if (typeof raw === "object") return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try {
      const v = JSON.parse(raw);
      return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }
  return {};
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isStreaming: false,
  streamingId: null,
  model: "catfish-private-main",
  persistedSessionId: null,
  lastPromptTokens: null,

  setMessages: (messages) => set({ messages }),
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
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
  setLastPromptTokens: (lastPromptTokens) =>
    set({ lastPromptTokens }),
  loadSession: (detail) =>
    set({
      messages: detail.messages.map(dbMessageToChat),
      isStreaming: false,
      streamingId: null,
      model: detail.meta.model,
      persistedSessionId: detail.meta.id,
    }),
  reset: () =>
    set({
      messages: [],
      isStreaming: false,
      streamingId: null,
      persistedSessionId: null,
      lastPromptTokens: null,  // BL-CONTEXT-COUNTER: 切会话清零
    }),
}));
