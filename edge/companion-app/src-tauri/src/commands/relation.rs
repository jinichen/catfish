//! BL-E16 关系建立 — Companion 透明 UI.
//!
//! 给前端 "鲶鱼对你的印象" Dashboard 卡:
//!   - 读 ~/.catfish/employee_journal.md 的最近条目 (最多 5 条)
//!   - 读 ~/.catfish/session_meta.json 的"今天第 N 次 / 距上次"
//!   - 清空两者 (员工隐私逃生口)
//!
//! 设计立场: 鲶鱼"记得"你的事, **员工必须能看到 + 删除**, 否则就 creepy.

use serde::Serialize;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Serialize)]
pub struct RelationView {
    /// 最近 N 条 journal 条目 (按 ## 标题切, 倒序, 最多 5 条)
    pub recent_entries: Vec<JournalEntry>,
    /// 距上次 chat 的人话描述 (e.g. "3 天 4 小时前"), 没记录返 None
    pub last_chat_human: Option<String>,
    /// 今天第几次找鲶鱼 (没记录返 None)
    pub today_count: Option<u32>,
    /// journal 文件总字节数 (员工想看"鲶鱼记多少事")
    pub journal_size_bytes: u64,
}

#[derive(Debug, Serialize)]
pub struct JournalEntry {
    pub title: String,
    pub body: String,
}

fn home_dir() -> Result<PathBuf, String> {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME 环境变量".to_string())
}

fn journal_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("employee_journal.md"))
}

fn meta_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("session_meta.json"))
}

/// 把 journal markdown 切成 entry 列表 (按 `## ` 开头切), 倒序, 最多 N 条.
fn parse_journal(content: &str, max_entries: usize) -> Vec<JournalEntry> {
    let mut entries: Vec<JournalEntry> = Vec::new();
    let mut current_title: Option<String> = None;
    let mut current_body: Vec<String> = Vec::new();

    for line in content.lines() {
        if let Some(title) = line.strip_prefix("## ") {
            if let Some(t) = current_title.take() {
                entries.push(JournalEntry {
                    title: t,
                    body: current_body.join("\n").trim().to_string(),
                });
                current_body.clear();
            }
            current_title = Some(title.trim().to_string());
        } else if current_title.is_some() {
            current_body.push(line.to_string());
        }
    }
    if let Some(t) = current_title.take() {
        entries.push(JournalEntry {
            title: t,
            body: current_body.join("\n").trim().to_string(),
        });
    }
    // 倒序 (最新在前) + 截断
    entries.reverse();
    entries.truncate(max_entries);
    entries
}

#[tauri::command]
pub fn relation_summary() -> Result<RelationView, String> {
    let mut view = RelationView {
        recent_entries: vec![],
        last_chat_human: None,
        today_count: None,
        journal_size_bytes: 0,
    };

    // journal
    let jp = journal_path()?;
    if jp.exists() {
        let content = fs::read_to_string(&jp).unwrap_or_default();
        view.journal_size_bytes = content.len() as u64;
        view.recent_entries = parse_journal(&content, 5);
    }

    // session_meta
    let mp = meta_path()?;
    if mp.exists() {
        if let Ok(raw) = fs::read_to_string(&mp) {
            if let Ok(json) = serde_json::from_str::<serde_json::Value>(&raw) {
                view.today_count = json.get("today_count").and_then(|v| v.as_u64()).map(|n| n as u32);
                if let Some(last) = json.get("last_chat_at").and_then(|v| v.as_str()) {
                    view.last_chat_human = humanize_since(last);
                }
            }
        }
    }
    Ok(view)
}

/// 清空 journal + meta (员工"我不想让鲶鱼记着我了" 逃生口).
/// 文件删失败仍返 ok (可能本来就没有), 失败 logger.warn 即可.
#[tauri::command]
pub fn relation_forget() -> Result<(), String> {
    if let Ok(jp) = journal_path() {
        let _ = fs::remove_file(&jp);
    }
    if let Ok(mp) = meta_path() {
        let _ = fs::remove_file(&mp);
    }
    Ok(())
}

/// chrono 太重 (不在依赖里), 自己 parse ISO8601 算"现在 - 那时"的秒差.
/// 失败返 None (前端就不显时长). 不抛.
fn humanize_since(iso: &str) -> Option<String> {
    use std::time::{SystemTime, UNIX_EPOCH};
    let last_ts = parse_iso_to_unix(iso)?;
    let now_ts = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_secs() as i64;
    let delta = now_ts.saturating_sub(last_ts);
    Some(humanize_seconds(delta))
}

fn parse_iso_to_unix(iso: &str) -> Option<i64> {
    // 极简 parse: "2026-05-03T10:35:00+08:00" / "2026-05-03T10:35:00Z" / 没 tz
    // 不引 chrono. 手撕 yyyy-mm-ddThh:mm:ss + 可选 ±hh:mm 或 Z
    let s = iso.trim();
    if s.len() < 19 {
        return None;
    }
    let date = &s[..10];  // 2026-05-03
    let time = &s[11..19]; // 10:35:00
    let rest = &s[19..];

    let mut iter = date.split('-');
    let y: i64 = iter.next()?.parse().ok()?;
    let mo: i64 = iter.next()?.parse().ok()?;
    let d: i64 = iter.next()?.parse().ok()?;
    let mut t_iter = time.split(':');
    let h: i64 = t_iter.next()?.parse().ok()?;
    let mi: i64 = t_iter.next()?.parse().ok()?;
    let se: i64 = t_iter.next()?.parse().ok()?;

    // tz 偏移秒
    let tz_offset_secs: i64 = if rest.is_empty() || rest == "Z" {
        0
    } else if rest.len() >= 6 && (rest.starts_with('+') || rest.starts_with('-')) {
        let sign = if rest.starts_with('+') { 1 } else { -1 };
        let hh: i64 = rest[1..3].parse().ok()?;
        let mm: i64 = rest[4..6].parse().ok()?;
        sign * (hh * 3600 + mm * 60)
    } else {
        0
    };

    let unix = days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60 + se - tz_offset_secs;
    Some(unix)
}

/// Howard Hinnant 的 days_from_civil 算法 — 把 (y, m, d) 算成 Unix days
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = (y - era * 400) as u64; // [0, 399]
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) as u64 + 2) / 5 + (d as u64) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy; // [0, 146096]
    era * 146_097 + doe as i64 - 719_468
}

fn humanize_seconds(secs: i64) -> String {
    if secs < 60 {
        return "刚刚".to_string();
    }
    if secs < 3600 {
        return format!("{} 分钟", secs / 60);
    }
    if secs < 86_400 {
        let h = secs / 3600;
        let m = (secs % 3600) / 60;
        if m == 0 {
            return format!("{} 小时", h);
        }
        return format!("{} 小时 {} 分", h, m);
    }
    let days = secs / 86_400;
    let hours = (secs % 86_400) / 3600;
    if hours == 0 {
        format!("{} 天", days)
    } else {
        format!("{} 天 {} 小时", days, hours)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_journal_extracts_titles_in_reverse() {
        let md = "## 2026-05-01 资质周报\n讨论了 X.\n\n## 2026-05-02 安全检查\n协商 Y.\n";
        let entries = parse_journal(md, 5);
        assert_eq!(entries.len(), 2);
        assert!(entries[0].title.contains("安全检查"));  // 倒序
        assert!(entries[1].title.contains("资质周报"));
    }

    #[test]
    fn parse_journal_truncates() {
        let mut md = String::new();
        for i in 0..10 {
            md.push_str(&format!("## entry {}\nbody {}\n\n", i, i));
        }
        let entries = parse_journal(&md, 3);
        assert_eq!(entries.len(), 3);
        assert!(entries[0].title.contains("entry 9"));
    }

    #[test]
    fn parse_journal_handles_empty() {
        assert_eq!(parse_journal("", 5).len(), 0);
        assert_eq!(parse_journal("just some text\nno headers\n", 5).len(), 0);
    }

    #[test]
    fn humanize_seconds_formats() {
        assert_eq!(humanize_seconds(30), "刚刚");
        assert_eq!(humanize_seconds(60), "1 分钟");
        assert_eq!(humanize_seconds(3600), "1 小时");
        assert_eq!(humanize_seconds(3 * 3600 + 30 * 60), "3 小时 30 分");
        assert_eq!(humanize_seconds(86_400), "1 天");
        assert_eq!(humanize_seconds(86_400 + 3 * 3600), "1 天 3 小时");
    }

    #[test]
    fn parse_iso_with_tz_offset() {
        // 2026-05-03T10:35:00+08:00 = 2026-05-03T02:35:00Z
        let utc = parse_iso_to_unix("2026-05-03T02:35:00Z").unwrap();
        let cst = parse_iso_to_unix("2026-05-03T10:35:00+08:00").unwrap();
        assert_eq!(utc, cst);
    }

    #[test]
    fn parse_iso_handles_z_suffix() {
        let v = parse_iso_to_unix("2026-05-03T00:00:00Z");
        assert!(v.is_some());
    }

    #[test]
    fn parse_iso_returns_none_on_garbage() {
        assert!(parse_iso_to_unix("not a date").is_none());
        assert!(parse_iso_to_unix("").is_none());
    }
}
