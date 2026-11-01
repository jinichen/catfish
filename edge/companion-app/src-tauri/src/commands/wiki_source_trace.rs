//! 知识卡「来源」→ 能点开的那几次对话 (11/02, 借 DeepTutor 的记忆脚注)。
//!
//! 卡的 frontmatter `sources` 里两种跟对话有关:
//!   session:<id>          精确到哪次对话 (11/02 起蒸馏时按核对过的原文段落补上)
//!   journal:YYYY-MM-DD    只到哪天 —— 把那天日志里记过的对话都列出来, 员工自己挑
//! 对话本身在 Hermes 的 state.db, 这里只读标题 / 时间 / 首句, 点开交给对话页 (openChatSession)。
//!
//! 日志抬头的格式见 catfish_memory_helpers._format_journal_entry:
//!   `## [2026-11-01 09:10] session | 20261101_091002_aaa111`
//! 8/4 之前只留后 6 位 (`…8d4bfe`) —— 按后缀匹配, 匹配到几条就列几条, 不挑。

use regex::Regex;
use rusqlite::{params, Connection, OpenFlags};
use serde::Serialize;
use std::path::Path;
use std::sync::OnceLock;

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct WikiSourceSession {
    /// 来自哪条 source (session:… / journal:…)
    pub source: String,
    pub session_id: String,
    /// 标题; 没标题用首句; 都没有为空
    pub title: String,
    /// ISO-8601
    pub started_at: String,
    /// true = session:<id> 精确指到的; false = 只按日期找到的
    pub exact: bool,
}

fn head_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?m)^##\s*\[(\d{4}-\d{2}-\d{2})[^\]]*\]\s*session(?:-raw)?\s*\|\s*…?(\S+)\s*$").unwrap()
    })
}

/// 那天日志里记过的对话 (完整 id 或老格式后缀), 按出现顺序去重
pub fn journal_hints(journal: &str, date: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for c in head_re().captures_iter(journal) {
        if &c[1] == date && !out.iter().any(|h| h == &c[2]) {
            out.push(c[2].to_string());
        }
    }
    out
}

fn unix_to_iso(unix_secs: f64) -> String {
    chrono::DateTime::from_timestamp(unix_secs.trunc() as i64, 0).map(|dt| dt.to_rfc3339()).unwrap_or_default()
}

/// `hint` 是完整 id 时精确查; 老格式后缀 (< 8 位) 用 LIKE '%hint' 且限定在那一天
fn lookup(conn: &Connection, hint: &str, date: Option<&str>) -> Vec<(String, String, String)> {
    let sql = r#"
        SELECT s.id, COALESCE(NULLIF(s.title, ''),
               (SELECT substr(m.content, 1, 60) FROM messages m
                 WHERE m.session_id = s.id AND m.role = 'user' AND m.content IS NOT NULL AND m.content != ''
                 ORDER BY m.timestamp ASC, m.rowid ASC LIMIT 1), ''),
               s.started_at
          FROM sessions s
         WHERE (s.id = ?1 OR (length(?1) < 8 AND s.id LIKE '%' || ?1
                               AND date(s.started_at, 'unixepoch', 'localtime') = ?2))
         ORDER BY s.started_at"#;
    let Ok(mut stmt) = conn.prepare(sql) else { return vec![] };
    let rows = stmt.query_map(params![hint, date.unwrap_or("")], |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, f64>(2)?))
    });
    match rows {
        Ok(it) => it.flatten().map(|(id, t, ts)| (id, t.replace('\n', " "), unix_to_iso(ts))).collect(),
        Err(_) => vec![],
    }
}

pub fn resolve(sources: &[String], journal: &str, conn: &Connection) -> Vec<WikiSourceSession> {
    let mut out: Vec<WikiSourceSession> = Vec::new();
    let mut push = |s: WikiSourceSession, out: &mut Vec<WikiSourceSession>| {
        if !out.iter().any(|o| o.session_id == s.session_id) {
            out.push(s);
        }
    };
    // 精确的排前面: 先 session:, 再 journal:
    for src in sources.iter().filter(|s| s.starts_with("session:")) {
        let id = src.trim_start_matches("session:").trim();
        for (sid, title, at) in lookup(conn, id, None) {
            push(WikiSourceSession { source: src.clone(), session_id: sid, title, started_at: at, exact: true }, &mut out);
        }
    }
    for src in sources.iter().filter(|s| s.starts_with("journal:")) {
        let date = src.trim_start_matches("journal:").trim();
        for hint in journal_hints(journal, date) {
            for (sid, title, at) in lookup(conn, &hint, Some(date)) {
                push(WikiSourceSession { source: src.clone(), session_id: sid, title, started_at: at, exact: false }, &mut out);
            }
        }
    }
    out
}

fn resolve_blocking(sources: Vec<String>) -> Result<Vec<WikiSourceSession>, String> {
    let db = crate::services::catfish_paths::hermes_state_db_path().ok_or("找不到 Hermes 用户目录")?;
    if !db.exists() {
        return Ok(vec![]);
    }
    let conn = Connection::open_with_flags(&db, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| format!("打开 state.db 失败: {e}"))?;
    let journal = crate::services::catfish_paths::catfish_home()
        .map(|h| read_journal(&h))
        .unwrap_or_default();
    Ok(resolve(&sources, &journal, &conn))
}

fn read_journal(home: &Path) -> String {
    std::fs::read_to_string(home.join("employee_journal.md")).unwrap_or_default()
}

/// 知识卡来源 → 能点开的对话 (精确的在前, 按日期找到的在后)
#[tauri::command]
pub async fn wiki_source_sessions(sources: Vec<String>) -> Result<Vec<WikiSourceSession>, String> {
    tokio::task::spawn_blocking(move || resolve_blocking(sources))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    const JOURNAL: &str = "## [2026-11-01 09:10] session | 20261101_091002_aaa111\n\n预算…\n\n\
        ## [2026-11-01 15:20] session | 20261101_152005_bbb222\n\n立项会…\n\n\
        ## [2026-11-01 18:00] distill | 3 entities\n\n\
        ## [2026-07-17 17:53] session | …8d4bfe\n\n老条目\n";

    fn db() -> Connection {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(
            "CREATE TABLE sessions (id TEXT, title TEXT, started_at REAL);
             CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp REAL);
             INSERT INTO sessions VALUES ('20261101_091002_aaa111', '季度预算', 1793502602);
             INSERT INTO sessions VALUES ('20261101_152005_bbb222', '', 1793524805);
             INSERT INTO messages VALUES ('20261101_152005_bbb222', 'user', '立项会每周几开?', 1793524806);",
        )
        .unwrap();
        c
    }

    #[test]
    fn 那天日志里的对话_老格式后缀也认() {
        assert_eq!(journal_hints(JOURNAL, "2026-11-01"), vec!["20261101_091002_aaa111", "20261101_152005_bbb222"]);
        assert_eq!(journal_hints(JOURNAL, "2026-07-17"), vec!["8d4bfe"]);
    }

    #[test]
    fn 精确的排前面_没标题用首句_不重复() {
        let srcs = vec!["journal:2026-11-01".to_string(), "session:20261101_152005_bbb222".to_string()];
        let got = resolve(&srcs, JOURNAL, &db());
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].session_id, "20261101_152005_bbb222");
        assert!(got[0].exact);
        assert_eq!(got[0].title, "立项会每周几开?");
        assert_eq!(got[1].session_id, "20261101_091002_aaa111");
        assert!(!got[1].exact);
    }

    #[test]
    fn 查不到的不编() {
        let got = resolve(&["session:20990101_000000_nope00".into(), "raw/sources/x".into()], JOURNAL, &db());
        assert!(got.is_empty());
    }
}
