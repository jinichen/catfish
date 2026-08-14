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
//!     # gateway_url / model 两个**都不用配** (8/14):
//!     #   地址 → 走 ~/.catfish/companion.yaml 的 endpoints 段 (单一真源)
//!     #   模型 → 不传, 由网关按 roles.yaml 的 embedding 角色解析
//!     #          (= 控制台 /admin/models 里那条"向量"类型的模型)
//!     # 只有"这台机器要跟全网用不一样的"才填, 填了就等于自己脱队。
//!     # gateway_url: http://10.10.40.50:8999
//!     # model: customer-x-embed
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
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Backend {
    /// 启动时 ping remote, 通用 remote, 否则 local. 一旦选定不动态切换 (重启 Companion 再检测).
    #[default]
    Auto,
    /// 本机 ONNX BGE-M3.
    Local,
    /// catfish-gateway /v1/embeddings 远程 API.
    Remote,
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
///
/// # 这里**没有**网关地址和模型名的代码默认值 (8/14)
///
/// 原来这两个字段各有一个 `default_xxx()` 硬编码:
///
///     fn default_gateway_url()  -> String { "http://127.0.0.1:8999".to_string() }
///     fn default_remote_model() -> String { "catfish-private-embed".to_string() }
///
/// `default_gateway_url` 上面还写着注释「跟 Companion endpoints.gateway_url 默认
/// 同源」—— 但它是**复制**不是同源, 没人保证两边一起改。
///
/// 后果都不报错, 只是悄悄换条路:
///
///   · 网关改成远端 IP、只改了 companion.yaml 的 endpoints 段
///     → 这里还 ping 127.0.0.1:8999 → 不通 → auto 退本机 ONNX
///   · sysadmin 在控制台换掉向量模型
///     → 这里还按 "catfish-private-embed" 请求 → 网关 404 → 同样退本机 ONNX
///
/// 而 Windows 的 msi 根本没编进 ort (Cargo.toml 只在 aarch64 拉 ort),
/// "退本机 ONNX" 在那边等于**没有向量**。
///
/// 现在两个字段都是 `Option`, 语义是"员工显式 override", 缺省时:
///
///   · 地址 → `endpoints::endpoints().gateway_base()` (companion.yaml 唯一真源)
///   · 模型 → **不传**, 由网关按 roles.yaml 的 `embedding` 角色解析
///     (= 控制台 /admin/models 里那条"向量"类型的模型, 见 app.py 的 /v1/embeddings)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteConfig {
    /// gateway base URL (不带 path). **缺省走 endpoints 单一真源**, 别在这里填默认值.
    #[serde(default)]
    pub gateway_url: Option<String>,
    /// 向量 model 名. **缺省不传, 由网关按 roles.yaml 的 embedding 角色解析**.
    /// 只有"这台机器要用跟全网不一样的向量模型"才配它 —— 配了就等于自己脱队。
    #[serde(default)]
    pub model: Option<String>,
    /// HTTP timeout. 默认 10s.
    #[serde(default = "default_timeout_seconds")]
    pub timeout_seconds: u64,
    /// remote 模型输出维度. 跟 model 绑死.
    #[serde(default = "default_embed_dim")]
    pub embed_dim: usize,
}

/// ⚠ 必须手写, **不能 `#[derive(Default)]`**。
///
/// `#[serde(default = "...")]` 只在**反序列化缺字段**时生效, 对
/// `Default::default()` 一点作用都没有。derive 出来的 Default 会给
/// `timeout_seconds = 0` / `embed_dim = 0`:
///
///   · timeout 0 → `Duration::from_secs(0)` → reqwest 每次请求当场超时
///   · embed_dim 0 → 维度校验和 sqlite `_meta` 全乱
///
/// 而 `EmbeddingConfig::default()` 正是"员工机上没有 embedding.yaml"的路径 ——
/// 也就是**绝大多数员工**。这条路上向量会静默全挂。
///
/// 8/14 我第一版顺手 derive 了 Default, 就是这个坑; 写下来免得下次又顺手。
impl Default for RemoteConfig {
    fn default() -> Self {
        Self {
            // 这两个没有代码默认值 —— 见结构体上的长注释
            gateway_url: None,
            model: None,
            timeout_seconds: default_timeout_seconds(),
            embed_dim: default_embed_dim(),
        }
    }
}

impl RemoteConfig {
    /// 实际要用的网关地址: yaml 显式 override > endpoints 单一真源.
    ///
    /// 空串按"没配"处理 —— 员工把 yaml 里的值删成 `gateway_url: ""` 时,
    /// 拼出来会是 `/v1/embeddings` 这种打不出去的地址, 而报错长得像网络问题。
    pub fn resolved_gateway_url(&self) -> String {
        match self
            .gateway_url
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
        {
            Some(u) => u.trim_end_matches('/').to_string(),
            None => crate::services::endpoints::endpoints().gateway_base(),
        }
    }

    /// 要不要在请求体里带 model. None = 让网关按 roles.yaml 解析.
    pub fn resolved_model(&self) -> Option<String> {
        self.model
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
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
                "[embedding_config] yaml 加载 OK: backend={:?} local.model={:?} \
                 remote.gateway={} remote.model={}",
                cf.embedding.backend,
                cf.embedding.local.model_path,
                // 打**解析后**的值 —— 打原始 Option 的话, 日志里只会看到 None,
                // 而现场最想知道的恰恰是"它到底连了哪"。
                cf.embedding.remote.resolved_gateway_url(),
                cf.embedding
                    .remote
                    .resolved_model()
                    .unwrap_or_else(|| "<由网关按 roles.yaml 解析>".to_string())
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

#[cfg(test)]
mod tests {
    //! 向量这条路上**不许再有第二份真源** (8/14)。
    //!
    //! 病历: `default_gateway_url()` 和 `default_remote_model()` 各自复制了
    //! 一份 "http://127.0.0.1:8999" / "catfish-private-embed"。前者的注释还
    //! 写着「跟 Companion endpoints.gateway_url 默认同源」—— 是复制不是同源。
    //!
    //! 两种漂移都不报错, 只是悄悄换条路:
    //!   · 网关改远端 IP → 这里还 ping 127.0.0.1 → auto 退本机 ONNX
    //!   · 控制台换向量模型 → 这里还按老名字请求 → 404 → 同样退本机 ONNX
    //! 而 Windows msi 没编 ort, "退本机 ONNX" = 没有向量。
    //!
    //! 这里不打桩 endpoints —— 直接摆一个真的 companion.yaml, 走真的
    //! `endpoints::endpoints()`。打桩的话就只能证明"我调了那个函数",
    //! 证不了"员工改 yaml 真的会被这条路看到"。

    use super::*;

    /// 摆一个 HOME, 里面放真的 companion.yaml, 并让 endpoints 重读。
    ///
    /// guard 必须返给调用方持有 —— HOME 是进程全局的, 见 util/test_env 的长注释。
    fn with_gateway(url: &str) -> (tempfile::TempDir, std::sync::MutexGuard<'static, ()>) {
        let guard = crate::util::test_env::env_lock();
        let tmp = tempfile::TempDir::new().unwrap();
        let dir = tmp.path().join(".catfish");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("companion.yaml"),
            format!("endpoints:\n  gateway_url: {url}\n"),
        )
        .unwrap();
        std::env::set_var("HOME", tmp.path());
        std::env::set_var("USERPROFILE", tmp.path());
        crate::services::endpoints::reload();
        (tmp, guard)
    }

    fn load(yaml: &str) -> RemoteConfig {
        serde_yaml::from_str::<ConfigFile>(yaml)
            .unwrap()
            .embedding
            .remote
    }

    #[test]
    fn 默认值里不能有零() {
        // ★★★ `#[serde(default = "...")]` 只管反序列化缺字段, **不管
        // Default::default()**。8/14 第一版顺手 `#[derive(Default)]`,
        // 于是 timeout_seconds=0 (reqwest 当场超时) + embed_dim=0 (维度全乱)。
        //
        // 而 EmbeddingConfig::default() 正是"员工机上没有 embedding.yaml"
        // 那条路 —— 也就是绝大多数员工。
        let c = RemoteConfig::default();
        assert_ne!(c.timeout_seconds, 0, "timeout=0 → 每次请求当场超时");
        assert_ne!(c.embed_dim, 0, "embed_dim=0 → 维度校验和 sqlite _meta 全乱");
    }

    #[test]
    fn 没配地址时_跟着_companion_yaml_走() {
        // ★★★ 这条就是事故本体: 员工改了 companion.yaml 的网关,
        // 向量这条路必须跟着改, 不能还盯着 127.0.0.1。
        let (_tmp, _g) = with_gateway("http://10.10.40.50:8999");
        let c = load("embedding:\n  remote:\n    timeout_seconds: 5\n");
        assert_eq!(c.resolved_gateway_url(), "http://10.10.40.50:8999");
    }

    #[test]
    fn 整个_embedding_yaml_都没有时_也跟着走() {
        // 绝大多数员工机上没有 embedding.yaml, 走的是 Default::default()
        let (_tmp, _g) = with_gateway("http://10.10.40.51:8999");
        let c = EmbeddingConfig::default().remote;
        assert_eq!(c.resolved_gateway_url(), "http://10.10.40.51:8999");
        assert_ne!(c.timeout_seconds, 0);
    }

    #[test]
    fn 没配模型时_不传_让网关按_roles_yaml_解析() {
        // ★★★ 员工端存模型名 = 控制台换了向量模型这边不知道。
        let c = load("embedding:\n  remote:\n    timeout_seconds: 5\n");
        assert_eq!(c.resolved_model(), None);
        assert_eq!(RemoteConfig::default().resolved_model(), None);
    }

    #[test]
    fn 显式配了就用配的() {
        // 向后兼容 + 留一个"这台机器要脱队"的出口
        let (_tmp, _g) = with_gateway("http://10.10.40.50:8999");
        let c = load(
            "embedding:\n  remote:\n    gateway_url: http://192.168.1.7:9000/\n\
             \x20   model: customer-x-embed\n",
        );
        assert_eq!(c.resolved_gateway_url(), "http://192.168.1.7:9000", "尾斜杠要削");
        assert_eq!(c.resolved_model().as_deref(), Some("customer-x-embed"));
    }

    #[test]
    fn 空串和纯空白按没配处理() {
        // ★★ 员工把值删成空串是很常见的"我不想配了"。
        // 不当成没配的话, 会拼出 "/v1/embeddings" 这种打不出去的地址,
        // 而报错长得像网络问题, 查半天。
        let (_tmp, _g) = with_gateway("http://10.10.40.50:8999");
        let c = load("embedding:\n  remote:\n    gateway_url: \"\"\n    model: \"   \"\n");
        assert_eq!(c.resolved_gateway_url(), "http://10.10.40.50:8999");
        assert_eq!(c.resolved_model(), None);
    }

    #[test]
    fn 老员工_yaml_里那两行仍然读得进来() {
        // 6/16 起 embedding.yaml 的示例就写着这两行, 员工机上是有的。
        // 字段从 String 改成 Option<String>, 老 yaml 不能读不进来。
        let c = load(
            "embedding:\n  remote:\n    gateway_url: http://127.0.0.1:8999\n\
             \x20   model: catfish-private-embed\n    timeout_seconds: 10\n    embed_dim: 1024\n",
        );
        assert_eq!(c.resolved_gateway_url(), "http://127.0.0.1:8999");
        assert_eq!(c.resolved_model().as_deref(), Some("catfish-private-embed"));
        assert_eq!(c.embed_dim, 1024);
    }

    #[test]
    fn 这个文件里不许再出现硬编码的网关地址或向量模型名() {
        // ★★ 防回流。上面几条都能通过"再加一个 default_xxx() 然后不调
        // resolved_*" 绕过去 —— 那正是 8/14 之前的形态。
        let src = include_str!("embedding_config.rs");
        let code: String = src
            .lines()
            .filter(|l| {
                let t = l.trim_start();
                !t.starts_with("//") && !t.starts_with("///") && !t.starts_with("//!")
            })
            .collect::<Vec<_>>()
            .join("\n");
        // 测试自己写的那些地址要排除掉 —— 只看 mod tests 之前的部分
        let prod = code.split("#[cfg(test)]").next().unwrap_or("");
        assert!(
            !prod.contains("127.0.0.1"),
            "又出现硬编码网关地址 —— 地址的真源是 companion.yaml 的 endpoints 段"
        );
        assert!(
            !prod.contains("catfish-private-embed"),
            "又出现硬编码向量模型名 —— 模型的真源是中央 roles.yaml 的 embedding 角色"
        );
    }
}
