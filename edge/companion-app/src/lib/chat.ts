/** 调 catfish-gateway /v1/chat/completions 的 streaming 客户端。
 *
 * 用 fetch + ReadableStream 解 SSE：
 *     data: {"choices":[{"delta":{"content":"..."}}]}\n\n
 *     data: {"choices":[{"delta":{"tool_calls":[...]}}]}\n\n
 *     data: [DONE]\n\n
 *
 * SOUL/memory 注入由 gateway middleware 自动处理（决策 3b），
 * 这里直接发 user messages 即可。
 *
 * tool calling: 客户端传 tools 参数,LLM 决定调哪个 → 我们执行 → 回传继续。
 */

import type { ChatMessage, ToolCall } from "../types/chat";
import { config } from "./env";
import { fetchWithAuth } from "./me";
import { useAgentStore } from "../store/agent";
import { useChatStore } from "../store/chat";
import { useTeachingStore } from "../store/teaching";
// 5/18 BL-CHAT-FALLBACK-MODEL-REVERT: fetchCatalog import 删了
// (老 BL-FIX45 B fallback 切模型用的). 删 import 防 tsc unused warning.
// 5/20 拆: applySteerPrefix 用在 chatWire.toWire 内部, 不再直接 import 这里
// 5/19 BL-COMPANION-CHAT-SWITCH-TO-HERMES Phase 2-2B: hermes API server 路径配置
import { hermesApiConfigGet, hermesApiAuthHeader, authWhoami } from "./tauri";

interface SendChatParams {
  model: string;
  messages: ChatMessage[];
  /** OpenAI tool calling 兼容的 tool 定义列表,空表示不带 tools */
  tools?: OpenAITool[];
  /** 5/23 BL-COMPANION-HERMES-SESSION-REUSE (鸿波): companion 已经 lazy create
   *  一个 state.db session_id, 通过 X-Hermes-Session-Id header 传给 hermes,
   *  hermes 复用而非新建 api-* session. 没传 → hermes 老行为, 自己 derive 一个.
   *  目的: 同一对话不再 2 个 session (companion source + api_server source).
   *  hermes 端已支持 (api_server.py:1188 provided_session_id).
   */
  sessionId?: string;
  /** 每个 token 来一次 */
  onDelta: (text: string) => void;
  /** LLM 决定调工具(stream 中 tool_calls 累积完毕)时触发 */
  onToolCalls?: (calls: ToolCall[]) => void;
  /** 流自然结束(finish_reason=stop / [DONE]) */
  onDone: (info?: ChatStreamDoneInfo) => void;
  /** 任何错误 */
  onError: (msg: string) => void;
  signal?: AbortSignal;
  /** BL-FIX45 (5/11) 内部递归用 — 错误恢复 retry 计数, 防死循环.
   *  外部调用方不应传, 仅 streamChat 自己 retry 时传.
   *
   *  fallback (DEPRECATED 5/18 BL-CHAT-FALLBACK-MODEL-REVERT): 老逻辑切别的
   *    model. 撤回. 字段保留作类型兼容, 新代码不读不写.
   *  401 reauth 已由 fetchWithAuth wrapper 内部处理, 这里不再计数 (BL-FIX45 A+ 5/11).
   *  upstreamFinalRetry (5/18 BL-CHAT-AUTO-RETRY-ON-UPSTREAM-500): 上游 500/502
   *    /503/504 时 sleep 5s 自动 retry **同 model** 一次 (上游间歇挂常 5s 内恢复).
   */
  _retryCounters?: {
    /** @deprecated 5/18 BL-CHAT-FALLBACK-MODEL-REVERT, 不再用 */
    fallback?: number;
    upstreamFinalRetry?: number;
  };
}

export interface OpenAITool {
  type: "function";
  function: {
    name: string;
    description?: string;
    parameters: Record<string, unknown>;
  };
}

export interface ChatUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface ChatStreamDoneInfo {
  finish_reason?: string;
  usage?: ChatUsage;
  /** BL-TASK-ASSESS (5/15): gateway 在 [DONE] 前多发一条 task_assessment 事件,
   *  Companion 用它做 promise-vs-reality 检测 (assistant 说"已生成"但
   *  cum_has_tool_call=false + 没真生成文件 → ⚠ 嘴炮). */
  task_assessment?: TaskAssessment;
  /** 5/23 BL-COMPANION-HERMES-SESSION-REUSE (鸿波): 本次 stream 是否走 hermes
   *  API server 路径. true → hermes 端已经往 state.db 写 assistant message,
   *  companion 不要再 sessionMessageAppend 写一遍 (会双写). false → 老
   *  gateway 路径, hermes 没参与, companion 必须自己写.
   */
  via_hermes?: boolean;
}

export interface TaskAssessment {
  /** OpenAI 兼容客户端忽略未知 object — Companion 嗅这字段拿 metadata */
  object: "task_assessment";
  model: string;
  finish_reason: string | null;
  /** 这一轮 stream 收到的 tool_calls chunk 总数 (不是单次 call 个数) */
  tool_call_count: number;
  /** 这一轮 assistant content 累计字符数 */
  content_chars: number;
  /** 这一轮是否累计有 tool_call (任何 1 个就 true) */
  cum_has_tool_call: boolean;
  /** skill_guard 是否在这次请求触发了铁律注入 */
  skill_guard_fired: boolean;
  /** 这条 session 历史里, agent 真调过 catfish_run_skill 没? */
  ever_called_catfish_run_skill_in_session: boolean;
}

/** Token 缓存 —— OAuth access_token / dev token 兜底.
 *
 * BL-FIX30 (5/9 鸿波诊断): chat.ts 之前完全不读 OAuth keychain, 真员工
 * OIDC 登录后 chat 还是用 dev_token, gateway 关掉 dev_token 通道后直接 401.
 * BL-FIX26 (me.ts) 同款修, chat 这条路漏了. 优先级跟 me.ts getToken 对齐:
 *   0. OAuth keychain access_token (登录员工真 token, 最优先)
 *   1. localStorage 切换器 override (dev 多账号测试)
 *   2. .env CATFISH_DEV_TOKEN (单 token 兜底, 现已注释 → 抛错跳到 3)
 *   3. fallback 'dev-token-local' (gateway 已拒, 触发 LoginGate 重登录)
 *
 * 五一 sprint 5/2 加多账号支持: 切换器选的 token (localStorage) 优先级高于 .env.
 */
// BL-FIX45 A+ (5/11): chat.ts 不再自己 getToken — 走 fetchWithAuth (me.ts).
// 5/12 (companion build 修 TS strict): 删 getToken + _cachedEnvToken +
// _clearTokenCache (整套已无 caller).


// 5/20 拆 805 → ~550: wire format (toWire + formatFileAttachment) 抽到 chatWire.ts
import { toWire } from "./chatWire";

// ── 流式 tool_calls 累积 ──
//
// LLM 流式返回 tool_calls 时, function.arguments 是 JSON 字符串, 分多个 chunk 来。
// 我们用 index 做 key, 累积成完整对象, 流结束时一次性 JSON.parse。
interface ToolCallAcc {
  id: string;
  name: string;
  argumentsJson: string;
}

export async function streamChat(params: SendChatParams): Promise<void> {
  const {
    model,
    messages,
    tools,
    sessionId,
    onDelta,
    onToolCalls,
    onDone,
    onError,
    signal,
  } = params;

  // 5/19 BL-COMPANION-CHAT-SWITCH-TO-HERMES Phase 2-2B: 切 hermes API server
  // (端口 8642) 灰度路径. 跟 BL-MEMORY-OWNERSHIP-FIX 对齐 — hermes 当 agent
  // runtime, 内部 memory inject (含 catfish-memory plugin) + tool calling +
  // 调下游 gateway. Companion 退化成纯 UI client.
  //
  // 灰度逻辑: hermes_api.enabled=true → 走 hermes 8642; false → 走老 gateway.
  // 配置在 ~/.catfish/companion.yaml `hermes_api:` 段, 详见
  // docs/HERMES-OPENAI-SERVER-RESEARCH.md + COMPANION-HERMES-AUTH-DESIGN.md.
  //
  // 安全: hermes auth header 由 Rust 端拼 ("Bearer <API_SERVER_KEY>"), JS 只
  // 看到组合好的字符串, raw key 不暴露.
  let hermesCfg: { enabled: boolean; url: string; has_key: boolean } | null = null;
  let hermesAuth: string | null = null;
  // BL-AUTH-DECOUPLE-A3 (5/19): hermes 路径要显式把当前员工 email 通过
  // X-Catfish-User header 传给 hermes (hermes 再透传给 gateway). 不再依赖
  // hermes 的 HERMES_DEFAULT_USER env fallback — 真上多租户时 env 单值挂不住.
  // authWhoami 直接读 keychain OAuth token 解出来的 email, 一次 IPC ~1ms.
  let userEmail: string | null = null;
  try {
    hermesCfg = await hermesApiConfigGet();
    if (hermesCfg.enabled && hermesCfg.has_key) {
      hermesAuth = await hermesApiAuthHeader();
      try {
        const who = await authWhoami();
        if (who.authenticated && who.email) userEmail = who.email;
      } catch {
        // whoami 挂 (keychain 没 token / OIDC 异常) → 不带 header, 让 hermes
        // 拒 401 (A1+A2 已要求 service-sub token 必带 X-Catfish-User).
      }
    }
  } catch {
    // Tauri 命令挂 — 走老 gateway 路径 (灰度安全降级)
  }
  const useHermes = hermesCfg !== null && hermesCfg.enabled && hermesAuth !== null;

  const url = useHermes
    ? `${hermesCfg!.url}/v1/chat/completions`
    : `${config.gatewayUrl}/v1/chat/completions`;

  // BL-COMPANION-MODEL-SELECTOR-HERMES-SYNC (5/19): 两边都传真 model 名.
  //   - 老 gateway 一直按 body.model 路由.
  //   - hermes API server 现在也接受 body.model 作为单次 override (api_server.py
  //     _create_agent.model_override); 留空 / "hermes-agent" / 当前 profile 名时
  //     回落到 config.yaml model.default, 不影响 Open WebUI 等老 OpenAI 客户端.
  // 之前写死成 "hermes-agent" 导致 UI 选的 model 被吞, hermes 全跑 config 默认
  // (deepseek-flash) — 40K context 不稳, 一调 skill 就 streaming error.
  const effectiveModel = model;

  const body: Record<string, unknown> = {
    model: effectiveModel,
    messages: toWire(messages),
    stream: true,
  };
  // 5/19 切 hermes 后**不再传 tools** — hermes 内部管 tool calling, 拼好结果返.
  // 老 gateway 路径仍传 tools.
  if (!useHermes && tools && tools.length > 0) {
    body.tools = tools;
    // tool_choice 默认 auto, 让 LLM 自己决定要不要调
  }

  // BL-E11 命名权: 把当前员工自定义的 agent name + personality 带过去, gateway
  // 拼 personalization preamble 在 SOUL 前面 (非默认值才发, 省 header 大小).
  const agentSnap = useAgentStore.getState();
  const agentHeaders: Record<string, string> = {};
  if (agentSnap.loaded) {
    if (agentSnap.name && agentSnap.name !== "小鲶" && agentSnap.name !== "Catfish") {
      agentHeaders["X-Catfish-Agent-Name"] = agentSnap.name;
    }
    if (agentSnap.personality && agentSnap.personality !== "gentle") {
      agentHeaders["X-Catfish-Agent-Personality"] = agentSnap.personality;
    }
  }

  // BL-LEAN-SESSION (5/13 鸿波拍板 "客户无法跑命令行"): 教学模式 toggle 开时
  // 带 X-Catfish-Teaching-Mode: 1, gateway 关 9 个干扰 inject + feedback retry.
  // 关时不带 header, 走完整注入 (副手"懂员工"). 跨 session 隔离, 不重启 gateway.
  //
  // 5/18 BL-COMPANION-VITE-CHUNK-WARN: 改 static import (顶部) — useAgentStore 已经
  // 顶部 static 进来了, teaching 也 static 没有循环依赖风险, 老 dynamic 注释 (zustand
  // 加载顺序) 是历史防御性代码, 实际不需要. Rollup 见 dynamic+static 混用挂 chunk warn.
  if (useTeachingStore.getState().on) {
    agentHeaders["X-Catfish-Teaching-Mode"] = "1";
  }

  // BL-GATEWAY-SOFT-HANDOFF (5/18): 切 model 后下一次请求带 X-Catfish-Prev-Model,
  // gateway 据此把历史 tool_calls 转 inline 文本 (新 model 不支持 tools 时), 防炸.
  // 同 model 续聊 → prevSentModel === current model, gateway 看相同就 no-op.
  // 没发过任何消息 (prevSentModel === null) → 不发 header, 跟老行为一样.
  const _prevSent = useChatStore.getState().prevSentModel;
  if (_prevSent && _prevSent !== model) {
    agentHeaders["X-Catfish-Prev-Model"] = _prevSent;
  }
  // 发完后 (无论成功 / 失败) 更新 prevSentModel = 本次 model, 下一次发送对比基准.
  // 放在最早 — 即使 fetch 抛错, 下次再发也能继续追踪 (老 model 已经"用过"了).
  useChatStore.getState().markModelSent();

  // BL-FIX45 A+ (5/11): 走 fetchWithAuth — 401 自动 reauth + retry, 不再 inline 处理.
  // Authorization header 由 wrapper 自动加.
  //
  // 5/19 Phase 2-2B: hermes 路径**不走 fetchWithAuth** (它假设 OIDC token 401
  // 后 reauth, 但 hermes 用 API_SERVER_KEY 静态 token, 401 reauth 没意义). 改
  // 直接 fetch + hermes auth header (Rust 端拼好的 "Bearer <key>").
  let resp: Response;
  try {
    if (useHermes) {
      const hermesHeaders: Record<string, string> = {
        "Content-Type": "application/json",
        Authorization: hermesAuth!,
        ...agentHeaders,
      };
      // BL-AUTH-DECOUPLE-A3 (5/19): 真员工 email 走 X-Catfish-User. hermes
      // OpenAI server 解 (依赖 X-Catfish-User, 不再回退到 HERMES_DEFAULT_USER env).
      if (userEmail) {
        hermesHeaders["X-Catfish-User"] = userEmail;
      }
      // 5/23 BL-COMPANION-HERMES-SESSION-REUSE (鸿波): 传 companion 创建的
      // session id, hermes 收到就复用, 不再 derive api-* 新 session.
      // 解决 "1 个对话 → state.db 出 2 条 session (companion + api_server)" 的双开.
      if (sessionId) {
        hermesHeaders["X-Hermes-Session-Id"] = sessionId;
      }
      resp = await fetch(url, {
        method: "POST",
        headers: hermesHeaders,
        body: JSON.stringify(body),
        signal,
      });
    } else {
      resp = await fetchWithAuth(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...agentHeaders,
        },
        body: JSON.stringify(body),
        signal,
      });
    }
  } catch (e) {
    onError(`无法连接 ${useHermes ? "hermes API" : "gateway"}: ${stringify(e)}`);
    return;
  }

  if (!resp.ok) {
    // Quota 超限 (429): gateway 返 detail.message 已是 friendly 中文话术
    // ("你今日 Pro 用满了, 切到 catfish-private-main 继续").
    // 直接展示, 不再加 'HTTP 429:' 前缀污染 UX.
    if (resp.status === 429) {
      let friendlyMsg = "Quota 超限. 切到 catfish-private-main (内网模型) 继续聊.";
      try {
        const errJson = await resp.json();
        const d = errJson?.detail;
        if (typeof d === "object" && d?.message) {
          friendlyMsg = d.message;
        } else if (typeof d === "string") {
          friendlyMsg = d;
        }
      } catch {
        /* keep default */
      }
      onError(`⚠️ ${friendlyMsg}`);
      return;
    }

    // ── BL-FIX45 A+ (5/11): 401 由 fetchWithAuth wrapper 自动 reauth + retry ──
    // 走到这里说明 wrapper retry 一次仍 401 — IdP 真挂或员工取消登录.
    if (resp.status === 401) {
      onError(
        "🔐 登录刷新后仍 401. 可能 IdP 不可达 / SSO 配置错 / 你关闭了浏览器登录窗. " +
          "检查 catfish-identity 服务或点右上角头像手动登录."
      );
      return;
    }

    // ── 5/18 BL-CHAT-FALLBACK-MODEL-REVERT: 500/502/503/504 单 model 重试 ─
    //
    // 老逻辑 (BL-FIX45 B 5/11): 上游 500 → 静默 fallback 切到 catalog 里下一个
    // 可达 model 最多 2 次. **撤回**, 跟 5/13 BL-FIX23/24 "gateway 不替员工做主"
    // 同精神:
    //   1. 你选 Qwen 因为它中文/国产/合规, 切到 nemotron / gemini 答案质量 +
    //      合规属性都变了, 你不知道. 公司可能配"只用国产 model", 切别的违反 RBAC
    //   2. 用户语义违背: 选了 model = "我要这个", 不是"任何能用的 model"
    //   3. quality 降级不可预测, 5/13 BL-FIX23 撤回的同型问题
    //
    // 新逻辑: 500 → sleep 5s → retry **同 model** 一次. 上游 LLM (Qwen vLLM /
    // DeepSeek) 间歇性挂常 5s 内恢复. retry 仍挂 → 诚实报错让用户决定 (换 model
    // 或稍后再试). upstreamFinalRetry 限 1 次, signal abort 能取消.
    if ([500, 502, 503, 504].includes(resp.status)) {
      const finalRetryCount = params._retryCounters?.upstreamFinalRetry ?? 0;
      // BL-C8 (5/16): 优先用 backend errors.py friendly_upstream_error 翻译过的中文短文案
      let detail = "";
      try {
        const errJson = await resp.json();
        detail =
          errJson?.detail?.friendly ||
          errJson?.detail?.message ||
          JSON.stringify(errJson);
      } catch {
        detail = await resp.text().catch(() => "");
      }

      if (finalRetryCount < 1) {
        onDelta(
          `\n⏳ \`${model}\` 上游 ${resp.status} 暂时不可达, ` +
            `5 秒后自动重试一次 (这段时间按 ⏸ 取消)...\n`,
        );
        try {
          await new Promise<void>((resolve, reject) => {
            const t = setTimeout(resolve, 5000);
            if (signal) {
              const onAbort = () => {
                clearTimeout(t);
                reject(new Error("aborted"));
              };
              if (signal.aborted) onAbort();
              else signal.addEventListener("abort", onAbort, { once: true });
            }
          });
        } catch {
          onError(`⚠️ \`${model}\` 上游 ${resp.status} 不可达. 用户取消自动重试.`);
          return;
        }
        return streamChat({
          ...params,
          _retryCounters: {
            ...params._retryCounters,
            upstreamFinalRetry: 1,
          },
        });
      }

      // retry 仍失败 → 诚实报错, 不替员工做主切别的 model
      onError(
        `⚠️ \`${model}\` 上游 ${resp.status} 不可达 (5s 后重试仍失败). ` +
          `上游 LLM 服务挂了, 你可以: (1) 换一个 model 重发 ` +
          `(2) 稍后再试 (3) 排查 gateway log + 上游 LLM 服务状态.` +
          (detail ? ` 详细: ${detail.slice(0, 200)}` : ""),
      );
      return;
    }

    // BL-C8 (5/16): 底层 fallback 同样优先 backend friendly 翻译.
    let detail = "";
    try {
      const errJson = await resp.json();
      detail =
        errJson?.detail?.friendly ||
        errJson?.detail?.message ||
        JSON.stringify(errJson);
    } catch {
      detail = await resp.text().catch(() => "");
    }
    onError(`HTTP ${resp.status}: ${detail || "unknown"}`);
    return;
  }
  if (!resp.body) {
    onError("响应没有 stream body");
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let usage: ChatUsage | undefined;
  let finishReason: string | undefined;
  // BL-TASK-ASSESS-2-CLIENT: gateway 在 [DONE] 前发 task_assessment 事件,
  // Companion 缓存在 stream loop 内, 在 onDone 时一起传给 caller.
  let taskAssessment: TaskAssessment | undefined;
  // index → ToolCallAcc
  const toolCallsAcc: Record<number, ToolCallAcc> = {};

  function finalizeToolCallsIfAny() {
    const indices = Object.keys(toolCallsAcc).map(Number).sort((a, b) => a - b);
    if (indices.length === 0) return;

    const finalized: ToolCall[] = [];
    for (const idx of indices) {
      const acc = toolCallsAcc[idx];
      if (!acc.id || !acc.name) continue;
      let args: Record<string, unknown> = {};
      if (acc.argumentsJson.trim()) {
        try {
          args = JSON.parse(acc.argumentsJson);
        } catch (e) {
          // 参数解析失败 — 把原始字符串塞进去让 caller 知道
          finalized.push({
            id: acc.id,
            name: acc.name,
            args: { _raw: acc.argumentsJson, _parse_error: String(e) },
            status: "error",
            error: `参数 JSON 解析失败: ${e}`,
          });
          continue;
        }
      }
      finalized.push({
        id: acc.id,
        name: acc.name,
        args,
        status: "pending",
      });
    }
    if (finalized.length > 0 && onToolCalls) {
      onToolCalls(finalized);
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);

        const lines = chunk.split("\n");
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (!data) continue;
          if (data === "[DONE]") {
            finalizeToolCallsIfAny();
            onDone({ finish_reason: finishReason, usage, task_assessment: taskAssessment, via_hermes: useHermes });
            return;
          }

          let parsed: unknown;
          try {
            parsed = JSON.parse(data);
          } catch {
            continue;
          }

          if (
            typeof parsed === "object" &&
            parsed !== null &&
            "error" in parsed &&
            typeof (parsed as { error: unknown }).error === "string"
          ) {
            onError((parsed as { error: string }).error);
            return;
          }

          // BL-TASK-ASSESS-2-CLIENT: 嗅 task_assessment 事件 (object 字段标记).
          // OpenAI 兼容客户端看到 unknown object 一般忽略, Companion 拿来做断言.
          if (
            typeof parsed === "object" &&
            parsed !== null &&
            (parsed as { object?: unknown }).object === "task_assessment"
          ) {
            taskAssessment = parsed as TaskAssessment;
            continue; // task_assessment 事件不走后面的 choices 解析
          }

          const obj = parsed as {
            choices?: Array<{
              delta?: {
                content?: string;
                // BL-FIX23 L2 (5/9): Qwen3.5 / DeepSeek thinking / Claude Sonnet 4.5+
                // 推理模式 stream 时 delta.content=null, delta.reasoning_content="...".
                // 之前只看 content 导致鲶鱼"半截就停" — 思考阶段全丢, 员工只看到
                // 开头 plan 句被截. 现在落到 onDelta 一起渲染 (后续 BL-FE3 区分思考).
                reasoning_content?: string;
                tool_calls?: Array<{
                  index?: number;
                  id?: string;
                  type?: string;
                  function?: { name?: string; arguments?: string };
                }>;
              };
              finish_reason?: string | null;
            }>;
            usage?: ChatUsage;
          };
          if (obj.usage) usage = obj.usage;
          const choice = obj.choices?.[0];
          if (!choice) continue;

          if (choice.finish_reason) finishReason = choice.finish_reason;

          const delta = choice.delta;
          if (delta?.content) onDelta(delta.content);
          // BL-FIX23 L2 (5/9): reasoning_content 也渲染, 防"半截就停"的核心修法.
          // 鸿波 5/9 抱怨 Qwen3.5 122B 长 context 推理时输出 reasoning_content 不是
          // content, Companion 只读 content 导致看着像"嘴说完话停了". 先让员工看到
          // 思考过程, 不丢内容. demo 后 BL-FE3 加折叠 UI 把思考分开展示.
          if (delta?.reasoning_content) onDelta(delta.reasoning_content);

          // 累积 tool_calls 块
          if (delta?.tool_calls) {
            for (const tc of delta.tool_calls) {
              const i = tc.index ?? 0;
              if (!toolCallsAcc[i]) {
                toolCallsAcc[i] = { id: "", name: "", argumentsJson: "" };
              }
              if (tc.id) toolCallsAcc[i].id = tc.id;
              if (tc.function?.name) toolCallsAcc[i].name = tc.function.name;
              if (tc.function?.arguments) {
                toolCallsAcc[i].argumentsJson += tc.function.arguments;
              }
            }
          }
        }
      }
    }
    // 流自然结束(没 [DONE]):也 finalize
    finalizeToolCallsIfAny();
    onDone({ finish_reason: finishReason, usage, task_assessment: taskAssessment, via_hermes: useHermes });
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      onDone({ finish_reason: "abort", usage, task_assessment: taskAssessment });
      return;
    }
    onError(`stream 中断: ${stringify(e)}`);
  }
}

function stringify(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
