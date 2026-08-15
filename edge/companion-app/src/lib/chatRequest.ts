/** 拼这一次 chat 请求要发的东西 —— URL / body / 自定义 header。
 *
 * 8/15 从 chat.ts 的 streamChat 里抽出来 (那个函数 752 行)。
 *
 * # 只抽了这一段, 为什么
 *
 * streamChat 里逐段数过 return / throw:
 *
 *     参数 + hermes 探测      75 行   0 return   ← 能抽, 但设了一堆后面要用的局部
 *     **拼请求 (本文件)**    136 行   0 return   ← 抽了
 *     idle timer 装配         32 行   0 return   ← 产出的是闭包, 抽出去更绕
 *     fetch + catch          104 行   3 return   ✗
 *     !resp.ok 错误映射       115 行   6 return   ✗
 *     reader + 内部 helper    107 行   4 return   ✗
 *     读流循环 + catch        208 行   7 return   ✗
 *
 * 带 return 的块**不能整块搬** —— 那些 return 是从 streamChat 里返回, 搬到
 * 独立函数里就只是从新函数返回, 控制流完全变了。要搬得先把每个 return 改成
 * "返回一个信号, 让调用方决定要不要 return", 那是**重构**不是搬运。
 *
 * 而 streamChat 一条测试都没有 (chat.test.ts 只测 computeIdleTimeoutMs)。
 * 在主聊天链路上、没有网的情况下改控制流, 不干。
 *
 * # ⚠ 这个函数有副作用: markModelSent()
 *
 * 名字叫"拼请求"却会写 store, 这不好看, 但**是原样搬过来的, 位置一个不差**。
 *
 * 原注释解释了为什么放在这么早: 「放在最早 —— 即使 fetch 抛错, 下次再发也能
 * 继续追踪 (老 model 已经"用过"了)」。它服务的是 X-Catfish-Prev-Model 那条
 * soft handoff: 切模型后下一次请求要告诉网关上一次用的是谁, 网关据此把历史
 * tool_calls 转成 inline 文本, 防新模型不支持 tools 时炸。
 *
 * 挪到 fetch 之后就会变成"发失败的那次不算用过", 下一次的对比基准就错了。
 * 所以位置不能动, 只能把副作用写在文档里。
 */
import { config } from "./env";
import { fetchIdentityBundle } from "./tauri";
// 5/20 拆 805 → ~550: wire format (toWire + formatFileAttachment) 抽到 chatWire.ts
import { toWire } from "./chatWire";
import { useAgentStore } from "../store/agent";
import { useChatStore } from "../store/chat";
import { useTeachingStore } from "../store/teaching";
import type { ChatMessage } from "../types/chat";
import type { OpenAITool } from "./chat_types";

export interface PreparedChatRequest {
  url: string;
  body: Record<string, unknown>;
  agentHeaders: Record<string, string>;
  /** 实际发给上游的 model 名。目前等于传入的 model, 保留这个名字是因为
   *  调用方拿它去算 idle timeout, 而"发出去的那个"才是该看的那个。 */
  effectiveModel: string;
}

/** ⚠ 有副作用: 会调 useChatStore.markModelSent()。见模块 docstring。 */
export async function prepareChatRequest(opts: {
  useHermes: boolean;
  hermesCfg: { enabled: boolean; url: string; has_key: boolean } | null;
  model: string;
  messages: ChatMessage[];
  tools?: OpenAITool[];
}): Promise<PreparedChatRequest> {
  const { useHermes, hermesCfg, model, messages, tools } = opts;

  // 8/15: 给员工聊天也打来源标记。
  //
  // 病: 网关账本 (gateway_audit) 里聊天一直是「(无标记)」。8/15 查配额时,
  // 10:30 之后 94% 的 token 落在这一栏 —— 一轮对话在 agent loop 里展开成
  // 20 次网关调用、97 万输入 token, 而只有第 1 次带得上归属, 后面十几次
  // 既没有 source 也丢了 user, 全记在 client:hermes-cli 名下。
  // 于是"钱花在哪"这个问题, 最大的一块答不上来。
  //
  // 这跟 plugin.py 里 P41 描述的是同一个病: 「advisor Call 1 和员工聊天在
  // 网关日志里完全无法区分」—— 当时排查早安卡死, 按 source grep 连错两次方向。
  //
  // 用 query 不用 header: 5/21 BL-CORS-DEBT-FIX 已经踩过 —— hermes proxy 8642
  // 的 CORS allow-list 没配 X-Catfish-* , preflight 直接拒。网关和 plugin 的
  // middleware 都是 header 优先、query 兜底, 所以 query 一样认。
  //
  // ⚠ 加这个标记**必须**同时给 P42 记忆闸开口子 (plugin_memory_gate.py 的
  // INTERACTIVE_SOURCES)。那道闸的判据是"有 source 就跳记忆", 前提正是
  // 「员工聊天不设 source」。不同步改的话, 聊天从此不进记忆, 而且
  // 按它自己 docstring 的说法这种失效"现场根本看不出来"。
  //
  // 其余按 source 分叉的地方都查过, 不受影响:
  //   · SOUL identity inject —— 看的是 catfish_skip_identity, 不是 source
  //     (briefing.ts:59 那句注释把两者混了, 别照着推断)
  //   · 工具裁剪 sanitize_tools —— source 不在 _SOURCE_TOOL_PROFILES 表里
  //     就整份不过滤 (tools_sanitizer.py:135)
  //   · P44 服务式瘦身 —— 白名单只有 email-scheduler / phishing-scan 两项
  const CHAT_SOURCE_QUERY = "?catfish_source=companion-chat";
  const url = useHermes
    ? `${hermesCfg!.url}/v1/chat/completions${CHAT_SOURCE_QUERY}`
    : `${config.gatewayUrl}/v1/chat/completions${CHAT_SOURCE_QUERY}`;

  // BL-COMPANION-MODEL-SELECTOR-HERMES-SYNC (5/19): 两边都传真 model 名.
  //   - 老 gateway 一直按 body.model 路由.
  //   - hermes API server 现在也接受 body.model 作为单次 override (api_server.py
  //     _create_agent.model_override); 留空 / "hermes-agent" / 当前 profile 名时
  //     回落到 config.yaml model.default, 不影响 Open WebUI 等老 OpenAI 客户端.
  // 之前写死成 "hermes-agent" 导致 UI 选的 model 被吞, hermes 全跑 config 默认
  // (deepseek-flash) — 40K context 不稳, 一调 skill 就 streaming error.
  const effectiveModel = model;

  // P3.5.2 (6/16) 原本在这里"每次 send 前写 picker_state.json", 8/10 删 —— 这里
  // 不该写盘。它写的是 store.model, 而员工没选过时 store.model 就是 ChatTab
  // catalog effect 灌的 catalog.default (= roles.yaml chat_default: deepseek)。
  // 污染有两条路 (setModel 一条 + 这条), 只堵前面那条, 下次 send 就被这条写回来。
  // 删掉不丢覆盖: 员工点 picker 时 ChatModelPicker + setModel(_, true) 照样落盘;
  // 从没选过的新机器本就该是空文件 (memory_enforce 读不到会退到 chat_default)。
  // 军规: picker 文件只记录**员工的选择**, 不记录系统替他填的默认值。

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

  return { url, body, agentHeaders, effectiveModel };
}
