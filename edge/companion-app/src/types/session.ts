/**
 * 与 Rust commands::sessions::SessionMeta 严格对齐。
 * 数据源是 ~/.hermes/state.db 的 sessions 表 —— Hermes 不记录 cwd / 项目目录，
 * 所以前端用 title 替代项目维度的展示。
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
}

export interface SessionDetail {
  meta: SessionMeta;
  lastUserMessage?: string;
  lastAssistantMessage?: string;
}
