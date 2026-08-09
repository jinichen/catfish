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
import { formatRecentOutputsFootnote } from "./drafts";
import { fetchIdentityBundle } from "./tauri";
import { useAgentStore } from "../store/agent";
import { useChatStore } from "../store/chat";
import { useTeachingStore } from "../store/teaching";
// 5/18 BL-CHAT-FALLBACK-MODEL-REVERT: fetchCatalog import 删了
// (老 BL-FIX45 B fallback 切模型用的). 删 import 防 tsc unused warning.
// P3.5.20.1 (6/17): applySteerPrefix 注释痕迹砍 — steer 整链退役.
// 5/19 BL-COMPANION-CHAT-SWITCH-TO-HERMES Phase 2-2B: hermes API server 路径配置
import { hermesApiConfigGet, hermesApiAuthHeader, authWhoami } from "./tauri";
// BL-CSP-PROXY (7/18 鸿波): hermes 8642 直连也走 Rust reqwest 代理, CSP 严格.
import { fetchViaProxy } from "./http_proxy";

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
  /** 请求真正选定的传输通道。必须由 streamChat 在完成动态配置解析后回报，
   *  caller 不得再用 build-time config 猜测，否则 App 和 Hermes 会同时写消息。 */
  onTransportResolved?: (transport: ChatTransport) => void;
  /** P3.5.18 Phase 2 (6/17 鸿波): hermes preflight 自动压缩 进度推 SSE.
   *  plugin.py P19 桥 agent.status_callback → tool_progress_callback(
   *    event_type="catfish.lifecycle.lifecycle|warn", tool_name="catfish-lifecycle", preview=msg).
   *  hermes SSE emit `hermes.tool.progress` 真`{tool: "catfish-lifecycle", label, status}`.
   *  Companion handle dispatch onLifecycle. text preview 字段 ("📦 Preflight compression...").
   *  status: "running" (push) or "completed" (清 inline).
   */
  onLifecycle?: (status: "running" | "completed", text: string) => void;
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
    /** P3.3.5 (6/9): hermes connection error 自动重试计数 (max 1).
     *  TypeError: Load failed → dispatch banner + sleep 5s + recursive 重发. */
    connRetry?: number;
    /** P3.5.34 修-A (6/18 鸿波 catch '对话框跑一半停下来 = 上游响应慢中断'):
     *  stream 90-180s 无 chunk → 主动 abort + retry 1 次. model-aware:
     *  catfish-private-* 给 180s (上游慢), 其它 90s. */
    idleRetry?: number;
    /** P3.5.34 修-D: stream 自然结束但 finish_reason 缺失 (= server 主动 close
     *  stream 没补 finish chunk) → 视同上游 timeout 切流, retry 1 次. */
    finishReasonRetry?: number;
  };
}

export type ChatTransport = "hermes" | "gateway";

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

/** P3.5.34 修-A (6/18 鸿波 catch '对话框跑一半停下来'): model-aware idle timeout 计算.
 *
 * stream idle (90-180s 无 chunk) → 主动 abort + retry 1 次. 不同 model 上游速度差大:
 *   - catfish-private-main / private-vision / private-coder: 内网模型, 40K context
 *     80-150s 单 call. 给 180s 容忍单 call. hermes 路径有 SSE keepalive 兜底, 不会真
 *     idle; gateway 路径无 keepalive, 180s 是上限.
 *   - 其它 (deepseek-flash / public qwen / gemini-pro 等): 快, 90s 足够.
 *
 * 抽 export 便于单测. 改动: 调用方 chat.ts 内一处, 老 inline 公式删.
 */
export function computeIdleTimeoutMs(model: string): number {
  // 7/30: 从 /catfish-private-(main|vision|coder)/ 改成认**前缀**。
  //
  // 原来枚举三个后缀, 而全代码库判断内外网用的都是前缀约定 catfish-private-
  // (PerfCard.tsx:262 / types/audit.ts:7 都是 startsWith)。两套判定并存,
  // 迟早对不上。
  //
  // 模型现在能在中央门户 /admin/models 界面上新增之后, 这个差别会真出事:
  // 客户加一个 catfish-private-glm (models.yaml 里正好有个注释掉的) 会落到
  // 90s 分支, 而上面注释自己写着内网模型单 call 要 80-150s —— 90s 会中途
  // abort, 症状正是本函数当初要修的那个 "对话框跑一半停下来" (P3.5.34)。
  //
  // 顺带: 枚举里的 coder 在 models.yaml 里根本不存在, 是预留了没建的名字。
  //
  // 今天没有行为变化 —— 现存 catfish-private-* 里除 main/vision 外只有
  // embed, 而 embedding 模型不走聊天流。改的是"以后加内网模型会不会踩坑"。
  return /^catfish-private-/i.test(model) ? 180_000 : 90_000;
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
    onTransportResolved,
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
  onTransportResolved?.(useHermes ? "hermes" : "gateway");

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

  // P3.5.2 (6/16 鸿波): 持久化 picker model 到 ~/.catfish/picker_state.json.
  //   catfish-memory plugin (in-hermes) sync_turn 读这个文件作为 summary model 来源,
  //   让 plugin 自动跟随 picker (绕过 hermes MemoryProvider API 没透传 picker 的限制).
  //   fire-and-forget, 失败静默, 不阻塞 chat send.
  void (async () => {
    try {
      const { savePickerState } = await import("./picker_state");
      savePickerState(effectiveModel);
    } catch (e) {
      console.warn("[chat] savePickerState import 失败 (静默):", e);
    }
  })();

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

  // BL-IDENTITY-INJECT-DECOUPLE (5/26): Companion 在员工 mac 读 SOUL/USER/memories
  // 6 字段, body 塞 _catfish_identity_bundle. gateway 解出后注入 system prompt,
  // pop 掉不 forward 给 upstream LLM. 失败 → 不挂 bundle, gateway fallback fs (dev OK,
  // SaaS 化后 fallback 永远空字符串, 鲶鱼退化无人格但服务不挂).
  //
  // 只 gateway 路径加 — hermes 自己有 identity 注入层, 不走 gateway 的 identity_inject.
  if (!useHermes) {
    try {
      const idBundle = await fetchIdentityBundle();
      // 任一字段非空 = 有内容. 全空 = 员工还没装 SOUL.md / Hermes 没写过 memory.
      const hasAny =
        idBundle.soul ||
        idBundle.soul_customer ||
        idBundle.soul_browser ||
        idBundle.soul_execute_code ||
        idBundle.user_memory ||
        idBundle.memory_dir;
      if (hasAny) {
        body._catfish_identity_bundle = idBundle;
      }
    } catch {
      // Tauri 命令挂 → 不挂 bundle, gateway fs fallback
    }
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

  // P3.5.34 修-A (6/18 鸿波 catch '对话框跑一半停下来 = 上游响应慢造成中断'):
  //   audit (chat.ts line 574-577): stream loop reader.read() while 循环 0 idle
  //   timeout, 上游卡死时客户端永远等. catfish-private-main 内网模型 80-150s/call,
  //   gateway 300s timeout 切流时**没发 finish_reason**, 客户端走 onDone path
  //   不抛 exception, UI 显 done 但 content 是 partial — 体感"跑一半".
  //   hermes 路径 (api_server.py:2235) 已有 SSE keepalive, 不会真 idle. gateway 路径
  //   没 keepalive — idle timer 真有用.
  // model-aware: catfish-private-* 慢 (40K context 80-150s 单 call), 给 180s; 其它 90s.
  const IDLE_TIMEOUT_MS = computeIdleTimeoutMs(effectiveModel);

  // P3.5.34 修-A: internal AbortController, 链上 caller signal. caller abort → internal abort.
  // idle timer 到时也 abort internal — catch e 通过 idleAborted 标志 distinguish.
  const internalCtrl = new AbortController();
  let idleAborted = false;
  if (signal) {
    if (signal.aborted) {
      internalCtrl.abort();
    } else {
      signal.addEventListener(
        "abort",
        () => internalCtrl.abort(),
        { once: true },
      );
    }
  }
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  const resetIdleTimer = () => {
    if (idleTimer !== null) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      idleAborted = true;
      internalCtrl.abort();
    }, IDLE_TIMEOUT_MS);
  };
  const clearIdleTimer = () => {
    if (idleTimer !== null) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  };

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
      // BL-CSP-PROXY (7/18): fetch → fetchViaProxy (Rust reqwest), CSP 严格. auto-detect
      // stream (URL /v1/chat/completions 走 event bridge).
      resp = await fetchViaProxy(url, {
        method: "POST",
        headers: hermesHeaders,
        body: JSON.stringify(body),
        // P3.5.34 修-A: 用 internalCtrl.signal 替 caller signal.
        // caller abort → internal abort (chained); idle timer 到也 abort internal.
        signal: internalCtrl.signal,
      });
    } else {
      resp = await fetchWithAuth(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...agentHeaders,
        },
        body: JSON.stringify(body),
        signal: internalCtrl.signal,
      });
    }
  } catch (e) {
    // P3.3.5 (6/9 鸿波): hermes connection error (TypeError: Load failed /
    // Failed to fetch / NetworkError) → dispatch banner event + auto retry 1 次
    // 5 秒后. hermes launchd KeepAlive 重启 + ThrottleInterval=300 兜底, 5s 通常
    // 够老 instance shutdown + 新 instance 接管.
    //
    // 真根因: hermes 内置 lark/weixin platform 在 DNS 失败时触发 sys.exit, launchd
    // 拉回 — 这段窗口 Companion fetch 必挂. 之前直接 onError 显红框, 用户得手动
    // 重发. 现在 banner + 自动重试一次, 用户体感 "卡 5-10s 后通了" 而不是红框.
    const isConnError =
      e instanceof TypeError &&
      /Load failed|Failed to fetch|NetworkError|ERR_CONNECTION/i.test(
        stringify(e),
      );
    if (isConnError && !params._retryCounters?.connRetry) {
      // 通知 UI 显重连 banner
      window.dispatchEvent(
        new CustomEvent("catfish:hermes-reconnecting", {
          detail: { attempt: 1, max: 1 },
        }),
      );
      await new Promise((r) => setTimeout(r, 5000));
      // 检查 signal 是否已 cancel (用户手动停了)
      if (signal?.aborted) {
        window.dispatchEvent(new CustomEvent("catfish:hermes-reconnect-end"));
        return;
      }
      // recursive retry, 标 connRetry=1 防死循环
      return streamChat({
        model,
        messages,
        tools,
        sessionId,
        onDelta,
        onToolCalls,
        onDone: (info) => {
          window.dispatchEvent(
            new CustomEvent("catfish:hermes-reconnect-end"),
          );
          onDone(info);
        },
        onError: (msg) => {
          window.dispatchEvent(
            new CustomEvent("catfish:hermes-reconnect-end"),
          );
          onError(msg);
        },
        signal,
        _retryCounters: { ...params._retryCounters, connRetry: 1 },
      });
    }
    // 真挂或非 connection error → onError 走老路
    if (isConnError) {
      window.dispatchEvent(new CustomEvent("catfish:hermes-reconnect-end"));
      onError(
        `无法连接 ${useHermes ? "hermes API" : "gateway"} (已自动重试 1 次仍失败). ` +
          `hermes 可能在重启中, 等几秒后手动重发. 详细: ${stringify(e)}`,
      );
    } else {
      onError(`无法连接 ${useHermes ? "hermes API" : "gateway"}: ${stringify(e)}`);
    }
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
      // BL-X (5/26): 附"过去 24h 鲶鱼已写文件" — 替代砍掉的 gateway recent_outputs inject
      const recentBlock = await formatRecentOutputsFootnote(24);
      onError(
        `⚠️ \`${model}\` 上游 ${resp.status} 不可达 (5s 后重试仍失败). ` +
          `上游 LLM 服务挂了, 你可以: (1) 换一个 model 重发 ` +
          `(2) 稍后再试 (3) 排查 gateway log + 上游 LLM 服务状态.` +
          (detail ? ` 详细: ${detail.slice(0, 200)}` : "") +
          recentBlock,
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

  // P3.5.34 修-D (6/18 鸿波 catch '对话框跑一半停下来'):
  //   stream 自然/[DONE] 结束但 finish_reason 缺失 (= server 主动 close stream
  //   没补 finish chunk, 例 hermes ConnectionReset / gateway upstream timeout 切流
  //   场景), 视同上游 timeout, retry 1 次.
  //   触发条件: 没 finish_reason + 没 tool_calls + 没用过该 counter.
  //   有 tool_calls 表示 LLM 已经决定调工具, 不该 retry (会重复 dispatch tool).
  //   audit 兼容性: hermes 正常完成 finish_reason="stop" (api_server.py:2257),
  //   不会误 trigger. gateway 路径正常完成上游 LLM 返 finish_reason, 也不会误 trigger.
  async function tryDoneOrRetry(): Promise<void> {
    clearIdleTimer();
    finalizeToolCallsIfAny();
    const hasToolCalls = Object.keys(toolCallsAcc).length > 0;
    const isAbnormalStop = !finishReason;
    if (
      isAbnormalStop &&
      !hasToolCalls &&
      !params._retryCounters?.finishReasonRetry
    ) {
      console.warn(
        "[chat] P3.5.34 修-D: finish_reason 缺失 + 无 tool_calls, 视同上游 timeout 切流, 3s 后 retry",
      );
      onDelta(
        `\n⚠️ 上游可能 timeout 切流 (无 finish_reason), 3 秒后自动重试一次 (这段时间按 ⏸ 取消)...\n`,
      );
      try {
        await new Promise<void>((resolve, reject) => {
          const t = setTimeout(resolve, 3000);
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
        // 员工 abort 等待期 → 走正常 abort 路径
        onDone({ finish_reason: "abort", usage, task_assessment: taskAssessment });
        return;
      }
      return streamChat({
        ...params,
        _retryCounters: {
          ...params._retryCounters,
          finishReasonRetry: 1,
        },
      });
    }
    onDone({
      finish_reason: finishReason,
      usage,
      task_assessment: taskAssessment,
      via_hermes: useHermes,
    });
  }

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

  // P3.5.34 修-A: stream 开始 → 启动 idle timer. 每次收到 chunk reset.
  resetIdleTimer();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // P3.5.34 修-A: 收到 chunk → 重置 idle timer (上游还在活).
      resetIdleTimer();
      buf += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);

        // P44 (6/5 鸿波 marathon audit): SSE message 可能跨多行 (event + data),
        // 老实现只看 data: 行 → hermes 发的 `event: hermes.tool.progress` event 被
        // silently 忽略. 现在先 scan 整 chunk 拿 event_type, 再 scan data.
        const lines = chunk.split("\n");
        let sseEventType: string | null = null;
        let sseData: string | null = null;
        for (const line of lines) {
          if (line.startsWith("event:")) {
            sseEventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            // 一个 message 内只取最后一个 data: (SSE spec 是多 data 拼接, 但我们的
            // hermes server 一个 message 只发一个 data 行, 拿最后一行 = 拿唯一)
            sseData = line.slice(5).trim();
          }
        }
        if (!sseData) continue;

        // 处理 hermes 自定义 event (line 2156 `event: hermes.tool.progress`).
        // payload 含 status: "approval_pending" 时, dispatch CustomEvent 让
        // ChatToolCall 弹 approval button. 其它 tool.progress 状态 (running/
        // completed) 暂时也 silently ignore (chat UI 走 OpenAI delta.tool_calls
        // 拼装, 不需要 hermes.tool.progress 这套额外协议).
        //
        // P3.5.18 Phase 2 (6/17 鸿波): tool === "catfish-lifecycle" 是 plugin P19
        // 桥 agent.status_callback → tool_progress_callback 的 marker. 这是 preflight
        // 压缩 / context warning 等 lifecycle event, 不是真tool call**. 走 onLifecycle
        // 让 useChat 设 inline status, 不进 tool_calls 列表 (avoid pollution).
        if (sseEventType === "hermes.tool.progress") {
          try {
            const ev = JSON.parse(sseData);
            if (ev?.tool === "catfish-lifecycle") {
              const status = ev?.status === "completed" ? "completed" : "running";
              const text = String(ev?.label ?? ev?.preview ?? "").trim();
              if (params.onLifecycle) {
                params.onLifecycle(status, text);
              }
            } else if (ev?.status === "approval_pending") {
              window.dispatchEvent(
                new CustomEvent("catfish:approval-pending", { detail: ev }),
              );
            }
          } catch {
            // ignore parse error
          }
          continue;
        }

        const data = sseData;
        {
          if (data === "[DONE]") {
            // P3.5.34 修-D: 走 helper, finish_reason 缺失时兜底 retry.
            await tryDoneOrRetry();
            return;
          }

          let parsed: unknown;
          try {
            parsed = JSON.parse(data);
          } catch {
            continue;
          }

          // 8/9: 两种形状都认。
          //   老: {"error": "人话文案"}                       (裸字符串)
          //   新: {"error": {"message": "人话文案", "type": …}} (OpenAI 标准)
          // 网关 8/9 改成了新形状 —— 因为 OpenAI 官方客户端 (hermes 用的那个)
          // 遇到裸字符串会把文案整个丢掉, 只抛一句 "An error occurred during
          // streaming"。两种都认是为了新旧网关 / 新旧 Companion 交叉组合都不瞎。
          const errField =
            typeof parsed === "object" && parsed !== null && "error" in parsed
              ? (parsed as { error: unknown }).error
              : undefined;
          if (typeof errField === "string") {
            onError(errField);
            return;
          }
          if (
            typeof errField === "object" &&
            errField !== null &&
            typeof (errField as { message?: unknown }).message === "string"
          ) {
            onError((errField as { message: string }).message);
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
    // 流自然结束(没 [DONE]):走 helper 兜底
    // P3.5.34 修-D: 兜底 finish_reason 缺失场景
    await tryDoneOrRetry();
  } catch (e) {
    clearIdleTimer();
    if ((e as Error).name === "AbortError") {
      // P3.5.34 修-A: 区分 idle abort vs caller abort.
      if (idleAborted) {
        // 上游卡了 IDLE_TIMEOUT_MS 没新 chunk → 自动 retry 1 次, 不显错给员工.
        if (!params._retryCounters?.idleRetry) {
          const idleSec = Math.round(IDLE_TIMEOUT_MS / 1000);
          console.warn(
            `[chat] P3.5.34 修-A: idle ${idleSec}s 无 chunk, 上游可能卡死, 3s 后 retry`,
          );
          onDelta(
            `\n⚠️ 上游 ${idleSec}s 无响应, 3 秒后自动重试一次 (这段时间按 ⏸ 取消)...\n`,
          );
          try {
            await new Promise<void>((resolve, reject) => {
              const t = setTimeout(resolve, 3000);
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
            onDone({ finish_reason: "abort", usage, task_assessment: taskAssessment });
            return;
          }
          return streamChat({
            ...params,
            _retryCounters: {
              ...params._retryCounters,
              idleRetry: 1,
            },
          });
        }
        // 已 retry 过 1 次还 idle → 诚实报错让员工拍
        const recentBlock = await formatRecentOutputsFootnote(24);
        onError(
          `⚠️ 上游响应过慢 (idle 重试 1 次仍无响应). 可以: (1) 换 model ` +
            `(2) 稍后再试 (3) 检查上游 LLM 服务${recentBlock}`,
        );
        return;
      }
      // caller abort (员工手动停)
      onDone({ finish_reason: "abort", usage, task_assessment: taskAssessment });
      return;
    }
    // BL-X (5/26): stream 中断 (网络挂 / fetch timeout / 上游切断) 时也带上"鲶鱼已写文件"
    const recentBlock = await formatRecentOutputsFootnote(24);
    onError(`stream 中断: ${stringify(e)}${recentBlock}`);
  }
}

function stringify(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
