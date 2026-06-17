/** Chat 状态 store —— 提到 Zustand 是为了**跨 tab 切换不丢消息**。
 *
 * 之前 useChat 用 useState 放在 ChatTab 组件里,React unmount 时 state 销毁,
 * 用户切到"控制台"再切回"对话"看不到之前的对话。
 *
 * 现在 store 是单例,跨组件 mount/unmount 持久。
 * Week 3 持久化 (state.db 持久 + sidebar 切换 + resume 历史) 钩这个 store。
 */

import { invoke } from "@tauri-apps/api/core";
import { create } from "zustand";
import type { ChatMessage } from "../types/chat";
import type { SessionDetail } from "../types/session";
// P3.3.19 C Phase 2b (6/11): db→chat 转换 + tool result join 抽到 lib/sessionMessages
import {
  loadSessionMessagesAsChat,
  loadSessionMessagesAsChatAsync,
} from "../lib/sessionMessages";

/** BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): session 级附件 row.
 *  对齐 Rust commands/attachments.rs AttachmentRow.
 *  切回老会话时调 attachment_list_by_session 拿到这份 list, 显示在 ChatTab
 *  顶部 ("📎 本会话历史附件: ..."), LLM 也能通过 catfish_search_attachments 查到. */
export interface SessionAttachment {
  id: string;
  userId: string;
  sessionId: string;
  messageId: string;
  kind: string;            // "image" | "file" | "audio"
  fileKind: string | null;  // "pdf" | "xlsx" | ...
  name: string;
  mimeType: string | null;
  sizeBytes: number | null;
  keptPath: string | null;
  parsedTextPath: string | null;
  meta: string | null;     // JSON string
  createdAt: number;
}

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
  /** BL-GATEWAY-SOFT-HANDOFF (5/18): 上轮请求用的 model 名. 切 model 后下一轮请求
   *  会带 X-Catfish-Prev-Model header, gateway 据此做 tool-history 兼容转译.
   *  null = 本 session 还没发过任何请求, 或者 reset 过. setModel 不动这个,
   *  发送时同步: send 前快照 model 进 header, send 完更新 prevSentModel = model. */
  prevSentModel: string | null;
  /** BL-HERMES013-RED-1A (5/13 借鉴 Hermes 0.13 ACP /queue): streaming 中
   *  用户排队的下一条消息. 当前 stream [DONE] → useChat 自动从 queue 取第一条
   *  send. 跟 BL-COMPANION-UX1 ⏹ 停下接着发 互补 (一个停一个排队).
   *  attachments 暂不支持 (内存) — 排队消息只能纯文字. */
  queue: Array<{ id: string; text: string; ts: string }>;
  /** BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): 当前 session 的所有历史附件 list.
   *  loadSession 后由 loadSessionAttachments 异步拉. ChatTab UI 顶部显示
   *  ("📎 本会话历史附件: a.pdf, b.xlsx"). LLM 也能通过 catfish_search_attachments
   *  查到. 切 session 时 reset 清零. */
  sessionAttachments: SessionAttachment[];

  // ── actions ──
  setMessages: (msgs: ChatMessage[]) => void;
  addMessage: (msg: ChatMessage) => void;
  updateMessage: (id: string, update: Partial<ChatMessage>) => void;
  appendToMessage: (id: string, delta: string) => void;
  /** BL-TASK-ASSESS-3-UI (5/15): 点"催它继续"按钮时计数器++. 3 次用完后按钮变灰. */
  incrementPromiseNudge: (id: string) => void;
  setIsStreaming: (v: boolean) => void;
  setStreamingId: (id: string | null) => void;
  setModel: (m: string) => void;
  setPersistedSessionId: (id: string | null) => void;
  /** BL-GATEWAY-SOFT-HANDOFF (5/18): 标记一次 send 已用过当前 model, 下次发送
   *  时如果 model 变了, X-Catfish-Prev-Model header 就带上这个旧值. */
  markModelSent: () => void;
  /** BL-HERMES013-RED-1A: queue 操作 */
  enqueueMessage: (text: string) => void;
  dequeueMessage: () => { id: string; text: string; ts: string } | undefined;
  removeQueuedMessage: (id: string) => void;
  clearQueue: () => void;
  /**
   * 把一个历史会话 (从 sessions_get 拿到的 SessionDetail) 灌进 store, 用于 resume。
   * - 把 DB 里的 SessionMessage[] 映射成 ChatMessage[]
   * - 设 persistedSessionId 让后续 send 顺着同一个 session 续写
   * - reset streaming 状态 (历史一定不在 streaming 中)
   * - model **不动**: picker 选的就是当前要用的, 切会话不应该被会话历史的 model
   *   覆盖。 prevSentModel 仍然记录 session 上次发送时的 model, 这样 gateway
   *   还能按"旧 model → picker 新 model"做 soft handoff 的 tool-history 转译。
   */
  loadSession: (detail: SessionDetail) => void;
  /** BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): loadSession 后异步拉附件 list.
   *  失败仅 console.warn (chat 仍 work). */
  loadSessionAttachments: (sessionId: string) => Promise<void>;
  /** P3.5.8 BL-FILE-SESSION-INDEX-V1 Phase 2 (6/16 鸿波): loadSession 后异步还原
   *  历史 image attachments base64, 让 toWire 走 multipart 带图给 LLM. 同步 set
   *  messages 后立刻 fire-and-forget. 失败仅 console.warn, chat 仍 work (只是没图). */
  restoreImageAttachments: (detail: SessionDetail) => Promise<void>;
  reset: () => void;
}

// P3.3.19 C Phase 2b (6/11): dbMessageToChat + safeParseArgs + tool result join
// 抽到 lib/sessionMessages.ts (单独 file 避 cyclic — lib/chat.ts 已 import
// store/chat.ts, 反向 import 会 cyclic). useTaskChat 共用同一 helper.

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isStreaming: false,
  streamingId: null,
  model: "catfish-private-main",
  persistedSessionId: null,
  prevSentModel: null,
  queue: [],
  sessionAttachments: [],

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
  incrementPromiseNudge: (id) =>
    set((s) => ({
      messages: s.messages.map((m) => {
        if (m.id !== id || !m._promise_check) return m;
        return {
          ...m,
          _promise_check: {
            ...m._promise_check,
            nudge_count: m._promise_check.nudge_count + 1,
          },
        };
      }),
    })),
  setIsStreaming: (v) => set({ isStreaming: v }),
  setStreamingId: (id) => set({ streamingId: id }),
  setModel: (model) => {
    set({ model });
    // P3.5.28 (6/17 鸿波"picker 联动现在就应该做"): 真**桥**给 Rust background task
    // (email_scheduler / phishing_scan). 写文件 ~/.catfish/picker_model 让 background
    // task 真 tick 时读. 员工 chat picker 切换真**下次 tick 生效**.
    //
    // Fire-and-forget — 不 throw, 不阻塞 picker UI. 失败仅 console.warn.
    invoke("set_picker_model", { name: model }).catch((e: unknown) => {
      console.warn("[P3.5.28 picker_model bridge] 写文件失败:", e);
    });
  },
  setPersistedSessionId: (persistedSessionId) =>
    set({ persistedSessionId }),
  markModelSent: () => set((s) => ({ prevSentModel: s.model })),
  enqueueMessage: (text) =>
    set((s) => ({
      queue: [
        ...s.queue,
        {
          id: crypto.randomUUID
            ? crypto.randomUUID()
            : `q-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          text,
          ts: new Date().toISOString(),
        },
      ],
    })),
  dequeueMessage: () => {
    let head: { id: string; text: string; ts: string } | undefined;
    set((s) => {
      if (s.queue.length === 0) return s;
      head = s.queue[0];
      return { queue: s.queue.slice(1) };
    });
    return head;
  },
  removeQueuedMessage: (id) =>
    set((s) => ({ queue: s.queue.filter((q) => q.id !== id) })),
  clearQueue: () => set({ queue: [] }),
  loadSession: (detail) =>
    set({
      // P27.3 真根因 fix (6/5 鸿波 audit 后): dbMessageToChat 1-to-1 map 丢了
      // tool_calls[i].result. state.db 里 tool result 存在另一行 role:"tool" +
      // tool_call_id 的 message content. 历史加载时必须 join 回 tool_calls[i].result,
      // 否则 ChatToolCall 渲染 call.result===undefined → 显示 (空), P27 approval
      // button regex 也 test 空字符串不 match → 永远不弹. 这是为什么 marathon
      // 25h+ 一直 debug "button 不弹" — 真根因不是 UI render 路径, 是历史加载丢字段.
      // P3.3.19 C Phase 2b (6/11): 整段 logic 抽到 lib/sessionMessages.ts
      // loadSessionMessagesAsChat. useTaskChat 共用. 行为不变.
      messages: loadSessionMessagesAsChat(detail),
      isStreaming: false,
      streamingId: null,
      // BL-FILE-SESSION-INDEX-V1 Phase 1: 切会话先清空附件 list, 等
      // loadSessionAttachments 异步填充. 防上个会话的附件残留显错.
      sessionAttachments: [],
      // BL-GLOBAL-MODEL (5/23): 切会话**不动 model**. picker 是全局选择,
      // 选了 deepseek 就一直用 deepseek, 不要因为历史会话最后一次用的是 gemini
      // 就把 picker 拽回 gemini. 用户反复反馈这个行为反直觉.
      persistedSessionId: detail.meta.id,
      // BL-GATEWAY-SOFT-HANDOFF (5/18): 历史 session resume 时, 把 session.model 当
      // 上次 send 的 model (员工切到别的 model 再发, 才算 handoff). 历史已有 tool_calls
      // 也按这个走 — gateway 会按 prev 对比新 model 决定是否转译.
      // 注: 即使现在不覆盖 model, prevSentModel 仍然必须按 session 自身的 model 走 —
      // 这样 picker 当前 model ≠ session 上次 model 时, gateway 才知道要做转译.
      prevSentModel: detail.meta.model,
    }),
  loadSessionAttachments: async (sessionId: string) => {
    if (!sessionId) return;
    try {
      const rows = await invoke<SessionAttachment[]>(
        "attachment_list_by_session",
        { sessionId },
      );
      // 防 race: 异步 invoke 期间用户可能已切走 session.
      // 拿到结果时如果 persistedSessionId 已变, 不 set (不污染新会话).
      set((s) => {
        if (s.persistedSessionId !== sessionId) return s;
        return { sessionAttachments: rows };
      });
    } catch (e) {
      console.warn("[BL-FILE-SESSION-INDEX-V1] loadSessionAttachments 失败:", e);
    }
  },
  restoreImageAttachments: async (detail: SessionDetail) => {
    const sessionId = detail.meta.id;
    if (!sessionId) return;
    try {
      // loadSessionMessagesAsChatAsync 内部 query attachments + 读 base64 + 填回
      // message.attachments. 整 messages 数组返新引用 (老 messages 不动).
      const restored = await loadSessionMessagesAsChatAsync(detail);
      // 防 race: invoke 期间用户可能已切走 session, 拿到结果时校验 persistedSessionId.
      set((s) => {
        if (s.persistedSessionId !== sessionId) return s;
        // 也防 race: streaming 中可能新 message 已 push, restored 是切前的快照
        // 不能直接覆盖. 但 restore 通常在切会话刚发生时跑, 那时 isStreaming=false,
        // 这种 race 极少. 简单起见: 直接覆盖. 复杂场景由下次 loadSession 兜底.
        return { messages: restored };
      });
    } catch (e) {
      console.warn(
        "[BL-FILE-SESSION-INDEX-V1 Phase 2] restoreImageAttachments 失败:",
        e,
      );
    }
  },
  reset: () =>
    set({
      messages: [],
      isStreaming: false,
      streamingId: null,
      persistedSessionId: null,
      prevSentModel: null,     // BL-GATEWAY-SOFT-HANDOFF: 新 session 没"上次"
      queue: [],  // BL-HERMES013-RED-1A: 切会话清队列
      sessionAttachments: [],  // BL-FILE-SESSION-INDEX-V1 Phase 1
    }),
}));
