/** P3.3.10 (6/10 鸿波): per-task chat hook for BriefingTab DetailPane.
 *
 * 跟 useChat (工作台 hook) 不同点:
 * - **本地 state** (useState), 不动 useChatStore — 避免跟工作台 chat 串
 * - **不跑** autoContinue / steer / vision switch / state.db 持久化
 * - **接** tool calling 循环 (streamChat onToolCalls → toolBridgeCallTool →
 *   再 streamChat 直到 finish_reason 非 tool_calls 或 MAX_ROUNDS)
 * - approval event (catfish:approval-pending / approval-send) 由
 *   chat.ts streamChat 内部自动 dispatch / DetailPane 自己监听
 * - 持久化交给 caller (DetailPane 写 task_chat jsonl, P3.3.7 Phase 2 既有逻辑)
 *
 * Tool 循环跟 useChat.runOneRound 用同款 logic, 只是 state 是 local.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { streamChat } from "../lib/chat";
import { ensureTools } from "./chat/toolsCache";
import { toolBridgeCallTool } from "../lib/tauri";
import type { ChatMessage, ToolCall } from "../types/chat";

/** 跟 useChat 同款上限. 跨 skill 一次最多 20 轮 (5/13 鸿波拍). */
const MAX_TOOL_ROUNDS = 20;

/** P3.3.14 (6/10): 单次 send 最多带 N 条历史 message 进 LLM history.
 *  audit (#2): 没截窗时 100 轮 chat ≈ 16K token / 次 send, 重复发整个历史浪费.
 *  40 条 ≈ 20 轮对话 (user/assistant 配对), 够 LLM 拿上下文, 又控 token. */
const HISTORY_WINDOW = 40;

function uuid(): string {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

export interface UseTaskChatOpts {
  model: string;
  /** system prompt 注入. 每次 send 都重算 (task 上下文可能变). */
  buildSystemPrompt: () => string;
  /** P3.3.11: 持久化回调, 接完整 ChatMessage. caller 把它写 task_chat jsonl.
   *  user / assistant (含 tool_calls) / tool 各类都会 emit. system 不 emit. */
  onPersist?: (msg: ChatMessage) => void;
}

export interface UseTaskChatReturn {
  messages: ChatMessage[];
  isStreaming: boolean;
  /** 直接 set 一组消息 (mount 时 load jsonl 历史用). */
  loadHistory: (msgs: ChatMessage[]) => void;
  send: (text: string) => Promise<void>;
  cancel: () => void;
  reset: () => void;
}

export function useTaskChat(opts: UseTaskChatOpts): UseTaskChatReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // P3.3.13 (6/10): mountedRef 防 unmount 后 setState ghost write.
  //   audit 报告 (#5): 切 task 时 DetailPane unmount + remount, 老 useTaskChat
  //   abortRef 引用丢, fetch closure 持有 signal 仍跑, onError / onDone 仍
  //   触发 setMessages — React 18+ 是 no-op 但 onPersist 仍会写到 jsonl 老 key,
  //   切回新 task 看到诡异"消息凭空出现". fix: unmount cleanup 立即 abort,
  //   且 mountedRef 拦 setMessages 跟 onPersist.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // 切 task 立即放弃 in-flight stream. ctrl.abort() → streamChat 读 signal
      // 触发 AbortError → onError 跑但 mountedRef.current=false → 跳过 persist.
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  // opts 用 ref, 闭包稳定不重建 send (model / buildSystemPrompt 都可能每次 render 变)
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const loadHistory = useCallback((msgs: ChatMessage[]) => {
    setMessages(msgs);
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setIsStreaming(false);
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    // 跟 useChat 同款保护: 流中再发会撞, caller 应 disable 输入框
    if (abortRef.current && !abortRef.current.signal.aborted) {
      console.warn("[useTaskChat] 流进行中, 跳过 send");
      return;
    }

    // 1. append user msg + persist
    const userMsg: ChatMessage = {
      id: uuid(),
      role: "user",
      content: trimmed,
      ts: nowIso(),
      status: "done",
    };
    let curMessages: ChatMessage[] = [];
    setMessages((prev) => {
      curMessages = [...prev, userMsg];
      return curMessages;
    });
    // P3.3.13: user msg 一定是 mounted 时刚发的, 不用判. 后面 assistant /
    //   tool persist 必须判 mountedRef (unmount 后 stream onError/onDone 仍可能
    //   触发, 不能写 ghost row 到 jsonl).
    optsRef.current.onPersist?.(userMsg);
    setIsStreaming(true);

    // 2. 拉 tools (跟工作台同款 — 60s TTL cache, tool_bridge 不可达返 [])
    const tools = await ensureTools();

    // 3. 构建 LLM history: system + 所有可见消息 (含 tool 历史轮的 assistant + tool msg)
    const sysMsg: ChatMessage = {
      id: "task-sys",
      role: "system",
      content: optsRef.current.buildSystemPrompt(),
      ts: nowIso(),
      status: "done",
    };
    // P3.3.14: 截最近 HISTORY_WINDOW 条进 LLM history. 老历史仍在 jsonl, UI
    //   也仍显, 只是不发给 LLM (advisor task chat summary 已 capture 老脉络).
    const windowedMessages = curMessages.slice(-HISTORY_WINDOW);
    let history: ChatMessage[] = [sysMsg, ...windowedMessages];

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
        // 新一轮的 assistant 消息
        const assistantId = uuid();
        const assistantMsg: ChatMessage = {
          id: assistantId,
          role: "assistant",
          content: "",
          ts: nowIso(),
          status: "streaming",
        };
        setMessages((prev) => [...prev, assistantMsg]);

        const callsRef: { calls: ToolCall[] } = { calls: [] };
        let finalContent = "";
        let streamErr: string | null = null;

        await streamChat({
          model: optsRef.current.model,
          messages: history,
          tools,
          onDelta: (chunk) => {
            finalContent += chunk;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + chunk }
                  : m,
              ),
            );
          },
          onToolCalls: (calls) => {
            callsRef.calls = calls;
          },
          onDone: () => {
            // approval-pending event 由 chat.ts 在解 SSE 时自动 dispatch,
            // DetailPane 自己 listen 弹 banner. 这里 nothing-to-do.
          },
          onError: (err) => {
            streamErr = err;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, status: "error", error: err }
                  : m,
              ),
            );
          },
          signal: ctrl.signal,
        });

        if (streamErr) {
          // 错误就停, 不再进 tool 循环
          break;
        }

        const collectedCalls = callsRef.calls;
        const doneAssistant: ChatMessage = {
          ...assistantMsg,
          content: finalContent,
          tool_calls: collectedCalls.length > 0 ? collectedCalls : undefined,
          status: "done",
        };
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? doneAssistant : m)),
        );

        // P3.3.11: 持久化完整 assistant 消息 (含 tool_calls).
        //   tool_calls 此刻还是 pending/running (tool 还没真跑), result 字段缺.
        //   后面 tool 跑完会再 persist 一条完整 assistant 覆盖 (append-only 写新一行,
        //   load 时按 id 取最后一行).
        // P3.3.13: mounted 时才 persist — unmount 后 stream onDone 可能仍触发,
        //   不能写 ghost row 到老 task 的 jsonl.
        if (
          mountedRef.current &&
          (finalContent.trim().length > 0 || collectedCalls.length > 0)
        ) {
          optsRef.current.onPersist?.(doneAssistant);
        }
        history = [...history, doneAssistant];

        // 没 tool_calls → 收尾
        if (collectedCalls.length === 0) break;

        // 跑 tool (跟 useChat runOneRound 同款串行)
        for (const tc of collectedCalls) {
          // mark running (assistant 卡片里那条 tool_calls[i] 进度)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    tool_calls: (m.tool_calls ?? []).map((c) =>
                      c.id === tc.id ? { ...c, status: "running" } : c,
                    ),
                  }
                : m,
            ),
          );

          let resultStr = "";
          let ok = false;
          try {
            const res = await toolBridgeCallTool(
              tc.name,
              tc.args as Record<string, unknown>,
            );
            ok = res.ok;
            resultStr =
              typeof res.result === "string"
                ? res.result
                : JSON.stringify(res.result);
          } catch (e) {
            ok = false;
            resultStr = String(e);
          }

          // update tool_call.result + status
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    tool_calls: (m.tool_calls ?? []).map((c) =>
                      c.id === tc.id
                        ? {
                            ...c,
                            status: ok ? "done" : "error",
                            result: resultStr,
                          }
                        : c,
                    ),
                  }
                : m,
            ),
          );

          // 拼 tool message 到 history (下一轮 LLM 能看到 result)
          const toolMsg: ChatMessage = {
            id: uuid(),
            role: "tool",
            content: resultStr,
            tool_call_id: tc.id,
            ts: nowIso(),
            status: "done",
          };
          history = [...history, toolMsg];

          // P3.3.11: tool result 也 persist — load 时 DetailPane 把 result join
          //   回 assistant.tool_calls[i].result (跟工作台 state.db load 同款思路).
          // P3.3.13: mounted 时才 persist (防 ghost write — 切走 task 后 stream
          //   还可能跑完一个 tool, 不能写到老 task 的 jsonl).
          if (mountedRef.current) {
            optsRef.current.onPersist?.(toolMsg);
          }
        }
      }
    } catch (e) {
      console.warn("[useTaskChat] send 异常:", e);
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, []);

  return { messages, isStreaming, loadHistory, send, cancel, reset };
}
