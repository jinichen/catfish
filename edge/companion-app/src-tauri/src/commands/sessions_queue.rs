//! 11/24 鸿波「是不是又卡住了」: 一个对话在本机服务 (hermes) 那边有几轮在跑 / 在排队。
//! 只读 state.db 的 session_admissions (hermes v0.23 的每轮登记表); 老版本没这张表 → 当作没人排队。
//! 前端等首个回复时用它判断「这条是在排队, 不是卡死」(src/lib/queueNotice.ts)。

use rusqlite::Connection;
use serde::Serialize;

#[derive(Serialize, Debug, PartialEq, Eq, Default)]
pub struct QueueState {
    pub started: u32,
    pub queued: u32,
}

pub(crate) fn queue_state(conn: &Connection, session_id: &str) -> QueueState {
    let count = |status: &str| -> u32 {
        conn.query_row(
            "SELECT COUNT(*) FROM session_admissions WHERE target_session_id = ?1 AND status = ?2",
            rusqlite::params![session_id, status],
            |row| row.get::<_, u32>(0),
        )
        .unwrap_or(0) // 没有这张表 (老 hermes) / 读不了: 当作没人排队
    };
    QueueState { started: count("started"), queued: count("queued") }
}

#[tauri::command]
pub async fn session_queue_state(session_id: String) -> Result<QueueState, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let conn = super::sessions::open_db()?;
        Ok(queue_state(&conn, &session_id))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 数这个对话在跑和在排队的_别的对话不算() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE session_admissions (seq INTEGER PRIMARY KEY, target_session_id TEXT, status TEXT);
             INSERT INTO session_admissions (target_session_id, status) VALUES
               ('s', 'terminal'), ('s', 'started'), ('s', 'queued'), ('s', 'queued'), ('other', 'queued');",
        )
        .unwrap();
        assert_eq!(queue_state(&conn, "s"), QueueState { started: 1, queued: 2 });
        assert_eq!(queue_state(&conn, "none"), QueueState::default());
    }

    #[test]
    fn 老版本没有这张表_当作没人排队() {
        let conn = Connection::open_in_memory().unwrap();
        assert_eq!(queue_state(&conn, "s"), QueueState::default());
    }
}
