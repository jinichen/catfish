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
import {
  toolBridgeListTools,
  toolBridgeCallTool,
  sessionCreate,
  sessionMessageAppend,
  sessionFinalize,
  fetchCatalog,
} from "../lib/tauri";
import type { Attachment, ChatMessage, ToolCall } from "../types/chat";
import type { CatalogModel } from "../types/catalog";

// 20 轮够用 — leadership-briefing skill 多附件场景一次成功的话只 1-3 轮 (拿
// schema + 真调). 如果模型 args 格式错循环, 也最多浪费 20 轮就停 (跟 10 轮
// 体验上区别不大, 但给跨 skill 复杂任务留余地). 鸿波 2026-04-30 反馈"10 轮
// 总是踩到上限" 后调高.
const MAX_TOOL_ROUNDS = 20;

// 视觉模型 fallback 优先级 (从高到低)
//   1. 内网 Qwen3-VL (免费, 本地, 国产 OCR 强)
//   2. 公共 Qwen-Flash (256K 多模态, 付费, 备份)
//   3. 公共 Gemini Flash (快, 付费)
//   4. 公共 Gemini Pro (慢但强, 付费)
const VISION_MODEL_PREFERENCE = [
  "catfish-private-vision",
  "catfish-public-qwen-flash",
  "catfish-public-gemini-flash",
  "catfish-public-gemini-pro",
];

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
//
// 三重失效策略 (鸿波 2026-04-29 多次翻车后加):
//   1. TTL 60s — 时间到自动失效
//   2. 关键工具缺失 — cache 里没 KEY_TOOLS 任意一个就立即重拉 (装新工具后能自动感知)
//   3. _clearToolsCache() 显式清 — 切账号 / 重启 tool_bridge / 装新 skill 后调
//
// KEY_TOOLS: 如果 tool-bridge 装上了**任意一个**新关键工具但 cache 里没看到, 立即重拉.
// 这样 Companion 启动早期拉到 8 个老工具后, tool-bridge 装上 catfish_run_skill (第 9 个),
// 下次 chat 自动检测 cache 缺 catfish_run_skill → 重拉拿到 9 个 → 模型立即可见新工具.
// 不需要用户 Cmd+R.
const KEY_TOOLS = ["catfish_run_skill"];
let _cachedTools: OpenAITool[] | null = null;
let _cachedAt = 0;
const _TOOLS_CACHE_TTL_MS = 60_000;

function _cacheHasAllKeyTools(cached: OpenAITool[]): boolean {
  const names = new Set(cached.map((t) => t.function.name));
  return KEY_TOOLS.every((kt) => names.has(kt));
}

async function ensureTools(): Promise<OpenAITool[]> {
  const now = Date.now();
  if (
    _cachedTools !== null
    && now - _cachedAt < _TOOLS_CACHE_TTL_MS
    && _cacheHasAllKeyTools(_cachedTools)
  ) {
    return _cachedTools;
  }
  if (_cachedTools !== null && !_cacheHasAllKeyTools(_cachedTools)) {
    console.info(
      "[catfish chat] cache 里缺关键工具, 强制重拉 tool_bridge",
    );
  }
  try {
    const list = await toolBridgeListTools();
    const usable = list.filter((t) => t.available);
    // tool-bridge 给的 ToolInfo 是扁平: {name, description, input_schema, emoji, toolset, available}
    // OpenAI tools API 要求: {type:"function", function:{name, description, parameters}}
    // input_schema 仅对应 parameters 字段; 早期版本误把整个 input_schema 当 function 用了,
    // 结果发出去的 tool 没有 name 字段, OpenAI 兼容路径 (Qwen) 宽容能跑,
    // 但 Gemini 走 GoogleAIStudioGeminiConfig.map_openai_params 会 KeyError: 'name' 直接挂。
    const wire: OpenAITool[] = usable.map((t) => ({
      type: "function" as const,
      function: {
        name: t.name,
        description: t.description,
        parameters: (t.input_schema as Record<string, unknown>) ?? {
          type: "object",
          properties: {},
        },
      },
    }));
    _cachedTools = wire;
    _cachedAt = Date.now();
    console.info(
      `[catfish chat] 加载 ${wire.length}/${list.length} 个工具(${list.length - wire.length} 个不可用 toolset, TTL 60s)`,
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
  _cachedAt = 0;
}

// ─── 视觉模型自动选择 ───────────────────────────────────
//
// 员工带图发送时, 如果当前模型不支持视觉 (比如默认主力 deepseek-flash 是纯文本),
// 直接发上去会被上游丢图 / 报错. 我们做透明切换:
//   1. 拉 catalog 找 supports_vision=true 的模型
//   2. 按 VISION_MODEL_PREFERENCE 优先级挑第一个 reachable + api_key_configured 的
//   3. 改当前 store 的 model, 在聊天里追加一条 system 角色消息说"已切到 X"
//
// 失败兜底: catalog 拉不到或没视觉模型 → 用原模型硬发, 让上游报错员工自己决策

interface VisionSwitchResult {
  switched: boolean;
  /** 改后的 model id (没切就是原值) */
  newModel: string;
  /** 给员工看的提示 (没切就是 null) */
  notice: string | null;
}

async function maybeSwitchToVision(
  currentModel: string,
): Promise<VisionSwitchResult> {
  let models: CatalogModel[];
  try {
    const cat = await fetchCatalog();
    models = cat.models ?? [];
  } catch (e) {
    console.warn("[catfish chat] 拉 catalog 失败, 不切视觉模型:", e);
    return { switched: false, newModel: currentModel, notice: null };
  }

  const cur = models.find((m) => m.id === currentModel);
  // 当前模型已经支持视觉 → 不切
  if (cur && cur.supports_vision) {
    return { switched: false, newModel: currentModel, notice: null };
  }

  // visionPool: 只看 supports_vision + api_key_configured。
  // 故意不看 is_reachable —— catalog 那个字段是 30s/15s 缓存, 抖动会误杀;
  // 即使探测时不通, 实际请求时可能恰好通了, 不该提前 block 切换。
  // 真不通会在 LiteLLM 调用时报 ConnectionError, 那时 fallback chain 接管。
  const visionPool = models.filter(
    (m) => m.supports_vision && m.api_key_configured,
  );
  if (visionPool.length === 0) {
    return {
      switched: false,
      newModel: currentModel,
      notice:
        "⚠ 没有可用的视觉模型 (supports_vision=true 且配了 API key 的为空)。" +
        "检查 catfish-private-vision 配置, 或在 .env 配 GEMINI_API_KEY / DASHSCOPE_API_KEY 启用公共视觉模型。",
    };
  }

  for (const preferred of VISION_MODEL_PREFERENCE) {
    const m = visionPool.find((x) => x.id === preferred);
    if (m) {
      return {
        switched: true,
        newModel: m.id,
        notice: `🔁 检测到图片附件, 已切到「${m.display_name.split(" · ")[0] || m.id}」(原 ${cur?.display_name || currentModel} 不支持视觉)`,
      };
    }
  }

  // PREFERENCE 列表里都没匹配, 就用 visionPool 第一个
  const first = visionPool[0];
  return {
    switched: true,
    newModel: first.id,
    notice: `🔁 检测到图片附件, 已切到「${first.display_name}」(原模型不支持视觉)`,
  };
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

      // 关键: model 从 store snapshot 读, 不用闭包捕获的 — 因为 send() 里
      // maybeSwitchToVision 可能在这一轮之前刚切过模型, closure 里的 model 还是旧值。
      await streamChat({
        model: useChatStore.getState().model,
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
        onDone: (info) => {
          if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
          }
          flushPending();
          // BL-CONTEXT-COUNTER (5/13): 把 usage.prompt_tokens 写 store, 状态栏渲染
          if (info?.usage?.prompt_tokens != null) {
            useChatStore.getState().setLastPromptTokens(info.usage.prompt_tokens);
          }
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

      // 五一 sprint 5/2 修: streamChat 的 onError 已经把 status 设成 'error',
      // 这里别无条件覆盖回 'done', 否则 quota 超限 / 鉴权错 / 上游 503 等错误
      // UI 显不出来 (踩过坑).
      const currentStatus = useChatStore
        .getState()
        .messages.find((m) => m.id === assistantId)?.status;
      const isError = currentStatus === "error";

      // 把 tool_calls 挂到当前 assistant 消息上
      const finalAssistant: ChatMessage = {
        ...assistantMsg,
        content:
          useChatStore.getState().messages.find((m) => m.id === assistantId)
            ?.content || "",
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
    async (
      content: string,
      attachments: Attachment[] = [],
      // BL-HERMES013-RED-1B (5/13 ACP /steer): steer() 调 send 时挂这个 — 让
      // user msg 携带 _steered 标记, toWire 拼 [STEER] prefix 给 LLM 看.
      // UI bubble 看到这字段会显角标 "🎯 已插话改方向".
      // 普通 send / cancelAndSend / enqueue 都不传, 只有 steer() 传.
      metadata?: { steered?: { atContent: string } },
    ) => {
      const trimmed = content.trim();
      // 文字+图片都为空才拒. 只发图(没文字)是允许的.
      if (!trimmed && attachments.length === 0) return;
      if (isStreaming) return;

      // 0. 第一次 send 时 lazy create state.db session (持久化的开端)
      await ensureSessionId();

      // 0.5. 如果带图但当前模型不支持视觉 → 透明切到视觉模型
      //      切了的话往聊天里追加一条 system 提示, 让员工知道发生了啥.
      //      没视觉模型可切 → 给员工 error 消息, **abort 这次发送** —
      //      硬发 deepseek-flash + image_url 上游会 400, 浪费一轮还误导员工.
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
        // BL-HERMES013-RED-1B: 中途插话标记, 走 toWire 拼 STEER prefix
        _steered: metadata?.steered,
      };
      const requestMessages = [...useChatStore.getState().messages, userMsg];
      addMessage(userMsg);
      // 持久化到 state.db: 附件不落库 (图片 base64 / 文件 text 都太大), 只存文字 + 占位
      const imgN = attachments.filter((a) => a.kind === "image").length;
      const fileN = attachments.filter((a) => a.kind === "file").length;
      const placeholderParts: string[] = [];
      if (imgN > 0) placeholderParts.push(`📎 ${imgN} 张图`);
      if (fileN > 0) {
        const fileNames = attachments
          .filter((a) => a.kind === "file")
          .map((a) => a.name)
          .join(", ");
        placeholderParts.push(`📄 ${fileN} 份文档 (${fileNames})`);
      }
      const persistContent =
        placeholderParts.length > 0
          ? `${trimmed}${trimmed ? "\n" : ""}[${placeholderParts.join(" + ")} — in-memory, 切会话不保留]`
          : trimmed;
      void persistMessage({ ...userMsg, content: persistContent, attachments: undefined });
      setIsStreaming(true);

      // 2. 拉 tools(第一次会调 tool_bridge,后续走 cache)
      const tools = await ensureTools();

      const ctrl = new AbortController();
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

        for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
          if (ctrl.signal.aborted) break;

          const result = await runOneRound({
            ctrl,
            roundIdx: round,
            currentMessages,
            tools,
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
              void persistMessage(continueMsg);
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
        currentStreamIdRef.current = null;
        setStreamingId(null);
        setIsStreaming(false);
        abortRef.current = null;
        // BL-HERMES013-RED-1A (5/13 借鉴 Hermes 0.13 ACP /queue): 当前 stream
        // 完成后看 store.queue 有没排队消息. 有就 dequeue + 立即 send 下一条.
        // 用户体验: 长任务跑完无缝接下一个问题, 不用手动按 send.
        // 用 setTimeout 避免 React state 还没 flush 就 send (跟 cancelAndSend 同模式).
        const queue = useChatStore.getState().queue;
        if (queue.length > 0 && !ctrl.signal.aborted) {
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

  /** BL-HERMES013-RED-1B (5/13 借鉴 Hermes 0.13 ACP /steer): streaming 中
   *  *中途插话* — 用户看 LLM 在 stream 觉得方向不对, 想立刻改方向.
   *
   *  跟 cancelAndSend 区别: cancelAndSend 是"我不要这个回答了, 重新问", LLM
   *  看不到自己刚说的部分; steer 是"你思路不对, 我打断你, 但记得你刚说什么,
   *  综合两边继续", LLM 看到自己 partial output + 新指令.
   *
   *  跟 /queue 区别: /queue 是排队等当前完, /steer 是当前轮就改.
   *
   *  路径 A (5/13 22:55 鸿波拍板): 断流 + 续接.
   *    1. 拿当前 streaming 的 assistant message 已生成内容 (partial)
   *    2. abort 当前 SSE
   *    3. 等 200ms 让 finally cleanup (跟 cancelAndSend 同模式)
   *    4. send 新轮, 在 user msg 上挂 _steered: { atContent: partial }
   *       toWire 自动拼 "[STEER · 用户中途插话] (我打断你时你正说到 ...)" prefix
   *
   *  attachments 暂不支持 — steer 是文字插话场景, 加图通常该走 cancelAndSend.
   */
  const steer = useCallback(
    async (text: string) => {
      const t = text.trim();
      if (!t) return;

      // 1. 拿当前 streaming 的 partial content (UI 渲染中的 assistant content)
      const state = useChatStore.getState();
      const streamingId = state.streamingId;
      const partialContent = streamingId
        ? state.messages.find((m) => m.id === streamingId)?.content ?? ""
        : "";

      // 2. abort 当前 SSE — finally 块里 ctrl.signal.aborted 为 true,
      //    queue dequeue 自动短路 (跟 cancel 同语义).
      if (abortRef.current) abortRef.current.abort();

      // 3. 等 finally cleanup 跑完 (isStreaming → false)
      await new Promise((r) => setTimeout(r, 200));

      // 4. send 新轮, 携带 _steered metadata
      await send(t, [], { steered: { atContent: partialContent } });
    },
    [send],
  );

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
    cancelAndSend,  // BL-COMPANION-UX1 (5/12): 一键停止+发新消息, 解锁死感
    enqueue,        // BL-HERMES013-RED-1A (5/13): ACP /queue 等价, 排队下一条
    steer,          // BL-HERMES013-RED-1B (5/13): ACP /steer 等价, 中途插话
    reset,
  };
}
