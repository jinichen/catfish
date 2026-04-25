/** 聊天会话的核心类型 —— 给 useChat / Chat tab 内部用。
 *
 * 不直接对应 ~/.hermes/state.db 的 messages 表（那是 Hermes 的存储格式）；
 * 这里是 Companion 内存里的运行时表示，Week 3 持久化时再做映射。
 */

export type ChatRole = "user" | "assistant" | "system" | "tool";

/** 工具调用 —— assistant 消息里可能含一个或多个,每个独立 status。 */
export interface ToolCall {
  /** OpenAI 兼容的 call id, 如 "call_abc123" */
  id: string;
  /** 工具名,如 "read_file" / "browser_navigate" / "session_search" */
  name: string;
  /** 解析后的参数。如果 LLM 返回的 arguments JSON 还在累积中, 可能是 {} */
  args: Record<string, unknown>;
  /** UI 状态 —— 不发给 LLM, 只用于 Companion 渲染 */
  status: "pending" | "running" | "done" | "error";
  /** 执行结果(tool_bridge 返回的 result 字段, 通常是 JSON 字符串) */
  result?: unknown;
  /** 执行失败时的错误描述 */
  error?: string;
}

export interface ChatMessage {
  /** 客户端生成的 uuid,渲染 React key 用 */
  id: string;
  role: ChatRole;
  /** 主文本内容;assistant 在 streaming 时这里持续 append */
  content: string;
  /** assistant 消息可能伴随多个 tool_calls(并行 / 串行都有可能) */
  tool_calls?: ToolCall[];
  /** tool 角色消息携带的 call id —— 关联到对应 assistant 的 tool_calls[i].id */
  tool_call_id?: string;
  /** ISO-8601;客户端打的本地时间,纯展示用 */
  ts: string;
  /** streaming 中、完成、出错的状态 */
  status?: "streaming" | "done" | "error";
  /** 错误消息(status=error 时填) */
  error?: string;
}

/** 一次会话的运行时状态(目前 store 直接展开到顶层,这个 type 留给 Week 3 持久化) */
export interface ChatSession {
  id: string;
  created_at: string;
  model: string;
  messages: ChatMessage[];
}
