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


import type { ToolCall } from "../types/chat";
import { fetchWithAuth } from "./me";
import { formatRecentOutputsFootnote } from "./drafts";
// 5/18 BL-CHAT-FALLBACK-MODEL-REVERT: fetchCatalog import 删了
// (老 BL-FIX45 B fallback 切模型用的). 删 import 防 tsc unused warning.
// P3.5.20.1 (6/17): applySteerPrefix 注释痕迹砍 — steer 整链退役.
// 5/19 BL-COMPANION-CHAT-SWITCH-TO-HERMES Phase 2-2B: hermes API server 路径配置
import { hermesApiConfigGet, hermesApiAuthHeader, authWhoami } from "./tauri";
// BL-CSP-PROXY (7/18 鸿波): hermes 8642 直连也走 Rust reqwest 代理, CSP 严格.
import { fetchViaProxy } from "./http_proxy";
import { prepareChatRequest } from "./chatRequest";

// 8/15: 5 个类型声明搬去 chat_types.ts。
//
// 这个文件 5/20 已经拆过一次 (wire format → chatWire.ts, 805 → 550), 三个月
// 又长回 934。长回来的不是新功能, 是注释 —— 几乎每个 if 上面都压着一段病历。
// 那些注释是这条链路最值钱的东西, 不为行数删, 所以按关注点再分一次。
//
// 类型先走: TS 的 interface 编译后完全消失, 零运行时风险。在一个 752 行、
// 一条测试都没有的主链路上, 先做零风险的那一半是合理的顺序。
export type {
  ChatStreamDoneInfo,
  ChatTransport,
  ChatUsage,
  OpenAITool,
  SendChatParams,
  ToolCallAcc,
} from "./chat_types";
import type {
  ChatUsage,
  SendChatParams,
  ToolCallAcc,
} from "./chat_types";

// 8/13 砍 TaskAssessment —— 见 hooks/chat/runOneRound.ts 那段说明。
// 一句话: 这条 SSE 产生在 hermes → gateway 之间, hermes 不转发, Companion 从来
// 没收到过; 而且 gateway 只看得见 agent loop 的一轮, 判据本身也在错的层。

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

  // 8/15: 拼请求 (URL / body / agentHeaders) 搬去 chatRequest.ts。
  //
  // 那是 streamChat 里唯一一段既够大 (136 行) 又**不含 return** 的。带 return
  // 的块搬不动 —— 那些 return 是从 streamChat 返回, 搬进独立函数就只是从新函数
  // 返回, 控制流全变。要搬得先把每个 return 改成"返回信号让调用方决定",
  // 那是重构不是搬运, 而这条链路一条测试都没有。逐块的 return 计数见
  // chatRequest.ts 的模块 docstring。
  //
  // ⚠ prepareChatRequest 有副作用: 它会调 markModelSent()。原来那行就在这个
  //   位置 (fetch 之前), 位置不能动 —— 见那边的说明。
  const { url, body, agentHeaders, effectiveModel } = await prepareChatRequest({
    useHermes,
    hermesCfg,
    model,
    messages,
    tools,
  });

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
        onDone({ finish_reason: "abort", usage });
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
            onDone({ finish_reason: "abort", usage });
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
      onDone({ finish_reason: "abort", usage });
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
