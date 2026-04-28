/** Audit / Telemetry 数据结构 — 跟 commands/audit.rs 对齐. */

export interface ModelUsage {
  model: string;
  count: number;
  total_tokens: number;
  /** catfish-private-* 前缀 = true. UI 上私有模型显绿点 (本地优先卖点) */
  is_private: boolean;
}

export interface SecurityConcern {
  /** 例: 'prompt_credential_detected' */
  kind: string;
  count: number;
}

export interface AuditSummary {
  request_count: number;
  ok_count: number;
  error_count: number;
  total_tokens: number;
  by_model: ModelUsage[];
  /** TTFT 中位数 (ms), null = 没数据 */
  ttft_p50_ms: number | null;
  ttft_p95_ms: number | null;
  security_concerns: SecurityConcern[];
  /** 数据新鲜度: 距最后一条记录多少秒 (-1 = 无数据) */
  data_freshness_secs: number;
}
