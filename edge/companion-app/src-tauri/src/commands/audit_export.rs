//! BL-AUDIT-EXPORT (P3.3.54, 6/12 鸿波 "信合规审计闭环"): 审计员看的多 sheet xlsx 导出.
//!
//! # 数据源
//! ==========
//! - `~/.catfish/decisions.jsonl` (P3.3.52 chain) — 决策留痕
//! - `~/.hermes/.catfish_audit.jsonl` (audit.py 写) — 工具调用
//! - `~/.catfish/outbound_log.db` (transparent_log A4) — HTTP 外发
//! - `audit_chain_verify(decisions.jsonl)` — chain 完整性校验报告
//!
//! # 输出
//! ======
//! `~/.catfish/exports/catfish_audit_<from>_<to>.xlsx` — 4 sheet 多页 xlsx.
//!
//! # 鸿波 6/12 拍板
//! ===============
//! - UI 卡放隐私 section
//! - 默认本月 (1 号 - 今天)
//! - 工具调用 sheet 全量导出 (不限 last 10k)
//! - 文件位置固定 ~/.catfish/exports/ 不让员工选

use std::path::PathBuf;

use rust_xlsxwriter::{Format, FormatAlign, Workbook};
use serde::Serialize;

use super::audit_chain::audit_chain_verify;
use super::transparent_log::{transparent_log_query, LogEntry};

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct ExportResult {
    pub output_path: String,
    pub bytes_written: u64,
    pub decisions_count: u64,
    pub tool_calls_count: u64,
    pub outbound_count: u64,
    pub chain_ok: bool,
    pub chain_broken_reason: Option<String>,
}

/// 工具调用 audit 一行 (parse 自 ~/.hermes/.catfish_audit.jsonl).
/// P3.3.55 (6/12): 加 pub + Serialize, 给前端审计视图用.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ToolCallRow {
    pub ts: String,
    pub tool: String,
    pub ok: bool,
    pub error: Option<String>,
    pub latency_ms: f64,
    pub args_preview: String,
}

fn home_dir() -> Result<PathBuf, String> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .ok_or_else(|| "找不到 HOME".into())
}

fn exports_dir() -> Result<PathBuf, String> {
    let home = home_dir()?;
    let dir = home.join(".catfish").join("exports");
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("创建 ~/.catfish/exports/ 失败: {e}"))?;
    Ok(dir)
}

fn decisions_jsonl_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("decisions.jsonl"))
}

fn hermes_audit_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".hermes").join(".catfish_audit.jsonl"))
}

/// ts 字符串在 [from, to] 范围内 (ISO-8601 字典序 = 时序).
fn ts_in_range(ts: &str, from: &Option<String>, to: &Option<String>) -> bool {
    if let Some(f) = from {
        if ts < f.as_str() {
            return false;
        }
    }
    if let Some(t) = to {
        if ts > t.as_str() {
            return false;
        }
    }
    true
}

/// P3.3.54 polish (6/12): 自己 parse decisions.jsonl, 拿到 sha256 / prev_sha256
/// 字段 (decision_list_recent 返 DecisionRecord struct, sha256 不在 schema).
fn read_decisions(
    from: &Option<String>,
    to: &Option<String>,
) -> Result<Vec<serde_json::Value>, String> {
    let path = home_dir()?.join(".catfish").join("decisions.jsonl");
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 decisions.jsonl 失败: {e}"))?;
    let mut out = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let v: serde_json::Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let ts = v.get("ts").and_then(|x| x.as_str()).unwrap_or("");
        if !ts_in_range(ts, from, to) {
            continue;
        }
        out.push(v);
    }
    Ok(out)
}

fn read_tool_calls(
    from: &Option<String>,
    to: &Option<String>,
) -> Result<Vec<ToolCallRow>, String> {
    let path = hermes_audit_path()?;
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 hermes audit 失败: {e}"))?;

    let mut out: Vec<ToolCallRow> = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let v: serde_json::Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue, // 坏行跳过
        };
        let ts = v.get("ts").and_then(|x| x.as_str()).unwrap_or("").to_string();
        if !ts_in_range(&ts, from, to) {
            continue;
        }
        out.push(ToolCallRow {
            ts,
            tool: v.get("tool").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            ok: v.get("ok").and_then(|x| x.as_bool()).unwrap_or(false),
            error: v.get("error").and_then(|x| x.as_str()).map(String::from),
            latency_ms: v.get("latency_ms").and_then(|x| x.as_f64()).unwrap_or(0.0),
            args_preview: v
                .get("args_preview")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string(),
        });
    }
    Ok(out)
}

/// 文件名安全 ts (去 ':' / '+' 等不友好字符, 留 YYYY-MM-DD).
fn ts_for_filename(ts: &str) -> String {
    ts.chars()
        .take_while(|c| *c != 'T' && *c != ' ')
        .collect::<String>()
        .replace(['/', '\\'], "-")
}

// ─── Tauri command ───────────────────────────────────────────────

#[tauri::command]
pub async fn audit_export_xlsx(
    from_ts: Option<String>,
    to_ts: Option<String>,
    include_decisions: bool,
    include_tool_calls: bool,
    include_outbound: bool,
) -> Result<ExportResult, String> {
    // ─── 1. 准备路径 ───
    let dir = exports_dir()?;
    let from_label = from_ts.as_deref().map(ts_for_filename).unwrap_or_else(|| "all".into());
    let to_label = to_ts.as_deref().map(ts_for_filename).unwrap_or_else(|| "now".into());
    let filename = format!("catfish_audit_{from_label}_{to_label}.xlsx");
    let output = dir.join(&filename);

    // ─── 2. 拉数据 ───
    // P3.3.54 polish: 自己 parse jsonl 拿全字段 (含 sha256), 不走 decision_list_recent
    let decisions: Vec<serde_json::Value> = if include_decisions {
        read_decisions(&from_ts, &to_ts).unwrap_or_default()
    } else {
        Vec::new()
    };

    let tool_calls: Vec<ToolCallRow> = if include_tool_calls {
        read_tool_calls(&from_ts, &to_ts).unwrap_or_default()
    } else {
        Vec::new()
    };

    let outbound: Vec<LogEntry> = if include_outbound {
        // transparent_log_query 已经 supports since 过滤. limit 大一点防截断.
        match transparent_log_query(
            from_ts.clone(),
            None,
            None,
            Some(100_000),
            Some(0),
        )
        .await
        {
            Ok(qr) => qr.entries.into_iter()
                .filter(|e| ts_in_range(&e.ts_request, &from_ts, &to_ts))
                .collect(),
            Err(_) => Vec::new(),
        }
    } else {
        Vec::new()
    };

    // ─── 3. chain verify ───
    let verify = audit_chain_verify(
        decisions_jsonl_path()?
            .to_string_lossy()
            .to_string(),
    )
    .await
    .ok();
    let chain_ok = verify.as_ref().map(|v| v.ok).unwrap_or(false);
    let chain_broken_reason = verify
        .as_ref()
        .and_then(|v| v.broken_reason.clone());

    // ─── 4. build workbook ───
    let mut wb = Workbook::new();
    let header_fmt = Format::new()
        .set_bold()
        .set_background_color("1F4E79")
        .set_font_color("FFFFFF")
        .set_align(FormatAlign::Center);
    let cell_fmt = Format::new();
    let ok_fmt = Format::new().set_font_color("16A34A");
    let err_fmt = Format::new().set_font_color("DC2626").set_bold();

    // sheet 1: 决策留痕
    if include_decisions {
        let ws = wb.add_worksheet().set_name("决策留痕").map_err(stringify)?;
        let headers = [
            "时间", "记录类型", "任务标题", "任务 uid", "新状态",
            "员工选择", "AI 倾向", "草稿路径", "sha256",
        ];
        for (col, h) in headers.iter().enumerate() {
            ws.write_string_with_format(0, col as u16, *h, &header_fmt)
                .map_err(stringify)?;
        }
        for (row_idx, d) in decisions.iter().enumerate() {
            let r = (row_idx + 1) as u32;
            let get_str = |key: &str| -> String {
                d.get(key)
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string()
            };
            ws.write_string_with_format(r, 0, get_str("ts"), &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 1, get_str("recordKind"), &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 2, get_str("taskTitle"), &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 3, get_str("taskUid"), &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 4, get_str("newStatus"), &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 5, get_str("userChoice"), &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 6, get_str("aiLean"), &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 7, get_str("draftPathChosen"), &cell_fmt).map_err(stringify)?;
            // P3.3.54 polish: 显示 sha256 前 20 字符 (审计员对比够, 全 64 字符占太宽)
            let sha = get_str("sha256");
            let short_sha = if sha.len() > 20 {
                format!("{}…", &sha[..20])
            } else {
                sha
            };
            ws.write_string_with_format(r, 8, &short_sha, &cell_fmt).map_err(stringify)?;
        }
        ws.set_column_width(0, 22).map_err(stringify)?;
        ws.set_column_width(2, 40).map_err(stringify)?;
    }

    // sheet 2: 工具调用
    if include_tool_calls {
        let ws = wb.add_worksheet().set_name("工具调用").map_err(stringify)?;
        let headers = ["时间", "工具", "成功", "错误信息", "延迟 (ms)", "参数预览"];
        for (col, h) in headers.iter().enumerate() {
            ws.write_string_with_format(0, col as u16, *h, &header_fmt)
                .map_err(stringify)?;
        }
        for (row_idx, t) in tool_calls.iter().enumerate() {
            let r = (row_idx + 1) as u32;
            ws.write_string_with_format(r, 0, &t.ts, &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 1, &t.tool, &cell_fmt).map_err(stringify)?;
            let ok_str = if t.ok { "✓" } else { "✗" };
            let fmt = if t.ok { &ok_fmt } else { &err_fmt };
            ws.write_string_with_format(r, 2, ok_str, fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 3, t.error.as_deref().unwrap_or(""), &cell_fmt).map_err(stringify)?;
            ws.write_number_with_format(r, 4, t.latency_ms, &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 5, &t.args_preview, &cell_fmt).map_err(stringify)?;
        }
        ws.set_column_width(0, 22).map_err(stringify)?;
        ws.set_column_width(1, 28).map_err(stringify)?;
        ws.set_column_width(3, 40).map_err(stringify)?;
        ws.set_column_width(5, 50).map_err(stringify)?;
    }

    // sheet 3: 数据外发
    if include_outbound {
        let ws = wb.add_worksheet().set_name("数据外发").map_err(stringify)?;
        let headers = [
            "请求时间", "方法", "URL", "上行字节", "下行字节",
            "HTTP 状态", "分类", "响应摘要", "错误",
        ];
        for (col, h) in headers.iter().enumerate() {
            ws.write_string_with_format(0, col as u16, *h, &header_fmt)
                .map_err(stringify)?;
        }
        for (row_idx, e) in outbound.iter().enumerate() {
            let r = (row_idx + 1) as u32;
            ws.write_string_with_format(r, 0, &e.ts_request, &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 1, &e.method, &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 2, &e.url, &cell_fmt).map_err(stringify)?;
            ws.write_number_with_format(r, 3, e.request_bytes as f64, &cell_fmt).map_err(stringify)?;
            ws.write_number_with_format(r, 4, e.response_bytes as f64, &cell_fmt).map_err(stringify)?;
            if let Some(s) = e.status {
                ws.write_number_with_format(r, 5, s as f64, &cell_fmt).map_err(stringify)?;
            }
            ws.write_string_with_format(r, 6, e.category.as_deref().unwrap_or(""), &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 7, e.response_summary.as_deref().unwrap_or(""), &cell_fmt).map_err(stringify)?;
            ws.write_string_with_format(r, 8, e.error.as_deref().unwrap_or(""), &cell_fmt).map_err(stringify)?;
        }
        ws.set_column_width(0, 22).map_err(stringify)?;
        ws.set_column_width(2, 50).map_err(stringify)?;
        ws.set_column_width(7, 30).map_err(stringify)?;
    }

    // sheet 4: 校验报告
    {
        let ws = wb.add_worksheet().set_name("校验报告").map_err(stringify)?;
        let headers = ["字段", "值"];
        for (col, h) in headers.iter().enumerate() {
            ws.write_string_with_format(0, col as u16, *h, &header_fmt)
                .map_err(stringify)?;
        }
        let rows: Vec<(&str, String, bool)> = vec![
            (
                "整体校验",
                if chain_ok {
                    "✓ chain 完整".into()
                } else {
                    "✗ chain 已被篡改".into()
                },
                !chain_ok,
            ),
            (
                "校验对象",
                "~/.catfish/decisions.jsonl + .chain.json".into(),
                false,
            ),
            (
                "断裂位置",
                verify
                    .as_ref()
                    .and_then(|v| v.broken_at)
                    .map(|n| format!("第 {n} 行"))
                    .unwrap_or_else(|| "无".into()),
                false,
            ),
            (
                "断裂原因",
                chain_broken_reason.clone().unwrap_or_else(|| "无".into()),
                chain_broken_reason.is_some(),
            ),
            (
                "总行数 / 期望行数",
                verify
                    .as_ref()
                    .map(|v| format!("{} / {}", v.total_lines, v.expected_count))
                    .unwrap_or_else(|| "—".into()),
                false,
            ),
            (
                "已校验行数",
                verify
                    .as_ref()
                    .map(|v| v.verified_lines.to_string())
                    .unwrap_or_else(|| "—".into()),
                false,
            ),
            (
                "chain 起头 sha256",
                verify
                    .as_ref()
                    .map(|v| v.chain_first_sha256.clone())
                    .unwrap_or_else(|| "—".into()),
                false,
            ),
            (
                "chain 末尾 sha256",
                verify
                    .as_ref()
                    .map(|v| v.chain_last_sha256.clone())
                    .unwrap_or_else(|| "—".into()),
                false,
            ),
        ];
        for (i, (field, val, is_err)) in rows.iter().enumerate() {
            let r = (i + 1) as u32;
            ws.write_string_with_format(r, 0, *field, &cell_fmt).map_err(stringify)?;
            let fmt = if *is_err { &err_fmt } else { &cell_fmt };
            ws.write_string_with_format(r, 1, val, fmt).map_err(stringify)?;
        }
        ws.set_column_width(0, 28).map_err(stringify)?;
        ws.set_column_width(1, 70).map_err(stringify)?;
    }

    // ─── 5. 写盘 ───
    wb.save(&output)
        .map_err(|e| format!("写 xlsx 失败: {e}"))?;
    let bytes = std::fs::metadata(&output).map(|m| m.len()).unwrap_or(0);

    Ok(ExportResult {
        output_path: output.to_string_lossy().to_string(),
        bytes_written: bytes,
        decisions_count: decisions.len() as u64,
        tool_calls_count: tool_calls.len() as u64,
        outbound_count: outbound.len() as u64,
        chain_ok,
        chain_broken_reason,
    })
}

fn stringify<E: std::fmt::Display>(e: E) -> String {
    format!("xlsx writer 错: {e}")
}

// ─── P3.3.55 (6/12 鸿波): 审计视图 Tab 用的 raw read API ────────────────

/// 拿 decisions.jsonl 的 raw event list (含 sha256 / prev_sha256), 倒序 (新 → 老).
#[tauri::command]
pub async fn audit_decisions_raw_read(
    from_ts: Option<String>,
    to_ts: Option<String>,
    limit: Option<u32>,
) -> Result<Vec<serde_json::Value>, String> {
    let n = limit.unwrap_or(500).min(5000) as usize;
    let mut all = read_decisions(&from_ts, &to_ts)?;
    all.reverse();
    all.truncate(n);
    Ok(all)
}

/// 拿 ~/.hermes/.catfish_audit.jsonl 的 ToolCallRow list, 倒序.
#[tauri::command]
pub async fn audit_hermes_jsonl_read(
    from_ts: Option<String>,
    to_ts: Option<String>,
    limit: Option<u32>,
) -> Result<Vec<ToolCallRow>, String> {
    let n = limit.unwrap_or(500).min(5000) as usize;
    let mut all = read_tool_calls(&from_ts, &to_ts)?;
    all.reverse();
    all.truncate(n);
    Ok(all)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 文件名 ts 安全化.
    #[test]
    fn ts_for_filename_strips_time_part() {
        assert_eq!(ts_for_filename("2026-06-12T13:35:52Z"), "2026-06-12");
        assert_eq!(ts_for_filename("2026-06-12"), "2026-06-12");
        assert_eq!(ts_for_filename("2026-06-12 13:35:52"), "2026-06-12");
    }

    #[test]
    fn ts_in_range_works() {
        let from = Some("2026-06-01".to_string());
        let to = Some("2026-06-30T23:59:59Z".to_string());
        assert!(ts_in_range("2026-06-12T10:00:00Z", &from, &to));
        assert!(!ts_in_range("2026-05-31T23:59:59Z", &from, &to));
        assert!(!ts_in_range("2026-07-01T00:00:00Z", &from, &to));
        // None 边界 = 不限
        assert!(ts_in_range("1990-01-01", &None, &None));
    }
}
