//! P3.5.127 (6/26 鸿波 catch "怎么知道文件是不是有被索引? 索引数量多少?"):
//! 本地搜索索引状态查询 — 直接读 ~/.catfish/search.db.
//!
//! 复用 Python `catfish_search.query.stats_summary()` (query.py:116) 一样的 3 个
//! SQL, 多加一个 `MAX(indexed_at)` 给 UI 显示 "最近一次入库时间".
//!
//! Schema 来自 catfish-local-search/src/catfish_search/indexer.py:21-39 (audit
//! 已确认):
//!   - documents (FTS5 virtual): path UNINDEXED, title, content, file_type
//!     UNINDEXED, tokenize='trigram'
//!   - file_meta: path TEXT PK, size_bytes INTEGER, mtime REAL, indexed_at REAL,
//!     content_hash TEXT
//!
//! DB 不存在 (员工没跑过 catfish-search index, 也没启 watcher) 不报错 — 返
//! `db_exists=false` + 0 项, UI 自己判 "未建索引" 的引导文案.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

fn search_db_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("search.db"))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TypeCount {
    /// 文件类型后缀 (含点, 如 ".md" / ".pdf")
    pub file_type: String,
    /// 该类型在 documents 表里几条
    pub count: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalSearchStats {
    /// search.db 文件是否存在 (false = 完全没跑过 index)
    pub db_exists: bool,
    /// 索引文件总数 — file_meta COUNT(*)
    pub total_files: i64,
    /// 按类型分组 — documents GROUP BY file_type ORDER BY count DESC
    pub by_type: Vec<TypeCount>,
    /// 索引文件总字节数 — file_meta SUM(size_bytes). UI 自己转 KB/MB.
    pub total_size_bytes: i64,
    /// 最近一次 indexed_at (epoch sec, float). 空表 → None.
    pub last_indexed_at: Option<f64>,
    /// search.db 完整路径 — 员工知道索引落哪
    pub db_path: String,
    /// 11/10: 该收但没收进去的文件数 (太大 / 抽不出字) — skipped_files 表。以前是静默跳过的。
    pub skipped_total: i64,
    /// 最近的跳过记录 (最多 SKIPPED_LIST_MAX 条, 新的在前)
    pub skipped: Vec<SkippedFile>,
    /// 识了字也没认出字的图片 (照片、视频帧) —— 正常情况, 不算进 skipped_total, 面板另起一行淡色显示。
    /// 鸿波机器上一个宣传片工程就有 479 帧, 混在一起会把真正该看的 18 份扫描件淹掉。
    pub images_without_text: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SkippedFile {
    pub path: String,
    /// "too_large" (超过大小上限) / "no_text" (抽不出文字: 扫描件 / 全是图片 / 文件坏了)
    pub reason: String,
    pub size_bytes: Option<i64>,
}

const SKIPPED_LIST_MAX: i64 = 50;

/// 「图片里没认出字」的判据 (跟 local-search ocr.IMAGE_EXTS 一致)
const IMAGE_NO_TEXT: &str = "(reason = 'no_text' AND (lower(path) LIKE '%.png' OR lower(path) LIKE '%.jpg' \
     OR lower(path) LIKE '%.jpeg' OR lower(path) LIKE '%.heic' OR lower(path) LIKE '%.heif' \
     OR lower(path) LIKE '%.webp' OR lower(path) LIKE '%.tif' OR lower(path) LIKE '%.tiff' \
     OR lower(path) LIKE '%.bmp' OR lower(path) LIKE '%.gif'))";

/// 跳过记录。老索引库 (watcher 还没升级) 没有这张表 → 当作 0 条。
/// 返回 (该看的跳过数, 列表, 没字的图片数)。
fn collect_skipped(conn: &rusqlite::Connection) -> (i64, Vec<SkippedFile>, i64) {
    let total: i64 = match conn.query_row(
        &format!("SELECT COUNT(*) FROM skipped_files WHERE NOT {IMAGE_NO_TEXT}"),
        [],
        |r| r.get(0),
    ) {
        Ok(n) => n,
        Err(_) => return (0, vec![], 0),
    };
    let images: i64 = conn
        .query_row(&format!("SELECT COUNT(*) FROM skipped_files WHERE {IMAGE_NO_TEXT}"), [], |r| r.get(0))
        .unwrap_or(0);
    let mut list = Vec::new();
    if let Ok(mut stmt) = conn.prepare(&format!(
        "SELECT path, COALESCE(reason, ''), size_bytes FROM skipped_files WHERE NOT {IMAGE_NO_TEXT} \
         ORDER BY at DESC LIMIT ?1"
    )) {
        if let Ok(rows) = stmt.query_map([SKIPPED_LIST_MAX], |r| {
            Ok(SkippedFile { path: r.get(0)?, reason: r.get(1)?, size_bytes: r.get(2)? })
        }) {
            list.extend(rows.flatten());
        }
    }
    (total, list, images)
}

/// 在已开 DB 上跑全部统计查询 — 抽出来给 #[cfg(test)] 用内存 DB 验 SQL.
fn collect_stats(conn: &rusqlite::Connection) -> Result<(i64, Vec<TypeCount>, i64, Option<f64>), String> {
    // 1) total_files
    let total_files: i64 = conn
        .query_row("SELECT COUNT(*) FROM file_meta", [], |r| r.get(0))
        .map_err(|e| format!("查 total_files 失败: {e}"))?;

    // 2) by_type — 跟 Python query.py:121-128 完全对齐 (file_type 非空, GROUP BY,
    //    ORDER BY n DESC)
    let mut stmt = conn
        .prepare(
            "SELECT file_type, COUNT(*) AS n FROM documents \
             WHERE file_type IS NOT NULL AND file_type != '' \
             GROUP BY file_type ORDER BY n DESC",
        )
        .map_err(|e| format!("准备 by_type 查询失败: {e}"))?;
    let rows = stmt
        .query_map([], |r| {
            Ok(TypeCount {
                file_type: r.get::<_, String>(0)?,
                count: r.get::<_, i64>(1)?,
            })
        })
        .map_err(|e| format!("执行 by_type 查询失败: {e}"))?;
    let mut by_type: Vec<TypeCount> = Vec::new();
    for row in rows {
        match row {
            Ok(tc) => by_type.push(tc),
            Err(e) => return Err(format!("读 by_type 行失败: {e}")),
        }
    }

    // 3) total_size_bytes — COALESCE 兜底空表
    let total_size_bytes: i64 = conn
        .query_row(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM file_meta",
            [],
            |r| r.get(0),
        )
        .map_err(|e| format!("查 total_size 失败: {e}"))?;

    // 4) last_indexed_at — MAX(indexed_at). 空表返 None.
    let last_indexed_at: Option<f64> = conn
        .query_row(
            "SELECT MAX(indexed_at) FROM file_meta",
            [],
            |r| r.get::<_, Option<f64>>(0),
        )
        .unwrap_or(None);

    Ok((total_files, by_type, total_size_bytes, last_indexed_at))
}

#[tauri::command]
pub async fn local_search_stats() -> Result<LocalSearchStats, String> {
    let db_path = search_db_path()?;
    let db_path_str = db_path.to_string_lossy().into_owned();

    if !db_path.exists() {
        // 没跑过 index — 直接返空, UI 显示引导文案
        return Ok(LocalSearchStats {
            db_exists: false,
            total_files: 0,
            by_type: vec![],
            total_size_bytes: 0,
            last_indexed_at: None,
            db_path: db_path_str,
            skipped_total: 0,
            skipped: vec![],
            images_without_text: 0,
        });
    }

    let conn = rusqlite::Connection::open(&db_path)
        .map_err(|e| format!("打开 search.db 失败: {e}"))?;

    let (total_files, by_type, total_size_bytes, last_indexed_at) = collect_stats(&conn)?;
    let (skipped_total, skipped, images_without_text) = collect_skipped(&conn);

    Ok(LocalSearchStats {
        db_exists: true,
        total_files,
        by_type,
        total_size_bytes,
        last_indexed_at,
        db_path: db_path_str,
        skipped_total,
        skipped,
        images_without_text,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setup_test_db() -> rusqlite::Connection {
        // 仿 indexer.py SCHEMA, 但 FTS5 用 trigram 在 test env 不一定 work, 改用
        // 普通表 — collect_stats 只 SELECT, schema 等价就够验 SQL 语法.
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE documents (
                path TEXT,
                title TEXT,
                content TEXT,
                file_type TEXT
            );
            CREATE TABLE file_meta (
                path TEXT PRIMARY KEY,
                size_bytes INTEGER,
                mtime REAL,
                indexed_at REAL,
                content_hash TEXT
            );",
        )
        .unwrap();
        conn
    }

    #[test]
    fn empty_db_returns_zeros() {
        let conn = setup_test_db();
        let (total, by_type, size, last) = collect_stats(&conn).unwrap();
        assert_eq!(total, 0);
        assert!(by_type.is_empty());
        assert_eq!(size, 0);
        assert!(last.is_none());
    }

    #[test]
    fn collects_counts_and_groups_by_type() {
        let conn = setup_test_db();
        conn.execute_batch(
            "INSERT INTO file_meta VALUES ('/a.md', 100, 1.0, 10.0, 'h1');
             INSERT INTO file_meta VALUES ('/b.md', 200, 2.0, 20.0, 'h2');
             INSERT INTO file_meta VALUES ('/c.txt', 50, 3.0, 5.0, 'h3');
             INSERT INTO documents VALUES ('/a.md', 'A', 'aa', '.md');
             INSERT INTO documents VALUES ('/b.md', 'B', 'bb', '.md');
             INSERT INTO documents VALUES ('/c.txt', 'C', 'cc', '.txt');",
        )
        .unwrap();

        let (total, by_type, size, last) = collect_stats(&conn).unwrap();
        assert_eq!(total, 3);
        assert_eq!(size, 350);
        assert_eq!(last, Some(20.0));
        // by_type ORDER BY n DESC → .md (2) 在 .txt (1) 前面
        assert_eq!(by_type.len(), 2);
        assert_eq!(by_type[0].file_type, ".md");
        assert_eq!(by_type[0].count, 2);
        assert_eq!(by_type[1].file_type, ".txt");
        assert_eq!(by_type[1].count, 1);
    }

    #[test]
    fn skipped_files_old_db_without_table_is_zero() {
        let conn = setup_test_db();
        let (n, list, imgs) = collect_skipped(&conn);
        assert_eq!(n, 0);
        assert!(list.is_empty());
        assert_eq!(imgs, 0);
    }

    #[test]
    fn skipped_files_newest_first() {
        let conn = setup_test_db();
        conn.execute_batch(
            "CREATE TABLE skipped_files (path TEXT PRIMARY KEY, reason TEXT, size_bytes INTEGER, at REAL);
             INSERT INTO skipped_files VALUES ('/old.md', 'too_large', 30000000, 1.0);
             INSERT INTO skipped_files VALUES ('/scan.pdf', 'no_text', 5000, 2.0);
             INSERT INTO skipped_files VALUES ('/frames/0001.PNG', 'no_text', 900, 3.0);
             INSERT INTO skipped_files VALUES ('/frames/0002.png', 'no_text', 900, 4.0);
             INSERT INTO skipped_files VALUES ('/old-mac/photo.jpg', 'needs_ocr', 900, 5.0);",
        )
        .unwrap();
        let (n, list, imgs) = collect_skipped(&conn);
        // 没字的图片另算; 本机没法识字的图片 (needs_ocr) 仍算该看的
        assert_eq!(imgs, 2);
        assert_eq!(n, 3);
        assert_eq!(list[0].path, "/old-mac/photo.jpg");
        let list: Vec<_> = list.into_iter().skip(1).collect();
        assert_eq!(list[0].path, "/scan.pdf");
        assert_eq!(list[0].reason, "no_text");
        assert_eq!(list[1].size_bytes, Some(30000000));
    }

    #[test]
    fn skips_empty_file_type() {
        let conn = setup_test_db();
        conn.execute_batch(
            "INSERT INTO documents VALUES ('/a', 'A', 'aa', '');
             INSERT INTO documents VALUES ('/b', 'B', 'bb', '.md');",
        )
        .unwrap();
        let (_, by_type, _, _) = collect_stats(&conn).unwrap();
        assert_eq!(by_type.len(), 1);
        assert_eq!(by_type[0].file_type, ".md");
    }
}
