//! Companion 自己建的会话怎么认 (11/06, hermes v0.23, docs/HERMES-023-UPGRADE-ASSESSMENT.md 第 1 条)。
//!
//! 原来 Companion 建会话时往 state.db 的 sessions 表写 `source='companion'`, 列表按来源筛。
//! hermes v0.23「一个网关管所有会话」: API 对话只肯接 `source` 为 `api_server` / `bot_room` 的会话,
//! 别的来源一律 `permission_denied` (H23 gateway/session_api_turn.py), 实测 Companion 的会话发消息直接空回。
//!
//! 所以:
//! - 新建会话写 `source='api_server'` ([`SOURCE`]); 「这是 Companion 的会话」记进我们自己的附表
//!   `catfish_companion_sessions` (不改 hermes 的表结构)。v0.22 不看来源, 两个版本都能用。
//! - 老库里的 `companion` / `companion-migrated` 会话第一次打开库时一次性改成 `api_server` 并记进附表 ([`ensure`])。
//!   这些会话 v0.23 网关不认, 不会被它接管着, 直接改是安全的。
//! - 会话列表按 [`LIST_FILTER`] 筛: 微信的 + 附表里的。插件 P40 (Companion 那条用户消息去重) 也认附表。

use std::collections::HashSet;
use std::sync::Mutex;

use rusqlite::{params, Connection};

/// Companion 新建会话写的来源 (v0.23 只接这个)。
pub const SOURCE: &str = "api_server";

/// 会话列表的筛选条件 (表别名 `s`): 员工在微信 / Companion 里发起的对话。
pub(crate) const LIST_FILTER: &str =
    "(s.source IN ('weixin', 'companion') OR s.id IN (SELECT session_id FROM catfish_companion_sessions))";

static MIGRATED: Mutex<Option<HashSet<String>>> = Mutex::new(None);

/// 建附表 (幂等); 这个库在本进程里第一次打开时把老来源迁过来。
pub(crate) fn ensure(conn: &Connection) -> Result<(), String> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS catfish_companion_sessions (
            session_id TEXT PRIMARY KEY,
            origin TEXT NOT NULL,
            created_at REAL NOT NULL
        );",
    )
    .map_err(|e| format!("建 catfish_companion_sessions 表失败: {e}"))?;
    let key = conn.path().unwrap_or("").to_string();
    {
        let mut done = MIGRATED.lock().map_err(|e| e.to_string())?;
        let set = done.get_or_insert_with(HashSet::new);
        if set.contains(&key) {
            return Ok(());
        }
        set.insert(key.clone());
    }
    if let Err(e) = migrate(conn) {
        if let Ok(mut done) = MIGRATED.lock() {
            if let Some(set) = done.as_mut() {
                set.remove(&key);
            }
        }
        return Err(e);
    }
    Ok(())
}

fn migrate(conn: &Connection) -> Result<(), String> {
    let n: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sessions WHERE source IN ('companion', 'companion-migrated')",
            [],
            |r| r.get(0),
        )
        .map_err(|e| format!("查老会话失败: {e}"))?;
    if n == 0 {
        return Ok(());
    }
    conn.execute_batch(
        "BEGIN IMMEDIATE;
         INSERT OR IGNORE INTO catfish_companion_sessions (session_id, origin, created_at)
             SELECT id, source, COALESCE(started_at, 0) FROM sessions
             WHERE source IN ('companion', 'companion-migrated');
         UPDATE sessions SET source = 'api_server' WHERE source IN ('companion', 'companion-migrated');
         COMMIT;",
    )
    .map_err(|e| {
        let _ = conn.execute_batch("ROLLBACK;");
        format!("迁移 Companion 老会话失败: {e}")
    })?;
    log::info!("[companion_sessions] {n} 个 Companion 老会话的来源改成 api_server (hermes v0.23 只接这个), 记进附表");
    Ok(())
}

/// 新建的会话记成 Companion 的。
pub(crate) fn mark(conn: &Connection, session_id: &str, origin: &str, now: f64) -> Result<(), String> {
    conn.execute(
        "INSERT OR IGNORE INTO catfish_companion_sessions (session_id, origin, created_at) VALUES (?1, ?2, ?3)",
        params![session_id, origin, now],
    )
    .map_err(|e| format!("记 Companion 会话失败: {e}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn db() -> (tempfile::TempDir, Connection) {
        let t = tempfile::tempdir().unwrap();
        let c = Connection::open(t.path().join("state.db")).unwrap();
        c.execute_batch(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL, deleted_at INTEGER);
             INSERT INTO sessions VALUES ('a', 'companion', 1.0, NULL), ('b', 'companion-migrated', 2.0, NULL),
                                         ('c', 'weixin', 3.0, NULL), ('d', 'api_server', 4.0, NULL), ('e', 'cron', 5.0, NULL);",
        )
        .unwrap();
        (t, c)
    }

    fn listed(c: &Connection) -> Vec<String> {
        let sql = format!("SELECT s.id FROM sessions s WHERE {LIST_FILTER} ORDER BY s.id");
        let mut st = c.prepare(&sql).unwrap();
        st.query_map([], |r| r.get(0)).unwrap().map(|x| x.unwrap()).collect()
    }

    #[test]
    fn 老会话改成_api_server_还在列表里_后台的不进列表() {
        let (_t, c) = db();
        ensure(&c).unwrap();
        let src: Vec<(String, String)> = c
            .prepare("SELECT id, source FROM sessions ORDER BY id")
            .unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(src[0], ("a".into(), "api_server".into()));
        assert_eq!(src[1], ("b".into(), "api_server".into()));
        assert_eq!(src[2].1, "weixin");
        // 记忆整理那类后台 api_server 会话 (d) 不进列表; cron 不进
        assert_eq!(listed(&c), vec!["a", "b", "c"]);
        let origin: String =
            c.query_row("SELECT origin FROM catfish_companion_sessions WHERE session_id='b'", [], |r| r.get(0)).unwrap();
        assert_eq!(origin, "companion-migrated");
        // 再来一次不出错、不重复
        ensure(&c).unwrap();
        migrate(&c).unwrap();
        assert_eq!(listed(&c), vec!["a", "b", "c"]);
    }

    #[test]
    fn 新建的记进附表就进列表() {
        let (_t, c) = db();
        ensure(&c).unwrap();
        c.execute("INSERT INTO sessions VALUES ('f', ?1, 6.0, NULL)", [SOURCE]).unwrap();
        mark(&c, "f", "companion", 6.0).unwrap();
        assert_eq!(listed(&c), vec!["a", "b", "c", "f"]);
    }
}
