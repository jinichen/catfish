/** 单轮 LLM 推理 + tool 执行 —— 原 useChat.ts 179~577 行, 8/3 拆出。
 *
 * # 为什么拆
 *
 * useChat.ts 到 8/3 已经 1160 行, 远超军规 800 行红线; 这一个闭包就占 406 行。
 *
 * 但行数只是表象。8/3 那个"停止按钮对 tool 调用完全无效"的 bug 说明了真正的
 * 代价: ctx.ctrl 在这 406 行里只被用过一次 (传给 streamChat 的 signal), tool
 * 执行整段从没查过 abort。在一个几百行、外部依赖靠闭包隐式捕获的函数里,
 * "这个状态有没有传到该到的地方" 没有任何东西会提醒你 —— 既不报错, 也不显眼。
 *
 * 拆出来之后依赖变成显式的 RoundDeps, 少传一个是编译错误。
 *
 * # 顺带修的
 *
 * 原 useCallback 的 deps 数组是 [addMessage, updateMessage, setStreamingId,
 * persistMessage] —— 漏了 appendToMessage 和 setLifecycleStatus。这两个是
 * zustand 的 setter, 引用恒定, 所以漏了也没炸; 但那是运气, 不是设计。
 * 现在六个依赖在 RoundDeps 里一起声明, 漏一个编译不过。
 *
 * # 搬运方式
 *
 * 函数体逐字未动。依赖在开头一次性解构, body 里的调用写法完全不变 —— 这样
 * diff 只有头尾, 中间 371 行可以肉眼确认没被改过。
 */

import { useChatStore } from "../../store/chat";
import { streamChat, type ChatTransport, type OpenAITool } from "../../lib/chat";
import { checkPromiseOnly } from "../../lib/promiseCheck";
import { detectToolBusinessError } from "../../lib/toolResult";
import * as streamRegistry from "../../lib/streamRegistry";
import { toolBridgeCallTool } from "../../lib/tauri";
import type { ChatMessage, ToolCall } from "../../types/chat";
import { uuid, nowIso } from "./ids";

export interface RoundCtx {
      ctrl: AbortController;
      roundIdx: number;
      currentMessages: ChatMessage[];
      tools: OpenAITool[];
      sessionId: string | null;
      // 5/28 鸿波 P0: send 入口快照 model, 整个 send 流程用同一值, 不依赖
      // useChatStore.getState() 实时读 (vite HMR + zustand 可能让 store 双 instance,
      // picker 拿一份 getState 拿另一份). Vision switch 改 store 时也更新这个值.
      sendModel: string;
      onTransportResolved?: (transport: ChatTransport) => void;
}

/** runOneRound 需要、但只有 hook 里才拿得到的东西。
 *
 * 全部来自 useChatStore 的 setter。以前靠闭包捕获, 现在显式传 —— 目的不是
 * 好看, 是让"某个依赖没接进来"变成编译期错误而不是运行期怪象。 */
export interface RoundDeps {
  addMessage: (m: ChatMessage) => void;
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void;
  appendToMessage: (id: string, delta: string) => void;
  setStreamingId: (id: string | null) => void;
  setLifecycleStatus: (s: string | null) => void;
  persistMessage: (msg: ChatMessage, sessionIdOverride?: string) => Promise<number | null>;
}

export interface RoundResult {
  shouldContinue: boolean;
  updatedMessages: ChatMessage[];
}

export async function runOneRound(
  ctx: RoundCtx,
  deps: RoundDeps,
): Promise<RoundResult> {
  // 逐字搬运: body 里仍然直接写 addMessage(...) 等, 不加 deps. 前缀。
  const {
    addMessage,
    updateMessage,
    appendToMessage,
    setStreamingId,
    setLifecycleStatus,
    persistMessage,
  } = deps;

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
        onTransportResolved: ctx.onTransportResolved,
        // P3.5.18 Phase 2 (6/17 鸿波): plugin P19 桥 hermes preflight `_emit_status`
        // → SSE `hermes.tool.progress` (tool="catfish-lifecycle") → 这里 set inline 状态.
        // status "running" → 显; "completed" → 清 (但 hermes 目前不发 completed —
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
      // P3.5.30 (6/17 鸿波): round 结束 镜像 final messages 到 registry.
      // ChatTab 切回时 mount restore (60 秒 TTL 内) 0 延迟看 final.
      // 多轮 tool_call 每轮都 sync, 最后一轮 finally streamRegistry.finish() 真标 not running**.
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
        // ★ 8/3 "停止按钮无效" 的真因就在这一句的缺失。
        //
        // 整个 runOneRound (180~525 行) 里 ctx.ctrl 只出现过**一次** —— 274 行
        // 传给 streamChat 的 signal。tool 这一段一次都没查过 abort。于是:
        //
        //   · LLM 正在吐字   → 按停止, 立刻停 ✓ (signal 到了 streamChat)
        //   · LLM 正在跑 tool → 按停止, 毫无反应。而且这一轮 collectedToolCalls
        //     里剩下的每一个还会挨个发起、挨个 addMessage、挨个 persistMessage,
        //     全跑完才轮到外层 `if (ctrl.signal.aborted) break` 生效。
        //
        // 员工看到的就是: 点了没用, 再点还是没用, 界面继续往外冒东西 —— 而
        // isStreaming 一直是 true, 按钮还老老实实显示着"停止"。
        //
        // 为什么一直没暴露: 纯聊天场景按停止是好使的, 只有"停在 tool 上"才犯。
        // 会话越重 (跑 skill / 生成 Excel / browser 自动化) 越容易撞上, 而那种
        // 会话恰恰是最想中途叫停的。
        //
        // 已经发出去的那个 tool 停不掉 —— toolBridgeCallTool 走 Tauri rawInvoke,
        // invoke 没有取消机制, 只能等它返回。但"已发出的停不掉"不构成"后面
        // 没发的也照发"的理由, 这两件事被混为一谈了。
        if (ctx.ctrl.signal.aborted) {
          const cancelledCalls = (
            useChatStore.getState().messages.find((m) => m.id === assistantId)
              ?.tool_calls ?? []
          ).map((c) =>
            c.status === "pending" || c.status === "running"
              ? { ...c, status: "error" as const, error: "已取消 (你按了停止)" }
              : c,
          );
          updateMessage(assistantId, { tool_calls: cancelledCalls });
          break;
        }

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
            // BL-TOOLCALL-FAKE-OK (7/27 鸿波实盘): res.ok 只说明"调到了 tool 且没抛
            // 异常" —— tool 完全可以调用成功地告诉你它失败了:
            //   browser_navigate → {"success": false, "error": "Blocked: URL targets
            //                       a private or internal address"}
            // 老代码到这就标 ✓ 了。鸿波截图里两个 browser tool 全是绿勾、页面纹丝不动,
            // 我俩据此往 CDP / 页面渲染方向查了好几轮,真相一直写在返回值里被 UI 吞掉。
            const bizErr = detectToolBusinessError(resultStr);
            if (bizErr) {
              ok = false;
              errMsg = bizErr;
            }
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

      // 8/3: 被取消就不再续下一轮。
      //
      // 外层 send() 那句 `if (ctrl.signal.aborted) break` 也能拦住, 但那是
      // **下一轮开头**才判 —— 中间还会白跑一遍 setStreamingId / addMessage。
      // 更要紧的是: 依赖调用方在正确位置补一句检查, 是这个 bug 一开始就成立的
      // 前提。判断留在知道自己被取消的那一层。
      if (ctx.ctrl.signal.aborted) {
        return { shouldContinue: false, updatedMessages };
      }

      // tool_calls 处理完 → 必继续下一轮 LLM 推理(让 LLM 看 tool 结果)
      return { shouldContinue: true, updatedMessages };
}
