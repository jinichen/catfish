//! Self-Evolution 可见化 —— 把 Hermes 默默在做的"自我学习"显性化给员工。
//!
//! Hermes 已有的能力 (我们不重新实现):
//!   - memory tool 自动写 USER.md + memories/
//!   - skill_manage tool 自动创建 SKILL.md
//!   - session_search 跨会话搜索经验
//!   - trajectory_compressor 压缩长会话
//!
//! 我们做的: 聚合多源数据回答"鲶鱼今天学了什么":
//!   1. memories 文件 (USER.md + memories/*.md) 今天有没有更新
//!   2. skills/ 下今天新增 / 修改的 SKILL.md 数量
//!   3. state.db sessions 今天启动的会话数 + 总 token
//!   4. state.db messages 今天的 tool 调用总数
//!
//! 数据全部从现有 ~/.hermes 读, 没引入任何新存储。

use std::path::PathBuf;
use std::time::UNIX_EPOCH;

use rusqlite::Connection;
use serde::Serialize;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MemoryFile {
    /// 文件名(不含 .md), 如 "USER" / "preferences"
    pub name: String,
    /// 字节数
    pub size: u64,
    /// ISO-8601 mtime
    pub modified_at: String,
    /// 是不是今天改的
    pub modified_today: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NewSkill {
    /// 完整 namespace/name, 如 "dogfood/auto-feishu-reply"
    pub full_name: String,
    /// SKILL.md 里 description
    pub description: String,
    /// ISO-8601 mtime
    pub modified_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TodayLearningStats {
    /// memories 总览 —— 今天有更新的列表
    pub memories: Vec<MemoryFile>,
    pub memories_updated_today: u32,

    /// 今天新增 / 修改的 skill (按 mtime)
    pub new_skills: Vec<NewSkill>,
    pub new_skills_count: u32,

    /// 今天启动的会话数 (跨 cli + companion)
    pub sessions_today: u32,
    /// 今天的总 tool 调用次数
    pub tool_calls_today: u32,
    /// 今天的 token 消耗 (input + output + cache + reasoning)
    pub total_tokens_today: u64,

    /// 一句人话总结
    pub summary: String,
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn unix_to_iso(secs: f64) -> String {
    let s = secs.trunc() as i64;
    let n = (secs.fract().abs() * 1e9) as u32;
    chrono::DateTime::from_timestamp(s, n)
        .map(|d| d.to_rfc3339())
        .unwrap_or_default()
}

fn today_start_unix() -> f64 {
    use chrono::{Local, NaiveTime, TimeZone};
    let now = Local::now();
    let today_midnight = now
        .date_naive()
        .and_time(NaiveTime::from_hms_opt(0, 0, 0).unwrap());
    Local
        .from_local_datetime(&today_midnight)
        .single()
        .map(|dt| dt.timestamp() as f64)
        .unwrap_or(0.0)
}

fn is_today(mtime_unix: f64) -> bool {
    mtime_unix >= today_start_unix()
}

// ============================================================
// 数据收集
// ============================================================

fn collect_memories(home: &PathBuf) -> Vec<MemoryFile> {
    let mut out = Vec::new();
    // ~/.hermes/USER.md
    let user_md = home.join(".hermes").join("USER.md");
    if let Ok(meta) = std::fs::metadata(&user_md) {
        let mtime = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        out.push(MemoryFile {
            name: "USER".to_string(),
            size: meta.len(),
            modified_at: unix_to_iso(mtime),
            modified_today: is_today(mtime),
        });
    }

    // ~/.hermes/memories/*.md  —— name 加 "memories/" 前缀,跟顶层 USER.md 区分
    let mem_dir = home.join(".hermes").join("memories");
    if let Ok(entries) = std::fs::read_dir(&mem_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("md") {
                continue;
            }
            let stem = path
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("(unnamed)");
            let name = format!("memories/{stem}");
            if let Ok(meta) = std::fs::metadata(&path) {
                let mtime = meta
                    .modified()
                    .ok()
                    .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                    .map(|d| d.as_secs_f64())
                    .unwrap_or(0.0);
                out.push(MemoryFile {
                    name,
                    size: meta.len(),
                    modified_at: unix_to_iso(mtime),
                    modified_today: is_today(mtime),
                });
            }
        }
    }
    out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
    out
}

fn collect_new_skills(home: &PathBuf) -> Vec<NewSkill> {
    let mut out = Vec::new();
    let skills_dir = home.join(".hermes").join("skills");
    let Ok(ns_entries) = std::fs::read_dir(&skills_dir) else {
        return out;
    };
    for ns_entry in ns_entries.flatten() {
        let ns_path = ns_entry.path();
        if !ns_path.is_dir() {
            continue;
        }
        let ns_name = match ns_path.file_name().and_then(|s| s.to_str()) {
            Some(n) if !n.starts_with('.') => n.to_string(),
            _ => continue,
        };
        let Ok(skill_iter) = std::fs::read_dir(&ns_path) else {
            continue;
        };
        for skill_entry in skill_iter.flatten() {
            let skill_path = skill_entry.path();
            if !skill_path.is_dir() {
                continue;
            }
            let manifest = skill_path.join("SKILL.md");
            let Ok(meta) = std::fs::metadata(&manifest) else {
                continue;
            };
            let mtime = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            if !is_today(mtime) {
                continue;
            }
            let skill_name = skill_path
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("(unnamed)")
                .to_string();
            // 抓 SKILL.md frontmatter description
            let description = std::fs::read_to_string(&manifest)
                .ok()
                .and_then(|text| extract_description(&text))
                .unwrap_or_else(|| "(无 description)".into());
            out.push(NewSkill {
                full_name: format!("{ns_name}/{skill_name}"),
                description,
                modified_at: unix_to_iso(mtime),
            });
        }
    }
    out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
    out
}

/// 从 SKILL.md 的 YAML frontmatter 抽 description
fn extract_description(text: &str) -> Option<String> {
    let trimmed = text.trim_start();
    if !trimmed.starts_with("---") {
        return None;
    }
    let after = trimmed.strip_prefix("---")?.trim_start_matches('\n');
    let end = after.find("\n---")?;
    let frontmatter = &after[..end];
    for line in frontmatter.lines() {
        if let Some(rest) = line.strip_prefix("description:") {
            let value = rest.trim().trim_matches(|c| c == '"' || c == '\'');
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }
    None
}

fn collect_db_stats(home: &PathBuf) -> (u32, u32, u64) {
    let db_path = home.join(".hermes").join("state.db");
    if !db_path.exists() {
        return (0, 0, 0);
    }
    let Ok(conn) = Connection::open(&db_path) else {
        return (0, 0, 0);
    };
    let today_start = today_start_unix();

    // 今天启动的会话 + token 求和
    let (sessions, tokens): (u32, u64) = conn
        .query_row(
            r#"
            SELECT
                COUNT(*) AS n,
                COALESCE(SUM(
                    COALESCE(input_tokens, 0) +
                    COALESCE(output_tokens, 0) +
                    COALESCE(cache_read_tokens, 0) +
                    COALESCE(cache_write_tokens, 0) +
                    COALESCE(reasoning_tokens, 0)
                ), 0) AS tok
            FROM sessions
            WHERE started_at >= ?1
        "#,
            [today_start],
            |row| {
                let n: i64 = row.get(0)?;
                let tok: i64 = row.get(1)?;
                Ok((n.max(0) as u32, tok.max(0) as u64))
            },
        )
        .unwrap_or((0, 0));

    // 今天的 tool_calls 总数
    let tool_calls: u32 = conn
        .query_row(
            r#"
            SELECT COUNT(*) FROM messages
            WHERE timestamp >= ?1
              AND tool_calls IS NOT NULL
              AND tool_calls != ''
        "#,
            [today_start],
            |row| row.get::<_, i64>(0).map(|n| n.max(0) as u32),
        )
        .unwrap_or(0);

    (sessions, tool_calls, tokens)
}

fn build_summary(
    memories_today: u32,
    skills_today: u32,
    sessions: u32,
    tool_calls: u32,
) -> String {
    if memories_today == 0 && skills_today == 0 && sessions == 0 {
        return "今天还没动静——跟小鲶聊聊它就开始学了。".into();
    }
    let mut parts: Vec<String> = Vec::new();
    if sessions > 0 {
        parts.push(format!("{sessions} 次对话"));
    }
    if tool_calls > 0 {
        parts.push(format!("调用 {tool_calls} 次工具"));
    }
    if memories_today > 0 {
        parts.push(format!("更新 {memories_today} 条 memory"));
    }
    if skills_today > 0 {
        parts.push(format!("新增 {skills_today} 个 skill"));
    }
    format!("今天小鲶 {}。", parts.join("、"))
}

// ============================================================
// 单测 —— pure logic (frontmatter parser / 时间边界 / summary builder)
// I/O 部分 (collect_memories / collect_db_stats) 跟 Python catfish_tools.py
// 测试镜像 (见 edge/tool-bridge/tests/test_catfish_tools.py 的 16 个边界)
// 跑法: cargo test --lib commands::learning
// ============================================================
#[cfg(test)]
mod tests {
    use super::*;

    // ---------- extract_description ----------

    #[test]
    fn extract_description_basic() {
        let text = "---\nname: foo\ndescription: hello world\n---\n# Body";
        assert_eq!(extract_description(text), Some("hello world".into()));
    }

    #[test]
    fn extract_description_quoted() {
        let text = "---\ndescription: \"quoted value\"\n---\n";
        assert_eq!(extract_description(text), Some("quoted value".into()));
    }

    #[test]
    fn extract_description_single_quoted() {
        let text = "---\ndescription: 'single'\n---\n";
        assert_eq!(extract_description(text), Some("single".into()));
    }

    #[test]
    fn extract_description_no_frontmatter() {
        let text = "# 直接是 markdown";
        assert_eq!(extract_description(text), None);
    }

    #[test]
    fn extract_description_no_description_field() {
        let text = "---\nname: foo\nversion: 1\n---\n";
        assert_eq!(extract_description(text), None);
    }

    #[test]
    fn extract_description_empty_value_skipped() {
        let text = "---\ndescription:\n---\n";
        assert_eq!(extract_description(text), None);
    }

    #[test]
    fn extract_description_unterminated_frontmatter() {
        let text = "---\ndescription: never closed\n";
        assert_eq!(extract_description(text), None);
    }

    // ---------- 时间边界 ----------

    #[test]
    fn today_start_is_in_past() {
        let start = today_start_unix();
        let now = std::time::SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        assert!(start <= now, "today_start should be <= now");
        assert!(now - start < 86_400.0, "today_start should be < 24h ago");
    }

    #[test]
    fn is_today_true_for_recent() {
        let now = std::time::SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        assert!(is_today(now));
    }

    #[test]
    fn is_today_false_for_yesterday() {
        let yesterday = today_start_unix() - 1.0;
        assert!(!is_today(yesterday));
    }

    // ---------- build_summary ----------

    #[test]
    fn build_summary_no_activity() {
        let s = build_summary(0, 0, 0, 0);
        assert!(s.contains("还没动静"));
    }

    #[test]
    fn build_summary_mixed_activity() {
        let s = build_summary(2, 1, 5, 12);
        assert!(s.contains("5 次对话"));
        assert!(s.contains("12 次工具"));
        assert!(s.contains("2 条 memory"));
        assert!(s.contains("1 个 skill"));
    }

    #[test]
    fn build_summary_only_sessions() {
        let s = build_summary(0, 0, 3, 0);
        assert!(s.contains("3 次对话"));
        assert!(!s.contains("memory"));
        assert!(!s.contains("skill"));
        assert!(!s.contains("工具"));
    }

    // ---------- unix_to_iso ----------

    #[test]
    fn unix_to_iso_basic() {
        // 2026-04-26 00:00:00 UTC = 1777161600
        let iso = unix_to_iso(1777161600.0);
        assert!(iso.starts_with("2026-04-26"));
    }

    #[test]
    fn unix_to_iso_zero_returns_unix_epoch() {
        let iso = unix_to_iso(0.0);
        assert!(iso.starts_with("1970-01-01"));
    }
}

// ============================================================
// Tauri command
// ============================================================

#[tauri::command]
pub async fn learning_today_stats() -> Result<TodayLearningStats, String> {
    tokio::task::spawn_blocking(|| {
        let home = home_dir().ok_or_else(|| "找不到 home 目录".to_string())?;
        let memories = collect_memories(&home);
        let memories_updated_today =
            memories.iter().filter(|m| m.modified_today).count() as u32;

        let new_skills = collect_new_skills(&home);
        let new_skills_count = new_skills.len() as u32;

        let (sessions_today, tool_calls_today, total_tokens_today) =
            collect_db_stats(&home);

        let summary = build_summary(
            memories_updated_today,
            new_skills_count,
            sessions_today,
            tool_calls_today,
        );

        Ok::<TodayLearningStats, String>(TodayLearningStats {
            memories,
            memories_updated_today,
            new_skills,
            new_skills_count,
            sessions_today,
            tool_calls_today,
            total_tokens_today,
            summary,
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}
