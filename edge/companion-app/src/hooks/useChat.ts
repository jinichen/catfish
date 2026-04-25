/** 聊天动作 —— send / cancel / reset, 含 tool calling 循环。
 *
 * Tool calling 流程:
 *   1. send 时如果 toolBridge 在跑, 拉一次 tools list 并 cache
 *   2. 第一轮 streamChat 带 tools 参数
 *   3. LLM 流式响应 — onDelta 累 content, onToolCalls 收尾时给一组 ToolCall
 *   4. 如果 onToolCalls 触发 —— 串行调 tool_bridge_call_tool 执行每个 tool
 *      期间 UI 显示 "🔧 调用 read_file..." 这种 inline 进度
 *   5. 把 [assistant tool_calls + 各 tool 结果] append 到 messages
 *   6. 递归 streamChat → 下一轮 LLM 输出, 可能继续调 tool 或纯文字结束
 *   7. 直到 finish_reason !== "tool_calls" 或者达到 MAX_ROUNDS
 */

import { useCallback, useRef } from "react";
import { useChatStore } from "../store/chat";
import { streamChat, type OpenAITool } from "../lib/chat";
import {
  toolBridgeListTools,
  toolBridgeCallTool,
  sessionCreate,
  sessionMessageAppend,
  sessionFinalize,
} from "../lib/tauri";
import type { ChatMessage, ToolCall } from "../types/chat";

const MAX_TOOL_ROUNDS = 10;

function uuid(): string {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

// ─── tools 列表缓存 ───────────────────────────────────
// 成功一次后用 cache 避免每次 send 都查 tool_bridge。
// 失败时**不缓存**,下次 send 会重试(tool_bridge 可能晚启动 / 中途重启)。
let _cachedTools: OpenAITool[] | null = null;

async function ensureTools(): Promise<OpenAITool[]> {
  if (_cachedTools !== null) return _cachedTools;
  try {
    const list = await toolBridgeListTools();
    const usable = list.filter((t) => t.available);
    const wire: OpenAITool[] = usable.map((t) => ({
      type: "function" as const,
      // hermes 的 input_schema 已经是 {name, description, parameters} 结构,
      // 直接当 OpenAI tool.function 用
      function: t.input_schema as unknown as OpenAITool["function"],
    }));
    _cachedTools = wire;
    console.info(
      `[catfish chat] 加载 ${wire.length}/${list.length} 个工具(${list.length - wire.length} 个不可用 toolset)`,
    );
    return wire;
  } catch (e) {
    // tool_bridge 没启动 / unix socket 不通 - 走纯文字模式,**不缓存**让下次 send 重试
    console.warn("[catfish chat] tool_bridge 不可达, 跳过 tools(下次重试):", e);
    return [];
  }
}

/** 切账号 / 重启 tool_bridge / 装新 skill 后调一次清缓存让 ensureTools 重拉 */
export function _clearToolsCache(): void {
  _cachedTools = null;
}

// ─── 主 hook ───────────────────────────────────

export function useChat(initialModel: string) {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const streamingId = useChatStore((s) => s.streamingId);
  const model = useChatStore((s) => s.model);
  const addMessage = useChatStore((s) => s.addMessage);
  const updateMessage = useChatStore((s) => s.updateMessage);
  const appendToMessage = useChatStore((s) => s.appendToMessage);
  const setIsStreaming = useChatStore((s) => s.setIsStreaming);
  const setStreamingId = useChatStore((s) => s.setStreamingId);
  const setModelInStore = useChatStore((s) => s.setModel);
  const setPersistedSessionId = useChatStore((s) => s.setPersistedSessionId);
  const resetStore = useChatStore((s) => s.reset);

  /** 持久化辅助:幂等地拿 / 创建 state.db 里的 session id, 失败不阻塞主流程。*/
  const ensureSessionId = useCallback(async (): Promise<string | null> => {
    const cur = useChatStore.getState().persistedSessionId;
    if (cur) return cur;
    try {
      const out = await sessionCreate({
        model: useChatStore.getState().model,
      });
      setPersistedSessionId(out.id);
      console.info(`[catfish chat] persisted session ${out.id} (source=companion)`);
      return out.id;
    } catch (e) {
      console.warn("[catfish chat] session_create 失败,会话不持久化:", e);
      return null;
    }
  }, [setPersistedSessionId]);

  /** append 一条消息到 state.db, 失败静默(不能影响 UI 流). */
  const persistMessage = useCallback(
    async (msg: ChatMessage): Promise<void> => {
      const sessionId = useChatStore.getState().persistedSessionId;
      if (!sessionId) return;
      try {
        await sessionMessageAppend({
          sessionId,
          role: msg.role,
          content: msg.content,
          toolCalls: msg.tool_calls
            ? JSON.stringify(
                msg.tool_calls.map((tc) => ({
                  id: tc.id,
                  type: "function",
                  function: {
                    name: tc.name,
                    arguments: JSON.stringify(tc.args ?? {}),
                  },
                })),
              )
            : undefined,
          toolCallId: msg.tool_call_id,
          finishReason: msg.status === "error" ? "error" : undefined,
        });
      } catch (e) {
        console.warn("[catfish chat] session_message_append 失败:", e);
      }
    },
    [],
  );

  // 首次进入,如果 store 里 model 还是默认值,用 caller 传的 initialModel
  if (
    model === "catfish-private-main" &&
    initialModel &&
    initialModel !== model
  ) {
    setModelInStore(initialModel);
  }

  const pendingDeltaRef = useRef("");
  const rafRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const currentStreamIdRef = useRef<string | null>(null);

  const flushPending = useCallback(() => {
    const delta = pendingDeltaRef.current;
    pendingDeltaRef.current = "";
    rafRef.current = null;
    if (!delta || !currentStreamIdRef.current) return;
    appendToMessage(currentStreamIdRef.current, delta);
  }, [appendToMessage]);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current !== null) return;
    rafRef.current = requestAnimationFrame(flushPending);
  }, [flushPending]);

  /** 单轮 streamChat,返回是否需要继续(tool_calls finish_reason)。
   *  把消息历史作为参数传入 (而不是依赖 store), 因为 React state 异步,
   *  连续递归时拿到的是旧 snapshot。 */
  const runOneRound = useCallback(
    async (
      ctx: {
        ctrl: AbortController;
        roundIdx: number;
        currentMessages: ChatMessage[];
        tools: OpenAITool[];
      },
    ): Promise<{
      shouldContinue: boolean;
      updatedMessages: ChatMessage[];
    }> => {
      // 创建新一轮的 assistant 消息
      const assistantId = uuid();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        ts: nowIso(),
        status: "streaming",
      };
      currentStreamIdRef.current = assistantId;
      addMessage(assistantMsg);
      setStreamingId(assistantId);

      // 用对象包裹规避 TS 的 flow narrowing —— `let x: T[] | null = null`
      // 在 await 之后会被错误收窄成 never,即使 callback 里改了 x。
      const refs: { calls: ToolCall[] } = { calls: [] };

      await streamChat({
        model,
        messages: ctx.currentMessages,
        tools: ctx.tools,
        signal: ctx.ctrl.signal,
        onDelta: (text) => {
          pendingDeltaRef.current += text;
          scheduleFlush();
        },
        onToolCalls: (calls) => {
          refs.calls = calls;
        },
        onDone: () => {
          if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
          }
          flushPending();
        },
        onError: (err) => {
          if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
          }
          flushPending();
          if (currentStreamIdRef.current) {
            updateMessage(currentStreamIdRef.current, {
              status: "error",
              error: err,
            });
          }
        },
      });

      const collectedToolCalls = refs.calls;

      // 把 tool_calls 挂到当前 assistant 消息上
      const finalAssistant: ChatMessage = {
        ...assistantMsg,
        content:
          useChatStore.getState().messages.find((m) => m.id === assistantId)
            ?.content || "",
        tool_calls:
          collectedToolCalls.length > 0 ? collectedToolCalls : undefined,
        status: "done",
      };
      if (collectedToolCalls.length > 0) {
        updateMessage(assistantId, {
          status: "done",
          tool_calls: collectedToolCalls,
        });
      } else {
        updateMessage(assistantId, { status: "done" });
      }
      // 持久化 assistant 消息(完整 content + tool_calls)
      void persistMessage(finalAssistant);

      // 重新拼当前 messages snapshot(给下一轮用)
      const updatedMessages: ChatMessage[] = [
        ...ctx.currentMessages,
        {
          ...assistantMsg,
          content:
            useChatStore.getState().messages.find((m) => m.id === assistantId)
              ?.content || "",
          tool_calls:
            collectedToolCalls.length > 0 ? collectedToolCalls : undefined,
          status: "done" as const,
        },
      ];

      // 没 tool_calls 就到此为止
      if (collectedToolCalls.length === 0) {
        return { shouldContinue: false, updatedMessages };
      }

      // 有 tool_calls → 串行执行每个,产生 tool 角色消息
      for (const tc of collectedToolCalls) {
        // UI: 标记 running
        const updatedCalls = (
          useChatStore.getState().messages.find((m) => m.id === assistantId)
            ?.tool_calls ?? []
        ).map((c) => (c.id === tc.id ? { ...c, status: "running" as const } : c));
        updateMessage(assistantId, { tool_calls: updatedCalls });

        let resultStr = "";
        let ok = false;
        let errMsg: string | undefined;
        try {
          const res = await toolBridgeCallTool(
            tc.name,
            tc.args as Record<string, unknown>,
          );
          ok = res.ok;
          if (res.ok) {
            resultStr =
              typeof res.result === "string"
                ? res.result
                : JSON.stringify(res.result);
          } else {
            errMsg = res.error ?? "tool 调用失败";
            resultStr = JSON.stringify({ error: errMsg });
          }
        } catch (e) {
          errMsg = String(e);
          resultStr = JSON.stringify({ error: errMsg });
        }

        // UI: 把这个 ToolCall 的结果填上,标 done/error
        const updatedCalls2 = (
          useChatStore.getState().messages.find((m) => m.id === assistantId)
            ?.tool_calls ?? []
        ).map((c) =>
          c.id === tc.id
            ? {
                ...c,
                status: ok ? ("done" as const) : ("error" as const),
                result: resultStr,
                error: errMsg,
              }
            : c,
        );
        updateMessage(assistantId, { tool_calls: updatedCalls2 });

        // append 一条 tool 角色消息(关联回 tool_call_id)给下一轮 LLM
        const toolMsg: ChatMessage = {
          id: uuid(),
          role: "tool",
          content: resultStr,
          tool_call_id: tc.id,
          ts: nowIso(),
          status: "done",
        };
        addMessage(toolMsg);
        updatedMessages.push(toolMsg);
        // 持久化 tool 角色消息
        void persistMessage(toolMsg);
      }

      // tool_calls 处理完 → 必继续下一轮 LLM 推理(让 LLM 看 tool 结果)
      return { shouldContinue: true, updatedMessages };
    },
    [
      model,
      addMessage,
      updateMessage,
      setStreamingId,
      scheduleFlush,
      flushPending,
      persistMessage,
    ],
  );

  const send = useCallback(
    async (content: string) => {
      if (!content.trim() || isStreaming) return;

      // 0. 第一次 send 时 lazy create state.db session (持久化的开端)
      await ensureSessionId();

      // 1. push user message
      const userMsg: ChatMessage = {
        id: uuid(),
        role: "user",
        content: content.trim(),
        ts: nowIso(),
        status: "done",
      };
      const requestMessages = [...messages, userMsg];
      addMessage(userMsg);
      void persistMessage(userMsg);
      setIsStreaming(true);

      // 2. 拉 tools(第一次会调 tool_bridge,后续走 cache)
      const tools = await ensureTools();

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      try {
        let currentMessages = requestMessages;
        for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
          if (ctrl.signal.aborted) break;

          const result = await runOneRound({
            ctrl,
            roundIdx: round,
            currentMessages,
            tools,
          });
          currentMessages = result.updatedMessages;
          if (!result.shouldContinue) break;

          if (round === MAX_TOOL_ROUNDS - 1) {
            // 最后一轮还想继续,告诉用户达到上限
            const limitMsg: ChatMessage = {
              id: uuid(),
              role: "assistant",
              content: `\n\n⚠ 工具调用达到 ${MAX_TOOL_ROUNDS} 轮上限,本轮停止。重新提问可以继续。`,
              ts: nowIso(),
              status: "done",
            };
            addMessage(limitMsg);
          }
        }
      } finally {
        currentStreamIdRef.current = null;
        setStreamingId(null);
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [
      isStreaming,
      messages,
      addMessage,
      setIsStreaming,
      setStreamingId,
      runOneRound,
      ensureSessionId,
      persistMessage,
    ],
  );

  const cancel = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
  }, []);

  const reset = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    pendingDeltaRef.current = "";
    rafRef.current = null;
    currentStreamIdRef.current = null;
    abortRef.current = null;

    // 关闭当前持久化的 session (写 ended_at)
    const oldSessionId = useChatStore.getState().persistedSessionId;
    if (oldSessionId) {
      void sessionFinalize({
        sessionId: oldSessionId,
        endReason: "companion_new_chat",
      }).catch((e) => {
        console.warn("[catfish chat] session_finalize 失败:", e);
      });
    }

    resetStore();
  }, [resetStore]);

  return {
    messages,
    isStreaming,
    streamingId,
    model,
    setModel: setModelInStore,
    send,
    cancel,
    reset,
  };
}
