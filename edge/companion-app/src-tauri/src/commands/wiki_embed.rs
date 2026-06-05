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

use ort::session::{Session, builder::GraphOptimizationLevel};
use ort::value::Value;
use serde::Serialize;
use std::fs;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use tokenizers::Tokenizer;

// ort 2.0 Session::run 要 &mut self → 走 Mutex 包. 单查 query 真序列化, 不 bottleneck.
static MODEL_SESSION: OnceLock<Option<Mutex<Session>>> = OnceLock::new();
static TOKENIZER: OnceLock<Option<Tokenizer>> = OnceLock::new();

const MAX_TOKENS: usize = 512;
const EMBED_DIM: usize = 1024;

fn home_dir() -> Result<PathBuf, String> {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME".to_string())
}

fn model_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("models").join("bge-m3.onnx"))
}

fn tokenizer_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("models").join("tokenizer.json"))
}

fn embed_db_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("wiki_embeddings.db"))
}

/// init_session: load BGE-M3 ONNX 真**`OnceLock cache**真. 失败 (model 文件缺)
/// 返 None → caller 走 fallback path.
fn init_session() -> Option<&'static Mutex<Session>> {
    MODEL_SESSION
        .get_or_init(|| {
            let path = model_path().ok()?;
            if !path.exists() {
                log::warn!("P38 wiki embed: model 文件不存在 {:?}, 走 fallback", path);
                return None;
            }
            log::info!("P38 wiki embed: 加载 BGE-M3 ONNX {:?}", path);
            let session = Session::builder()
                .ok()?
                .with_optimization_level(GraphOptimizationLevel::Level3)
                .ok()?
                .with_intra_threads(4)
                .ok()?
                .commit_from_file(&path)
                .ok()?;
            Some(Mutex::new(session))
        })
        .as_ref()
}

fn init_tokenizer() -> Option<&'static Tokenizer> {
    TOKENIZER
        .get_or_init(|| {
            let path = tokenizer_path().ok()?;
            if !path.exists() {
                log::warn!("P38: tokenizer.json 缺 {:?}, 走 fallback", path);
                return None;
            }
            Tokenizer::from_file(&path).ok()
        })
        .as_ref()
}

/// embed_text: text → 1024-dim f32 vector. 失败返 None (caller 走 fallback).
fn embed_text(text: &str) -> Option<Vec<f32>> {
    let session_mutex = init_session()?;
    let tokenizer = init_tokenizer()?;

    let encoding = tokenizer
        .encode(text, true)
        .map_err(|e| log::warn!("tokenizer encode 失败: {e}"))
        .ok()?;
    let ids = encoding.get_ids();
    let mask = encoding.get_attention_mask();

    // truncate to MAX_TOKENS
    let n = ids.len().min(MAX_TOKENS);
    let input_ids: Vec<i64> = ids[..n].iter().map(|&x| x as i64).collect();
    let attention_mask: Vec<i64> = mask[..n].iter().map(|&x| x as i64).collect();

    // P38 fix (6/5): 用 ort tuple syntax (shape, Vec<T>) 不依赖 ndarray version
    // (ort-rc12 内部用 ndarray 0.17, 我们的 0.16 冲突 → 改 tuple)
    let shape = vec![1_i64, n as i64];
    let inputs = ort::inputs![
        "input_ids" => Value::from_array((shape.clone(), input_ids)).ok()?,
        "attention_mask" => Value::from_array((shape, attention_mask)).ok()?,
    ];

    let mut session = session_mutex.lock().ok()?;
    let outputs = session.run(inputs).ok()?;
    // BGE-M3 ONNX 输出 last_hidden_state shape [1, seq_len, 1024]
    // 真**`mean pooling**` 真**`真**真**` 真**`[seq_len, 1024] → [1024]**真
    let (_, output_data) = outputs[0].try_extract_tensor::<f32>().ok()?;
    if output_data.len() < EMBED_DIM {
        log::warn!("ONNX output too short: {}", output_data.len());
        return None;
    }
    // mean pool: avg over seq_len dim
    let seq_len = output_data.len() / EMBED_DIM;
    let mut pooled = vec![0.0f32; EMBED_DIM];
    for s in 0..seq_len {
        for i in 0..EMBED_DIM {
            pooled[i] += output_data[s * EMBED_DIM + i];
        }
    }
    for v in pooled.iter_mut() {
        *v /= seq_len as f32;
    }
    // L2 normalize (cosine 真**`等价**` 真**`dot product**真)
    let norm = (pooled.iter().map(|v| v * v).sum::<f32>()).sqrt().max(1e-8);
    for v in pooled.iter_mut() {
        *v /= norm;
    }
    Some(pooled)
}

/// L2-normalized vectors 真 dot product = cosine similarity (我们 embed_text 已 L2 norm 真)
fn cosine(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() {
        return 0.0;
    }
    let mut s = 0.0f32;
    for i in 0..a.len() {
        s += a[i] * b[i];
    }
    s
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
/// 首次 call 自动同步 index 全 wiki/. 后续真**`增量逻辑**`** 真**`留 P38.3**真.
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

    // 检查 model + tokenizer
    if init_session().is_none() || init_tokenizer().is_none() {
        return Ok(WikiSemanticResult {
            hits: vec![],
            model_loaded: false,
            indexed_count: 0,
            message: format!(
                "BGE-M3 model 未装. 手动下载: \n\
                 mkdir -p ~/.catfish/models && \n\
                 curl -L -o ~/.catfish/models/bge-m3.onnx https://huggingface.co/Xenova/bge-m3/resolve/main/onnx/model_quantized.onnx && \n\
                 curl -L -o ~/.catfish/models/tokenizer.json https://huggingface.co/Xenova/bge-m3/resolve/main/tokenizer.json"
            ),
        });
    }

    // ensure index up-to-date (简化: 每次重 index, 56 entries < 5s)
    let indexed = ensure_index().map_err(|e| format!("index 失败: {e}"))?;

    // embed query
    let query_vec = embed_text(q).ok_or_else(|| "query embedding 失败".to_string())?;

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
        if vec_blob.len() != EMBED_DIM * 4 {
            continue;
        }
        let mut v = vec![0.0f32; EMBED_DIM];
        for i in 0..EMBED_DIM {
            v[i] = f32::from_le_bytes([
                vec_blob[i * 4],
                vec_blob[i * 4 + 1],
                vec_blob[i * 4 + 2],
                vec_blob[i * 4 + 3],
            ]);
        }
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
fn ensure_index() -> Result<usize, String> {
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

    let mut count = 0usize;
    for sub in &["wiki/entities", "wiki/concepts", "wiki/queries"] {
        let dir = home.join(".catfish").join(sub);
        if !dir.is_dir() {
            continue;
        }
        let entries = fs::read_dir(&dir).map_err(|e| format!("read_dir: {e}"))?;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("md") {
                continue;
            }
            let rel_path = format!(
                "{}/{}",
                sub,
                path.file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
            );
            let content = match fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };
            let mtime = entry
                .metadata()
                .ok()
                .and_then(|m| m.modified().ok())
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0);

            // skip 真**`如果 mtime 没**变`** 真**`SQLite cache 真**`** 真**`hit`**
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

            // parse 真**`title + kind`** 真**`frontmatter`** + snippet
            let (title, kind, snippet) = parse_for_embed(&content);
            // embed full content (frontmatter + body)
            let vec = match embed_text(&content) {
                Some(v) => v,
                None => continue,
            };
            let mut blob = Vec::with_capacity(EMBED_DIM * 4);
            for v in &vec {
                blob.extend_from_slice(&v.to_le_bytes());
            }
            conn.execute(
                "INSERT OR REPLACE INTO wiki_embed (rel_path, title, kind, snippet, vector, mtime)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                rusqlite::params![rel_path, title, kind, snippet, blob, mtime],
            )
            .map_err(|e| format!("insert: {e}"))?;
            count += 1;
        }
    }
    Ok(count)
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
