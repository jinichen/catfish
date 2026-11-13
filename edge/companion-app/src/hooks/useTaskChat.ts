/** P3.3.10 (6/10 鸿波): per-task chat hook for BriefingTab DetailPane.
 *
 * 跟 useChat (工作台 hook) 不同点:
 * - 状态不放 useChatStore (避免跟工作台 chat 串), 放在本文件的 runtime 表里, 按
 *   任务 key 一份 —— **不跟着组件走**。11/13 之前是组件里的 useState, 而且卸载
 *   时主动 abort: 员工发完一句切到别的待办 / 别的 tab, 这一轮就被掐断, 回来只剩
 *   自己那句话 (鸿波: 「切到其他TAB或任务，这个任务就断了」)。现在切走后这一轮
 *   照常跑完、照常落库, 切回来接着看; 只有点「停止」才会停。
 * - **不跑** autoContinue / steer / vision switch / state.db 持久化
 * - **接** tool calling 循环 (streamChat onToolCalls → toolBridgeCallTool →
 *   再 streamChat 直到 finish_reason 非 tool_calls 或 MAX_ROUNDS)
 * - approval event (catfish:approval-pending / approval-send) 由
 *   chat.ts streamChat 内部自动 dispatch / DetailPane 自己监听
 * - 持久化交给 caller (DetailPane 写 task_chat jsonl, P3.3.7 Phase 2 既有逻辑)
 *
 * Tool 循环跟 useChat.runOneRound 用同款 logic, 只是 state 是 local.
 */

import { useCallback, useRef, useSyncExternalStore } from "react";
import { streamChat } from "../lib/chat";
import { ensureTools } from "./chat/toolsCache";
import { toolBridgeCallTool, sessionMessageAppend } from "../lib/tauri";
import { detectToolBusinessError } from "../lib/toolResult";
import type { Attachment, ChatMessage, ToolCall } from "../types/chat";

/** 跟 useChat 同款上限. 跨 skill 一次最多 20 轮 (5/13 鸿波拍). */
const MAX_TOOL_ROUNDS = 20;
const TASK_CHAT_TOOLS_TIMEOUT_MS = 60_000;
const TASK_CHAT_TOOL_TIMEOUT_MS = 60_000;

/** P3.3.14 (6/10): 单次 send 最多带 N 条历史 message 进 LLM history.
 *  audit (#2): 没截窗时 100 轮 chat ≈ 16K token / 次 send, 重复发整个历史浪费.
 *  40 条 ≈ 20 轮对话 (user/assistant 配对), 够 LLM 拿上下文, 又控 token. */
const HISTORY_WINDOW = 40;

export type TaskChatPhase =
  | "preparing"
  | "loading_tools"
  | "waiting_model"
  | "generating"
  | "running_tool"
  | "error"
  | "cancelled";

export interface TaskChatStatus {
  phase: TaskChatPhase;
  startedAt: number;
  detail?: string;
  error?: string;
}

function readableError(value: unknown): string {
  const raw = value instanceof Error ? value.message : String(value ?? "");
  const message = raw.replace(/^Error:\s*/i, "").trim();
  return message || "模型没有返回可用结果";
}

function isAbortError(value: unknown): boolean {
  return /abort|cancel|停止|取消/i.test(readableError(value));
}

/** 给任务对话的准备步骤和工具调用设置上限，并能响应界面上的停止操作。 */
function withTaskChatTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  timeoutMessage: string,
  signal: AbortSignal,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const timer = window.setTimeout(() => finishReject(new Error(timeoutMessage)), timeoutMs);
    const onAbort = () => finishReject(new Error("已停止"));

    const cleanup = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    };
    const finishResolve = (value: T) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(value);
    };
    const finishReject = (error: unknown) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };

    if (signal.aborted) {
      finishReject(new Error("已停止"));
      return;
    }
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(finishResolve, finishReject);
  });
}

function uuid(): string {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

export interface UseTaskChatOpts {
  /** 这段对话属于哪条待办 (task uid)。同一个 key 的对话状态在组件卸载后还在。 */
  chatKey: string;
  model: string;
  /** system prompt 注入. 每次 send 都重算 (task 上下文可能变). */
  buildSystemPrompt: () => string;
  /** P3.5.10 (6/16 鸿波 lazy create): caller 提供 lazy ensure session callback.
   *  send 入口调一次, 拿到真 sessionId 后用于本轮所有 persistMessage.
   *  返 null = 建 session 失败, 该轮 chat 仍能跑但不 persist.
   *
   *  改前 (P3.3.19 C Phase 2c): caller mount 即 sessionGetByTaskUid /
   *  sessionCreate 把 sessionId prop 传进来. 副作用: 早安 tab 默认选中第一个
   *  task → DetailPane mount → 立即建空 session, 即使员工没真聊. advisor 每次
   *  refresh tasks 变 → DetailPane 重 mount → 又建新空 session. 累积一堆
   *  0 条 session 污染工作台 sidebar. lazy create 后纯浏览不建. */
  ensureSessionId: () => Promise<string | null>;
}

export interface UseTaskChatReturn {
  messages: ChatMessage[];
  isStreaming: boolean;
  status: TaskChatStatus | null;
  /** 直接 set 一组消息 (mount 时 load jsonl 历史用). */
  loadHistory: (msgs: ChatMessage[]) => void;
  /** P3.3.20 (6/11): attachments 走 in-memory (跟 useChat 同款 MVP),
   *  关掉再回来附件丢, state.db 只存占位文字 "[📎 N 张图 + 📄 M 份文档]". */
  send: (text: string, attachments?: Attachment[]) => Promise<void>;
  cancel: () => void;
  retry: () => void;
  reset: () => void;
}

interface TaskChatSnapshot {
  messages: ChatMessage[];
  isStreaming: boolean;
  status: TaskChatStatus | null;
}

/** 一条待办的对话运行时。活在模块里, 组件挂不挂都在。 */
class TaskChatRuntime {
  snap: TaskChatSnapshot = { messages: [], isStreaming: false, status: null };
  abort: AbortController | null = null;
  last: { text: string; attachments: Attachment[] } | null = null;
  /** 本次运行期间在这里发过消息 —— 之后内存里的就是最全的, 不再让库里读出来的覆盖
   *  (刚跑完那一刻最后几条可能还在落库的路上)。 */
  touched = false;
  private listeners = new Set<() => void>();

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  };
  getSnapshot = () => this.snap;
  private emit(next: Partial<TaskChatSnapshot>) {
    this.snap = { ...this.snap, ...next };
    this.listeners.forEach((fn) => fn());
  }
  setMessages(next: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) {
    this.emit({ messages: typeof next === "function" ? next(this.snap.messages) : next });
  }
  setIsStreaming(v: boolean) { this.emit({ isStreaming: v }); }
  setStatus(next: TaskChatStatus | null | ((prev: TaskChatStatus | null) => TaskChatStatus | null)) {
    this.emit({ status: typeof next === "function" ? next(this.snap.status) : next });
  }
}

const runtimes = new Map<string, TaskChatRuntime>();
const runningListeners = new Set<() => void>();
function runtimeFor(key: string): TaskChatRuntime {
  let rt = runtimes.get(key);
  if (!rt) {
    rt = new TaskChatRuntime();
    rt.subscribe(() => runningListeners.forEach((fn) => fn()));
    runtimes.set(key, rt);
  }
  return rt;
}
/** 这条待办的对话是不是正在跑 (左栏标记用; 配 subscribeTaskChatRunning)。 */
export function isTaskChatRunning(key: string): boolean {
  return runtimes.get(key)?.snap.isStreaming ?? false;
}
export function subscribeTaskChatRunning(fn: () => void): () => void {
  runningListeners.add(fn);
  return () => { runningListeners.delete(fn); };
}
/** 正在跑的那些待办的 key, 排好序用换行拼成一个字符串 (useSyncExternalStore 要稳定的快照)。 */
export function runningTaskChatKeys(): string {
  const keys: string[] = [];
  runtimes.forEach((rt, key) => { if (rt.snap.isStreaming) keys.push(key); });
  return keys.sort().join("\n");
}
/** 测试用: 清掉所有运行时。 */
export function _resetTaskChatRuntimes(): void {
  runtimes.forEach((rt) => rt.abort?.abort());
  runtimes.clear();
}

export function useTaskChat(opts: UseTaskChatOpts): UseTaskChatReturn {
  const current = runtimeFor(opts.chatKey);
  const { messages, isStreaming, status } = useSyncExternalStore(current.subscribe, current.getSnapshot);

  // opts 用 ref, 闭包稳定不重建 send (model / buildSystemPrompt 都可能每次 render 变)
  const optsRef = useRef(opts);
  optsRef.current = opts;

  /** P3.3.19 C Phase 2c (6/11): 把 ChatMessage append 进 hermes state.db.
   *  跟 useChat persistMessage 同款 (复制自 useChat.ts:101-131), 不动 useChatStore.
   *  失败静默 — chat UI 仍 work, 只是该条 msg 没进 db.
   *
   *  P3.5.10 (6/16): sessionId 改 caller 传, 不再读 opts. send 入口先 await
   *  ensureSessionId 拿真 sid, 整轮所有 persist 都用这个 sid (跟 useChat
   *  sessionIdForStream 同思路, 防 send 中途用户切走 task 让 persist 落到错 session). */
  const persistMessage = useCallback(
    async (msg: ChatMessage, sessionId: string | null): Promise<void> => {
      if (!sessionId) {
        console.warn("[useTaskChat] sessionId 缺, 跳过 persist");
        return;
      }
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
        console.warn("[useTaskChat] sessionMessageAppend 失败:", e);
      }
    },
    [],
  );

  const loadHistory = useCallback((msgs: ChatMessage[]) => {
    const rt = runtimeFor(optsRef.current.chatKey);
    // 切回来时调用方会把库里的历史再读一遍塞进来。这里发过消息的话内存里的更全
    // (含还在跑的那一轮), 不能被覆盖。
    if (rt.touched) return;
    rt.setMessages(msgs);
  }, []);

  const reset = useCallback(() => {
    const rt = runtimeFor(optsRef.current.chatKey);
    rt.abort?.abort();
    rt.abort = null;
    rt.touched = false;
    rt.last = null;
    rt.setMessages([]);
    rt.setIsStreaming(false);
    rt.setStatus(null);
  }, []);

  const cancel = useCallback(() => {
    const rt = runtimeFor(optsRef.current.chatKey);
    const ctrl = rt.abort;
    if (!ctrl) return;
    ctrl.abort();
    rt.setIsStreaming(false);
    rt.setMessages((prev) =>
      prev.map((message) =>
        message.status === "streaming"
          ? { ...message, status: "done", content: message.content || "已停止" }
          : message,
      ),
    );
    rt.setStatus((prev) =>
      prev
        ? { ...prev, phase: "cancelled", detail: "正在停止本次回答…", error: undefined }
        : null,
    );
  }, []);

  const send = useCallback(async (text: string, attachments?: Attachment[]) => {
    const trimmed = text.trim();
    const atts = attachments ?? [];
    // P3.3.20 (6/11): 纯文字或纯附件都允许 (跟 useChat 同款).
    if (!trimmed && atts.length === 0) return;
    // 这一轮属于发送那一刻选中的待办; 之后切到别的待办也不换
    const rt = runtimeFor(optsRef.current.chatKey);
    // 跟 useChat 同款保护: 流中再发会撞, caller 应 disable 输入框
    if (rt.abort && !rt.abort.signal.aborted) {
      console.warn("[useTaskChat] 流进行中, 跳过 send");
      return;
    }

    const startedAt = Date.now();
    const ctrl = new AbortController();
    rt.abort = ctrl;
    rt.last = { text: trimmed, attachments: atts };
    rt.touched = true;
    rt.setIsStreaming(true);
    rt.setStatus({ phase: "preparing", startedAt });

    // 1. append user msg (带 attachments, in-memory only) + persist
    const userMsg: ChatMessage = {
      id: uuid(),
      role: "user",
      content: trimmed,
      attachments: atts.length > 0 ? atts : undefined,
      ts: nowIso(),
      status: "done",
    };
    let curMessages: ChatMessage[] = [];
    rt.setMessages((prev) => {
      curMessages = [...prev, userMsg];
      return curMessages;
    });
    // P3.3.19 C Phase 2c (6/11): persist 走 state.db (sessionMessageAppend) 而非 jsonl.
    // P3.3.20 (6/11): 附件内容不落 state.db (base64 / preview 太大),
    //   只存 "[📎 N 张图 + 📄 M 份文档]" 占位 — 跟 useChat 同款 (useChat.ts:560).
    const imgN = atts.filter((a) => a.kind === "image").length;
    const fileN = atts.filter((a) => a.kind === "file").length;
    const placeholderParts: string[] = [];
    if (imgN > 0) placeholderParts.push(`📎 ${imgN} 张图`);
    if (fileN > 0) {
      const fileNames = atts
        .filter((a) => a.kind === "file")
        .map((a) => a.name)
        .join(", ");
      placeholderParts.push(`📄 ${fileN} 份文档 (${fileNames})`);
    }
    const persistContent =
      placeholderParts.length > 0
        ? `${trimmed}${trimmed ? "\n" : ""}[${placeholderParts.join(" + ")}]`
        : trimmed;

    // P3.5.10 (6/16 鸿波 lazy create): 在 persist user msg 之前 await
    // ensureSessionId 拿真 sid. caller 内部: 已有 sid 直接返, 没就 sessionCreate
    // + sessionSetTaskUid 建关联 + setSessionId. 整轮 send 都用这一个 sid
    // (snapshot, 防 send 中途用户切走 task 让后续 assistant/tool persist 落到错 session).
    // 拿不到 (建 session 失败) → null → persistMessage 软退化跳过 persist, chat UI 仍 work.
    let terminal: "success" | "error" | "cancelled" = "success";
    try {
      const sidForRound = await optsRef.current.ensureSessionId();
      if (ctrl.signal.aborted) {
        terminal = "cancelled";
        return;
      }
      void persistMessage({ ...userMsg, content: persistContent, attachments: undefined }, sidForRound);

      // 2. 拉 tools (跟工作台同款 — 60s TTL cache, tool_bridge 不可达返 [])
      rt.setStatus({ phase: "loading_tools", startedAt, detail: "正在准备可用工具" });
      const tools = await withTaskChatTimeout(
        ensureTools(),
        TASK_CHAT_TOOLS_TIMEOUT_MS,
        "工具准备超过 60 秒未返回",
        ctrl.signal,
      );
      if (ctrl.signal.aborted) {
        terminal = "cancelled";
        return;
      }

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

      for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
        rt.setStatus({
          phase: "waiting_model",
          startedAt,
          detail: round === 0 ? "已连接模型，等待首段输出" : `等待模型继续（第 ${round + 1} 轮）`,
        });
        // 新一轮的 assistant 消息
        const assistantId = uuid();
        const assistantMsg: ChatMessage = {
          id: assistantId,
          role: "assistant",
          content: "",
          ts: nowIso(),
          status: "streaming",
        };
        rt.setMessages((prev) => [...prev, assistantMsg]);

        const callsRef: { calls: ToolCall[] } = { calls: [] };
        let finalContent = "";
        let streamErr: string | null = null;

        await streamChat({
          model: optsRef.current.model,
          messages: history,
          tools,
          // 10/26: 批准请求标上是这段任务对话的 (store/approvals.ts)
          uiSessionId: sidForRound ?? undefined,
          onDelta: (chunk) => {
            if (ctrl.signal.aborted) return;
            finalContent += chunk;
            if (chunk.trim()) {
              rt.setStatus({ phase: "generating", startedAt, detail: "正在生成回答" });
            }
            rt.setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + chunk }
                  : m,
              ),
            );
          },
          onToolCalls: (calls) => {
            if (ctrl.signal.aborted) return;
            callsRef.calls = calls;
            if (calls.length > 0) {
              rt.setStatus({
                phase: "running_tool",
                startedAt,
                detail: `模型准备执行 ${calls.length} 个动作`,
              });
            }
          },
          onDone: () => {
            // approval-pending event 由 chat.ts 在解 SSE 时自动 dispatch,
            // DetailPane 自己 listen 弹 banner. 这里 nothing-to-do.
          },
          onError: (err) => {
            if (ctrl.signal.aborted) return;
            streamErr = err;
            rt.setStatus({ phase: "error", startedAt, error: readableError(err) });
            rt.setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, status: "error", error: err }
                  : m,
              ),
            );
          },
          signal: ctrl.signal,
        });

        if (ctrl.signal.aborted) {
          terminal = "cancelled";
          break;
        }
        if (streamErr) {
          // 错误就停, 不再进 tool 循环
          terminal = ctrl.signal.aborted ? "cancelled" : "error";
          break;
        }

        const collectedCalls = callsRef.calls;
        const doneAssistant: ChatMessage = {
          ...assistantMsg,
          content: finalContent,
          tool_calls: collectedCalls.length > 0 ? collectedCalls : undefined,
          status: "done",
        };
        rt.setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? doneAssistant : m)),
        );

        // P3.3.11: 持久化完整 assistant 消息 (含 tool_calls).
        //   tool_calls 此刻还是 pending/running (tool 还没真跑), result 字段缺.
        //   后面 tool 跑完会再 persist 一条完整 assistant 覆盖 (append-only 写新一行,
        //   load 时按 id 取最后一行).
        // 11/13: 不再看组件还在不在 —— 切走后这一轮照常跑, 结果落到发送时锁定的
        //   sidForRound (send 入口 snapshot), 不会写到别的待办的 session。
        if (finalContent.trim().length > 0 || collectedCalls.length > 0) {
          void persistMessage(doneAssistant, sidForRound);
        }
        history = [...history, doneAssistant];

        // 没 tool_calls → 收尾
        if (collectedCalls.length === 0) break;

        // 跑 tool (跟 useChat runOneRound 同款串行)
        for (const tc of collectedCalls) {
          rt.setStatus({
            phase: "running_tool",
            startedAt,
            detail: `正在执行 ${tc.name}（第 ${round + 1} 轮）`,
          });
          // mark running (assistant 卡片里那条 tool_calls[i] 进度)
          rt.setMessages((prev) =>
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
            const res = await withTaskChatTimeout(
              toolBridgeCallTool(tc.name, tc.args as Record<string, unknown>),
              TASK_CHAT_TOOL_TIMEOUT_MS,
              `工具 ${tc.name} 超过 60 秒未返回`,
              ctrl.signal,
            );
            ok = res.ok;
            resultStr =
              typeof res.result === "string"
                ? res.result
                : JSON.stringify(res.result);
            // BL-TOOLCALL-FAKE-OK (7/27): 跟 useChat 同款 —— res.ok 只是"调到了
            // tool 没抛异常", tool 可以调用成功地告诉你它失败了 (hermes
            // browser_navigate 返 {"success":false,"error":"Blocked..."}).
            // 不看这层, UI 就把失败渲染成 ✓, 员工和排查的人一起被带偏。
            if (ok && detectToolBusinessError(resultStr)) {
              ok = false;
            }
          } catch (e) {
            if (ctrl.signal.aborted) throw e;
            ok = false;
            resultStr = readableError(e);
          }

          // update tool_call.result + status
          rt.setMessages((prev) =>
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

          // P3.3.11: tool result 也 persist — load 时 join 回 assistant.tool_calls[i].result.
          // sidForRound 是 send 入口 snapshot, 锁本轮 session。
          void persistMessage(toolMsg, sidForRound);
        }
      }
    } catch (e) {
      if (ctrl.signal.aborted || isAbortError(e)) {
        terminal = "cancelled";
        if (rt.abort === ctrl) {
          rt.setStatus({ phase: "cancelled", startedAt, detail: "本次回答已停止，可重新发送" });
        }
      } else {
        terminal = "error";
        const message = readableError(e);
        console.warn("[useTaskChat] send 异常:", e);
        if (rt.abort === ctrl) {
          rt.setStatus({ phase: "error", startedAt, error: message });
        }
      }
    } finally {
      // 取消后立即重试可能已经创建了新 ctrl，旧请求不能清掉新请求的状态。
      if (rt.abort === ctrl) {
        rt.setIsStreaming(false);
        rt.abort = null;
        if (terminal === "success") rt.setStatus(null);
      }
    }
  }, []);

  const retry = useCallback(() => {
    const request = runtimeFor(optsRef.current.chatKey).last;
    if (request) void send(request.text, request.attachments);
  }, [send]);

  return { messages, isStreaming, status, loadHistory, send, cancel, retry, reset };
}
