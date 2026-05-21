//! BL-BRIEFING-DECISION (5/21): 早安播报 Phase 5 综合判断的上下文拉取.
//!
//! Phase 3 把数据机械分桶 (急邮件→1级, 中→2级), 不算"综合判断". Phase 5 喂 LLM
//! 5 类数据 → 出 Decision JSON (主线 + 证据 + 行动 + 风险). 本文件给 LLM 用的
//! "上下文包" 拉取.
//!
//! 输出 BriefingContext 一次性拉:
//!   - distilled_facts: ~/.catfish/distilled_facts.md 全文 (gateway memory_distill 5/20 ship 写)
//!   - recent_session_titles: 最近 N session 的 title + first_user_message
//!     (代替读全部 chat history, 太重)
//!   - session_goal: ~/.catfish/session_goal.txt (员工自己写的今日重点, 5/20 GoalInput
//!     虽 UI 删了文件还在用)
//!
//! 不在这里:
//!   - 邮件 / 日历 / TODO — 前端有现成 commands 拉. 复用.

use std::path::PathBuf;
use serde::Serialize;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BriefingContext {
    /// ~/.catfish/distilled_facts.md 全文 (gateway memory_distill 跨 session 蒸馏).
    /// 内容是 markdown bullet list, 描述员工长期偏好 / 客户 / 项目.
    /// 不存在 / 读失败 → 空字符串
    pub distilled_facts: String,

    /// 最近 N 个 session 的 title + first_user_message, 给 LLM 看"最近聊了什么".
    /// 来自 ~/.hermes/state.db sessions 表 (复用 sessions_list 同源).
    /// 5/21 Phase 6: 从 24h 扩到 7 天 (周维度需要)
    pub recent_session_briefs: Vec<SessionBrief>,

    /// ~/.catfish/session_goal.txt (员工自己设的今日重点). 不存在返空.
    pub session_goal: String,

    // ── 5/21 Phase 6 加的 3 个新数据源 (周维度) ──

    /// 5/21 Phase 6: ~/.catfish/workplan.md (员工自己写的本周/本月计划, 可选).
    /// 格式自由 markdown, 给 LLM 看作"员工自述目标". 不存在 → 空字符串.
    pub workplan: String,

    /// 5/21 Phase 6: ~/.catfish/projects.md (员工自己维护的项目跟踪, 可选).
    /// markdown bullet list "- 项目 A: 状态 X (60%)" 这种. 不存在 → 空字符串.
    /// LLM 看了知道"员工在做的几个项目跑到哪步".
    pub projects: String,

    /// 5/21 Phase 6: scan ~/.catfish/outputs/ 下 weekly-* / weekly_report-* 文件名 + mtime.
    /// 给 LLM 知道"员工上周/本周生成过哪些周报". 内容不读 (.docx/.xlsx 二进制),
    /// 只列文件名 + 时间. LLM 综合时知道员工有/没有周报历史.
    pub weekly_reports: Vec<WeeklyReportRef>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WeeklyReportRef {
    pub filename: String,
    /// ISO-8601 mtime
    pub modified_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionBrief {
    pub id: String,
    pub title: String,
    pub started_at: String,
    pub first_user_message: String,
    pub message_count: u32,
}

fn read_file_safe(path: &PathBuf, max_bytes: usize) -> String {
    if !path.exists() {
        return String::new();
    }
    match std::fs::read_to_string(path) {
        Ok(content) => {
            if content.len() > max_bytes {
                // utf-8 char boundary 切到 max_bytes
                let mut end = max_bytes;
                while end > 0 && !content.is_char_boundary(end) {
                    end -= 1;
                }
                content[..end].to_string() + "\n…(已截断)"
            } else {
                content
            }
        }
        Err(_) => String::new(),
    }
}

/// 拉早安播报综合判断需要的上下文包.
///
/// 限定:
///   - distilled_facts 最多 3KB (LLM token 控)
///   - recent_session_briefs 最多 5 个 (最近 24h, 否则太老不相关)
///   - session_goal 最多 500 字 (员工写的就一两句话)
#[tauri::command]
pub async fn briefing_context_fetch() -> Result<BriefingContext, String> {
    let home = std::env::var("HOME").map_err(|e| format!("HOME 没设: {e}"))?;
    let catfish_dir = PathBuf::from(&home).join(".catfish");

    let distilled_facts = read_file_safe(&catfish_dir.join("distilled_facts.md"), 3000);
    let session_goal = read_file_safe(&catfish_dir.join("session_goal.txt"), 500);
    // 5/21 Phase 6: workplan + projects 是员工 optional 维护的, 不存在不挂
    let workplan = read_file_safe(&catfish_dir.join("workplan.md"), 2000);
    let projects = read_file_safe(&catfish_dir.join("projects.md"), 2000);

    // 5/21 Phase 6: sessions 从 24h 扩到 7 天 (周维度), 取 top 10
    let recent_session_briefs = tokio::task::spawn_blocking(|| {
        fetch_recent_sessions(10, 168)  // limit=10, hours=7*24=168
    })
    .await
    .map_err(|e| format!("join error: {e}"))?
    .unwrap_or_default();

    // 5/21 Phase 6: scan ~/.catfish/outputs/ 下 weekly-* 文件
    let outputs_dir = catfish_dir.join("outputs");
    let weekly_reports = scan_weekly_reports(&outputs_dir).unwrap_or_default();

    Ok(BriefingContext {
        distilled_facts,
        recent_session_briefs,
        session_goal,
        workplan,
        projects,
        weekly_reports,
    })
}

/// 5/21 Phase 6: 扫 ~/.catfish/outputs/ 下文件名含 'weekly' 的, 列文件名 + mtime.
/// 不读内容 (.docx/.xlsx 二进制, LLM 看不了), 只让 LLM 知道员工有/没周报历史 + 哪个时段.
fn scan_weekly_reports(dir: &PathBuf) -> Result<Vec<WeeklyReportRef>, String> {
    if !dir.exists() || !dir.is_dir() {
        return Ok(Vec::new());
    }
    let entries = std::fs::read_dir(dir).map_err(|e| format!("read_dir 失败: {e}"))?;
    let mut out: Vec<WeeklyReportRef> = Vec::new();
    for e in entries.flatten() {
        let path = e.path();
        let name = match path.file_name().and_then(|n| n.to_str()) {
            Some(n) => n.to_string(),
            None => continue,
        };
        let lower = name.to_lowercase();
        // 匹配 weekly-*, weekly_*, *-周报*, *_周报*
        let is_weekly = lower.contains("weekly") || lower.contains("周报");
        if !is_weekly {
            continue;
        }
        let mtime = match e.metadata().and_then(|m| m.modified()) {
            Ok(t) => {
                let dt: chrono::DateTime<chrono::Utc> = t.into();
                dt.to_rfc3339()
            }
            Err(_) => continue,
        };
        out.push(WeeklyReportRef { filename: name, modified_at: mtime });
    }
    // 按 mtime 倒序, 取最近 8 个 (~2 个月)
    out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
    out.truncate(8);
    Ok(out)
}

/// 读 ~/.hermes/state.db sessions 表, 拿最近 N 个 session 的 title + first_user_message.
/// 复用 sessions.rs 的逻辑思路但简化 (不读完整 messages, 只要元数据).
///
/// 5/21 Phase 6: 加 hours 参数, 默认 24h 但周维度时传 7*24=168.
fn fetch_recent_sessions(limit: usize, hours: i64) -> Result<Vec<SessionBrief>, String> {
    use rusqlite::Connection;

    let home = std::env::var("HOME").map_err(|e| format!("HOME 没设: {e}"))?;
    let db_path = PathBuf::from(home).join(".hermes").join("state.db");
    if !db_path.exists() {
        return Ok(Vec::new());  // hermes 没装 / state.db 不存在
    }

    let conn = Connection::open(&db_path).map_err(|e| format!("打开 state.db 失败: {e}"))?;

    // 最近 N 小时的 sessions, 按 started_at DESC. 不要 deleted_at IS NOT NULL 的 (软删除).
    // first_user_message 走 messages 表 first row content (跟 sessions.rs 思路一致, 但简化).
    let cutoff = chrono::Utc::now() - chrono::Duration::hours(hours);
    let cutoff_iso = cutoff.to_rfc3339();

    let mut stmt = conn
        .prepare(
            "SELECT id, COALESCE(title, ''), started_at, COALESCE(message_count, 0)
             FROM sessions
             WHERE deleted_at IS NULL AND started_at >= ?1
             ORDER BY started_at DESC
             LIMIT ?2",
        )
        .map_err(|e| format!("prepare 失败: {e}"))?;

    let rows = stmt
        .query_map(rusqlite::params![cutoff_iso, limit as i64], |row| {
            Ok(SessionBrief {
                id: row.get(0)?,
                title: row.get(1)?,
                started_at: row.get(2)?,
                message_count: row.get::<_, i64>(3)? as u32,
                first_user_message: String::new(),  // 下面补
            })
        })
        .map_err(|e| format!("query 失败: {e}"))?;

    let mut briefs: Vec<SessionBrief> = Vec::new();
    for r in rows {
        match r {
            Ok(s) => briefs.push(s),
            Err(_) => continue,
        }
    }

    // 补 first_user_message (每个 session 一次 query, 数量少 ≤ 5 不算重)
    for brief in briefs.iter_mut() {
        if let Ok(mut msg_stmt) = conn.prepare(
            "SELECT COALESCE(content, '') FROM messages
             WHERE session_id = ?1 AND role = 'user'
             ORDER BY rowid ASC LIMIT 1",
        ) {
            if let Ok(content) = msg_stmt.query_row(rusqlite::params![&brief.id], |row| {
                row.get::<_, String>(0)
            }) {
                // 截到前 120 字
                let truncated: String = content.chars().take(120).collect();
                brief.first_user_message = truncated;
            }
        }
    }

    Ok(briefs)
}
