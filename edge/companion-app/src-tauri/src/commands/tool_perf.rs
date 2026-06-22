//! P3.5.59 (6/22 鸿波 catch "我们现在是不是少了一个类似 benchmark 测试后性能指
//! 标参数, 现在无法知道我们的性能的状态"):
//!
//! tool-bridge audit jsonl (`~/.hermes/.catfish_audit.jsonl`) 聚合 per-tool
//! 性能统计 — count / success_rate / latency p50/p95/p99.
//!
//! # 跟 audit.rs 区别
//!
//! - `audit.rs` 读 `~/.catfish/gateway_audit.jsonl` (gateway 写的 LLM call metadata)
//! - 本模块读 `~/.hermes/.catfish_audit.jsonl` (tool-bridge 写的 tool dispatch
//!   metadata, 见 tool-bridge/src/catfish_tool_bridge/audit.py:98-148)
//!
//! 两个 jsonl 路径 + schema 都不同, 各算各的, 前端 PerfCard 一并显示.
//!
//! # 数据 (audit.py:98-128 真 schema)
//!
//! ```json
//! {
//!   "ts": "2026-04-28T14:30:00.123Z",    // ISO-8601 UTC
//!   "tool": "read_file",                   // tool name
//!   "ok": true,                            // bool
//!   "error": null,                         // 截短 200 字
//!   "latency_ms": 23.4,                    // 延迟
//!   "args_preview": "{...}"                // 200 字截短入参
//! }
//! ```
//!
//! # 性能 (tail TAIL_LINES 防 OOM)
//!
//! 鸿波本机文件实测 ~3.7MB / 23k 行, 1 年估 145MB (轻量). tail 10k 行覆盖
//! ~2-3 天 (Phase 1 不切窗, 算全 tail). 真 1y 大量后续切窗 / archive.

use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

/// tail 最近 N 行, 跟 audit.rs:24 TAIL_LINES 同等级, 防大文件 OOM.
const TAIL_LINES: usize = 10_000;

/// 单条 audit 事件 — 跟 tool-bridge/src/catfish_tool_bridge/audit.py:98-128 对齐
#[derive(Debug, Clone, Deserialize)]
struct ToolAuditRecord {
    /// ISO-8601 UTC timestamp
    ts: String,
    tool: String,
    #[serde(default = "default_ok")]
    ok: bool,
    /// 老 audit 可能缺 latency_ms (audit.py 后期加的), 兼容空
    #[serde(default)]
    latency_ms: Option<f64>,
    #[serde(default)]
    #[allow(dead_code)] // 字段在但目前不聚合 (后续 PerfCard 看错误前 N 可加)
    error: Option<String>,
}

fn default_ok() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct ToolPerfSummary {
    /// 聚合时间窗 (小时, 0 = 不切窗算全 tail)
    pub window_hours: i64,
    /// 总调用次数
    pub total_calls: u64,
    /// 总成功次数
    pub total_ok: u64,
    /// 总失败次数
    pub total_error: u64,
    /// 整体成功率 (0.0-1.0)
    pub overall_success_rate: f64,
    /// 整体 latency 分位 (ms). 缺数据返 None
    pub overall_p50_ms: Option<f64>,
    pub overall_p95_ms: Option<f64>,
    pub overall_p99_ms: Option<f64>,
    /// 按 tool 拆分, 按 count desc 排序
    pub by_tool: Vec<ToolStat>,
    /// 数据新鲜度 (latest event ago seconds)
    pub data_freshness_secs: i64,
    /// 真读到几行 audit (诊断用 — 0 = 文件不存在或空)
    pub lines_scanned: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ToolStat {
    pub tool: String,
    pub count: u64,
    pub ok_count: u64,
    pub error_count: u64,
    pub success_rate: f64,
    pub p50_ms: Option<f64>,
    pub p95_ms: Option<f64>,
    pub p99_ms: Option<f64>,
    /// 平均 latency (ms), 缺数据返 None
    pub avg_ms: Option<f64>,
}

/// audit 文件路径 — 跟 tool-bridge/audit.py:68-73 _audit_path() 对齐
fn audit_path() -> PathBuf {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".hermes").join(".catfish_audit.jsonl")
}

/// 读最近 TAIL_LINES 行 — 复用 audit.rs read_tail_lines 同 pattern
/// (不能直接 pub use 因为 audit.rs:114 是 file-private).
fn read_tail_lines(path: &std::path::Path, n: usize) -> std::io::Result<Vec<String>> {
    use std::io::{BufRead, BufReader};
    let f = std::fs::File::open(path)?;
    let reader = BufReader::new(f);
    let mut buf: Vec<String> = Vec::with_capacity(n);
    for line in reader.lines() {
        let line = line?;
        if buf.len() == n {
            buf.remove(0);
        }
        buf.push(line);
    }
    Ok(buf)
}

/// p quantile from sorted samples. 跟 audit.rs:209 percentile() 同算法.
/// (不复用因为 audit.rs 该函数 file-private. 算法极简 13 行无重复成本.)
fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() as f64 - 1.0) * p).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

/// ISO-8601 string → unix epoch seconds. 无效返 None.
fn parse_iso8601_ts(s: &str) -> Option<i64> {
    // 用 chrono parse, 跟 catfish 仓库其他地方一致.
    chrono::DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|dt| dt.timestamp())
}

/// 聚合算法 — 给定 records 算 ToolPerfSummary.
fn aggregate(records: Vec<ToolAuditRecord>, window_hours: i64) -> ToolPerfSummary {
    let now_ts = chrono::Utc::now().timestamp();
    let mut summary = ToolPerfSummary {
        window_hours,
        lines_scanned: records.len() as u64,
        data_freshness_secs: -1,
        ..Default::default()
    };

    // 时间窗 filter (window_hours=0 → 不切, 全用)
    let cutoff = if window_hours > 0 {
        now_ts - window_hours * 3600
    } else {
        i64::MIN
    };

    let mut latest_ts: i64 = 0;
    let mut overall_latencies: Vec<f64> = Vec::new();
    let mut per_tool: BTreeMap<String, (u64, u64, u64, Vec<f64>)> = BTreeMap::new();
    //                              ^count ^ok ^err ^latencies

    for r in records {
        let ts = match parse_iso8601_ts(&r.ts) {
            Some(t) => t,
            None => continue, // 坏行 skip
        };
        if ts < cutoff {
            continue;
        }
        if ts > latest_ts {
            latest_ts = ts;
        }

        summary.total_calls += 1;
        if r.ok {
            summary.total_ok += 1;
        } else {
            summary.total_error += 1;
        }

        if let Some(lat) = r.latency_ms {
            if lat > 0.0 {
                overall_latencies.push(lat);
            }
        }

        let entry = per_tool
            .entry(r.tool.clone())
            .or_insert((0, 0, 0, Vec::new()));
        entry.0 += 1;
        if r.ok {
            entry.1 += 1;
        } else {
            entry.2 += 1;
        }
        if let Some(lat) = r.latency_ms {
            if lat > 0.0 {
                entry.3.push(lat);
            }
        }
    }

    // overall 成功率 + percentile
    if summary.total_calls > 0 {
        summary.overall_success_rate =
            summary.total_ok as f64 / summary.total_calls as f64;
    }
    if !overall_latencies.is_empty() {
        overall_latencies.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        summary.overall_p50_ms = Some(percentile(&overall_latencies, 0.5));
        summary.overall_p95_ms = Some(percentile(&overall_latencies, 0.95));
        summary.overall_p99_ms = Some(percentile(&overall_latencies, 0.99));
    }

    // per-tool stats
    let mut tool_stats: Vec<ToolStat> = per_tool
        .into_iter()
        .map(|(tool, (count, ok_count, error_count, mut lats))| {
            let success_rate = if count > 0 {
                ok_count as f64 / count as f64
            } else {
                0.0
            };
            let (p50, p95, p99, avg) = if lats.is_empty() {
                (None, None, None, None)
            } else {
                lats.sort_by(|a, b| {
                    a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal)
                });
                let sum: f64 = lats.iter().sum();
                let avg = sum / lats.len() as f64;
                (
                    Some(percentile(&lats, 0.5)),
                    Some(percentile(&lats, 0.95)),
                    Some(percentile(&lats, 0.99)),
                    Some(avg),
                )
            };
            ToolStat {
                tool,
                count,
                ok_count,
                error_count,
                success_rate,
                p50_ms: p50,
                p95_ms: p95,
                p99_ms: p99,
                avg_ms: avg,
            }
        })
        .collect();
    tool_stats.sort_by(|a, b| b.count.cmp(&a.count));
    summary.by_tool = tool_stats;

    summary.data_freshness_secs = if latest_ts > 0 {
        (now_ts - latest_ts).max(0)
    } else {
        -1
    };

    summary
}

/// 聚合 tool-bridge audit jsonl 给出性能统计.
///
/// window_hours: 0 = 全 tail (10k 行约 2-3 天) ; > 0 = 限定时间窗.
/// 文件不存在返空 summary, 不抛错 (员工刚装 tool-bridge 还没跑过 tool).
#[tauri::command]
pub async fn tool_perf_summary(window_hours: Option<i64>) -> Result<ToolPerfSummary, String> {
    let path = audit_path();
    let lines = match read_tail_lines(&path, TAIL_LINES) {
        Ok(v) => v,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            // 文件不存在 → tool-bridge 还没跑过 / 鸿波 dev 没装. 返空, 不挂.
            return Ok(ToolPerfSummary::default());
        }
        Err(e) => return Err(format!("读 tool audit jsonl 失败: {e}")),
    };

    let mut records: Vec<ToolAuditRecord> = Vec::with_capacity(lines.len());
    for line in lines {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(r) = serde_json::from_str::<ToolAuditRecord>(line) {
            records.push(r);
        }
        // 坏行 skip — 兼容老 schema / 部分写 / 测试 / 损坏
    }

    let win = window_hours.unwrap_or(24);
    Ok(aggregate(records, win))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentile_basic() {
        let sorted = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        assert_eq!(percentile(&sorted, 0.5), 3.0);
        assert_eq!(percentile(&sorted, 0.0), 1.0);
        assert_eq!(percentile(&sorted, 1.0), 5.0);
    }

    #[test]
    fn percentile_empty() {
        assert_eq!(percentile(&[], 0.5), 0.0);
    }

    #[test]
    fn aggregate_basic_window() {
        let now_iso = chrono::Utc::now().to_rfc3339();
        let recs = vec![
            ToolAuditRecord {
                ts: now_iso.clone(),
                tool: "read_file".into(),
                ok: true,
                latency_ms: Some(10.0),
                error: None,
            },
            ToolAuditRecord {
                ts: now_iso.clone(),
                tool: "read_file".into(),
                ok: true,
                latency_ms: Some(20.0),
                error: None,
            },
            ToolAuditRecord {
                ts: now_iso.clone(),
                tool: "search_files".into(),
                ok: false,
                latency_ms: Some(5.0),
                error: Some("timeout".into()),
            },
        ];
        let s = aggregate(recs, 24);
        assert_eq!(s.total_calls, 3);
        assert_eq!(s.total_ok, 2);
        assert_eq!(s.total_error, 1);
        assert!((s.overall_success_rate - 2.0 / 3.0).abs() < 0.001);
        assert_eq!(s.by_tool.len(), 2);
        // read_file 应排第一 (count desc)
        assert_eq!(s.by_tool[0].tool, "read_file");
        assert_eq!(s.by_tool[0].count, 2);
        assert!((s.by_tool[0].success_rate - 1.0).abs() < 0.001);
    }

    #[test]
    fn aggregate_skips_outside_window() {
        // 老 event 在窗外应被跳
        let old_iso = (chrono::Utc::now() - chrono::Duration::hours(48)).to_rfc3339();
        let recent_iso = chrono::Utc::now().to_rfc3339();
        let recs = vec![
            ToolAuditRecord {
                ts: old_iso,
                tool: "read_file".into(),
                ok: true,
                latency_ms: Some(99.0),
                error: None,
            },
            ToolAuditRecord {
                ts: recent_iso,
                tool: "read_file".into(),
                ok: true,
                latency_ms: Some(10.0),
                error: None,
            },
        ];
        let s = aggregate(recs, 24); // 24h 窗
        assert_eq!(s.total_calls, 1); // 老 event 被跳
        assert_eq!(s.by_tool[0].avg_ms, Some(10.0));
    }

    #[test]
    fn aggregate_no_window() {
        let old_iso = (chrono::Utc::now() - chrono::Duration::days(30)).to_rfc3339();
        let recs = vec![ToolAuditRecord {
            ts: old_iso,
            tool: "read_file".into(),
            ok: true,
            latency_ms: Some(10.0),
            error: None,
        }];
        let s = aggregate(recs, 0); // 不切窗
        assert_eq!(s.total_calls, 1);
    }

    #[test]
    fn aggregate_handles_missing_latency() {
        // 老 audit 可能没 latency_ms 字段, 不该挂
        let now_iso = chrono::Utc::now().to_rfc3339();
        let recs = vec![ToolAuditRecord {
            ts: now_iso,
            tool: "old_tool".into(),
            ok: true,
            latency_ms: None,
            error: None,
        }];
        let s = aggregate(recs, 24);
        assert_eq!(s.total_calls, 1);
        assert_eq!(s.overall_p50_ms, None);
        assert_eq!(s.by_tool[0].avg_ms, None);
    }
}
