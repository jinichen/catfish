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

/// 把 journal markdown 切成 entry 列表.
///
/// journal 格式 (gateway 写的):
/// ```
/// ## 2026-05-04 14:30 · session `…abc123`     <- ## 是 meta 头 (日期 + session id)
///
/// ### 资质周报草稿                              <- ### 是 LLM 写的真主题
/// 鸿波让我写本周周报. 协商 4 段格式...           <- 正文
///
/// ## 2026-05-04 13:00 · session `…xyz`
/// ### 跟领导沟通邮件
/// ...
/// ```
///
/// 5/5 鸿波拍板:
/// - 标题用 ### (LLM 写的主题), 不用 ## (没意义的 session id 字符串)
/// - 同 meta (日期+session_id) 重复条目去重 (race condition 时代留下的污染),
///   留**正文最长**那条 (最完整)
/// - 倒序 (最新在前, 按 meta 字符串 DESC, "YYYY-MM-DD" 字母序 == 时间序)
/// - 截最近 N 条
fn parse_journal(content: &str, max_entries: usize) -> Vec<JournalEntry> {
    struct Raw {
        meta: String,    // ## 行 (date + session id), 既做去重 key 也做时间排序
        title: String,   // ### 行 (LLM 主题), 找不到 fallback meta
        body: String,
    }

    // 1. 解析所有 raw entries
    let mut raws: Vec<Raw> = Vec::new();
    let mut current: Option<Raw> = None;
    for line in content.lines() {
        if let Some(meta) = line.strip_prefix("## ") {
            if let Some(r) = current.take() {
                raws.push(r);
            }
            current = Some(Raw {
                meta: meta.trim().to_string(),
                title: String::new(),
                body: String::new(),
            });
            continue;
        }
        if let Some(r) = current.as_mut() {
            // 第一个 ### 抽成 title, 之后的 ### 仍当 body 一部分 (Markdown 内嵌的)
            if r.title.is_empty() {
                if let Some(t) = line.strip_prefix("### ") {
                    r.title = t.trim().to_string();
                    continue;
                }
            }
            if !r.body.is_empty() {
                r.body.push('\n');
            }
            r.body.push_str(line);
        }
    }
    if let Some(r) = current.take() {
        raws.push(r);
    }

    // 2. dedup by meta — 留 body 最长的
    let mut by_meta: std::collections::HashMap<String, Raw> = std::collections::HashMap::new();
    for r in raws.into_iter() {
        let body_len = r.body.len();
        match by_meta.get(&r.meta) {
            Some(existing) if existing.body.len() >= body_len => {}
            _ => {
                by_meta.insert(r.meta.clone(), r);
            }
        }
    }

    // 3. sort by meta DESC (yaml meta 形如 "YYYY-MM-DD HH:MM · session …", 字母序 == 时间序)
    let mut sorted: Vec<Raw> = by_meta.into_values().collect();
    sorted.sort_by(|a, b| b.meta.cmp(&a.meta));

    // 4. truncate + 转 JournalEntry
    sorted.truncate(max_entries);
    sorted
        .into_iter()
        .map(|r| JournalEntry {
            title: if r.title.is_empty() { r.meta.clone() } else { r.title },
            body: r.body.trim().to_string(),
        })
        .collect()
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
        // 5/5 鸿波拍板:
        //   v1 (5/5 早): 5 → 30 条
        //   v2 (5/5 凌晨): "不要只限 30 条, 要能垂直滚动看到所有" → 10000 (实际全量)
        // 跟 sessions_list MAX_SESSIONS=10000 同上限. journal 一年也就几百条,
        // 远 < 10K. 前端 RelationCard 已有 max-height + overflow:auto 支撑滚动.
        view.recent_entries = parse_journal(&content, 10000);
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

/// 5/6 BL-E13.5 真主动 Phase A: 给 useProactiveTriggers 用的 raw journal 文本.
/// recent_entries 是 LLM summary 已抽象, 信号触发器要原文扫 deadline 关键词.
/// 限大小 (≤ 200KB tail), 防异常大 journal 卡死.
///
/// 5/7 修 bug: byte slice 必须落在 UTF-8 char boundary, 不能切到中文 / emoji 中间.
/// 鸿波 journal 580KB 时, content.len()-MAX=392690 正好落在 '原' 的字节中间, panic.
#[tauri::command]
pub fn journal_read_raw() -> Result<String, String> {
    let jp = journal_path()?;
    if !jp.exists() {
        return Ok(String::new());
    }
    let content = fs::read_to_string(&jp)
        .map_err(|e| format!("读 journal 失败: {e}"))?;
    const MAX: usize = 200_000;
    if content.len() <= MAX {
        return Ok(content);
    }
    // 算 tail 起点 (期望 byte 位置), 然后向前找最近的 char boundary
    // 防止落在多字节字符中间 panic.
    let mut start = content.len() - MAX;
    while start < content.len() && !content.is_char_boundary(start) {
        start += 1;
    }
    Ok(content[start..].to_string())
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
    fn parse_journal_extracts_llm_title_not_meta() {
        // 5/5 鸿波拍板: title 应该是 LLM 写的 ### 主题, 不是 ## meta 行
        let md = "## 2026-05-01 14:30 · session `…abc`\n\n### 资质周报草稿\n讨论 X.\n\n## 2026-05-02 09:00 · session `…def`\n\n### 安全检查清单\n协商 Y.\n";
        let entries = parse_journal(md, 5);
        assert_eq!(entries.len(), 2);
        // 倒序 (5/2 在前)
        assert_eq!(entries[0].title, "安全检查清单");
        assert_eq!(entries[1].title, "资质周报草稿");
    }

    #[test]
    fn parse_journal_falls_back_to_meta_when_no_llm_title() {
        // ### 缺时, 用 ## meta 当 title (老格式 / LLM 没写好的兜底)
        let md = "## 2026-05-01 直接正文没主题\n讨论了 X.\n";
        let entries = parse_journal(md, 5);
        assert_eq!(entries.len(), 1);
        assert!(entries[0].title.contains("2026-05-01"));
    }

    #[test]
    fn parse_journal_dedupes_same_meta_keeps_longest() {
        // race condition 时代: 同一 session 写了 3 次, 留 body 最长的那条
        let md = "## 2026-05-03 17:26 · session `…dfe`\n\n### 短的\nshort\n\n## 2026-05-03 17:26 · session `…dfe`\n\n### 长的\nthis is much much longer body content here.\n\n## 2026-05-03 17:26 · session `…dfe`\n\n### 中等\nmedium\n";
        let entries = parse_journal(md, 5);
        assert_eq!(entries.len(), 1, "同 meta 应去重成 1 条");
        assert_eq!(entries[0].title, "长的");  // body 最长那条的 title
    }

    #[test]
    fn parse_journal_sorts_by_meta_desc() {
        let md = "## 2026-05-01 09:00 · session `…aaa`\n### 早\n.\n\n## 2026-05-03 17:26 · session `…ccc`\n### 晚\n.\n\n## 2026-05-02 12:00 · session `…bbb`\n### 中\n.\n";
        let entries = parse_journal(md, 5);
        assert_eq!(entries.len(), 3);
        // meta 字符串倒序 == 时间倒序 (因为 YYYY-MM-DD 字母序 == 时间序)
        assert_eq!(entries[0].title, "晚");   // 5/3 最新
        assert_eq!(entries[1].title, "中");   // 5/2
        assert_eq!(entries[2].title, "早");   // 5/1 最老
    }

    #[test]
    fn parse_journal_truncates() {
        let mut md = String::new();
        for i in 0..10 {
            // 用真 meta 形态, sort 才有意义
            md.push_str(&format!("## 2026-05-{:02} 12:00 · session `…s{}`\n### entry {}\nbody {}\n\n", i + 1, i, i, i));
        }
        let entries = parse_journal(&md, 3);
        assert_eq!(entries.len(), 3);
        // 取最近 3 条 (i=9, 8, 7)
        assert_eq!(entries[0].title, "entry 9");
        assert_eq!(entries[1].title, "entry 8");
        assert_eq!(entries[2].title, "entry 7");
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
