//! P3.5.4.2 (6/16 鸿波): advisor 相关性排序 — BGE-M3 cosine + sqlite cache.
//!
//! # 目的
//!
//! advisor 注入 distilled_facts / hermes memory § / prev_tasks 之前, 按今天输入
//! (todos + emails + events) 算语义相关性, 取 top-K. 砍 prompt + 砍 LLM 输出长度,
//! advisor 不再 truncated → Call 1 直接 success 无需 Call 2 兜底.
//!
//! 设计 (鸿波 6/16 拍, 不是粗暴 slice):
//!   - query: 今天 todos + emails + events (拼起来)
//!   - candidates: distilled_facts 切段 + memory § + prev_tasks
//!   - 排序: cosine similarity (BGE-M3 1024-d, services::embedding)
//!   - 返 RankedItem{idx, score, from_cache}, caller 按 top-K slice
//!
//! # cache
//!
//! `~/.catfish/advisor_embed_cache.db` SQLite — content_hash (sha256) → vector BLOB.
//! 段内容不变就复用 vector, 不重 embed. 大部分 distilled_facts / memory 跨 refresh 不变,
//! 缓存命中率高 (~90% 后续 refresh).
//!
//! # fallback
//!
//! BGE-M3 model 缺 → 返 model_loaded=false + ranked 空. TS caller 自己 fallback
//! "不筛, 全量注入" (跟原有行为一致).

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::services::embedding::{
    cosine, embed_dim, embed_text, is_provider_ready, vector_from_blob, vector_to_blob,
};

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RankedItem {
    /// 在原 candidates 数组里的 index
    pub idx: usize,
    /// cosine 相似度 (0-1, 越大越相关). 单条 embed 失败时返 0.
    pub score: f32,
    /// 这次是不是命中 cache (debug 用 — 看缓存命中率)
    pub from_cache: bool,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelevanceResult {
    /// BGE-M3 model 是否成功加载. false 时 ranked 为空, caller 走 fallback (不筛).
    pub model_loaded: bool,
    /// 按 score 降序排序的 ranked 列表 (全部, 不截断). caller 自己 slice top-K.
    pub ranked: Vec<RankedItem>,
    /// 错误信息 (model 缺等). 仅 model_loaded=false 时填.
    pub message: String,
}

fn cache_db_path() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .ok_or("HOME 未设")?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir.join("advisor_embed_cache.db"))
}

fn sha256_hex(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    format!("{:x}", hasher.finalize())
}

fn ensure_schema(conn: &rusqlite::Connection) -> Result<(), String> {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS advisor_embed_cache (
            content_hash TEXT PRIMARY KEY,
            content_preview TEXT NOT NULL,
            vector BLOB NOT NULL,
            source TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )",
        [],
    )
    .map_err(|e| format!("CREATE TABLE 失败: {e}"))?;
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_advisor_embed_created ON advisor_embed_cache(created_at DESC)",
        [],
    )
    .map_err(|e| format!("CREATE INDEX 失败: {e}"))?;

    // P3.5.15 (6/16 鸿波): dim 校验 + 自动清空过期 cache.
    //
    // 切换 backend / 换模型 / 改 yaml.embed_dim → BLOB 长度变 → vector_from_blob 拒读
    // → 每条都 cache miss → 每条都重 embed → 但 upsert 时 BLOB 长度不对 → 整 db 永远
    // 半新半旧. 这里加 _meta 表存当前 dim, 启动跟 active provider embed_dim() 不符
    // 就 DROP TABLE + 重建. 真切 backend 后第一次跑会 cold (重 embed 全部), 但之后
    // 一致, 不再"半旧半新"灾难态.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )",
        [],
    )
    .map_err(|e| format!("CREATE _meta 表失败: {e}"))?;

    let current_dim = embed_dim();
    let stored_dim: Option<usize> = conn
        .query_row(
            "SELECT value FROM _meta WHERE key = 'embed_dim'",
            [],
            |row| row.get::<_, String>(0),
        )
        .ok()
        .and_then(|s| s.parse::<usize>().ok());

    match stored_dim {
        Some(d) if d == current_dim => {
            // 维度匹配, cache 可复用
        }
        Some(d) => {
            log::warn!(
                "[advisor_relevance] cache embed_dim 不匹配 (stored={}, current={}) — DROP + 重建",
                d,
                current_dim
            );
            conn.execute("DROP TABLE IF EXISTS advisor_embed_cache", [])
                .map_err(|e| format!("DROP cache 失败: {e}"))?;
            conn.execute(
                "CREATE TABLE advisor_embed_cache (
                    content_hash TEXT PRIMARY KEY,
                    content_preview TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    source TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )",
                [],
            )
            .map_err(|e| format!("rebuild cache 失败: {e}"))?;
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_advisor_embed_created ON advisor_embed_cache(created_at DESC)",
                [],
            )
            .map_err(|e| format!("rebuild index 失败: {e}"))?;
        }
        None => {
            // 老 db 没 _meta — 第一次跑这版代码. 不 DROP (BLOB 长度可能跟 P3.5.4 老
            // hardcoded EMBED_DIM=1024 一致, vector_from_blob 仍能用). 直接写当前 dim
            // 进 meta, 下次启动起守门. 真有不一致 vector_from_blob 拒读 → cache miss 重 embed.
            log::info!(
                "[advisor_relevance] _meta 不存在, 第一次跑 P3.5.15, 写入 embed_dim={}",
                current_dim
            );
        }
    }
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('embed_dim', ?1)",
        rusqlite::params![current_dim.to_string()],
    )
    .map_err(|e| format!("写 _meta 失败: {e}"))?;
    Ok(())
}

/// P3.5.4.2 主入口 — caller 喂 query + candidates, 拿排序结果.
///
/// `source_hint` 仅 debug 用 (cache row 标记来源, e.g. "distilled" / "memory" / "prev_task").
/// 不影响算法.
#[tauri::command(rename_all = "camelCase")]
pub async fn advisor_rank_relevance(
    query: String,
    candidates: Vec<String>,
    source_hint: String,
) -> Result<RelevanceResult, String> {
    let query = query.trim().to_string();
    if query.is_empty() {
        return Ok(RelevanceResult {
            model_loaded: false,
            ranked: vec![],
            message: "query 为空".into(),
        });
    }
    if candidates.is_empty() {
        return Ok(RelevanceResult {
            model_loaded: true,
            ranked: vec![],
            message: "candidates 为空".into(),
        });
    }

    // P3.5.15: 检查 active provider 就绪 (local 加载 model+tokenizer 成功, 或 remote 有 token).
    // 没就绪 → caller fallback 不筛全量注入 (跟 P3.5.4 行为一致).
    if !is_provider_ready() {
        return Ok(RelevanceResult {
            model_loaded: false,
            ranked: vec![],
            message: "embedding provider 未就绪 (model 缺 / remote token 缺). caller 走 fallback (不筛, 全量注入)".into(),
        });
    }

    // embed query (always live, 不缓存 — query 每次 advisor refresh 都变)
    let query_vec = match embed_text(&query).await {
        Some(v) => v,
        None => {
            return Ok(RelevanceResult {
                model_loaded: false,
                ranked: vec![],
                message: "query embedding 失败 (tokenize/onnx 异常)".into(),
            });
        }
    };

    // 打开 cache db
    let db_path = cache_db_path()?;
    let conn = rusqlite::Connection::open(&db_path)
        .map_err(|e| format!("打开 advisor_embed_cache.db 失败: {e}"))?;
    ensure_schema(&conn)?;

    // 顺次 embed 每个 candidate, 算 score
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let mut ranked: Vec<RankedItem> = Vec::with_capacity(candidates.len());

    for (idx, content) in candidates.iter().enumerate() {
        let trimmed = content.trim();
        if trimmed.is_empty() {
            ranked.push(RankedItem { idx, score: 0.0, from_cache: false });
            continue;
        }
        let hash = sha256_hex(trimmed);

        // lookup cache
        let cached: Option<Vec<u8>> = conn
            .query_row(
                "SELECT vector FROM advisor_embed_cache WHERE content_hash = ?1",
                rusqlite::params![hash],
                |row| row.get::<_, Vec<u8>>(0),
            )
            .ok();

        let (vec, from_cache) = match cached {
            Some(blob) => match vector_from_blob(&blob) {
                Some(v) => (v, true),
                None => {
                    // blob 长度不对 (schema 老版?), 重 embed + 覆盖
                    log::warn!(
                        "[advisor_relevance] cache vector 长度异常 (hash={}), 重 embed",
                        &hash[..16]
                    );
                    match embed_text(trimmed).await {
                        Some(v) => {
                            let _ = upsert_cache(&conn, &hash, trimmed, &v, &source_hint, now);
                            (v, false)
                        }
                        None => {
                            ranked.push(RankedItem { idx, score: 0.0, from_cache: false });
                            continue;
                        }
                    }
                }
            },
            None => {
                // miss → embed + 写 cache
                match embed_text(trimmed).await {
                    Some(v) => {
                        let _ = upsert_cache(&conn, &hash, trimmed, &v, &source_hint, now);
                        (v, false)
                    }
                    None => {
                        ranked.push(RankedItem { idx, score: 0.0, from_cache: false });
                        continue;
                    }
                }
            }
        };

        let score = cosine(&query_vec, &vec);
        ranked.push(RankedItem { idx, score, from_cache });
    }

    // sort by score desc
    ranked.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));

    Ok(RelevanceResult {
        model_loaded: true,
        ranked,
        message: String::new(),
    })
}

fn upsert_cache(
    conn: &rusqlite::Connection,
    hash: &str,
    content: &str,
    vector: &[f32],
    source: &str,
    now: i64,
) -> Result<(), String> {
    let preview: String = content.chars().take(100).collect();
    let blob = vector_to_blob(vector);
    conn.execute(
        "INSERT OR REPLACE INTO advisor_embed_cache
         (content_hash, content_preview, vector, source, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        rusqlite::params![hash, preview, blob, source, now],
    )
    .map_err(|e| format!("upsert cache 失败: {e}"))?;
    Ok(())
}

/// 调试: 看 cache 状态.
#[tauri::command(rename_all = "camelCase")]
pub async fn advisor_relevance_cache_stats() -> Result<CacheStats, String> {
    let db_path = cache_db_path()?;
    if !db_path.exists() {
        return Ok(CacheStats { rows: 0, sources: vec![] });
    }
    let conn = rusqlite::Connection::open(&db_path)
        .map_err(|e| format!("打开 cache db 失败: {e}"))?;
    ensure_schema(&conn)?;
    let rows: i64 = conn
        .query_row("SELECT COUNT(*) FROM advisor_embed_cache", [], |row| row.get(0))
        .unwrap_or(0);
    let mut stmt = conn
        .prepare("SELECT source, COUNT(*) FROM advisor_embed_cache GROUP BY source")
        .map_err(|e| format!("SQL prep 失败: {e}"))?;
    let mapped = stmt
        .query_map([], |row| {
            Ok(SourceCount {
                source: row.get(0)?,
                count: row.get(1)?,
            })
        })
        .map_err(|e| format!("SQL query 失败: {e}"))?;
    let sources: Vec<SourceCount> = mapped.filter_map(|r| r.ok()).collect();
    Ok(CacheStats { rows, sources })
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CacheStats {
    pub rows: i64,
    pub sources: Vec<SourceCount>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceCount {
    pub source: String,
    pub count: i64,
}
