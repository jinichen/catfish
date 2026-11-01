//! 题库与复习的数量 (11/02, 借 DeepTutor Practice) —— 早安简报「今天有 N 道要复习」用。
//!
//! 题库在 `~/.catfish/practice.db`, 由 tool-bridge 的 catfish_practice 工具写 (practice.py)。
//! 这里只读几个数, 不写; 没有这个库 (还没用过) 就全是 0。表结构以 practice.py 为准。

use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use std::path::Path;

#[derive(Debug, Default, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PracticeStats {
    pub total: i64,
    /// 还没掌握的错题
    pub wrong: i64,
    /// 今天到期 (含逾期) 要复习的
    pub due_today: i64,
    /// 今天已经复习过的 (题数)
    pub reviewed_today: i64,
}

pub fn stats_at(db: &Path, end_of_today: f64, start_of_today: f64) -> PracticeStats {
    if !db.exists() {
        return PracticeStats::default();
    }
    let Ok(conn) = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY) else {
        return PracticeStats::default();
    };
    let one = |sql: &str, p: &[&dyn rusqlite::ToSql]| conn.query_row(sql, p, |r| r.get::<_, i64>(0)).unwrap_or(0);
    PracticeStats {
        total: one("SELECT count(*) FROM questions", &[]),
        wrong: one(
            "SELECT count(*) FROM review_state r JOIN questions q ON q.id = r.question_id WHERE q.mastered = 0",
            &[],
        ),
        due_today: one(
            "SELECT count(*) FROM review_state r JOIN questions q ON q.id = r.question_id \
             WHERE q.mastered = 0 AND r.due <= ?1",
            &[&end_of_today],
        ),
        reviewed_today: one("SELECT count(DISTINCT question_id) FROM review_events WHERE ts >= ?1", &[&start_of_today]),
    }
}

pub fn stats_now() -> PracticeStats {
    use chrono::{Local, TimeZone};
    let Some(home) = crate::services::catfish_paths::catfish_home() else { return PracticeStats::default() };
    let today = Local::now().date_naive();
    let ts = |h: u32, m: u32, s: u32| {
        Local
            .from_local_datetime(&today.and_hms_opt(h, m, s).unwrap())
            .earliest()
            .map(|d| d.timestamp() as f64)
            .unwrap_or(0.0)
    };
    stats_at(&home.join("practice.db"), ts(23, 59, 59), ts(0, 0, 0))
}

#[tauri::command]
pub async fn practice_stats() -> Result<PracticeStats, String> {
    tokio::task::spawn_blocking(stats_now).await.map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 没有题库全是0_有就按今天数() {
        let t = tempfile::tempdir().unwrap();
        let db = t.path().join("practice.db");
        assert_eq!(stats_at(&db, 100.0, 0.0), PracticeStats::default());
        let c = Connection::open(&db).unwrap();
        c.execute_batch(
            "CREATE TABLE questions (id INTEGER PRIMARY KEY, mastered INTEGER DEFAULT 0);
             CREATE TABLE review_state (question_id INTEGER, due REAL);
             CREATE TABLE review_events (question_id INTEGER, ts REAL);
             INSERT INTO questions (id, mastered) VALUES (1, 0), (2, 0), (3, 1), (4, 0);
             INSERT INTO review_state VALUES (1, 50.0), (2, 500.0), (3, 10.0);
             INSERT INTO review_events VALUES (1, 20.0), (1, 30.0), (4, -5.0);",
        )
        .unwrap();
        // 今天 = [0, 100]
        assert_eq!(stats_at(&db, 100.0, 0.0), PracticeStats { total: 4, wrong: 2, due_today: 1, reviewed_today: 1 });
    }
}
