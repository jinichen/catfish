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
import * as streamRegistry from "../lib/streamRegistry";
import {
  sessionCreate,
  sessionMessageAppend,
  sessionFinalize,
} from "../lib/tauri";
import type { Attachment, ChatMessage } from "../types/chat";


// 5/20 拆分: VISION_MODEL_PREFERENCE 移到 chat/visionSwitch.ts (跟 maybeSwitchToVision 一起)



// 5/20 拆 907 → ~700: tools cache + vision switch 抽到 chat/
export { _clearToolsCache } from "./chat/toolsCache";
import { runOneRound as runOneRoundImpl, type RoundCtx } from "./chat/runOneRound";
import { sendMessage } from "./chat/sendMessage";

// ─── 主 hook ───────────────────────────────────

export function useChat(_initialModel: string) {
  // 5/28 鸿波: initialModel 参数保留 (caller 仍传), 但 useChat 内部不再用 —
  // 原 line 130-137 render-time setModelInStore(initialModel) 已 disable
  // (那段是 picker 覆盖 bug 的根因, 任何 re-render 都把 store 拉回 catalog.default).
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  // 8/3: 按了停止但流还没停下来的窗口 — 见 store/chat.ts 的注释
  const isCancelling = useChatStore((s) => s.isCancelling);
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
  /** 单轮推理 —— 实现搬到 chat/runOneRound.ts (8/3 拆, 见那边文件头注释)。
   *  这里只负责把 hook 才拿得到的六个 store setter 接进去。 */
  const runOneRound = useCallback(
    (ctx: RoundCtx) =>
      runOneRoundImpl(ctx, {
        addMessage,
        updateMessage,
        appendToMessage,
        setStreamingId,
        setLifecycleStatus,
        persistMessage,
      }),
    [
      addMessage,
      updateMessage,
      appendToMessage,      // ← 原 deps 数组漏了这个
      setStreamingId,
      setLifecycleStatus,   // ← 和这个
      persistMessage,
    ],
  );

  /** 发送 —— 实现搬到 chat/sendMessage.ts (8/3 拆, 见那边文件头注释)。
   *  显式类型标注是必须的: body 里队列续发会调 send 自己, 不标注的话
   *  useCallback 的返回类型要靠自身推断, TS 会报 "隐式 any / 循环引用"。 */
  const send: (content: string, attachments?: Attachment[]) => Promise<void> =
    useCallback(
      (content, attachments = []) =>
        sendMessage(content, attachments, {
          isStreaming,
          model,
          addMessage,
          setIsStreaming,
          setStreamingId,
          setLifecycleStatus,
          ensureSessionId,
          persistMessage,
          runOneRound,
          abortRef,
          send: (c, a) => send(c, a),
        }),
      [
        isStreaming,
        model,
        addMessage,
        setIsStreaming,
        setStreamingId,
        setLifecycleStatus,   // ← 原 deps 数组漏了这个
        ensureSessionId,
        persistMessage,
        runOneRound,
      ],
    );

  const cancel = useCallback(() => {
    // 5/24 BL-MULTI-SESSION-STREAM: cancel 按 current session 走 registry.
    // 老逻辑 abortRef.current 是 "send 最后一次设的 controller", 用户切走会话
    // 后这个 ref 还指向后台老 stream, cancel 会误杀后台. 现在精准: 拿 store
    // current sessionId → registry.cancel(sessionId), 只动当前看的这条流.
    // 如果当前 session 没在 stream → no-op, abortRef fallback (no-session 情况下用).
    // 8/3: 先如实告诉界面"收到了"。abort() 本身是同步的, 但流真正停下来要等
    // 当前那个 tool 返回 (Tauri invoke 不可取消)。不置这个位, 员工点完看不出
    // 任何变化, 只能得出"按钮无效"的结论 —— 而它其实已经生效了。
    useChatStore.getState().setIsCancelling(true);
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

  /** BL-COMPANION-RESEND (7/23 达华 POC 催): 从指定 user msg 重发.
   *
   * 用户场景 · 员工发出一句话看到 AI 开始回 · 意识到自己那句问错了 · 想改主意.
   * 老 cancelAndSend 是"停当前 stream + 发**新一句**" · 但历史里错的那句还在 · 后
   * 续 LLM 又看着错句子上下文答 · 效果差. resend 是把错句**从历史里删** · 再发同款.
   *
   * 流程:
   *   1. 校验 · msg 存在 · role=user · 否则 fail-loud
   *   2. 拿 content + attachments 快照 (下一步 truncate 后 msg 就没了)
   *   3. cancel 当前 stream (若在 stream · abortRef abort) · wait 200ms 让 finally cleanup
   *   4. truncate 该 msg 及之后 (store.truncateFromMessage · 军规 id 不存在会 throw)
   *   5. send(content, attachments) 走完整发送路径 (新 uuid / 持久化 / vision 检 全走)
   *
   * ⚠ 不能"编辑内容后重发" · 那是 P1. 本 P0 只支持原样重发 (typo 场景员工自己删了输入
   * 框重打就是了 · 我们不做 UI 编辑).
   */
  const resendFromUserMsg = useCallback(
    async (id: string) => {
      const store = useChatStore.getState();
      const msg = store.messages.find((m) => m.id === id);
      if (!msg) {
        // 军规 fail-loud · UI stale id 应该崩
        throw new Error(`[resendFromUserMsg] msg id=${id} 不存在`);
      }
      if (msg.role !== "user") {
        throw new Error(`[resendFromUserMsg] 只能重发 role=user · 拿到 role=${msg.role}`);
      }
      const content = msg.content;
      const attachments = msg.attachments || [];

      // cancel 当前 stream (跟 cancelAndSend 同款 · 200ms 等 finally 清 isStreaming)
      if (abortRef.current) abortRef.current.abort();
      await new Promise((r) => setTimeout(r, 200));

      // truncate 该 msg 及之后 · send 会重新 append 新 uuid 的 user msg
      store.truncateFromMessage(id);

      await send(content, attachments);
    },
    [send],
  );

  /** BL-COMPANION-EDIT (7/23 达华 POC 催 · P1 续 P0 RESEND):
   *  编辑 user msg 内容后重发. P0 是原样重发 (typo 场景员工重打输入框),
   *  P1 是员工点 ✏️ 编辑 · 直接改 msg content · 确认后走跟 P0 同款流程 · 差异 · 用新 content.
   *
   * 流程 (跟 resendFromUserMsg 对称 · 只 content 换 newContent):
   *   1. 校验 · msg 存在 · role=user · 否则 fail-loud
   *   2. 校验 · newContent trimmed 非空 或 attachments 非空 · 否则 fail-loud
   *      (跟 send() 早退语义一致 · UI 层已 gate · 到这里说明 gate 漏了)
   *   3. attachments 从旧 msg 拿 (不动附件 · YAGNI. 员工要换图 · 删这句重发新的更清晰)
   *   4. cancel + 200ms + truncate + send(trimmed, attachments)
   *
   * ⚠ 不加 edited_at / 版本号. state.db 持久化天然记了每次 send · 需要审计从 DB 追.
   */
  const editAndResendUserMsg = useCallback(
    async (id: string, newContent: string) => {
      const store = useChatStore.getState();
      const msg = store.messages.find((m) => m.id === id);
      if (!msg) {
        throw new Error(`[editAndResendUserMsg] msg id=${id} 不存在`);
      }
      if (msg.role !== "user") {
        throw new Error(`[editAndResendUserMsg] 只能编辑 role=user · 拿到 role=${msg.role}`);
      }
      const trimmed = newContent.trim();
      const attachments = msg.attachments || [];
      if (!trimmed && attachments.length === 0) {
        // 军规 fail-loud · UI 应在 confirm 前 disable · 到这说明 gate 漏 · 别 silent
        throw new Error(`[editAndResendUserMsg] newContent 空且无附件 · 无内容可发`);
      }

      if (abortRef.current) abortRef.current.abort();
      await new Promise((r) => setTimeout(r, 200));

      store.truncateFromMessage(id);
      await send(trimmed, attachments);
    },
    [send],
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
    isCancelling,
    streamingId,
    model,
    setModel: setModelInStore,
    send,
    cancel,
    cancelAndSend,          // BL-COMPANION-UX1 (5/12): 一键停止+发新消息, 解锁死感
    enqueue,                // BL-HERMES013-RED-1A (5/13): ACP /queue 等价, 排队下一条
    resendFromUserMsg,      // BL-COMPANION-RESEND (7/23): 从错的 user msg 起重发
    editAndResendUserMsg,   // BL-COMPANION-EDIT (7/23 P1): 编辑 user msg 后重发
    // P3.5.20.1 (6/17): steer 砍 — BL-HERMES013-RED-1B 实测同效 cancelAndSend.
    reset,
  };
}
