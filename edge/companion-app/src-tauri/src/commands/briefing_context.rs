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

use std::path::{Path, PathBuf};
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

    /// P3.4.6 (6/15 鸿波): ~/.hermes/memories/MEMORY.md 近期 § 段, 当"近期事项
    /// context" 喂 advisor.
    ///
    /// # 背景
    /// hermes memory_tool 写 MEMORY.md 时按 § 分段追加, 每段一个语义单元
    /// (资质评估 / 月度通报模版 / 一级建造师补位 / 6/10 待办...). 这些段是
    /// **员工最有营养的近期事实**, 但之前 advisor 完全看不到 — catfish-memory
    /// plugin prefetch 只注 wiki 标题列表, 不动 hermes MEMORY 内容.
    ///
    /// # 为啥不直接当 TODO 喂 journal_todos_fetch
    /// MEMORY.md 设计上**不是** TODO 列表, 是员工长期记忆 (含事实 / 决策 /
    /// 待办碎片混在一起). 直接抽段当 TODO 显示在早安卡 "TODO" 卡片里会让
    /// 员工误以为 "AI 把我的 hermes memory 都当作必须办的事" — 反而误导.
    /// 当 advisor prompt 的"近期事项 context"用 (不强制 advisor 推 TODO 卡)
    /// 语义最稳, 跟 distilled_facts 同位 (长期画像 vs 近期事项 两层).
    ///
    /// # 实现
    /// 读 MEMORY.md (max 8KB) → split('§') → trim 空段 → 倒序累加到 max 3KB → 翻回正序 → join.
    /// 文件不存在 / 读失败 → 空字符串 (跟其他字段同模式).
    pub hermes_memory_recent: String,
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

fn read_file_safe(path: &Path, max_bytes: usize) -> String {
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

    // P3.4.6 (6/15 鸿波): 抽 hermes MEMORY.md 近期 § 段当"近期事项 context".
    //   ~/.catfish/employee_journal.md 不存在时, 之前 advisor 三件套 todos 全空,
    //   但 hermes MEMORY 里其实有大量员工实质内容 (鸿波 case: 资质评估 / 一级建造师
    //   补位 / 6/10 待办), 不喂可惜. 注: 不当 TODO, 当 context — TODO 严格走
    //   employee_journal - [ ] 显式 checkbox (journal_todos_fetch 行为不变).
    let hermes_memory_recent = read_hermes_memory_recent(3000);

    Ok(BriefingContext {
        distilled_facts,
        recent_session_briefs,
        session_goal,
        workplan,
        projects,
        weekly_reports,
        hermes_memory_recent,
    })
}

/// P3.4.6 (6/15 鸿波): 读 ~/.hermes/memories/MEMORY.md 近期 § 段, 喂 advisor 当
/// "近期事项 context".
///
/// 算法:
///   1. 读 max 8KB (防大文件占内存, 同 read_file_safe 走 utf-8 boundary)
///   2. 委托 extract_recent_segments 抽段 (纯函数, 见下面单测)
///
/// 文件不存在 / 读失败 → 空字符串 (跟 read_file_safe 同模式).
fn read_hermes_memory_recent(max_bytes: usize) -> String {
    let home = match std::env::var("HOME") {
        Ok(h) => h,
        Err(_) => return String::new(),
    };
    let path = PathBuf::from(home)
        .join(".hermes")
        .join("memories")
        .join("MEMORY.md");
    if !path.exists() {
        return String::new();
    }
    let raw = read_file_safe(&path, 8192);
    extract_recent_segments(&raw, max_bytes)
}

/// P3.4.6 (6/15 鸿波): 抽 § 段算法 — 纯函数, 单测覆盖.
///
/// 算法:
///   1. split('§') 切段, trim 每段, 过滤空
///   2. 倒序累加段长度 (= "近期", hermes append-only 末尾段是最新写的) 到
///      max_bytes 止
///   3. 翻回正序, join("\n§\n") 还原分隔符
///
/// 边界:
///   - 空输入 → 空字符串
///   - 全是 § / 全空段 → 空字符串
///   - 第一段就超 max_bytes → 仍至少返 1 段 (不能因 max 卡死, 不然 advisor 啥都看不到)
pub(crate) fn extract_recent_segments(raw: &str, max_bytes: usize) -> String {
    if raw.is_empty() {
        return String::new();
    }

    let segments: Vec<&str> = raw
        .split('§')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect();
    if segments.is_empty() {
        return String::new();
    }

    // 倒序累加段, 到 max_bytes 止 (但至少返 1 段). 然后翻正序.
    //   每段算 len + 4 ("\n§\n" 分隔符开销 + 容差)
    let mut out: Vec<&str> = Vec::new();
    let mut total = 0usize;
    for seg in segments.iter().rev() {
        let len = seg.len() + 4;
        if !out.is_empty() && total + len > max_bytes {
            break;
        }
        out.push(seg);
        total += len;
    }
    out.reverse();
    out.join("\n§\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_empty_input() {
        assert_eq!(extract_recent_segments("", 1000), "");
    }

    #[test]
    fn test_extract_only_separators() {
        assert_eq!(extract_recent_segments("§§§", 1000), "");
        assert_eq!(extract_recent_segments("§\n§\n§", 1000), "");
    }

    #[test]
    fn test_extract_single_segment() {
        let raw = "资质评估流程：以后决定资质是否需要建设之前, 必须进行正式评估.";
        let out = extract_recent_segments(raw, 1000);
        assert_eq!(out, "资质评估流程：以后决定资质是否需要建设之前, 必须进行正式评估.");
    }

    #[test]
    fn test_extract_multiple_under_limit() {
        let raw = "段一\n§\n段二\n§\n段三";
        let out = extract_recent_segments(raw, 1000);
        assert_eq!(out, "段一\n§\n段二\n§\n段三");
    }

    #[test]
    fn test_extract_trim_whitespace() {
        let raw = "  段一  \n§\n\n  段二  \n";
        let out = extract_recent_segments(raw, 1000);
        assert_eq!(out, "段一\n§\n段二");
    }

    #[test]
    fn test_extract_recent_under_limit() {
        // 鸿波 MEMORY.md 真实示例 (摘): 5 个段, max_bytes 充裕 → 全返
        let raw = "资质评估流程：必须正式评估.\n§\nmylearning.cn 代理模式补充.\n§\n月度资质通报模版.\n§\n一级建造师补位已完成.\n§\n中电高新发票佐证已发起.";
        let out = extract_recent_segments(raw, 2000);
        assert!(out.contains("资质评估流程"));
        assert!(out.contains("中电高新发票"));
        assert!(out.matches("§").count() >= 4);
    }

    #[test]
    fn test_extract_recent_over_limit_keeps_tail() {
        // 5 段, 每段 ~50 bytes, max_bytes=120 → 只能放后 2-3 段
        let raw = "段 1 内容大约 50 字节长的文本占位 padding xxxxx\n§\n段 2 内容大约 50 字节长的文本占位 padding xxxxx\n§\n段 3 内容大约 50 字节长的文本占位 padding xxxxx\n§\n段 4 内容大约 50 字节长的文本占位 padding xxxxx\n§\n段 5 内容大约 50 字节长的文本占位 padding xxxxx";
        let out = extract_recent_segments(raw, 120);
        // 必须包含末尾 (近期) 段, 不能含前几段
        assert!(out.contains("段 5"), "应保留末尾段 (近期); out: {out}");
        assert!(!out.contains("段 1"), "不应保留开头段 (旧); out: {out}");
        assert!(out.len() <= 200);  // 留点容差
    }

    #[test]
    fn test_extract_single_seg_over_limit_still_returned() {
        // 单段超 max_bytes: 仍返 1 段 (不卡死 advisor 啥都看不到)
        let raw = "这是一个很长的单段, 长度肯定超过 10 字节的 max 限制. 仍然要返回.";
        let out = extract_recent_segments(raw, 10);
        assert!(!out.is_empty(), "单段超 max 时仍应返 1 段, 当前: {out:?}");
        assert!(out.contains("很长的单段"));
    }
}

/// 5/21 Phase 6: 扫 ~/.catfish/outputs/ 下文件名含 'weekly' 的, 列文件名 + mtime.
/// 不读内容 (.docx/.xlsx 二进制, LLM 看不了), 只让 LLM 知道员工有/没周报历史 + 哪个时段.
fn scan_weekly_reports(dir: &Path) -> Result<Vec<WeeklyReportRef>, String> {
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
