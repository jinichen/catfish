/**
 * 与 Rust commands::sessions::SessionMeta / SessionMessage / SessionDetail 严格对齐。
 * 数据源是 ~/.hermes/state.db 的 sessions / messages 表。
 *
 * NOTE: Hermes 不记录会话的 cwd / 项目目录，前端用 title 替代项目维度的展示。
 */

export interface SessionMeta {
  id: string;
  /** 人话标题 —— 员工自己起或 Hermes 推断；为空则 fallback 显示 id */
  title: string | null;
  model: string;
  /** ISO-8601 UTC */
  startedAt: string;
  endedAt?: string;
  /** 结束原因，例如 "cli_close"；为空表示会话还活着 */
  endReason?: string;
  messageCount: number;
  /** input + output + cache_read + cache_write + reasoning 之和 */
  totalTokens: number;
  /** "cli" / "companion" / undefined (老数据) —— sidebar 区分来源加 badge 用 */
  source?: string;
}

/** 单条消息 —— 跟 Rust SessionMessage 对齐, resume 时会 map 成 ChatMessage 灌进 store */
export interface SessionMessage {
  id: number;
  role: "user" | "assistant" | "system" | "tool" | string;
  content: string;
  /** OpenAI tool_calls JSON 字符串, 没工具调用就 undefined */
  toolCalls?: string;
  toolCallId?: string;
  toolName?: string;
  /** ISO-8601 UTC */
  timestamp: string;
}

export interface SessionDetail {
  meta: SessionMeta;
  /** 全部消息 (timestamp asc) —— Plan C Week 3 resume 用 */
  messages: SessionMessage[];
  /** 老接口字段, 兼容 SessionDetail UI */
  lastUserMessage?: string;
  lastAssistantMessage?: string;
}
