/** send —— 一次完整发送: 建 session / vision 切换 / 多轮 tool 循环 / 自动续跑 /
 *  持久化 / 队列续发。原 useChat.ts 190~614 行, 8/3 拆出。
 *
 * # 为什么拆
 *
 * 接着 runOneRound 那次拆。useChat.ts 里两个大闭包, 这是第二个 (425 行)。
 * 拆的理由跟上次同一条: 依赖靠闭包隐式捕获时, "少接了一个" 不产生任何信号。
 * 8/3 的停止按钮 bug 就是这么活下来的。
 *
 * # 依赖比看上去少 —— 这一点是数出来的, 不是估的
 *
 * 先按标识符粗数, send 体内像是引用了 messages(5) / model(8) / send(15)。
 * 剥掉注释再数, 真实情况差很远:
 *
 *   · messages     → 0 次。line 90 是 useChatStore.getState().messages,
 *                    line 146/147 是 detail.messages (局部变量)。跟 hook 那个
 *                    render 捕获的 messages 无关, 根本不是依赖。
 *   · model        → 1 次 (checkVisionSupport(model))。另两处都是
 *                    useChatStore.getState().model —— 5/28 那次 picker bug 之后
 *                    特意改成实时读的, 不能退回闭包值。
 *   · send         → 1 次, 在队列续发那里递归调自己。
 *
 * 所以 SendDeps 只有 10 项。如果按粗数的结果去传, 会平白多出两个 render 捕获
 * 的值, 反而制造新的 stale 面。
 *
 * # 自递归
 *
 * 队列续发 (`void send(head.text)`) 让 send 引用自己。这里作为 deps.send 显式
 * 传入 —— 调用方在 useChat 里把自己接回去。
 *
 * # 搬运方式
 *
 * 跟 runOneRound 一样: 函数体逐字未动, 依赖开头一次性解构。diff 只有头尾,
 * 中间 406 行可以逐字比对。
 */

import type { MutableRefObject } from "react";
import { invoke } from "@tauri-apps/api/core";
import { useChatStore } from "../../store/chat";
import {
  useAutoContinueStore,
  MAX_AUTO_CONTINUES,
  AUTO_CONTINUE_PROMPT,
} from "../../store/auto_continue";
import {
  createFirstTransportHandler,
  shouldPersistUserLocally,
} from "../../lib/chatPersistence";
import * as streamRegistry from "../../lib/streamRegistry";
import type { Attachment, ChatMessage } from "../../types/chat";
import { uuid, nowIso } from "./ids";
import { ensureTools } from "./toolsCache";
import { checkVisionSupport } from "./visionSwitch";
import type { RoundCtx, RoundResult } from "./runOneRound";

// 20 轮够用 — leadership-briefing skill 多附件场景一次成功的话只 1-3 轮 (拿
// schema + 真调). 如果模型 args 格式错循环, 也最多浪费 20 轮就停. 鸿波
// 2026-04-30 反馈 "10 轮总是踩到上限" 后调高.
const MAX_TOOL_ROUNDS = 20;

/** send 需要、但只有 hook 里才拿得到的东西。
 *
 * 数量是数出来的 (见文件头), 不是照着 useCallback 的 deps 数组抄的 —— 那个
 * 数组本身就漏过项 (runOneRound 那次漏了 appendToMessage / setLifecycleStatus)。*/
export interface SendDeps {
  /** render 捕获值: 进行中就早退 */
  isStreaming: boolean;
  /** render 捕获值: 只用于 checkVisionSupport。其余取模型的地方一律
   *  useChatStore.getState().model 实时读 (5/28 picker bug 的修法, 别退回来) */
  model: string;
  addMessage: (m: ChatMessage) => void;
  setIsStreaming: (v: boolean) => void;
  setStreamingId: (id: string | null) => void;
  setLifecycleStatus: (s: string | null) => void;
  ensureSessionId: () => Promise<string | null>;
  persistMessage: (msg: ChatMessage, sessionIdOverride?: string) => Promise<number | null>;
  runOneRound: (ctx: RoundCtx) => Promise<RoundResult>;
  abortRef: MutableRefObject<AbortController | null>;
  /** 队列续发要调自己 —— 调用方把 send 接回来 */
  send: (content: string, attachments?: Attachment[]) => Promise<void>;
}

export async function sendMessage(
  content: string,
  attachments: Attachment[] = [],
  deps: SendDeps,
): Promise<void> {
  // 逐字搬运: body 里仍然直接写 addMessage(...) / send(...) 等, 不加 deps. 前缀。
  const {
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
    send,
  } = deps;

      const trimmed = content.trim();
      // 文字+图片都为空才拒. 只发图(没文字)是允许的.
      if (!trimmed && attachments.length === 0) return;
      if (isStreaming) return;
      // 8/3: 清上一次取消的残留 (上一轮若在 finally 之外的路径退出)
      useChatStore.getState().setIsCancelling(false);

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
        // P3.5.140 (6/29 鸿波"不要再主动切 visionSwitch, 如果需要视觉选择的模型不支持,
        // 直接报错"): 老 maybeSwitchToVision 自动切被砍 — 严格 picker 军规一致.
        // 当前 picker 不支持视觉 → 显错 block send, 让员工自己 picker 切到视觉 model.
        const check = await checkVisionSupport(model);
        if (!check.ok) {
          const noticeMsg: ChatMessage = {
            id: uuid(),
            role: "assistant",
            content: check.error || "vision 检查失败",
            ts: nowIso(),
            status: "error",
            error: check.error || "vision 检查失败",
          };
          addMessage(noticeMsg);
          // 不 persistMessage — UI 提示性质, 不进 state.db
          // 短路返回, block send (老逻辑会硬发让上游 400 浪费一轮)
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
      // BL-CHAT-USER-PERSIST (7/18 鸿波 catch "切走再切回 user 气泡消失"): 6/21
      // P3.5.54 撤销 Companion 写 user msg · 假设 chat 走 hermes 8642 · hermes 独 write.
      // 但员工场景 hermes_api.enabled=false (Task #60) · chat 走 gateway 直连 (chat.ts:222) ·
      // hermes 8642 完全没参与 · user 消息**无人 write** · state.db 只 assistant.
      //
      // 铁证 (7/18): SELECT role FROM messages WHERE session_id LIKE '20260718_105516_%'
      //   → 5 条全 assistant · 用户发的 hi/hello/test 全丢.
      //
      // 修法: 不能再看 build-time config.useHermes — 实际通道由 chat.ts 在运行时
      // 综合 companion.yaml + API key 决定。streamChat 解析完成后通过
      // onTransportResolved 回报；Hermes 路径不写，gateway 直连才补写。
      // createFirstTransportHandler 让自动 retry 也只能触发一次判断。
      const onTransportResolved = createFirstTransportHandler((transport) => {
        // 8/13: 记下实际通道, 给 UI 显示降级提示。
        // hermes 不可达 / 没配 key 时 chat.ts 会**静默**回落直连网关, 此时没有
        // agent loop、没有工具、没有记忆注入。员工只会觉得"小鲶今天变笨了",
        // 界面从来没告诉过他。静默降级比降级本身更糟。
        useChatStore.getState().setTransport(transport);
        if (
          shouldPersistUserLocally(transport) &&
          sessionIdForStream
        ) {
          void persistMessage(userMsg, sessionIdForStream);
        }
      });
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
            const { getSession } = await import("../../lib/tauri");
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
            const { authWhoami } = await import("../../lib/tauri");
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
            onTransportResolved,
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
          useChatStore.getState().setIsCancelling(false);
          // P3.5.18 Phase 2: stream 结束 清 inline lifecycle status.
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
}
