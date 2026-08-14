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
    cosine, embed_text, provider_not_ready_reason, vector_from_blob, vector_to_blob,
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

    // 8/14: dim 校验挪进 services/embed_cache_meta.rs, 两套缓存共用一份逻辑。
    //
    // 原来这里是 P3.5.15 写的一段: 拿 `embed_dim()` (值来自员工机 yaml 的 1024)
    // 跟 _meta 比, 不符就 DROP。两个毛病:
    //   · 判据是**配置**不是实际产出 —— 中央换成 768 维的模型, 员工 yaml 不改的话
    //     这里永远"匹配", 而真正的向量早就对不上了
    //   · 只比维度。同维度换模型察觉不到, 而那正是最坏的一种
    //
    // 现在只建表; 真正的对账在拿到向量之后做 (embed_cache_meta::reconcile),
    // 因为"实际产出的维度/模型"要等第一次成功 embed 才知道。
    crate::services::embed_cache_meta::ensure_table(&conn)?;
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
    // 8/14: 同 wiki_embed —— 报真原因, 不报"model 缺 / remote token 缺"这种把两条
    // 分支并列的说法。这条 message 会进日志, 而"到底缺哪个"正是排查的起点。
    if let Some(reason) = provider_not_ready_reason() {
        return Ok(RelevanceResult {
            model_loaded: false,
            ranked: vec![],
            message: format!("{reason}\n\n(相关性筛选跳过, 全量注入)"),
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

    // 8/14: 对账. 走到这里 query 已经 embed 成功了, 所以"实际的维度/模型"是已知的。
    // 跟库里记的账不符 → 说明中央换了向量模型 → 清缓存重建。
    // 清表闭包由这里给, 因为建表 SQL 是本模块的事。
    let cleared = crate::services::embed_cache_meta::reconcile(&conn, |c| {
        c.execute("DELETE FROM advisor_embed_cache", [])
            .map(|n| log::warn!("[advisor_relevance] 清掉 {n} 条旧向量"))
            .map_err(|e| format!("清 advisor_embed_cache 失败: {e}"))
    })?;
    if cleared {
        log::warn!("[advisor_relevance] 本轮全部重 embed (缓存刚被重建)");
    }

    // 解 BLOB 用的维度: 观测到的优先, 其次 _meta 里上次记的。
    // 两个都没有 → 全部当 cache miss —— 拿一个猜的维度去解, 解出来的是能算出
    // 分数的垃圾, 比 miss 坏得多。
    let expected_dim = crate::services::embed_cache_meta::expected_dim(&conn);

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
            Some(blob) => match expected_dim.and_then(|d| vector_from_blob(&blob, d)) {
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
