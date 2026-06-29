//! P38 (6/5 鸿波) — 本机 ONNX BGE-M3 embedding, wiki 语义搜索, 100% 离线.
//!
//! 架构:
//!   ~/.catfish/models/bge-m3.onnx           Xenova/bge-m3 INT8 quantized model (~570MB)
//!   ~/.catfish/models/tokenizer.json        BGE-M3 XLM-R BPE tokenizer
//!   ~/.catfish/wiki_embeddings.db           SQLite vector cache (1024-dim f16)
//!
//! 部署 (鸿波本机手动):
//!   mkdir -p ~/.catfish/models
//!   curl -L -o ~/.catfish/models/bge-m3.onnx \
//!     https://huggingface.co/Xenova/bge-m3/resolve/main/onnx/model_quantized.onnx
//!   curl -L -o ~/.catfish/models/tokenizer.json \
//!     https://huggingface.co/Xenova/bge-m3/resolve/main/tokenizer.json
//!
//! 没下载 → wiki_search_semantic 返 fallback hint "model not loaded".
//!
//! # P3.5.4.1 (6/16 鸿波) refactor
//!
//! `embed_text` / `cosine` / model session / tokenizer / EMBED_DIM 移到
//! `services::embedding`, 让 advisor_relevance + 后续场景共享同一份 ONNX session.
//! wiki_embed 这里只剩 wiki 业务路径 (wiki_search_semantic + ensure_index).

use serde::Serialize;
use std::fs;
use std::path::PathBuf;

use crate::services::embedding::{
    cosine, embed_text, is_provider_ready, vector_from_blob, vector_to_blob,
};

fn home_dir() -> Result<PathBuf, String> {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME".to_string())
}

fn embed_db_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("wiki_embeddings.db"))
}

/// 跟 wiki_search_text 同 schema 让前端共用 UI.
#[derive(Debug, Serialize)]
pub struct WikiSemanticHit {
    pub rel_path: String,
    pub title: String,
    pub kind: String,
    pub score: f64,
    pub snippet: String,
}

#[derive(Debug, Serialize)]
pub struct WikiSemanticResult {
    pub hits: Vec<WikiSemanticHit>,
    pub model_loaded: bool,
    pub indexed_count: usize,
    pub message: String,
}

/// wiki_search_semantic — embed query, cosine vs cached entity/concept embeddings, top-K.
/// 首次 call 自动同步 index 全 wiki/. 后续增量逻辑留 P38.3.
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_search_semantic(
    query: String,
    top_k: Option<usize>,
) -> Result<WikiSemanticResult, String> {
    let q = query.trim();
    if q.is_empty() {
        return Ok(WikiSemanticResult {
            hits: vec![],
            model_loaded: false,
            indexed_count: 0,
            message: "query 空".into(),
        });
    }

    // P3.5.15: 检查 active provider 就绪. 改 is_provider_ready() 后兼容 local / remote /
    //   auto 三种 backend, 不再绑死 local ONNX 文件存在.
    if !is_provider_ready() {
        return Ok(WikiSemanticResult {
            hits: vec![],
            model_loaded: false,
            indexed_count: 0,
            message: format!(
                "embedding provider 未就绪.\n\
                 走本机 ONNX 时手动下载:\n\
                  mkdir -p ~/.catfish/models && \n\
                  curl -L -o ~/.catfish/models/bge-m3.onnx https://huggingface.co/Xenova/bge-m3/resolve/main/onnx/model_quantized.onnx && \n\
                  curl -L -o ~/.catfish/models/tokenizer.json https://huggingface.co/Xenova/bge-m3/resolve/main/tokenizer.json\n\
                 走 catfish-gateway 远程时: 检查 ~/.hermes/.env 里 CATFISH_INTERNAL_DEV_TOKEN 是否配置."
            ),
        });
    }

    // ensure index up-to-date (简化: 每次重 index, 56 entries < 5s)
    let indexed = ensure_index().await.map_err(|e| format!("index 失败: {e}"))?;

    // embed query
    let query_vec = embed_text(q).await.ok_or_else(|| "query embedding 失败".to_string())?;

    // load all from db, cosine
    let db_path = embed_db_path()?;
    let conn = rusqlite::Connection::open(&db_path)
        .map_err(|e| format!("SQLite open 失败: {e}"))?;
    let mut stmt = conn
        .prepare("SELECT rel_path, title, kind, snippet, vector FROM wiki_embed")
        .map_err(|e| format!("SQL prep 失败: {e}"))?;
    let mut rows = stmt
        .query([])
        .map_err(|e| format!("SQL query 失败: {e}"))?;
    let mut hits: Vec<WikiSemanticHit> = Vec::new();
    while let Some(row) = rows.next().map_err(|e| format!("SQL next 失败: {e}"))? {
        let rel_path: String = row.get(0).map_err(|e| format!("col 0: {e}"))?;
        let title: String = row.get(1).map_err(|e| format!("col 1: {e}"))?;
        let kind: String = row.get(2).map_err(|e| format!("col 2: {e}"))?;
        let snippet: String = row.get(3).map_err(|e| format!("col 3: {e}"))?;
        let vec_blob: Vec<u8> = row.get(4).map_err(|e| format!("col 4: {e}"))?;
        let v = match vector_from_blob(&vec_blob) {
            Some(v) => v,
            None => continue,  // blob 长度不对 (schema 不匹配?), 跳过这一行
        };
        let score = cosine(&query_vec, &v) as f64;
        hits.push(WikiSemanticHit {
            rel_path,
            title,
            kind,
            score,
            snippet,
        });
    }
    drop(rows);
    drop(stmt);

    hits.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    let k = top_k.unwrap_or(20).min(hits.len());
    hits.truncate(k);

    Ok(WikiSemanticResult {
        hits,
        model_loaded: true,
        indexed_count: indexed,
        message: format!("✓ {} entries indexed", indexed),
    })
}

/// ensure_index — 简化版: 每次重 index 全 wiki/, 内存够小不 incremental.
/// 返 indexed count.
///
/// P3.5.15: 改 async 因内部 embed_text 走 provider (local 同步 / remote HTTP), 都需要 .await.
async fn ensure_index() -> Result<usize, String> {
    let home = home_dir()?;
    let db_path = embed_db_path()?;

    // init db
    let conn =
        rusqlite::Connection::open(&db_path).map_err(|e| format!("SQLite open 失败: {e}"))?;
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wiki_embed (
            rel_path TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            snippet TEXT NOT NULL,
            vector BLOB NOT NULL,
            mtime INTEGER NOT NULL
        )",
        [],
    )
    .map_err(|e| format!("create table: {e}"))?;

    // P3.5.35 (6/18 鸿波 catch '装到本机后部门 wiki 不就是自家了吗'):
    // 用 collect_all_wiki_md helper 一并索引自家 + 装机部门 wiki, 不再 hardcode 3 子目录.
    // catfish_home: collect_all_wiki_md 用 home.join(".catfish") — 老代码也是 home.join(".catfish").join(sub),
    // 路径根一致. rel_path 改成相对 ~/.catfish/ (strip_prefix 算), 兼容 wiki/ 跟 wiki-shared/dept/<部门>/.
    let catfish_root = home.join(".catfish");
    let mut count = 0usize;
    // P3.5.132 #1 (6/29 鸿波 catch "wiki 删了搜出 404"): 收集真实存在真 rel_path,
    // 末尾扫一遍 SQLite 删孤儿 row (软删 wiki 后 cache 留着造成 wiki_search_semantic
    // 命中已删文件, 员工点开 404). 用 Set 兼顾 O(1) lookup.
    let mut existing_rels: std::collections::HashSet<String> = std::collections::HashSet::new();
    for path in crate::commands::wiki_read::collect_all_wiki_md(&catfish_root) {
        // rel_path 改用 strip_prefix, 兼容 wiki-shared/dept/<部门>/file_id.md 嵌套.
        let rel_path = match path.strip_prefix(&catfish_root) {
            Ok(p) => p.to_string_lossy().replace('\\', "/"),
            Err(_) => continue,
        };
        existing_rels.insert(rel_path.clone());
        let content = match fs::read_to_string(&path) {
            Ok(c) => c,
            Err(_) => continue,
        };
        let mtime = path
            .metadata()
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);

        // skip if mtime unchanged → SQLite cache hit
        let cached_mtime: Option<i64> = conn
            .query_row(
                "SELECT mtime FROM wiki_embed WHERE rel_path = ?1",
                [&rel_path],
                |row| row.get(0),
            )
            .ok();
        if cached_mtime == Some(mtime) {
            count += 1;
            continue;
        }

        // parse title + kind from frontmatter + snippet
        let (title, kind, snippet) = parse_for_embed(&content);
        // embed full content (frontmatter + body)
        let vec = match embed_text(&content).await {
            Some(v) => v,
            None => continue,
        };
        let blob = vector_to_blob(&vec);
        conn.execute(
            "INSERT OR REPLACE INTO wiki_embed (rel_path, title, kind, snippet, vector, mtime)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            rusqlite::params![rel_path, title, kind, snippet, blob, mtime],
        )
        .map_err(|e| format!("insert: {e}"))?;
        count += 1;
    }

    // P3.5.132 #1: 扫 SQLite 所有 rel_path, 不在 existing_rels 真删.
    let purged = purge_orphans(&conn, &existing_rels)?;
    if purged > 0 {
        log::info!("[wiki_embed] purged {purged} orphan row(s)");
    }

    Ok(count)
}

/// P3.5.132 #1: 删 wiki_embed 真不在 existing rel_path set 真孤儿 row.
/// 抽纯函数让单测能用 in-memory SQLite 验.
fn purge_orphans(
    conn: &rusqlite::Connection,
    existing: &std::collections::HashSet<String>,
) -> Result<usize, String> {
    let cached_rels: Vec<String> = {
        let mut stmt = conn
            .prepare("SELECT rel_path FROM wiki_embed")
            .map_err(|e| format!("prep cached_rels: {e}"))?;
        let rows = stmt
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|e| format!("query cached_rels: {e}"))?;
        let mut v = Vec::new();
        for r in rows {
            if let Ok(s) = r {
                v.push(s);
            }
        }
        v
    };
    let mut purged = 0usize;
    for cached in cached_rels {
        if !existing.contains(&cached) {
            conn.execute("DELETE FROM wiki_embed WHERE rel_path = ?1", [&cached])
                .map_err(|e| format!("delete orphan {cached}: {e}"))?;
            purged += 1;
        }
    }
    Ok(purged)
}

fn parse_for_embed(content: &str) -> (String, String, String) {
    let mut title = String::new();
    let mut kind = String::new();
    let body_start: usize;
    if content.starts_with("---\n") {
        if let Some(end) = content.find("\n---\n") {
            let fm = &content[4..end];
            for line in fm.lines() {
                if let Some(rest) = line.strip_prefix("title:") {
                    title = rest.trim().trim_matches('"').to_string();
                }
                if let Some(rest) = line.strip_prefix("type:") {
                    kind = rest.trim().trim_matches('"').to_string();
                }
            }
            body_start = end + 5;
        } else {
            body_start = 0;
        }
    } else {
        body_start = 0;
    }
    let body = &content[body_start..];
    let snippet: String = body.chars().take(120).collect::<String>().replace('\n', " ");
    (title, kind, snippet)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    fn setup_test_db() -> rusqlite::Connection {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        conn.execute(
            "CREATE TABLE wiki_embed (
                rel_path TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                snippet TEXT NOT NULL,
                vector BLOB NOT NULL,
                mtime INTEGER NOT NULL
            )",
            [],
        )
        .unwrap();
        conn
    }

    fn insert_row(conn: &rusqlite::Connection, rel_path: &str) {
        conn.execute(
            "INSERT INTO wiki_embed (rel_path, title, kind, snippet, vector, mtime)
             VALUES (?1, 't', 'entity', 's', X'00', 1)",
            [rel_path],
        )
        .unwrap();
    }

    #[test]
    fn purge_orphans_removes_missing_files() {
        let conn = setup_test_db();
        insert_row(&conn, "wiki/entities/alive.md");
        insert_row(&conn, "wiki/entities/deleted.md");

        let mut existing = HashSet::new();
        existing.insert("wiki/entities/alive.md".to_string());

        let purged = purge_orphans(&conn, &existing).unwrap();
        assert_eq!(purged, 1, "应删除 1 个孤儿 (deleted.md)");

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM wiki_embed", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1, "alive.md 仍在");

        let remaining: String = conn
            .query_row(
                "SELECT rel_path FROM wiki_embed LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(remaining, "wiki/entities/alive.md");
    }

    #[test]
    fn purge_orphans_empty_existing_clears_all() {
        let conn = setup_test_db();
        insert_row(&conn, "wiki/entities/x.md");
        insert_row(&conn, "wiki/entities/y.md");

        let purged = purge_orphans(&conn, &HashSet::new()).unwrap();
        assert_eq!(purged, 2);

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM wiki_embed", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn purge_orphans_all_existing_purges_none() {
        let conn = setup_test_db();
        insert_row(&conn, "wiki/entities/a.md");
        insert_row(&conn, "wiki/entities/b.md");

        let mut existing = HashSet::new();
        existing.insert("wiki/entities/a.md".to_string());
        existing.insert("wiki/entities/b.md".to_string());

        let purged = purge_orphans(&conn, &existing).unwrap();
        assert_eq!(purged, 0);
    }
}
