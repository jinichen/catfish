//! 11/09 小鲶秘书: 找「周期性的事」用的原料 —— 员工最近在 Companion / 微信里开的对话 (第一句 + 开始时刻)。
//! 读的是 hermes state.db, 跟会话列表 (sessions.rs) 同一个库、同一个筛选; 拆出来是因为 sessions.rs 到 800 行了。

use rusqlite::{params, Connection};
use serde::Serialize;

use super::sessions::{decode_hermes_content, open_db};


/// 员工最近发起的一段对话: 什么时候开的 + 第一句话。
#[derive(Debug, Clone, Serialize)]
pub struct UserRequest {
    pub session_id: String,
    /// ISO-8601 (带时区偏移)
    pub started_at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    /// 第一句用户消息, 截 200 字
    pub text: String,
}

/// 最多读这么多条 —— 8 周里一天十几段对话也够了, 再多 LLM 也看不过来
const USER_REQUESTS_MAX: i64 = 600;

/// 最近 `days` 天员工在 Companion / 微信里发起的对话 (跟会话列表同一个筛选, 不含定时任务 / 后台会话),
/// 按时间正序。sessions_list 只给最近 100 段, 不够看出「每月一次」这种节奏, 所以单开一个。
#[tauri::command]
pub async fn sessions_user_requests(days: u32) -> Result<Vec<UserRequest>, String> {
    tokio::task::spawn_blocking(move || user_requests_blocking(days))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

fn user_requests_blocking(days: u32) -> Result<Vec<UserRequest>, String> {
    let conn = open_db()?;
    let _ = conn.execute("ALTER TABLE sessions ADD COLUMN deleted_at INTEGER", []);
    super::companion_sessions::ensure(&conn)?;
    query_user_requests(&conn, days)
}

fn query_user_requests(conn: &Connection, days: u32) -> Result<Vec<UserRequest>, String> {
    let sql = format!(
        r#"
            SELECT s.id, s.title, s.started_at,
                (SELECT m.content FROM messages m
                 WHERE m.session_id = s.id AND m.role = 'user'
                   AND m.content IS NOT NULL AND m.content != ''
                 ORDER BY m.timestamp ASC, m.rowid ASC LIMIT 1) AS first_user_message
            FROM sessions s
            WHERE s.deleted_at IS NULL
              AND {filter}
              AND s.started_at >= CAST(strftime('%s', 'now') AS REAL) - ?1 * 86400.0
            ORDER BY s.started_at ASC
            LIMIT ?2
        "#,
        filter = super::companion_sessions::LIST_FILTER,
    );
    let mut stmt = conn.prepare(&sql).map_err(|e| format!("SQL 准备失败: {e}"))?;
    let rows = stmt
        .query_map(params![days as f64, USER_REQUESTS_MAX], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, f64>(2)?,
                row.get::<_, Option<String>>(3)?,
            ))
        })
        .map_err(|e| format!("SQL 查询失败: {e}"))?;
    let mut out = Vec::new();
    for row in rows {
        let (id, title, started, first) = row.map_err(|e| format!("行解析失败: {e}"))?;
        let Some(text) = first.map(decode_hermes_content) else { continue };
        let t = text.split_whitespace().collect::<Vec<_>>().join(" ");
        if t.is_empty() {
            continue;
        }
        out.push(UserRequest {
            session_id: id,
            started_at: unix_to_iso_local(started),
            title: title.filter(|s| !s.trim().is_empty()),
            text: t.chars().take(200).collect(),
        });
    }
    Ok(out)
}

/// 本地时区的 ISO —— 「周一上午」这种节奏要按员工的钟点看, UTC 会把周一 8 点算成周日
fn unix_to_iso_local(unix_secs: f64) -> String {
    use chrono::{Local, TimeZone};
    Local
        .timestamp_opt(unix_secs.trunc() as i64, 0)
        .single()
        .map(|dt| dt.to_rfc3339())
        .unwrap_or_default()
}

#[cfg(test)]
mod user_requests_tests {
    use super::*;

    #[test]
    fn reads_recent_user_sessions_in_order_and_skips_background_ones() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, source TEXT, started_at REAL, deleted_at INTEGER);
             CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp REAL);",
        )
        .unwrap();
        super::super::companion_sessions::ensure(&conn).unwrap();
        let now = chrono::Utc::now().timestamp() as f64;
        let ins = |id: &str, src: &str, ago_days: f64, text: &str, deleted: Option<i64>| {
            let t = now - ago_days * 86400.0;
            conn.execute(
                "INSERT INTO sessions (id, title, source, started_at, deleted_at) VALUES (?1, NULL, ?2, ?3, ?4)",
                params![id, src, t, deleted],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?1, 'user', ?2, ?3)",
                params![id, text, t],
            )
            .unwrap();
        };
        ins("w1", "weixin", 14.0, "汇总  这周的\n周报", None);
        ins("w2", "weixin", 7.0, "汇总这周的周报", None);
        ins("old", "weixin", 90.0, "太老了", None);
        ins("cron", "cron", 3.0, "定时任务的会话", None);
        ins("del", "weixin", 2.0, "删掉的", Some(1));
        let rows = query_user_requests(&conn, 56).unwrap();
        let ids: Vec<_> = rows.iter().map(|r| r.session_id.as_str()).collect();
        assert_eq!(ids, vec!["w1", "w2"]);
        assert_eq!(rows[0].text, "汇总 这周的 周报");
        assert!(rows[0].started_at.contains('T'));
    }
}
