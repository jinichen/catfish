//! P3.5.15 (6/16 鸿波): embedding provider yaml 配置.
//!
//! 解决硬编码痛点 — 换模型 / 调参 / 本机 vs 远程切换都得改 Rust + recompile.
//!
//! # yaml 位置
//!
//! ~/.catfish/embedding.yaml (员工本机)
//!
//! 配置不在 → fallback 全部 default (= 现 P3.5.4 硬编码行为, 走本机 BGE-M3).
//! 向后兼容: 老员工没装 yaml 也 work, 行为不变.
//!
//! # schema
//!
//! ```yaml
//! embedding:
//!   backend: auto              # auto | local | remote
//!                              # auto: 启动 ping remote (timeout_seconds), 通则 remote, 否则 local
//!   local:
//!     model_path: ~/.catfish/models/bge-m3.onnx
//!     tokenizer_path: ~/.catfish/models/tokenizer.json
//!     embed_dim: 1024          # 必须跟 onnx 输出维度对得上 (启动校验)
//!     max_tokens: 512          # 截断上限. BGE-M3 原生 8192 但 512 是性能折中
//!     intra_threads: 4         # ort Session 推理线程
//!   remote:
//!     gateway_url: http://127.0.0.1:8999
//!     model: catfish-private-embed   # catfish-gateway models.yaml 里 mode=embedding 的 model
//!     timeout_seconds: 10      # HTTP timeout
//!     embed_dim: 1024          # 必须跟 remote 模型对得上 (启动校验)
//!     # auth token 从 env CATFISH_INTERNAL_DEV_TOKEN 拿, 不放 yaml (secret 防泄露)
//! ```
//!
//! # cache 安全
//!
//! 切换 backend / 换模型 / 改 embed_dim → sqlite BLOB 长度变 → vector_from_blob 拒读.
//! 新代码 ensure_schema 加 `_meta` 表存 dim, 启动跟当前 embed_dim 不符 → DROP + 重建.
//! 防 schema 升级时老 cache 撞错.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

/// embedding provider 选择. auto = 启动检测.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Backend {
    /// 启动时 ping remote, 通用 remote, 否则 local. 一旦选定不动态切换 (重启 Companion 再检测).
    Auto,
    /// 本机 ONNX BGE-M3.
    Local,
    /// catfish-gateway /v1/embeddings 远程 API.
    Remote,
}

impl Default for Backend {
    fn default() -> Self {
        Backend::Auto
    }
}

/// 本机 ONNX 配置.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalConfig {
    /// onnx 模型路径. 默认 ~/.catfish/models/bge-m3.onnx.
    #[serde(default = "default_model_path")]
    pub model_path: String,
    /// tokenizer.json 路径. 默认 ~/.catfish/models/tokenizer.json.
    #[serde(default = "default_tokenizer_path")]
    pub tokenizer_path: String,
    /// embed 输出维度. BGE-M3 默认 1024. 跟模型绑死, 启动校验.
    #[serde(default = "default_embed_dim")]
    pub embed_dim: usize,
    /// 输入 token 截断上限. 默认 512.
    #[serde(default = "default_max_tokens")]
    pub max_tokens: usize,
    /// ort Session intra-op 线程数. 默认 4.
    #[serde(default = "default_intra_threads")]
    pub intra_threads: usize,
}

impl Default for LocalConfig {
    fn default() -> Self {
        Self {
            model_path: default_model_path(),
            tokenizer_path: default_tokenizer_path(),
            embed_dim: default_embed_dim(),
            max_tokens: default_max_tokens(),
            intra_threads: default_intra_threads(),
        }
    }
}

/// 远程 catfish-gateway 配置.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteConfig {
    /// gateway base URL (不带 path). 默认 http://127.0.0.1:8999.
    #[serde(default = "default_gateway_url")]
    pub gateway_url: String,
    /// model 名 — catfish-gateway models.yaml 里 mode=embedding 的 model.
    /// 默认 catfish-private-embed.
    #[serde(default = "default_remote_model")]
    pub model: String,
    /// HTTP timeout. 默认 10s.
    #[serde(default = "default_timeout_seconds")]
    pub timeout_seconds: u64,
    /// remote 模型输出维度. 跟 model 绑死.
    #[serde(default = "default_embed_dim")]
    pub embed_dim: usize,
}

impl Default for RemoteConfig {
    fn default() -> Self {
        Self {
            gateway_url: default_gateway_url(),
            model: default_remote_model(),
            timeout_seconds: default_timeout_seconds(),
            embed_dim: default_embed_dim(),
        }
    }
}

/// 完整配置 — yaml 顶级 `embedding:` 段映射.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct EmbeddingConfig {
    #[serde(default)]
    pub backend: Backend,
    #[serde(default)]
    pub local: LocalConfig,
    #[serde(default)]
    pub remote: RemoteConfig,
}

/// yaml 文件 wrapper — yaml 顶级一定是 `embedding:` 包一层, 跟 hermes / catfish 其他 yaml 同模式.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ConfigFile {
    #[serde(default)]
    pub embedding: EmbeddingConfig,
}

/// 默认 yaml 路径 ~/.catfish/embedding.yaml.
fn config_yaml_path() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(|h| PathBuf::from(h).join(".catfish").join("embedding.yaml"))
}

/// load_config: 读 yaml, 不存在 / 解析失败回 default.
///
/// 设计取舍: yaml 失败时不 panic, 因为 embedding 不是 P0 路径 (没 yaml = 老硬编码行为).
/// 真坏 yaml 只 log warn, 让 Companion 仍能起.
pub fn load_config() -> EmbeddingConfig {
    let path = match config_yaml_path() {
        Some(p) => p,
        None => return EmbeddingConfig::default(),
    };
    if !path.exists() {
        log::info!(
            "[embedding_config] yaml 不存在 {:?}, 走 default (本机 BGE-M3 硬编码值)",
            path
        );
        return EmbeddingConfig::default();
    }
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) => {
            log::warn!("[embedding_config] 读 yaml 失败 {:?}: {}, 走 default", path, e);
            return EmbeddingConfig::default();
        }
    };
    match serde_yaml::from_str::<ConfigFile>(&text) {
        Ok(cf) => {
            log::info!(
                "[embedding_config] yaml 加载 OK: backend={:?} local.model={:?} remote.model={}",
                cf.embedding.backend,
                cf.embedding.local.model_path,
                cf.embedding.remote.model
            );
            cf.embedding
        }
        Err(e) => {
            log::warn!("[embedding_config] yaml 解析失败: {}, 走 default", e);
            EmbeddingConfig::default()
        }
    }
}

// ─── default 函数 (跟 P3.5.4 老硬编码值对齐, 100% 向后兼容) ────────────

fn default_model_path() -> String {
    // P3.5.4 老 model_path() 同款 ~/.catfish/models/bge-m3.onnx
    expand_home("~/.catfish/models/bge-m3.onnx")
}

fn default_tokenizer_path() -> String {
    expand_home("~/.catfish/models/tokenizer.json")
}

fn default_embed_dim() -> usize {
    1024 // BGE-M3 输出维度
}

fn default_max_tokens() -> usize {
    512 // P3.5.4 老 MAX_TOKENS
}

fn default_intra_threads() -> usize {
    4 // P3.5.4 老 with_intra_threads(4)
}

fn default_gateway_url() -> String {
    // 跟 Companion endpoints.gateway_url 默认 + catfish-memory plugin yaml 同源
    "http://127.0.0.1:8999".to_string()
}

fn default_remote_model() -> String {
    // catfish-gateway models.yaml line 112-122 装的 mode=embedding 那条
    "catfish-private-embed".to_string()
}

fn default_timeout_seconds() -> u64 {
    10
}

/// `~/...` 展开成 $HOME/...
pub fn expand_home(path: &str) -> String {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
            return PathBuf::from(home).join(rest).to_string_lossy().to_string();
        }
    }
    path.to_string()
}
