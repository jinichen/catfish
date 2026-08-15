/** chat 流式客户端的类型定义。
 *
 * 8/15 从 chat.ts 搬出来 (934 行, 过了 CLAUDE.md §1 的 800 红线)。
 *
 * # 这个文件是第二次拆同一个文件了
 *
 * 5/20 已经拆过一次 —— 那次把 wire format (toWire / formatFileAttachment) 抽到
 * chatWire.ts, 805 → 550。三个月后又长回 934。
 *
 * 长回来的不是新功能, 是**注释**: 这个文件里几乎每个 if 上面都压着一段"为什么
 * 是这样"的病历 (BL-FIX45 / BL-GATEWAY-SOFT-HANDOFF / P3.5.34 …)。那些注释是
 * 这条链路最值钱的东西, 不该为了行数删。所以这次拆的思路是按**关注点**分,
 * 让每个文件的注释密度看起来是正常的, 而不是去精简注释。
 *
 * # 为什么类型先走
 *
 * TS 的 interface 编译后完全消失, 搬它们是**零运行时风险**的操作 —— 在一个
 * 主链路上、而且 streamChat 那 752 行**一条测试都没有** (chat.test.ts 只测了
 * computeIdleTimeoutMs) 的时候, 先做零风险的那一半是合理的顺序。
 */
import type { ChatMessage, ToolCall } from "../types/chat";

export interface SendChatParams {
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
  /** 5/23 BL-COMPANION-HERMES-SESSION-REUSE (鸿波): 本次 stream 是否走 hermes
   *  API server 路径. true → hermes 端已经往 state.db 写 assistant message,
   *  companion 不要再 sessionMessageAppend 写一遍 (会双写). false → 老
   *  gateway 路径, hermes 没参与, companion 必须自己写.
   */
  via_hermes?: boolean;
}

// ── 流式 tool_calls 累积 ──
//
// LLM 流式返回 tool_calls 时, function.arguments 是 JSON 字符串, 分多个 chunk 来。
// 我们用 index 做 key, 累积成完整对象, 流结束时一次性 JSON.parse。
export interface ToolCallAcc {
  id: string;
  name: string;
  argumentsJson: string;
}
