/** P3.5.59 (6/22 鸿波 catch "现在无法知道我们的性能的状态"):
 *  tool-bridge audit jsonl 聚合 wrapper.
 *
 *  数据源: ~/.hermes/.catfish_audit.jsonl (Rust 本机读, 不走网络).
 *  Rust 端实现: src-tauri/src/commands/tool_perf.rs
 *
 *  跟 useAudit (gateway LLM perf) 互补 — 这个是 tool dispatch perf, 不是 LLM call.
 */

import { invoke } from "@tauri-apps/api/core";

export interface ToolStat {
  tool: string;
  count: number;
  ok_count: number;
  error_count: number;
  success_rate: number;          // 0.0 - 1.0
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  avg_ms: number | null;
}

export interface ToolPerfSummary {
  window_hours: number;          // 0 = 不切窗
  total_calls: number;
  total_ok: number;
  total_error: number;
  overall_success_rate: number;  // 0.0 - 1.0
  overall_p50_ms: number | null;
  overall_p95_ms: number | null;
  overall_p99_ms: number | null;
  by_tool: ToolStat[];           // count desc
  data_freshness_secs: number;   // -1 = 无数据
  lines_scanned: number;
}

/** 聚合 tool perf. window_hours 默认 24h. 0 = 不切窗算全 tail. */
export function fetchToolPerfSummary(window_hours: number = 24): Promise<ToolPerfSummary> {
  return invoke<ToolPerfSummary>("tool_perf_summary", { windowHours: window_hours });
}
