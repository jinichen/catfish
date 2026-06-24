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
import { invoke } from "@tauri-apps/api/core";
import { useChatStore } from "../store/chat";
import {
  useAutoContinueStore,
  MAX_AUTO_CONTINUES,
  AUTO_CONTINUE_PROMPT,
} from "../store/auto_continue";  // 5/13 鸿波"长程任务咋办" — gateway 删 BL-FIX23 后客户端补
import { streamChat, type OpenAITool } from "../lib/chat";
import { checkPromiseOnly } from "../lib/promiseCheck";
import * as streamRegistry from "../lib/streamRegistry";
import {
  toolBridgeCallTool,
  sessionCreate,
  sessionMessageAppend,
  sessionFinalize,
} from "../lib/tauri";
import type { Attachment, ChatMessage, ToolCall } from "../types/chat";

// 20 轮够用 — leadership-briefing skill 多附件场景一次成功的话只 1-3 轮 (拿
// schema + 真调). 如果模型 args 格式错循环, 也最多浪费 20 轮就停 (跟 10 轮
// 体验上区别不大, 但给跨 skill 复杂任务留余地). 鸿波 2026-04-30 反馈"10 轮
// 总是踩到上限" 后调高.
const MAX_TOOL_ROUNDS = 20;

// 5/20 拆分: VISION_MODEL_PREFERENCE 移到 chat/visionSwitch.ts (跟 maybeSwitchToVision 一起)

function uuid(): string {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function nowIso(): string {
  return new Date().toISOString();
}


// 5/20 拆 907 → ~700: tools cache + vision switch 抽到 chat/
export { _clearToolsCache } from "./chat/toolsCache";
import { ensureTools } from "./chat/toolsCache";
import { maybeSwitchToVision } from "./chat/visionSwitch";

// ─── 主 hook ───────────────────────────────────

export function useChat(_initialModel: string) {
  // 5/28 鸿波: initialModel 参数保留 (caller 仍传), 但 useChat 内部不再用 —
  // 原 line 130-137 render-time setModelInStore(initialModel) 已 disable
  // (那段是 picker 覆盖 bug 的根因, 任何 re-render 都把 store 拉回 catalog.default).
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const streamingId = useChatStore((s) => s.streamingId);
  const model = useChatStore((s) => s.model);
  const addMessage = useChatStore((s) => s.addMessage);
  const updateMessage = useChatStore((s) => s.updateMessage);
  const appendToMessage = useChatStore((s) => s.appendToMessage);
  const setIsStreaming = useChatStore((s) => s.setIsStreaming);
  const setStreamingId = useChatStore((s) => s.setStreamingId);
  // P3.5.18 Phase 2 (6/17 鸿波): hermes preflight 自动压缩 inline status.
  const setLifecycleStatus = useChatStore((s) => s.setLifecycleStatus);
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

  /** append 一条消息到 state.db, 失败静默(不能影响 UI 流).
   *
   * BL-MULTI-SESSION-STREAM (5/24): 加 sessionIdOverride 参数. 老代码读
   * store.persistedSessionId — 但用户切走会话后这个值变了, 老 stream 的尾巴
   * (onDone 的 final assistant + tool 消息) 会被写到 *新* session 名下,
   * 老 session 收不到. 现在 send 开头 ensureSessionId 锁定一个 id, 整轮所有
   * persistMessage 都用这个 override, 保证写到对的 session.
   */
  const persistMessage = useCallback(
    async (
      msg: ChatMessage,
      sessionIdOverride?: string,
    ): Promise<number | null> => {
      // P3.5.8 BL-FILE-SESSION-INDEX-V1 Phase 2 (6/16): 返 state.db rowid 让
      // attachment_record 用 rowid 当 messageId. 否则 uuid (userMsg.id) ≠ rowid,
      // resume 时 sessionMessages.indexAttachmentsByMessageId join 不上,
      // image attachments 还原不出来 → 后续 wire 仍丢图.
      const sessionId =
        sessionIdOverride ?? useChatStore.getState().persistedSessionId;
      if (!sessionId) return null;
      try {
        const rowid = await sessionMessageAppend({
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
        return rowid;
      } catch (e) {
        console.warn("[catfish chat] session_message_append 失败:", e);
        return null;
      }
    },
    [],
  );

  // [5/28 鸿波 21:30 disable] 这段 render-time 副作用是 picker 不生效的根因 —
  // 任何重 render (useCatalog 15s polling / HMR / 别的 state 变) 都触发, model
  // 一被某地方设回 "main" 就立刻覆盖成 catalog.default (= deepseek-flash 因为
  // main 内网挂 is_available=false). picker 切 Gemini 后下一次 render 这条 trigger,
  // 又把 store.model 覆盖回 deepseek. picker 看起来生效 (UI 一闪 Gemini) 实际 store 仍 deepseek.
  // Disable — picker 选啥就是啥, 不自动覆盖 store.
  // if (
  //   model === "catfish-private-main" &&
  //   initialModel &&
  //   initialModel !== model
  // ) {
  //   setModelInStore(initialModel);
  // }

  // 5/24 BL-MULTI-SESSION-STREAM: pendingDelta/raf/currentStreamId 从 hook 级 useRef
  // 收进 runOneRound 局部. 否则两个 session 并发 streaming 时, 共享 ref 会让 A 的 delta
  // 串到 B 的消息上 (B send 时 currentStreamIdRef 被覆盖, A 的下一个 flushPending 会
  // 用 B 的 id 调 appendToMessage). 局部化后每条流自己一套 raf+pending+id, 不串.
  // abortRef 保留 hook 级 — cancel() 走 registry 兜底用.
  const abortRef = useRef<AbortController | null>(null);

  /** 单轮 streamChat,返回是否需要继续(tool_calls finish_reason)。
   *  把消息历史作为参数传入 (而不是依赖 store), 因为 React state 异步,
   *  连续递归时拿到的是旧 snapshot。
   *
   *  BL-MULTI-SESSION-STREAM (5/24): ctx 加 sessionId — 整轮 persistMessage 都
   *  用它而不是读 store.persistedSessionId, 防 stream 进行中用户切走会话后
   *  尾巴消息写到错的 session 名下. */
  const runOneRound = useCallback(
    async (
      ctx: {
        ctrl: AbortController;
        roundIdx: number;
        currentMessages: ChatMessage[];
        tools: OpenAITool[];
        sessionId: string | null;
        // 5/28 鸿波 P0: send 入口快照 model, 整个 send 流程用同一值, 不依赖
        // useChatStore.getState() 实时读 (vite HMR + zustand 可能让 store 双 instance,
        // picker 拿一份 getState 拿另一份). Vision switch 改 store 时也更新这个值.
        sendModel: string;
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
      addMessage(assistantMsg);
      setStreamingId(assistantId);

      // 5/24 BL-MULTI-SESSION-STREAM: 局部 raf-throttled flush (不再共享 hook ref).
      // 每条 stream 自己一套, 跨 session 并发不会串 delta.
      let pendingDelta = "";
      let rafId: number | null = null;
      // P3.5.97 (6/24 鸿波): 闭包累 assistant content, 不再读 store.
      //
      // # 真因 (P3.5.96 audit 完整)
      //
      // 老 P3.5.30 mirror 直接 useChatStore.getState().messages —— 切走会话后
      // store.messages 已是新 session 的, 这把新 session messages 错误镜像到老
      // session 的 registry. 切回老 session 时 ChatTab restore effect 用 registry.messages
      // 覆盖 UI, 鸿波看到 "切会话就断" (实际是 UI 显错 messages).
      //
      // # 修法
      //
      // round 内独立维护 assistantContent — appendToMessage(store) 切走变 no-op 但
      // 闭包累不变. mirror 用 ctx.currentMessages (round 入口锁定) + 闭包 assistant
      // snapshot, 完全不读 store. session 安全.
      let assistantContent = "";
      const flushThisRound = () => {
        const delta = pendingDelta;
        pendingDelta = "";
        rafId = null;
        if (!delta) return;
        // appendToMessage 按 id 找, 老 id 不在 store 时 (用户切走了) 自动 no-op.
        // 数据没丢: 最终 persistMessage 写完整 final assistant content 到 db.
        appendToMessage(assistantId, delta);
        assistantContent += delta;  // 闭包累, 跨 store 不变
        // P3.5.30 (6/17) + P3.5.97 (6/24): 镜像到 registry. 用 closure local 不读 store —
        // 切走后 store 是新 session 的, 老读会污染老 registry (P3.5.96 真 bug).
        if (ctx.sessionId) {
          streamRegistry.update(ctx.sessionId, (s) => {
            s.messages = [
              ...ctx.currentMessages,
              { ...assistantMsg, content: assistantContent, status: "streaming" },
            ];
            s.streamingId = assistantId;
          });
        }
      };
      const scheduleFlushThisRound = () => {
        if (rafId !== null) return;
        rafId = requestAnimationFrame(flushThisRound);
      };

      // 用对象包裹规避 TS 的 flow narrowing —— `let x: T[] | null = null`
      // 在 await 之后会被错误收窄成 never,即使 callback 里改了 x。
      // 5/23 BL-COMPANION-HERMES-SESSION-REUSE (鸿波): 加 viaHermes flag.
      // hermes 路径下 hermes 自己往 state.db 写 assistant message, companion
      // 不能再 persistMessage 写一遍 (会双写, db 出现 2 条相同 assistant row).
      const refs: { calls: ToolCall[]; viaHermes?: boolean } = { calls: [] };

      // 5/28 鸿波 P0 fix: 用 ctx.sendModel (send 入口快照), 不读 store getState.
      // 旧实现 useChatStore.getState().model — vite HMR + zustand 多 instance 时
      // picker UI 跟 send 拿到的 store 不一致, picker 显 Gemini 但 send 真发 deepseek.
      // ctx.sendModel 在 send 入口锁定 picker 当前值, 整个 send 流程稳定一致.
      // (Vision switch 改 store 仍然 work — send 会 update ctx.sendModel 跟着切.)
      await streamChat({
        model: ctx.sendModel,
        messages: ctx.currentMessages,
        tools: ctx.tools,
        // 5/23 BL-COMPANION-HERMES-SESSION-REUSE (鸿波): send() 开头已 ensureSessionId()
        // lazy create state.db session, 这里读 store 把 id 透传给 hermes header.
        // hermes 收到就复用, 不再 derive api-* 新 session → 修双开.
        // 5/24 BL-MULTI-SESSION-STREAM: ctx.sessionId 是 send 入口锁定的, 不读 store,
        // 防切走会话后这一轮的 hermes header 飘到新 session.
        sessionId: ctx.sessionId ?? undefined,
        signal: ctx.ctrl.signal,
        onDelta: (text) => {
          pendingDelta += text;
          scheduleFlushThisRound();
        },
        onToolCalls: (calls) => {
          refs.calls = calls;
        },
        onDone: (info) => {
          if (rafId !== null) {
            cancelAnimationFrame(rafId);
            rafId = null;
          }
          flushThisRound();
          // 5/23 BL-COMPANION-HERMES-SESSION-REUSE: 记本次走 hermes 没, 后面
          // line 276 据此决定要不要 persist assistant (hermes 写过就别再写).
          refs.viaHermes = info?.via_hermes === true;
          // P3.5.17.c.2 (6/17): 5/13 BL-CONTEXT-COUNTER setLastPromptTokens 砍 —
          // info.usage.prompt_tokens 是 hermes turn 内多 LLM API call 累加 cost,
          // 不是 ctx 占用. ContextCounter / ContextOverflowBanner 都砍, store 字段拆.
// BL-TASK-ASSESS-3-UI (5/15 鸿波"客户端要评估完成情况"): 拿 gateway 给的
          // task_assessment 做 promise-vs-reality 检测, 命中嘴炮 → 写
          // assistant message._promise_check, UI 渲染 ⚠ badge + 催继续按钮.
          // 5/24: 用 assistantId (闭包内) 替代老 currentStreamIdRef, 不串.
          if (info?.task_assessment) {
            // P3.5.97 (6/24): 用闭包 assistantContent, 不读 store (切走污染防御).
            // 老逻辑读 store 在切走后会拿到新 session 的 messages → find by id 返空 →
            // checkPromiseOnly 拿空字符串误判嘴炮. 闭包累的内容永远是本 round 真实输出.
            const check = checkPromiseOnly(assistantContent, info.task_assessment);
            if (check.is_promise_only) {
              updateMessage(assistantId, {
                _promise_check: {
                  is_promise_only: true,
                  promised_paths: check.promised_paths,
                  nudge_count: 0,
                  skill_guard_fired: info.task_assessment.skill_guard_fired,
                  ever_called_skill:
                    info.task_assessment.ever_called_catfish_run_skill_in_session,
                },
              });
            }
          }
        },
        onError: (err) => {
          if (rafId !== null) {
            cancelAnimationFrame(rafId);
            rafId = null;
          }
          flushThisRound();
          // 5/24: 用 assistantId (闭包) 替代 currentStreamIdRef, 防并发串.
          updateMessage(assistantId, {
            status: "error",
            error: err,
          });
        },
        // P3.5.18 Phase 2 (6/17 鸿波): plugin P19 桥 hermes preflight `_emit_status`
        // → SSE `hermes.tool.progress` (tool="catfish-lifecycle") → 这里 set inline 状态.
        // status "running" → 显; "completed" → 清 (但 hermes 真**目前不发 completed** —
        // preflight emit 一次后 compress 跑完 stream 自然 done, finally block 清).
        onLifecycle: (status, text) => {
          if (status === "completed" || !text) {
            setLifecycleStatus(null);
          } else {
            setLifecycleStatus(text);
          }
        },
      });

      const collectedToolCalls = refs.calls;

      // 五一 sprint 5/2 修: streamChat 的 onError 已经把 status 设成 'error',
      // 这里别无条件覆盖回 'done', 否则 quota 超限 / 鉴权错 / 上游 503 等错误
      // UI 显不出来 (踩过坑).
      const currentStatus = useChatStore
        .getState()
        .messages.find((m) => m.id === assistantId)?.status;
      const isError = currentStatus === "error";

      // P3.5.97 (6/24): finalAssistant.content 用闭包 assistantContent 不读 store.
      // 切走后 store 是新 session 的, find by id 返空, 把 final 写空 db (data loss).
      const finalAssistant: ChatMessage = {
        ...assistantMsg,
        content: assistantContent,
        tool_calls:
          collectedToolCalls.length > 0 ? collectedToolCalls : undefined,
        status: isError ? "error" : "done",
      };
      if (isError) {
        // streamChat onError 已经处理了 status + error 字段, 这里不动
      } else if (collectedToolCalls.length > 0) {
        updateMessage(assistantId, {
          status: "done",
          tool_calls: collectedToolCalls,
        });
      } else {
        updateMessage(assistantId, { status: "done" });
      }
      // P3.5.30 (6/17 鸿波): 真**round 结束 镜像 final messages 到 registry**.
      // ChatTab 切回时 mount restore (60 秒 TTL 内) 真**0 延迟看 final**.
      // 多轮 tool_call 真**每轮都 sync**, 真**最后一轮 finally streamRegistry.finish() 真**标 not running**.
      //
      // P3.5.97 (6/24): 同 flushThisRound 修法 — 用 closure local 拼 mirror,
      // 不读 store. round 结束时 store 可能已切走, 读 store 会污染老 registry.
      if (ctx.sessionId) {
        streamRegistry.update(ctx.sessionId, (s) => {
          s.messages = [...ctx.currentMessages, finalAssistant];
          s.streamingId = null;
        });
      }
      // 持久化 assistant 消息(完整 content + tool_calls)
      //
      // P3.5.25 (6/17 鸿波"还是重复") **revert P3.5.21**: 改回 5/23
      // BL-COMPANION-HERMES-SESSION-REUSE 老逻辑.
      //
      // P3.5.21 误诊: 我看 hermes log "history=58 应该 59 差 1" 就跳结论"assistant 没 persist".
      // 鸿波本机 db query 真证: hermes per-segment 写 (一个 turn 内每个 tool_call
      // response 段单独 1 行, 4-6 行/turn), 而 P3.5.21 改 Companion 总 persist 时
      // Companion 写 per-turn 整段 (1 行 = 4 段拼接 + "\n\n" 前缀, length 257 vs
      // hermes 的 23/40/84/96). 两边 content **fundamentally byte 不同**,
      // idempotent (严格 == 或 trim) 都不命中 → 双写 → UI 真重复, 鸿波 6/17 screenshot 验证.
      //
      // P3.5.24 加 trim() + IMMEDIATE 也救不了 (60 chars vs 257 chars 完全不同 string).
      //
      // revert P3.5.21 → Companion 不写, 信任 hermes per-segment 真持久化. 没重复.
      //
      // P3.5.21 真诉求"切走再回来不见" — 留 fresh session 真 audit, 真因可能在
      // loadSession 路径或 UI render bug, 不靠 hermes log 推测.
      //
      // Rust session_write.rs idempotent (P3.5.21 + .24 trim+IMMEDIATE) 留不动 —
      // 无害, Companion 不调就不触发. 后续若真要复用同款防双写做 atomic dedup,
      // 现成 transaction 包好.
      //
      // 5/24 BL-MULTI-SESSION-STREAM 保留.
      if (!refs.viaHermes && ctx.sessionId) {
        void persistMessage(finalAssistant, ctx.sessionId);
      }

      // 重新拼当前 messages snapshot(给下一轮用)
      // P3.5.97 (6/24): content 用闭包 assistantContent, 不读 store.
      // (上面 finalAssistant 用同款修法, 这里直接复用避免重复 spread)
      const updatedMessages: ChatMessage[] = [
        ...ctx.currentMessages,
        {
          ...finalAssistant,
          // 给下一轮 LLM 用的 snapshot status 永远 "done" (上面 finalAssistant 在
          // isError 时是 "error", 但发给下一轮 LLM 看的 history 都该是 done 状态).
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
          // BL-TODO-BRIDGE-STORE follow-up (5/16): 实接 sessionId 透传, 让 per-session
          // stateful tool (hermes todo) 真按 session 隔离. 老 caller (Dashboard 各卡
          // 调 catfish_user_profile_* 等) 不传, 走 __default__ 行为不变.
          // 5/24 BL-MULTI-SESSION-STREAM: 用 ctx.sessionId (入口锁定) 不读 store.
          const res = await toolBridgeCallTool(
            tc.name,
            tc.args as Record<string, unknown>,
            ctx.sessionId ?? undefined,
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
        // P3.5.97 (6/24): tool 循环每次 push toolMsg 后也 mirror.
        // 老 P3.5.30 mirror 只在 round-end, tool 循环里 push 完不 sync, 用户切走
        // 切回时 registry 里没 tool result, ChatTab restore 覆盖回 partial UI.
        // 用闭包 updatedMessages (含 tool messages) 不读 store.
        if (ctx.sessionId) {
          streamRegistry.update(ctx.sessionId, (s) => {
            s.messages = [...updatedMessages];
          });
        }
        // 持久化 tool 角色消息. 5/24 BL-MULTI-SESSION-STREAM: 同 final assistant
        // 一样, 用 ctx.sessionId 锁定, 防切走会话后 tool 消息飘到错的 session.
        if (ctx.sessionId) {
          void persistMessage(toolMsg, ctx.sessionId);
        }
      }

      // tool_calls 处理完 → 必继续下一轮 LLM 推理(让 LLM 看 tool 结果)
      return { shouldContinue: true, updatedMessages };
    },
    [
      // 5/24 BL-MULTI-SESSION-STREAM: 去掉 scheduleFlush/flushPending —
      // 局部化进 runOneRound 内部, 不再走 hook ref. model 也不需要 — 内部读 store.
      addMessage,
      updateMessage,
      setStreamingId,
      persistMessage,
    ],
  );

  const send = useCallback(
    async (
      content: string,
      attachments: Attachment[] = [],
      // P3.5.20.1 (6/17): metadata param 砍 — steer 整链退役.
    ) => {
      const trimmed = content.trim();
      // 文字+图片都为空才拒. 只发图(没文字)是允许的.
      if (!trimmed && attachments.length === 0) return;
      if (isStreaming) return;

      // 0. 第一次 send 时 lazy create state.db session (持久化的开端).
      // 5/24 BL-MULTI-SESSION-STREAM: 捕获 id 到 closure, 整轮 persistMessage /
      // tool 调用 / streamChat header 全用它, 防 stream 进行中切走 store 变了
      // 把消息写到新 session.
      const sessionIdForStream = await ensureSessionId();

      // 0.5. 如果带图但当前模型不支持视觉 → 透明切到视觉模型
      //      切了的话往聊天里追加一条 system 提示, 让员工知道发生了啥.
      //      没视觉模型可切 → 给员工 error 消息, **abort 这次发送** —
      //      硬发 deepseek-flash + image_url 上游会 400, 浪费一轮还误导员工.
      // vision switch 移到 for round 之前已经做过 (line 451). 这里改用 sendModel
      // 不是闭包 model — 防 send 入口之前的旧 model 飘. 但 sendModel 在下面 let
      // 才声明, 这里用闭包 model 是 send 入口快照前的, OK (vision 判断只看带图与否).
      // 切到 vision 后, 下面 sendModel 也要同步 update. 这里改 store 不影响 sendModel
      // 直到下面赋值. 注: hello 不带图不进这分支, 不影响今晚的 picker 锁问题.
      if (attachments.some((a) => a.kind === "image")) {
        const sw = await maybeSwitchToVision(model);
        if (sw.switched) {
          setModelInStore(sw.newModel);
        }
        if (sw.notice) {
          // 用 assistant 角色 + status='done' 显示提示 (UI ChatMessage 不渲染 system)
          const noticeMsg: ChatMessage = {
            id: uuid(),
            role: "assistant",
            content: sw.notice,
            ts: nowIso(),
            status: sw.switched ? "done" : "error",
            // status=error 给红色错误 styling, 让员工立刻注意到
            error: sw.switched ? undefined : sw.notice,
          };
          addMessage(noticeMsg);
          // 不 persistMessage —— UI 提示性质, 不进 state.db
        }
        // 没切成 + 有 notice = 当前模型不支持视觉但视觉模型也找不到/拉不到。
        // 短路返回, 不去硬发让上游 400 浪费一轮 + 误导员工. notice 已经写进
        // 错误消息显示给员工了.
        // (notice=null + switched=false = 当前模型本来就支持视觉, 继续往下发)
        if (!sw.switched && sw.notice) {
          return;
        }
      }

      // 0.7 BL-L26 (5/7): 大文件 (parsedTextPath 存在) → BM25 取 top-K 段落,
      //    塞到 attachment.bm25Passages, toWire 时替代 preview 注入.
      //    每个附件并发跑, 失败不阻塞 (返空数组 → fallback 走 preview).
      const bm25Targets = attachments.filter(
        (a) => a.kind === "file" && a.parsedTextPath,
      );
      const enrichedAttachments: Attachment[] = await (async () => {
        if (bm25Targets.length === 0) return attachments;
        const enriched = await Promise.all(
          attachments.map(async (a) => {
            if (a.kind !== "file" || !a.parsedTextPath) return a;
            try {
              type Bm25Out = {
                passages: Array<{ text: string; score: number; ord: number }>;
                total_passages: number;
                query_strategy: string;
                error: string;
              };
              const r = await invoke<Bm25Out>("attachment_bm25_search", {
                parsedTextPath: a.parsedTextPath,
                query: trimmed,
                topK: 5,
              });
              if (r.error) {
                console.warn("[BL-L26] bm25_search 软失败:", r.error);
              }
              return { ...a, bm25Passages: r.passages };
            } catch (e) {
              console.warn("[BL-L26] bm25_search 异常 (fallback preview):", e);
              return a;
            }
          }),
        );
        return enriched;
      })();

      // 1. push user message (带 attachments, in-memory only)
      const userMsg: ChatMessage = {
        id: uuid(),
        role: "user",
        content: trimmed,
        attachments: enrichedAttachments.length > 0 ? enrichedAttachments : undefined,
        ts: nowIso(),
        status: "done",
        // P3.5.20.1 (6/17): _steered 砍 — steer 整链退役.
      };
      const requestMessages = [...useChatStore.getState().messages, userMsg];
      addMessage(userMsg);
      // P3.5.54 (6/21 鸿波 catch "UI 双显"): 真因——hermes v0.17 自己写 user msg.
      //
      // 验证 (state.db dump):
      //   session 20260621_185745_5e9945:
      //     12387 user 'hi' @ 1782039465.224  ← Companion persistMessage
      //     12388 user 'hi' @ 1782039465.573  ← hermes 350ms 后写
      //     12389 assistant 'hi，有啥事？'
      //   多个 session 全有同款 user msg 双行.
      //
      // hermes v0.17 路径: gateway/run.py:9786 `self.session_store.append_to_transcript(
      //   ..., skip_db=agent_persisted)`, 见 gateway/session.py:1358 "Used when the
      //   agent already persisted messages to SQLite via its own
      //   _flush_messages_to_session_db(), preventing the duplicate-write bug
      //   (#860)." — hermes 自己也踩过 dup write, 加了 skip_db 防自己内部双写;
      //   但 Companion 走 Rust session_message_append 是第三路, hermes 看不见, dedup
      //   也覆不到 (Rust idempotent guard session_write.rs:278 是 assistant-only).
      //
      // 修法: Companion 撤回 user msg 写入. hermes 是 user msg 唯一 writer.
      // ChatTab.tsx polling 5s 后 reload 也只读到 1 行, 不再 2 user bubble.
      //
      // 附件 case: attachment_record 走 fire-and-forget poll hermes rowid (见下).
      // 拿不到 rowid 用 userMsg.id (uuid) 兜底 — image base64 resume 失败但
      // SessionAttachmentsBar chip 仍显 (走 attachment_list_by_session 不靠 join).
      if (sessionIdForStream && enrichedAttachments.length > 0) {
        void (async () => {
          // poll getSession 等 hermes 写完 user row (一般 < 1s, 留 5s 容差).
          // hermes 写的 content 就是 trimmed (没 Companion 的 "[📎...]" placeholder).
          let hermesRowid: string | null = null;
          try {
            const { getSession } = await import("../lib/tauri");
            for (let attempt = 0; attempt < 25; attempt++) {
              await new Promise((r) => setTimeout(r, 200));
              try {
                const detail = await getSession(sessionIdForStream);
                for (let i = detail.messages.length - 1; i >= 0; i--) {
                  const m = detail.messages[i];
                  if (
                    m.role === "user" &&
                    (m.content ?? "").trim() === trimmed
                  ) {
                    hermesRowid = String(m.id);
                    break;
                  }
                }
              } catch {
                // db lock / network jitter — 下轮再试
              }
              if (hermesRowid) break;
            }
          } catch (e) {
            console.warn("[P3.5.54] getSession 异常:", e);
          }
          if (!hermesRowid) {
            console.warn(
              "[P3.5.54] hermes 5s 内没写 user row, attachment_record 用 client uuid 兜底; " +
                "image base64 resume 会失效 (chip 仍显)",
            );
            hermesRowid = userMsg.id;
          }

          let userId = "anonymous";
          try {
            const { authWhoami } = await import("../lib/tauri");
            const who = await authWhoami();
            if (who.authenticated && who.email) userId = who.email;
          } catch {
            // 拿不到就 anonymous
          }
          for (const a of enrichedAttachments) {
            try {
              await invoke("attachment_record", {
                input: {
                  userId,
                  sessionId: sessionIdForStream,
                  messageId: hermesRowid,
                  kind: a.kind,
                  fileKind:
                    a.kind === "file"
                      ? (a as Attachment & { fileKind?: string }).fileKind ?? null
                      : null,
                  name: a.name,
                  mimeType: a.mimeType ?? null,
                  sizeBytes: a.sizeBytes ?? null,
                  // P3.5.8 Phase 2 (6/16): image 也存 keptPath (attachment_save_image
                  // 落盘后填的). 旧只 file 存 — image 一直没 keptPath, resume 时
                  // attachment_load_base64 没文件可读. 现统一: 两类都从 attachment
                  // 自身的 keptPath 字段取.
                  keptPath:
                    (a as Attachment & { keptPath?: string }).keptPath ?? null,
                  parsedTextPath:
                    a.kind === "file"
                      ? (a as Attachment & { parsedTextPath?: string })
                          .parsedTextPath ?? null
                      : null,
                  meta:
                    a.kind === "file" &&
                    (a as Attachment & { meta?: Record<string, unknown> }).meta
                      ? JSON.stringify(
                          (a as Attachment & { meta?: Record<string, unknown> })
                            .meta,
                        )
                      : null,
                },
              });
            } catch (e) {
              console.warn(
                "[BL-FILE-SESSION-INDEX-V1] attachment_record 失败:",
                e,
              );
            }
          }
        })();
      }
      setIsStreaming(true);

      // 2. 拉 tools(第一次会调 tool_bridge,后续走 cache)
      const tools = await ensureTools();

      // 5/24 BL-MULTI-SESSION-STREAM: AbortController 走 registry, 跨 React 生命周期.
      // 切走会话不再 abort, 老 stream 继续在 registry 里跑. cancel() 通过 registry
      // 按 current sessionId 取 controller 调用 abort. Sidebar 通过 registry 显 ⏳.
      // sessionIdForStream 是 null 的 fallback 走老的本地 controller (持久化失败时).
      let ctrl: AbortController;
      if (sessionIdForStream) {
        const state = streamRegistry.start(
          sessionIdForStream,
          useChatStore.getState().model,
          requestMessages,
        );
        ctrl = state.controller;
      } else {
        ctrl = new AbortController();
      }
      abortRef.current = ctrl;

      try {
        let currentMessages = requestMessages;
        // 连续 parse_error 计数 — 模型生成不合法 JSON args 时, Companion 会
        // fallback 到 _raw + _parse_error 字段. 连续 3 轮同样问题 = 模型卡循环, 早停.
        // 鸿波 4-30 踩过坑: 第 1 轮 catfish_run_skill 已成功生成 .docx, 但模型继续
        // "再优化一版" args 一直 JSON 错, 烧完 10 轮上限. 早停让员工立刻看第一次成果.
        let consecutiveParseErrors = 0;
        // 5/13 鸿波"长程任务咋办": 这次 send() 跑过 tool 没. 跑过才允许 auto-continue
        // (用户首问得到答复就 stop 是正常的, 不该续; 跑过 tool 后 stop 是 mid-task
        // 中途停, 才该续).
        let hadToolCallThisSend = false;
        // 自动续跑次数 — 上限 MAX_AUTO_CONTINUES (3), 防死循环
        let autoContinues = 0;
        // 5/28 鸿波 P0 fix: send 入口快照 model, 整个 send 流程用这个值.
        // 防 vite HMR + zustand 多 instance race — picker UI 显 X 但 send 真发 Y.
        // 闭包 model (line 63 useChatStore selector) 是 hook render 时的值, 在 send
        // 调用瞬间已经 fresh (picker 最近 onChange 触发 re-render). vision switch 改
        // store 后这个 let 也要更新 (line 449 下面 sendModel = sw.newModel).
        const sendModel = useChatStore.getState().model;

        for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
          if (ctrl.signal.aborted) break;

          const result = await runOneRound({
            ctrl,
            roundIdx: round,
            currentMessages,
            tools,
            sessionId: sessionIdForStream,  // 5/24 BL-MULTI-SESSION-STREAM
            sendModel,
          });
          currentMessages = result.updatedMessages;

          // 检测本轮 tool_calls 是否都是 parse_error (LLM args JSON 不合法)
          const lastMsg = result.updatedMessages[result.updatedMessages.length - 1];
          const allParseErrors =
            lastMsg?.role === "assistant"
            && (lastMsg.tool_calls?.length ?? 0) > 0
            && lastMsg.tool_calls!.every(
              (tc) => "_parse_error" in (tc.args as Record<string, unknown>)
            );
          if (allParseErrors) {
            consecutiveParseErrors++;
            if (consecutiveParseErrors >= 3) {
              const earlyStopMsg: ChatMessage = {
                id: uuid(),
                role: "assistant",
                content:
                  "\n\n⚠ 检测到模型连续 3 轮生成不合法 JSON 参数 (tool args parse error). "
                  + "早停以避免烧 token. 如果之前有 ✓ 成功的 tool_call, 那次的输出就是结果, "
                  + "看上面的 FilePill 打开. 重新提问 (或简化数据) 可以继续.",
                ts: nowIso(),
                status: "done",
              };
              addMessage(earlyStopMsg);
              break;
            }
          } else {
            consecutiveParseErrors = 0;
          }

          // 标记: 这次 send 跑过 tool 没
          if (
            lastMsg?.role === "assistant"
            && (lastMsg.tool_calls?.length ?? 0) > 0
          ) {
            hadToolCallThisSend = true;
          }

          if (!result.shouldContinue) {
            // 5/13 BL-AUTO-CONTINUE: LLM stop 没调 tool. 看要不要自动续跑.
            // 触发条件 (4 条都满足):
            //   1. toggle on (用户主动开)
            //   2. 这次 send 之前跑过 tool (是 mid-task stop, 不是首问回答完)
            //   3. 自动续跑次数 < 上限 (防死循环)
            //   4. 没被用户 abort
            const autoOn = useAutoContinueStore.getState().on;
            const shouldAutoContinue =
              autoOn
              && hadToolCallThisSend
              && autoContinues < MAX_AUTO_CONTINUES
              && !ctrl.signal.aborted;
            if (shouldAutoContinue) {
              autoContinues++;
              const continueMsg: ChatMessage = {
                id: uuid(),
                role: "user",
                content: AUTO_CONTINUE_PROMPT,
                ts: nowIso(),
                status: "done",
                // _autoContinue 标记 (UI 显淡色 + 角标 "🔄 自动续 N/3", 让员工看见)
                _autoContinue: { round: autoContinues, max: MAX_AUTO_CONTINUES },
              };
              addMessage(continueMsg);
              currentMessages = [...currentMessages, continueMsg];
              // 5/24 BL-MULTI-SESSION-STREAM
              if (sessionIdForStream) {
                void persistMessage(continueMsg, sessionIdForStream);
              }
              continue;  // 跑下一轮
            }
            break;
          }

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
        // 5/24 BL-MULTI-SESSION-STREAM: pendingDelta/raf/currentStreamId 已收进
        // runOneRound 局部, 这里不需要清.
        // 5/24 BL-MULTI-SESSION-STREAM: 只清当前还在显示这个 session 的 store 状态.
        // 如果 stream 进行中用户切走了, 当前 store.persistedSessionId 已是另一个
        // session, 不能把它的 isStreaming/streamingId 清掉 (那个 session 自己可能
        // 也在 streaming). 用 sessionIdForStream 跟 store 当前对比.
        const currentStoreSession = useChatStore.getState().persistedSessionId;
        if (currentStoreSession === sessionIdForStream) {
          setStreamingId(null);
          setIsStreaming(false);
          // P3.5.18 Phase 2: stream 结束 真**清 inline lifecycle status**.
          setLifecycleStatus(null);
        }
        abortRef.current = null;
        // 通知 registry stream 结束 (sidebar ⏳ 也跟着消失)
        if (sessionIdForStream) {
          streamRegistry.finish(sessionIdForStream);
        }
        // BL-HERMES013-RED-1A (5/13 借鉴 Hermes 0.13 ACP /queue): 当前 stream
        // 完成后看 store.queue 有没排队消息. 有就 dequeue + 立即 send 下一条.
        // 用户体验: 长任务跑完无缝接下一个问题, 不用手动按 send.
        // 用 setTimeout 避免 React state 还没 flush 就 send (跟 cancelAndSend 同模式).
        // 5/24 BL-MULTI-SESSION-STREAM: queue 仍是 store 全局的 (单视图概念), 排队
        // 只针对 user 当前看的 session. 如果 stream 是后台 (sessionIdForStream !=
        // currentStoreSession), 别去 dequeue, queue 属于 currentStoreSession 那条线.
        const queue = useChatStore.getState().queue;
        const isForegroundStream =
          currentStoreSession === sessionIdForStream;
        if (
          queue.length > 0
          && !ctrl.signal.aborted
          && isForegroundStream
        ) {
          setTimeout(() => {
            const head = useChatStore.getState().dequeueMessage();
            if (head) {
              void send(head.text);
            }
          }, 200);
        }
      }
    },
    [
      isStreaming,
      model,
      addMessage,
      setIsStreaming,
      setStreamingId,
      setModelInStore,
      runOneRound,
      ensureSessionId,
      persistMessage,
    ],
  );

  const cancel = useCallback(() => {
    // 5/24 BL-MULTI-SESSION-STREAM: cancel 按 current session 走 registry.
    // 老逻辑 abortRef.current 是 "send 最后一次设的 controller", 用户切走会话
    // 后这个 ref 还指向后台老 stream, cancel 会误杀后台. 现在精准: 拿 store
    // current sessionId → registry.cancel(sessionId), 只动当前看的这条流.
    // 如果当前 session 没在 stream → no-op, abortRef fallback (no-session 情况下用).
    const curSession = useChatStore.getState().persistedSessionId;
    if (curSession && streamRegistry.isInflight(curSession)) {
      streamRegistry.cancel(curSession);
      return;
    }
    if (abortRef.current) abortRef.current.abort();
  }, []);

  /** BL-COMPANION-UX1 (5/12 鸿波 "锁死" 抱怨): streaming 中员工想发新消息.
   *
   * 老行为: streaming 时按钮变"停止", 点了 abort 当前 stream 但 textarea 内容
   * 没发送, 员工还得重打一次. UX 差.
   *
   * 新行为: 一键 abort + 发新消息. 内部:
   *   1. abort 当前 stream (abortRef.current.abort())
   *   2. 等 200ms 让 send() 的 finally cleanup 跑完 (isStreaming → false)
   *   3. 调 send() 发新消息
   */
  const cancelAndSend = useCallback(
    async (text: string, attachments: Attachment[]) => {
      if (abortRef.current) abortRef.current.abort();
      // 等 abort 把 state 清干净 (send 的 finally block, 设 isStreaming=false)
      await new Promise((r) => setTimeout(r, 200));
      await send(text, attachments);
    },
    [send],
  );

  /** BL-HERMES013-RED-1A (5/13 借鉴 Hermes 0.13 ACP /queue): streaming 中
   *  排队下一条. 不打断当前 stream, 等 [DONE] 后 useChat send finally 自动
   *  dequeue + send. attachments 暂不支持 (in-memory 太大), 排队只能纯文字. */
  const enqueue = useCallback(
    (text: string) => {
      const t = text.trim();
      if (!t) return;
      useChatStore.getState().enqueueMessage(t);
    },
    [],
  );

  // P3.5.20.1 (6/17 鸿波): steer callback 砍 — BL-HERMES013-RED-1B (5/13)
  // 设计意图 (LLM 看 partial 接力) 未实现, 200 字 tail 跟 cancelAndSend 实测同效.
  // ACP /steer 等价路径退役.

  const reset = useCallback(() => {
    // 5/24 BL-MULTI-SESSION-STREAM: reset 是"+ 新对话", 不再 abort 老 stream.
    // 老 stream 在 streamRegistry 里继续跑直到 onDone 自然结束 / 持久化.
    // 用户场景: 我在跑 A 的长任务, 想开新对话同时跟它聊 B, 老 A 继续没事.
    // 真要 abort 老 stream → 走 cancel() (跟当前 visible session 走 registry).
    // rafId/pendingDelta/currentStreamId 已经局部化进 runOneRound, 这里没 ref 可清.
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
    cancelAndSend,  // BL-COMPANION-UX1 (5/12): 一键停止+发新消息, 解锁死感
    enqueue,        // BL-HERMES013-RED-1A (5/13): ACP /queue 等价, 排队下一条
    // P3.5.20.1 (6/17): steer 砍 — BL-HERMES013-RED-1B 实测同效 cancelAndSend.
    reset,
  };
}
