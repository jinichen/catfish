//! Audit / Telemetry 仪表盘数据源.
//!
//! 读 `~/.catfish/gateway_audit.jsonl` (gateway 写的 metrics JSONL),
//! 聚合成 dashboard 卡片要展示的统计.
//!
//! 字段约定 (跟 catfish_gateway/metrics.py 对齐):
//!   ts (unix int), type, user, model, prompt_tokens, completion_tokens,
//!   total_tokens, latency_ms, ttft_ms (optional), status, error (optional),
//!   security_concern (optional)
//!
//! 设计:
//!   - 只算 **今日 (本地时间 0:00 起)**, 历史趋势 P2 加
//!   - 文件不存在 / 损坏 → 返回空统计, 不抛
//!   - 日志文件可能很大, 只 tail 最近 N 行 (10000), 而不是全文件 parse
//!   - 这是 read-only 操作, 不锁文件, 让 gateway 继续写

use std::cmp::Reverse;
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use chrono::TimeZone;
use serde::{Deserialize, Serialize};

const TAIL_LINES: usize = 10_000;

/// audit JSONL 一行的结构 (跟 metrics.py 对齐).
///
/// `user` / `latency_ms` 现在 aggregate 里没用, 但保留字段是为了:
///   1. 反序列化兼容 — 万一 JSON 里有这字段, 不至于 strict mode 抛
///   2. 后续 P2 扩展 (per-user 聚合 / 延迟分布) 不用改 schema
#[derive(Debug, Clone, Deserialize)]
struct AuditRecord {
    ts: i64,
    #[serde(default)]
    #[allow(dead_code)] // P2 per-user 聚合
    user: String,
    #[serde(default)]
    model: String,
    #[serde(default)]
    prompt_tokens: i64,
    #[serde(default)]
    completion_tokens: i64,
    #[serde(default)]
    #[allow(dead_code)] // P2 总延迟分布 (现在只看 TTFT)
    latency_ms: f64,
    #[serde(default)]
    ttft_ms: Option<f64>,
    #[serde(default = "default_status_ok")]
    status: String,
    #[serde(default)]
    security_concern: Option<String>,
}

fn default_status_ok() -> String {
    "ok".into()
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct AuditSummary {
    /// 今天的请求总数
    pub request_count: u64,
    /// 今天 OK 的请求数
    pub ok_count: u64,
    /// 今天 error 的请求数
    pub error_count: u64,
    /// 今天 prompt + completion token 总和
    pub total_tokens: i64,
    /// 模型用量分布: model_name -> request_count
    pub by_model: Vec<ModelUsage>,
    /// TTFT 中位数 (ms), null = 没数据
    pub ttft_p50_ms: Option<f64>,
    /// TTFT p95 (ms), null = 没数据
    pub ttft_p95_ms: Option<f64>,
    /// 今天发生的 security_concern 计数 (例: prompt_credential_detected -> 3)
    pub security_concerns: Vec<SecurityConcern>,
    /// 数据时间范围 (展示给员工看, 让他们知道 dashboard 是新的)
    pub data_freshness_secs: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ModelUsage {
    pub model: String,
    pub count: u64,
    pub total_tokens: i64,
    /// 是不是 private tier (private/* prefix). 给 UI 显示绿点 (本地优先卖点)
    pub is_private: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct SecurityConcern {
    pub kind: String,
    pub count: u64,
}

/// audit 文件路径. 跟 gateway/metrics.py 的 audit_path() 同步.
fn audit_path() -> PathBuf {
    if let Ok(env) = std::env::var("CATFISH_AUDIT_PATH") {
        return PathBuf::from(env);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".catfish").join("gateway_audit.jsonl")
}

/// 今天本地时间 0:00 的 unix timestamp.
fn today_start_ts() -> i64 {
    let now = chrono::Local::now();
    let today = now.date_naive().and_hms_opt(0, 0, 0).unwrap();
    chrono::Local
        .from_local_datetime(&today)
        .unwrap()
        .timestamp()
}

/// 读最近 TAIL_LINES 行 (避免大文件全量 parse).
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

/// 解析 + 聚合.
fn aggregate(records: Vec<AuditRecord>) -> AuditSummary {
    let mut summary = AuditSummary::default();

    let now_ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let mut latest_ts: i64 = 0;

    let mut by_model: BTreeMap<String, (u64, i64)> = BTreeMap::new();
    let mut ttft_samples: Vec<f64> = Vec::new();
    let mut concern_counts: BTreeMap<String, u64> = BTreeMap::new();

    for r in records {
        if r.ts > latest_ts {
            latest_ts = r.ts;
        }

        summary.request_count += 1;
        if r.status == "ok" {
            summary.ok_count += 1;
        } else {
            summary.error_count += 1;
        }
        let toks = r.prompt_tokens + r.completion_tokens;
        summary.total_tokens += toks;

        let entry = by_model.entry(r.model.clone()).or_insert((0, 0));
        entry.0 += 1;
        entry.1 += toks;

        if let Some(ttft) = r.ttft_ms {
            if ttft > 0.0 {
                ttft_samples.push(ttft);
            }
        }
        if let Some(concern) = r.security_concern {
            *concern_counts.entry(concern).or_insert(0) += 1;
        }
    }

    // model usage list, 按 count desc
    let mut model_list: Vec<ModelUsage> = by_model
        .into_iter()
        .map(|(model, (count, total_tokens))| ModelUsage {
            is_private: model.starts_with("catfish-private-"),
            model,
            count,
            total_tokens,
        })
        .collect();
    model_list.sort_by_key(|m| Reverse(m.count));
    summary.by_model = model_list;

    // TTFT p50 / p95
    if !ttft_samples.is_empty() {
        ttft_samples.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        summary.ttft_p50_ms = Some(percentile(&ttft_samples, 0.5));
        summary.ttft_p95_ms = Some(percentile(&ttft_samples, 0.95));
    }

    // security concerns sorted by count desc
    let mut concern_list: Vec<SecurityConcern> = concern_counts
        .into_iter()
        .map(|(kind, count)| SecurityConcern { kind, count })
        .collect();
    concern_list.sort_by_key(|c| Reverse(c.count));
    summary.security_concerns = concern_list;

    summary.data_freshness_secs = if latest_ts > 0 {
        (now_ts - latest_ts).max(0)
    } else {
        -1
    };

    summary
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() as f64 - 1.0) * p).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

#[tauri::command]
pub async fn audit_summary() -> Result<AuditSummary, String> {
    let path = audit_path();
    let lines = match read_tail_lines(&path, TAIL_LINES) {
        Ok(v) => v,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            // 没文件 — gateway 还没跑过, 返回空统计
            return Ok(AuditSummary::default());
        }
        Err(e) => return Err(format!("读 audit 失败: {e}")),
    };

    let today_start = today_start_ts();
    let mut records: Vec<AuditRecord> = Vec::with_capacity(lines.len());
    for line in lines {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match serde_json::from_str::<AuditRecord>(line) {
            Ok(r) if r.ts >= today_start => records.push(r),
            Ok(_) => {} // 不是今天, 跳过
            Err(_) => {} // 损坏行, 跳过
        }
    }

    Ok(aggregate(records))
}
