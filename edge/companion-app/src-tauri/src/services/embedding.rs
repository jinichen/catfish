//! P3.5.4.1 (6/16 鸿波): 公共 BGE-M3 ONNX embedding service.
//! P3.5.15 (6/16 鸿波): provider 抽象 + yaml 配置 + 本机/远程切换 + auto fallback.
//!
//! # 历史
//!
//! P38 (6/5) wiki_embed.rs 加了本机 ONNX BGE-M3, 私有 fn 不能跨 command 共享.
//! P3.5.4 (6/16) advisor_relevance 也要用, 抽到 services::embedding 共享 ONNX session.
//! P3.5.15 (6/16) 鸿波反馈: 全部硬编码, 换模型 / 调参 / 本机 vs 远程切换都要改 Rust
//! + recompile. 加 yaml + Provider enum 解决.
//!
//! # 两种 backend
//!
//! 1. **Local ONNX** (`Provider::Local`) — 本机 `~/.catfish/models/bge-m3.onnx` 推理.
//!    离线可用. ort + tokenizers crate. 570MB 模型驻留 mac 内存.
//! 2. **Remote API** (`Provider::Remote`) — catfish-gateway `/v1/embeddings`.
//!    走中央 GPU 算力, mac 不烧 CPU. 需要内网联通 + auth token.
//! 3. **Auto** — 启动时 ping remote (timeout), 通则 remote, 否则 local.
//!    一旦选定不动态切换 (重启 Companion 才重新检测).
//!
//! # 配置
//!
//! `~/.catfish/embedding.yaml` (没配 = 走 default = P3.5.4 老硬编码值).
//! 详细 schema 见 `embedding_config.rs` 顶部注释.
//!
//! # 共享状态
//!
//! `ACTIVE_PROVIDER`: 全 process 唯一 OnceLock. wiki_embed + advisor_relevance + 任何
//! caller 拿同一份. Local 内 Session 也 OnceLock, 不重 load 570MB ONNX.
//!
//! # caller API (跟 P3.5.4 兼容)
//!
//! - `embed_text(text) -> Option<Vec<f32>>` 现 async (Remote 走 HTTP, Local sync 跑)
//! - `cosine(a, b) -> f32` 同 (sync)
//! - `vector_to_blob(v) / vector_from_blob(blob) -> Option<Vec<f32>>` 同 (sync, 跟 cache 互通)
//! - `is_provider_ready() -> bool` 替代 P3.5.4 `init_session().is_some() && init_tokenizer().is_some()`
//! - `embed_dim() -> usize` 替代 const EMBED_DIM, 运行时取真值 (跟 active provider 绑死)

use ort::session::{Session, builder::GraphOptimizationLevel};
use ort::value::Value;
use serde::Deserialize;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;
use tokenizers::Tokenizer;

use crate::services::embedding_config::{
    Backend, EmbeddingConfig, LocalConfig, RemoteConfig, expand_home, load_config,
};

// ─── ACTIVE_PROVIDER — 全 process 唯一, 启动 lazy init ────────────

static ACTIVE_PROVIDER: OnceLock<Provider> = OnceLock::new();

/// Provider enum — 不用 trait + dyn 避 async_trait 依赖, 直接 match 路由.
///
/// P39 (clippy large_enum_variant): LocalProvider 装 ONNX Session (1000+ bytes),
/// RemoteProvider 只 72 bytes — 差 16x. Box LocalProvider 让 enum 变紧, 内存
/// 只在 Local 场景多一次堆分配 (整个 process 只 init 一次, 无损).
enum Provider {
    Local(Box<LocalProvider>),
    Remote(RemoteProvider),
}

impl Provider {
    fn embed_dim(&self) -> usize {
        match self {
            Provider::Local(p) => p.config.embed_dim,
            Provider::Remote(p) => p.config.embed_dim,
        }
    }

    fn is_ready(&self) -> bool {
        match self {
            Provider::Local(p) => p.is_ready(),
            Provider::Remote(p) => p.is_ready(),
        }
    }

    async fn embed_text(&self, text: &str) -> Option<Vec<f32>> {
        match self {
            Provider::Local(p) => p.embed_text(text).await,
            Provider::Remote(p) => p.embed_text(text).await,
        }
    }
}

/// 启动时 lazy init. caller 第一次访问 `active_provider()` 时跑 (一次).
///
/// 决策路径:
///   - backend=Local → 直接 Local
///   - backend=Remote → 直接 Remote
///   - backend=Auto → ping remote /v1/models (timeout), 通则 Remote, 否则 Local
fn init_active_provider() -> Provider {
    let cfg: EmbeddingConfig = load_config();
    match cfg.backend {
        Backend::Local => {
            log::info!("[embedding] backend=local (yaml 显式)");
            Provider::Local(Box::new(LocalProvider::new(cfg.local)))
        }
        Backend::Remote => {
            log::info!(
                "[embedding] backend=remote (yaml 显式) → {}",
                cfg.remote.gateway_url
            );
            Provider::Remote(RemoteProvider::new(cfg.remote))
        }
        Backend::Auto => {
            log::info!("[embedding] backend=auto, ping remote 决定");
            let remote = RemoteProvider::new(cfg.remote.clone());
            // 启动期短超时 ping. 跑独立 blocking client (init_active_provider 在 sync context).
            if probe_remote_alive(&remote) {
                log::info!("[embedding] auto → remote OK (gateway 通)");
                Provider::Remote(remote)
            } else {
                log::info!("[embedding] auto → local (remote 不通, fallback ONNX)");
                Provider::Local(Box::new(LocalProvider::new(cfg.local)))
            }
        }
    }
}

/// 同步 ping remote /v1/catalog. 启动期用 (在 init_active_provider 内, sync context).
/// 不发真 embed 请求 — 只 GET /v1/catalog 检查可达, 几十 ms. 失败返 false → fallback local.
///
/// P3.5.22 (6/17 鸿波) 改 /v1/models → /v1/catalog 真因:
///   /v1/models 强 auth (app.py:1695 Depends(get_current_user)), 需 Bearer token.
///   auth_token() 从 env CATFISH_INTERNAL_DEV_TOKEN 读, 但 macOS GUI Tauri app
///   不继承 ~/.hermes/.env 的 env var → token 真空 → 不带 header → gateway 401.
///   每次 Companion 启动 + 周期性重 init 都撞 401 noise (鸿波 14:37/14:43 log 验证).
///
///   /v1/catalog 是 anon endpoint (no auth required) — 鸿波本机 log 一直
///   `GET /v1/catalog 200 OK` 频繁验证 anon 通. 用它当 "gateway 是否在线" 代理
///   判断, 语义跟 /v1/models 等价, 不再 401.
fn probe_remote_alive(remote: &RemoteProvider) -> bool {
    let url = format!(
        "{}/v1/catalog",
        remote.config.gateway_url.trim_end_matches('/')
    );
    let probe_timeout = Duration::from_secs(remote.config.timeout_seconds.min(3));
    let client = match reqwest::blocking::Client::builder()
        .timeout(probe_timeout)
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    // /v1/catalog 不需 auth, 不再带 bearer_auth.
    matches!(client.get(&url).send(), Ok(r) if r.status().is_success())
}

/// 暴露 active provider (lazy init). 全 process 唯一.
fn active_provider() -> &'static Provider {
    ACTIVE_PROVIDER.get_or_init(init_active_provider)
}

// ─── Public API (跟 P3.5.4 接口兼容, embed_text 加 async) ────────────

/// embed_text — text → L2-normalized f32 vector (dim = embed_dim()).
/// 失败返 None, caller 走 fallback (不筛, 全量注入).
pub async fn embed_text(text: &str) -> Option<Vec<f32>> {
    active_provider().embed_text(text).await
}

/// 当前 active provider 是否就绪 (model 加载好 / remote 可达 + token).
pub fn is_provider_ready() -> bool {
    active_provider().is_ready()
}

/// 当前 active provider 的输出维度. cache BLOB 长度 = embed_dim * 4 bytes.
pub fn embed_dim() -> usize {
    active_provider().embed_dim()
}

/// L2-normalized vectors 点积 = cosine similarity. 长度不匹配返 0.
pub fn cosine(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() {
        return 0.0;
    }
    let mut s = 0.0f32;
    for i in 0..a.len() {
        s += a[i] * b[i];
    }
    s
}

/// 序列化 vector 到 little-endian f32 BLOB (跟 cache db 同格式).
pub fn vector_to_blob(v: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(v.len() * 4);
    for &f in v {
        out.extend_from_slice(&f.to_le_bytes());
    }
    out
}

/// 反序列化 little-endian f32 BLOB. 长度跟当前 embed_dim 不符返 None.
/// caller 拿到 None 视为 cache miss → 重 embed.
pub fn vector_from_blob(blob: &[u8]) -> Option<Vec<f32>> {
    let dim = embed_dim();
    if blob.len() != dim * 4 {
        return None;
    }
    let mut v = vec![0.0f32; dim];
    for i in 0..dim {
        v[i] = f32::from_le_bytes([
            blob[i * 4],
            blob[i * 4 + 1],
            blob[i * 4 + 2],
            blob[i * 4 + 3],
        ]);
    }
    Some(v)
}

// ─── Local ONNX provider — 老 P3.5.4 路径重构进来 ────────────

struct LocalProvider {
    config: LocalConfig,
    session: OnceLock<Option<Mutex<Session>>>,
    tokenizer: OnceLock<Option<Tokenizer>>,
}

impl LocalProvider {
    fn new(config: LocalConfig) -> Self {
        Self {
            config,
            session: OnceLock::new(),
            tokenizer: OnceLock::new(),
        }
    }

    fn init_session(&self) -> Option<&Mutex<Session>> {
        self.session
            .get_or_init(|| {
                let path = PathBuf::from(expand_home(&self.config.model_path));
                if !path.exists() {
                    log::warn!(
                        "[embedding/local] ONNX 文件不存在 {:?}, embed_text 将返 None",
                        path
                    );
                    return None;
                }
                log::info!(
                    "[embedding/local] 加载 ONNX {:?} (threads={})",
                    path,
                    self.config.intra_threads
                );
                let session = Session::builder()
                    .ok()?
                    .with_optimization_level(GraphOptimizationLevel::Level3)
                    .ok()?
                    .with_intra_threads(self.config.intra_threads)
                    .ok()?
                    .commit_from_file(&path)
                    .ok()?;
                Some(Mutex::new(session))
            })
            .as_ref()
    }

    fn init_tokenizer(&self) -> Option<&Tokenizer> {
        self.tokenizer
            .get_or_init(|| {
                let path = PathBuf::from(expand_home(&self.config.tokenizer_path));
                if !path.exists() {
                    log::warn!(
                        "[embedding/local] tokenizer.json 缺 {:?}, embed_text 将返 None",
                        path
                    );
                    return None;
                }
                Tokenizer::from_file(&path).ok()
            })
            .as_ref()
    }

    fn is_ready(&self) -> bool {
        self.init_session().is_some() && self.init_tokenizer().is_some()
    }

    async fn embed_text(&self, text: &str) -> Option<Vec<f32>> {
        // 注: ONNX 推理 sync 长跑 (10-50ms). 主线程跑可接受 (P3.5.4 实测 OK).
        //     真 spawn_blocking 跨线程要 Arc<Session>, 复杂度 / 收益不平衡, 保持当前.
        let session_mutex = self.init_session()?;
        let tokenizer = self.init_tokenizer()?;
        let dim = self.config.embed_dim;
        let max_tokens = self.config.max_tokens;

        let encoding = tokenizer
            .encode(text, true)
            .map_err(|e| log::warn!("[embedding/local] tokenize 失败: {e}"))
            .ok()?;
        let ids = encoding.get_ids();
        let mask = encoding.get_attention_mask();

        let n = ids.len().min(max_tokens);
        let input_ids: Vec<i64> = ids[..n].iter().map(|&x| x as i64).collect();
        let attention_mask: Vec<i64> = mask[..n].iter().map(|&x| x as i64).collect();

        let shape = vec![1_i64, n as i64];
        let inputs = ort::inputs![
            "input_ids" => Value::from_array((shape.clone(), input_ids)).ok()?,
            "attention_mask" => Value::from_array((shape, attention_mask)).ok()?,
        ];

        let mut session = session_mutex.lock().ok()?;
        let outputs = session.run(inputs).ok()?;
        let (_, output_data) = outputs[0].try_extract_tensor::<f32>().ok()?;
        if output_data.len() < dim {
            log::warn!(
                "[embedding/local] ONNX 输出长度 {} < embed_dim {} — 模型跟 yaml.local.embed_dim 不匹配",
                output_data.len(),
                dim
            );
            return None;
        }
        let seq_len = output_data.len() / dim;
        let mut pooled = vec![0.0f32; dim];
        for s in 0..seq_len {
            for i in 0..dim {
                pooled[i] += output_data[s * dim + i];
            }
        }
        for v in pooled.iter_mut() {
            *v /= seq_len as f32;
        }
        let norm = (pooled.iter().map(|v| v * v).sum::<f32>()).sqrt().max(1e-8);
        for v in pooled.iter_mut() {
            *v /= norm;
        }
        Some(pooled)
    }
}

// ─── Remote API provider — catfish-gateway /v1/embeddings ────────────

struct RemoteProvider {
    config: RemoteConfig,
    client: reqwest::Client,
}

impl RemoteProvider {
    fn new(config: RemoteConfig) -> Self {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(config.timeout_seconds))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());
        Self { config, client }
    }

    /// is_ready: remote 不 lazy load model, 只要 auth token 拿得到就算 ready.
    /// 真连通靠每次 embed_text 时的 HTTP, 失败 caller 拿 None 走 fallback.
    fn is_ready(&self) -> bool {
        !auth_token().is_empty()
    }

    async fn embed_text(&self, text: &str) -> Option<Vec<f32>> {
        if text.is_empty() {
            return None;
        }
        let url = format!(
            "{}/v1/embeddings",
            self.config.gateway_url.trim_end_matches('/')
        );
        let body = serde_json::json!({
            "model": self.config.model,
            "input": text,
        });

        let token = auth_token();
        if token.is_empty() {
            log::warn!("[embedding/remote] CATFISH_INTERNAL_DEV_TOKEN 未配 → caller 走 fallback");
            return None;
        }

        let resp = self
            .client
            .post(&url)
            .bearer_auth(&token)
            .json(&body)
            .send()
            .await
            .map_err(|e| log::warn!("[embedding/remote] POST {url} 失败: {e}"))
            .ok()?;

        if !resp.status().is_success() {
            log::warn!(
                "[embedding/remote] POST {} → {} (caller 走 fallback)",
                url,
                resp.status()
            );
            return None;
        }

        let parsed: EmbeddingApiResponse = resp
            .json()
            .await
            .map_err(|e| log::warn!("[embedding/remote] 解析 response JSON 失败: {e}"))
            .ok()?;

        let first = parsed.data.into_iter().next()?;
        if first.embedding.len() != self.config.embed_dim {
            log::warn!(
                "[embedding/remote] 返回 dim {} ≠ yaml.remote.embed_dim {} — model 跟 yaml 不匹配",
                first.embedding.len(),
                self.config.embed_dim
            );
            return None;
        }

        // 强制 L2 normalize — 跟 local 行为一致, 让 cosine 等价 dot product.
        // 上游已 normalize 时 idempotent (norm ≈ 1, 再除 1 不影响).
        let mut v = first.embedding;
        let norm = (v.iter().map(|x| x * x).sum::<f32>()).sqrt().max(1e-8);
        for x in v.iter_mut() {
            *x /= norm;
        }
        Some(v)
    }
}

/// 从 env 读 catfish-gateway internal dev token. ~/.hermes/.env 也常配同名.
fn auth_token() -> String {
    std::env::var("CATFISH_INTERNAL_DEV_TOKEN")
        .ok()
        .unwrap_or_default()
}

#[derive(Debug, Deserialize)]
struct EmbeddingApiResponse {
    data: Vec<EmbeddingApiDatum>,
}

#[derive(Debug, Deserialize)]
struct EmbeddingApiDatum {
    embedding: Vec<f32>,
}

// P3.5.15 (6/16 鸿波 cargo check 通过后清理): 老 P3.5.4 init_session / init_tokenizer
// 两个 pub fn 已删. caller 全部迁到 is_provider_ready() / embed_text() — 看 provider
// (Local + Remote) 是否就绪不再绑死本机 ONNX 文件存在. 删后 cargo check 0 warning.
