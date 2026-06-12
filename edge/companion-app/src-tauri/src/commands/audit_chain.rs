//! BL-AUDIT-CHAIN (P3.3.51, 6/12 鸿波 "信合规审计闭环"): jsonl 防篡改哈希链.
//!
//! # 为啥需要
//! ==========
//! catfish 现有 4 套 audit/log (`~/.hermes/.catfish_audit.jsonl`, `~/.catfish/
//! gateway_audit.jsonl`, `~/.catfish/decisions.jsonl`, `~/.catfish/outbound_log.db`)
//! 都在写, 但**审计员上门时不能证明日志没被员工事后编辑**. catfish 的 manifesto
//! 公理 1 (员工主权) 允许员工删自己数据, 但**审计员要看出"被改过"** 才能闭环.
//!
//! 这一层加在 decisions.jsonl + political_scan.jsonl 这种"需要可证不被篡改" 的
//! jsonl 上 — 每行 event 含 `prev_sha256` + `sha256`, 跟同名 `.chain.json` 互
//! 验. 改任一行 → 之后所有行 hash 对不上 → `chain_verify` 报告 broken_at.
//!
//! # 文件布局
//! ==========
//! - `~/.catfish/decisions.jsonl`             # event 行 (每行 1 JSON)
//! - `~/.catfish/decisions.jsonl.chain.json`  # {first_sha256, last_sha256, count, last_ts}
//!
//! # event 行 JSON 字段约定 (caller 自由加业务字段)
//! ====================================================
//! ```json
//! {
//!   "ts": "2026-06-12T11:30:45+00:00",       // chain_append 自动加
//!   "prev_sha256": "abc123...",               // chain_append 自动加 (首行 = "GENESIS")
//!   "event_type": "decision",                 // caller 提供
//!   "...业务字段...": ...,
//!   "sha256": "def456..."                     // chain_append 自动算
//! }
//! ```
//!
//! sha256 = SHA256(prev_sha256 + "\x00" + canonical_json(event_without_sha256))
//!
//! canonical_json 用 BTreeMap 强制 key 字典序, 保 hash deterministic.
//!
//! # 不做的事 (audit 报告 6/12 砍掉的)
//! =====================================
//! - 不镜像 hermes audit — 那条链路 hermes 端写 jsonl, catfish 直接 read 即可
//! - 不按月切文件 — 现有 jsonl 一年也只 MB 级别, 切了反而要管 manifest
//! - 不强制 caller 一定走这层 — 只 decisions / political_scan 等审计相关用
//!
//! # 并发
//! ======
//! 全局 Mutex 串行化 chain_append. 跟 P3.3.50 profile_save 同款 pattern.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

/// 全局锁: 防多 caller 并发 append 撞 chain.json 写入.
/// Mutex::new const since Rust 1.63 → 直接 static.
static CHAIN_LOCK: Mutex<()> = Mutex::new(());

/// "GENESIS" 是首行的 prev_sha256 占位 (chain 起头标记).
const GENESIS: &str = "GENESIS";

/// chain.json 持久化结构 — jsonl 文件的哈希链 metadata.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChainState {
    /// 对应的 jsonl 绝对路径
    pub jsonl_path: String,
    /// 首行的 sha256 (count=0 时 = "GENESIS")
    pub first_sha256: String,
    /// 末行的 sha256 (count=0 时 = "GENESIS")
    pub last_sha256: String,
    /// 当前累计 event 行数 (不含空行)
    pub count: u64,
    /// 末行 ts (ISO-8601). count=0 时空串.
    pub last_ts: String,
}

impl ChainState {
    fn genesis(jsonl_path: &str) -> Self {
        Self {
            jsonl_path: jsonl_path.to_string(),
            first_sha256: GENESIS.into(),
            last_sha256: GENESIS.into(),
            count: 0,
            last_ts: String::new(),
        }
    }
}

/// `<jsonl>.chain.json` 路径.
fn chain_state_path(jsonl_path: &str) -> PathBuf {
    PathBuf::from(format!("{}.chain.json", jsonl_path))
}

/// 读 chain.json. 不存在或损坏 → 返 genesis (相当于初始状态).
fn load_chain(jsonl_path: &str) -> ChainState {
    let p = chain_state_path(jsonl_path);
    if !p.exists() {
        return ChainState::genesis(jsonl_path);
    }
    match std::fs::read_to_string(&p) {
        Ok(text) => serde_json::from_str(&text).unwrap_or_else(|_| ChainState::genesis(jsonl_path)),
        Err(_) => ChainState::genesis(jsonl_path),
    }
}

/// 原子写 chain.json — tmp + rename (跟 P3.3.50 profile_save 同款).
fn save_chain(state: &ChainState) -> Result<(), String> {
    let p = chain_state_path(&state.jsonl_path);
    if let Some(parent) = p.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建 chain.json 父目录失败: {e}"))?;
    }
    let text = serde_json::to_string_pretty(state)
        .map_err(|e| format!("序列化 chain.json 失败: {e}"))?;
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    let tmp = p.with_extension(format!("json.tmp.{nanos}"));
    std::fs::write(&tmp, text)
        .map_err(|e| format!("写 {} 失败: {e}", tmp.display()))?;
    std::fs::rename(&tmp, &p)
        .map_err(|e| format!("rename chain.json 失败: {e}"))?;
    Ok(())
}

/// 把 JSON object map 序列化成 canonical (字典序) JSON 字符串.
/// 用 BTreeMap 强制顶层 key 排序; 嵌套 Value 顺序由 serde_json 默认行为决定
/// (event payload 通常 1 层不嵌套, 不会撞 reproducibility).
fn canonical_json_object(map: &serde_json::Map<String, Value>) -> String {
    let sorted: BTreeMap<String, Value> = map
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    serde_json::to_string(&sorted).unwrap_or_default()
}

/// 算 event sha = SHA256(prev_sha + 0x00 + canonical_json(event_without_sha256))
fn compute_event_sha(prev_sha: &str, event_without_sha: &serde_json::Map<String, Value>) -> String {
    let mut hasher = Sha256::new();
    hasher.update(prev_sha.as_bytes());
    hasher.update(b"\x00");
    hasher.update(canonical_json_object(event_without_sha).as_bytes());
    hex::encode(hasher.finalize())
}

// ─── Tauri commands ─────────────────────────────────────────────────

/// Append 一行到 jsonl 走 hash chain (核心 impl). 给 Rust 内部 caller 用 (decisions /
/// political_scan 等不需要走 Tauri IPC). Tauri command `audit_chain_append` 是 thin wrapper.
///
/// caller 传 payload (业务字段), 本函数自动:
///   1. 取 chain.json 的 last_sha256 作 prev_sha256
///   2. 如果 payload 已含 ts 字段则用 caller 的; 否则自动加 UTC ISO-8601
///   3. 算 sha256 = SHA256(prev + 0x00 + canonical_json(event_without_sha))
///   4. event 加 sha256 字段, 写 jsonl
///   5. 更新 chain.json (first/last sha / count / last_ts)
///
/// 返新行的 sha256 (调用方可以 log 跟踪).
///
/// 错误情况: caller 必须传 object payload, payload 不能有 prev_sha256 / sha256
/// 字段 (chain 自管, 会拒). ts 字段 caller 可传可不传 — P3.3.52 加, 让 decisions
/// 这种自带 ts 的 caller 不被覆盖.
pub async fn chain_append_impl(jsonl_path: String, payload: Value) -> Result<String, String> {
    let _guard = CHAIN_LOCK
        .lock()
        .map_err(|e| format!("CHAIN_LOCK 中毒: {e} (前次 panic 留下)"))?;

    let mut event = match payload {
        Value::Object(m) => m,
        _ => return Err("payload 必须是 JSON object".into()),
    };

    // 保留字段防意外覆盖 (P3.3.52: ts 移出 reserved, 让 caller 自由传)
    for reserved in &["sha256", "prev_sha256"] {
        if event.contains_key(*reserved) {
            return Err(format!(
                "payload 不能含保留字段 '{reserved}' — chain_append 会自动加"
            ));
        }
    }

    let state = load_chain(&jsonl_path);
    let prev_sha = state.last_sha256.clone();

    // P3.3.52: 用 caller 的 ts, 没传则自动加
    let ts = event
        .get("ts")
        .and_then(|v| v.as_str())
        .map(String::from)
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    event.insert("ts".into(), Value::String(ts.clone()));
    event.insert("prev_sha256".into(), Value::String(prev_sha.clone()));

    let sha = compute_event_sha(&prev_sha, &event);
    event.insert("sha256".into(), Value::String(sha.clone()));

    // append 到 jsonl
    let path = PathBuf::from(&jsonl_path);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建 jsonl 父目录失败: {e}"))?;
    }
    let line = serde_json::to_string(&Value::Object(event))
        .map_err(|e| format!("序列化 event 失败: {e}"))?;
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("打开 {jsonl_path} 失败: {e}"))?;
    writeln!(f, "{line}")
        .map_err(|e| format!("写 jsonl 失败: {e}"))?;

    // 更新 chain state
    let mut new_state = state.clone();
    if new_state.count == 0 {
        new_state.first_sha256 = sha.clone();
    }
    new_state.last_sha256 = sha.clone();
    new_state.count += 1;
    new_state.last_ts = ts;
    save_chain(&new_state)?;

    Ok(sha)
}

/// Tauri command wrapper — 给前端 JS 用. Rust 内部 caller 走 chain_append_impl 即可.
#[tauri::command]
pub async fn audit_chain_append(jsonl_path: String, payload: Value) -> Result<String, String> {
    chain_append_impl(jsonl_path, payload).await
}

/// chain verify 报告.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VerifyReport {
    /// 整体是否 OK — 没有任何 broken 行 + 跟 chain.json 一致
    pub ok: bool,
    /// 实际扫到的 event 行数 (跳过空行)
    pub total_lines: u64,
    /// chain.json 记的 count
    pub expected_count: u64,
    /// 走通校验的行数 (从开头到第一个 broken 之前)
    pub verified_lines: u64,
    /// 第一行 broken 的位置 (1-indexed)
    pub broken_at: Option<u64>,
    /// broken 的原因 (具体描述, 给审计员看)
    pub broken_reason: Option<String>,
    /// chain.json 记录的首尾 sha (审计员可现场对比)
    pub chain_first_sha256: String,
    pub chain_last_sha256: String,
}

/// 校验 jsonl 的 hash chain. 逐行重算 sha256, 跟记录值 + chain.json 对比.
/// 不打开 chain.json 也能验 (chain.json 丢失 → ok=false, 报告"chain.json 缺失").
#[tauri::command]
pub async fn audit_chain_verify(jsonl_path: String) -> Result<VerifyReport, String> {
    let state = load_chain(&jsonl_path);
    let chain_exists = chain_state_path(&jsonl_path).exists();
    let path = PathBuf::from(&jsonl_path);

    let mut report = VerifyReport {
        ok: false,
        total_lines: 0,
        expected_count: state.count,
        verified_lines: 0,
        broken_at: None,
        broken_reason: None,
        chain_first_sha256: state.first_sha256.clone(),
        chain_last_sha256: state.last_sha256.clone(),
    };

    if !path.exists() {
        // 文件不存在 — count=0 算 OK, count>0 算 broken
        if state.count == 0 {
            report.ok = true;
        } else {
            report.broken_reason = Some(format!(
                "jsonl 文件 {jsonl_path} 不存在, 但 chain.json 显示有 {} 条记录",
                state.count
            ));
        }
        return Ok(report);
    }

    if !chain_exists && state.count == 0 {
        // jsonl 存在但 chain.json 没起头 — 看 jsonl 是否真有内容
        let txt = std::fs::read_to_string(&path)
            .map_err(|e| format!("读 jsonl 失败: {e}"))?;
        let non_empty = txt.lines().filter(|l| !l.trim().is_empty()).count();
        if non_empty == 0 {
            report.ok = true;
            return Ok(report);
        }
        report.total_lines = non_empty as u64;
        report.broken_at = Some(1);
        report.broken_reason = Some(format!(
            "jsonl 含 {non_empty} 行但 chain.json 缺失, 无法校验起点"
        ));
        return Ok(report);
    }

    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 jsonl 失败: {e}"))?;

    let mut prev_sha = GENESIS.to_string();
    let mut last_sha = GENESIS.to_string();

    for raw_line in text.lines() {
        if raw_line.trim().is_empty() {
            continue;
        }
        report.total_lines += 1;
        let line_no = report.total_lines;  // 1-indexed event 行序

        // 已经 broken 的话仍继续 count 但不再校验
        if report.broken_at.is_some() {
            continue;
        }

        let mut event: serde_json::Map<String, Value> = match serde_json::from_str(raw_line) {
            Ok(Value::Object(m)) => m,
            _ => {
                report.broken_at = Some(line_no);
                report.broken_reason = Some(format!(
                    "第 {line_no} 行不是合法 JSON object"
                ));
                continue;
            }
        };

        let recorded_sha = event
            .remove("sha256")
            .and_then(|v| v.as_str().map(|s| s.to_string()))
            .unwrap_or_default();
        let recorded_prev = event
            .get("prev_sha256")
            .and_then(|v| v.as_str().map(|s| s.to_string()))
            .unwrap_or_default();

        if recorded_sha.is_empty() {
            report.broken_at = Some(line_no);
            report.broken_reason = Some(format!("第 {line_no} 行缺 sha256 字段"));
            continue;
        }
        if recorded_prev != prev_sha {
            report.broken_at = Some(line_no);
            report.broken_reason = Some(format!(
                "第 {line_no} 行 prev_sha256='{recorded_prev}', 应='{prev_sha}'"
            ));
            continue;
        }

        let computed = compute_event_sha(&prev_sha, &event);
        if computed != recorded_sha {
            report.broken_at = Some(line_no);
            report.broken_reason = Some(format!(
                "第 {line_no} 行 sha256 不匹配 (该行被改动过)"
            ));
            continue;
        }

        report.verified_lines += 1;
        prev_sha = recorded_sha.clone();
        last_sha = recorded_sha;
    }

    // 最后比对 chain.json
    if report.broken_at.is_none() {
        if report.total_lines != state.count {
            report.broken_at = Some(report.total_lines.max(1));
            report.broken_reason = Some(format!(
                "jsonl 共 {} 行, chain.json 记 {} 行, 数量不匹配 (可能有行被删/加)",
                report.total_lines, state.count
            ));
        } else if last_sha != state.last_sha256 {
            report.broken_at = Some(report.total_lines);
            report.broken_reason = Some(format!(
                "末行 sha256='{last_sha}' 跟 chain.json last_sha256='{}' 不一致",
                state.last_sha256
            ));
        } else {
            report.ok = true;
        }
    }

    Ok(report)
}

/// 读 chain.json 当前状态 (UI / debug 用).
#[tauri::command]
pub async fn audit_chain_status(jsonl_path: String) -> Result<ChainState, String> {
    Ok(load_chain(&jsonl_path))
}

// ─── 单测 ───────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    /// 每个 test 独占一个 tmp 路径, 防并发互踩.
    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn tmp_jsonl(tag: &str) -> String {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let mut p = std::env::temp_dir();
        p.push(format!(
            "catfish_audit_chain_test_{}_{}_{}_.jsonl",
            tag,
            std::process::id(),
            n
        ));
        let _ = std::fs::remove_file(&p);
        let _ = std::fs::remove_file(format!("{}.chain.json", p.display()));
        p.to_string_lossy().to_string()
    }

    #[tokio::test]
    async fn empty_file_verifies_ok() {
        let p = tmp_jsonl("empty");
        let r = audit_chain_verify(p).await.unwrap();
        assert!(r.ok, "{:?}", r);
        assert_eq!(r.total_lines, 0);
        assert_eq!(r.expected_count, 0);
    }

    #[tokio::test]
    async fn append_then_verify_ok() {
        let p = tmp_jsonl("append_verify");

        audit_chain_append(
            p.clone(),
            serde_json::json!({"event_type": "decision", "task": "alpha", "user_choice": "B"}),
        )
        .await
        .unwrap();

        audit_chain_append(
            p.clone(),
            serde_json::json!({"event_type": "decision", "task": "beta", "user_choice": "A"}),
        )
        .await
        .unwrap();

        let r = audit_chain_verify(p.clone()).await.unwrap();
        assert!(r.ok, "{:?}", r);
        assert_eq!(r.total_lines, 2);
        assert_eq!(r.verified_lines, 2);
        assert_eq!(r.expected_count, 2);
        assert!(r.broken_at.is_none());
    }

    #[tokio::test]
    async fn tamper_line_detected() {
        let p = tmp_jsonl("tamper");

        audit_chain_append(
            p.clone(),
            serde_json::json!({"event_type": "decision", "user_choice": "B"}),
        )
        .await
        .unwrap();
        audit_chain_append(
            p.clone(),
            serde_json::json!({"event_type": "decision", "user_choice": "A"}),
        )
        .await
        .unwrap();

        // 篡改第一行 — 改 user_choice B → C
        let content = std::fs::read_to_string(&p).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        let tampered = lines[0].replace("\"B\"", "\"C\"");
        let new_content = format!("{}\n{}\n", tampered, lines[1]);
        std::fs::write(&p, new_content).unwrap();

        let r = audit_chain_verify(p).await.unwrap();
        assert!(!r.ok, "篡改应被检测出: {:?}", r);
        assert_eq!(r.broken_at, Some(1));
    }

    #[tokio::test]
    async fn delete_line_detected() {
        let p = tmp_jsonl("delete");

        for i in 0..3 {
            audit_chain_append(
                p.clone(),
                serde_json::json!({"event_type": "decision", "n": i}),
            )
            .await
            .unwrap();
        }

        // 删第 2 行
        let content = std::fs::read_to_string(&p).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        let new_content = format!("{}\n{}\n", lines[0], lines[2]);
        std::fs::write(&p, new_content).unwrap();

        let r = audit_chain_verify(p).await.unwrap();
        assert!(!r.ok, "删行应被检测出: {:?}", r);
        // 第 1 行仍正确, 第 2 行 (原第 3) 的 prev_sha256 不匹配
        assert_eq!(r.broken_at, Some(2));
    }

    #[tokio::test]
    async fn reserved_field_rejected() {
        let p = tmp_jsonl("reserved");
        let r = audit_chain_append(
            p,
            serde_json::json!({"event_type": "test", "sha256": "preset"}),
        )
        .await;
        assert!(r.is_err());
        assert!(r.unwrap_err().contains("sha256"));
    }

    #[tokio::test]
    async fn non_object_payload_rejected() {
        let p = tmp_jsonl("non_obj");
        let r = audit_chain_append(p, serde_json::json!([1, 2, 3])).await;
        assert!(r.is_err());
    }

    #[tokio::test]
    async fn status_reflects_state() {
        let p = tmp_jsonl("status");
        let sha = audit_chain_append(
            p.clone(),
            serde_json::json!({"event_type": "test", "x": 1}),
        )
        .await
        .unwrap();

        let s = audit_chain_status(p).await.unwrap();
        assert_eq!(s.count, 1);
        assert_eq!(s.first_sha256, sha);
        assert_eq!(s.last_sha256, sha);
    }
}
