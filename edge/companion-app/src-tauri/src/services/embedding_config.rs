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
//!                              # auto: 启动看 token + ping remote, 都过才 remote;
//!                              #       运行时远程连续失败也会整体退回本机 (8/14)
//!   local:
//!     model_path: ~/.catfish/models/bge-m3.onnx
//!     tokenizer_path: ~/.catfish/models/tokenizer.json
//!     max_tokens: 512          # 截断上限. BGE-M3 原生 8192 但 512 是性能折中
//!     intra_threads: 4         # ort Session 推理线程
//!   remote:
//!     # ⚠ 这里**没有** gateway_url, 也没有 model (8/14)。
//!     #   地址 → ~/.catfish/companion.yaml 的 endpoints 段
//!     #   模型 → 中央控制台 /admin/models 里那条"向量"类型的模型,
//!     #          经 roles.yaml 的 embedding 角色, 员工端根本不传
//!     # 老 yaml 里这两行还在的话会被忽略, 但启动时会 warn 一声。
//!     timeout_seconds: 10      # HTTP timeout
//!     # auth token 从 env CATFISH_INTERNAL_DEV_TOKEN 拿, 不放 yaml (secret 防泄露)
//! ```
//!
//! # cache 安全
//!
//! 8/14 起**维度不再是配置** —— 从实际产出的向量里数出来 (remote 数响应,
//! local 读 ONNX 输出的 shape)。缓存的失效判断也跟着挪进
//! services/embed_cache_meta.rs, 判据是"这批向量是谁产的"(维度 + 模型标识),
//! 而不是"yaml 里写的维度"。中央换向量模型 → 员工端下次搜索时自己重建缓存。

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

/// embedding provider 选择. auto = 启动检测.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Backend {
    /// 启动时看 token + ping remote, 都过才 remote, 否则 local.
    /// 运行时远程连续失败到阈值也会整体退回本机 (8/14, 见 embedding.rs)。
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
    /// ⚠ 已废弃, 只用于报警 (8/14)。本机这条路的维度直接从 ONNX 输出的
    /// tensor shape 里取 —— shape 的最后一维就是 hidden size, 比配置准,
    /// 而且换模型文件不用改配置。
    #[serde(default, rename = "embed_dim")]
    pub legacy_embed_dim: Option<usize>,
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
            legacy_embed_dim: None,
            max_tokens: default_max_tokens(),
            intra_threads: default_intra_threads(),
        }
    }
}

/// 远程 catfish-gateway 配置.
///
/// # 这里**没有**网关地址, 也没有模型名 (8/14)
///
/// 原来两个字段各有一个硬编码默认值:
///
///     fn default_gateway_url()  -> String { "http://127.0.0.1:8999".to_string() }
///     fn default_remote_model() -> String { "catfish-private-embed".to_string() }
///
/// 第一版改成了 `Option`, 语义是"员工显式 override"。鸿波追问「远程的 embedding
/// 模型名应该从模型设置里面取回来, 这里写 yaml 不是有问题吗」—— 是有问题, 而且
/// 比"多一份副本"严重得多:
///
/// ## 为什么连 override 口子都不能留
///
/// 向量维度的一致性有 `_meta` 表兜着 (advisor_relevance.rs:97), 但它**只存一个
/// embed_dim 数字**。两个同样 1024 维的不同向量模型, 这道检查一点问题都看不出来:
/// 缓存里新旧向量长度相同、却来自不同的向量空间, cosine 算出来全是垃圾, 而且
/// 没有任何一处会报错。
///
/// 也就是说"这台机器用不一样的向量模型"这个场景本身就不成立 —— 向量是拿来跟
/// 索引里已有的向量比的, 换模型只会让结果静默地错。而向量模型是**管道类**,
/// catalog.py:48 明写"不给员工选"; 员工机的 yaml 能覆盖它, 跟那条规矩也是冲突的。
///
/// 真源:
///   · 地址 → `~/.catfish/companion.yaml` 的 endpoints 段 (services/endpoints.rs)
///   · 模型 → 中央 roles.yaml 的 `embedding` 角色, 员工端**根本不传**,
///     由网关自己解析 (app.py 的 /v1/embeddings)
///
/// 下面两个 `legacy_*` 只为了**能报警**: 老员工机上这两行是存在的, serde 默认
/// 忽略未知字段, 直接删字段的话它们会被静默无视 —— 员工不知道自己配的东西
/// 不生效了。留着解析, 读到就 warn。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteConfig {
    /// ⚠ 已废弃, 只用于报警. 地址真源是 companion.yaml 的 endpoints 段.
    #[serde(default, rename = "gateway_url")]
    pub legacy_gateway_url: Option<String>,
    /// ⚠ 已废弃, 只用于报警. 模型真源是中央 roles.yaml 的 embedding 角色.
    #[serde(default, rename = "model")]
    pub legacy_model: Option<String>,
    /// HTTP timeout. 默认 10s.
    #[serde(default = "default_timeout_seconds")]
    pub timeout_seconds: u64,
    /// ⚠ 已废弃, 只用于报警 (8/14)。
    ///
    /// 维度是**中央那个模型的属性**, 现在从实际响应里数出来
    /// (services/embedding.rs 的 EmbedIdentity)。
    ///
    /// 留着它当配置的代价不是"多一份副本"那么轻: 老代码拿这个值去**否决响应** ——
    /// 中央换成 768 维的模型, 员工端就把每次拿回来的向量全丢掉, 只留一行 warn,
    /// 表现是"向量整个不能用", 而员工根本不知道中央换过。
    #[serde(default, rename = "embed_dim")]
    pub legacy_embed_dim: Option<usize>,
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
            legacy_gateway_url: None,
            legacy_model: None,
            timeout_seconds: default_timeout_seconds(),
            legacy_embed_dim: None,
        }
    }
}

impl RemoteConfig {
    /// 网关地址. **只有一个来源** —— companion.yaml 的 endpoints 段.
    pub fn resolved_gateway_url(&self) -> String {
        crate::services::endpoints::endpoints().gateway_base()
    }

    /// 老 yaml 里那两行还在的话, 说清楚它们已经不生效了.
    ///
    /// 静默忽略是最坏的处理: 员工改了个值、重启、行为没变, 也没有任何提示。
    pub fn warn_legacy_keys(&self) {
        if let Some(u) = &self.legacy_gateway_url {
            log::warn!(
                "[embedding_config] ~/.catfish/embedding.yaml 里的 remote.gateway_url={u} \
                 **已不再生效** —— 网关地址的唯一来源是 ~/.catfish/companion.yaml 的 \
                 endpoints 段 (当前解析为 {})。这一行可以删了。",
                self.resolved_gateway_url()
            );
        }
        if let Some(d) = self.legacy_embed_dim {
            log::warn!(
                "[embedding_config] ~/.catfish/embedding.yaml 里的 remote.embed_dim={d} \
                 **已不再生效** —— 维度现在从网关实际返回的向量里数出来。\
                 中央换成别的维度时员工端会自己跟上并重建缓存, 不用再手动改这里。"
            );
        }
        if let Some(m) = &self.legacy_model {
            log::warn!(
                "[embedding_config] ~/.catfish/embedding.yaml 里的 remote.model={m} \
                 **已不再生效** —— 向量模型由中央决定 (控制台 /admin/models 里那条\"向量\"\
                 类型的模型, 经 roles.yaml 的 embedding 角色), 员工端不再传模型名。\
                 这一行可以删了。"
            );
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
            cf.embedding.remote.warn_legacy_keys();
            log::info!(
                "[embedding_config] yaml 加载 OK: backend={:?} local.model={:?} \
                 remote.gateway={} (向量模型由中央 roles.yaml 决定)",
                cf.embedding.backend,
                cf.embedding.local.model_path,
                // 打**解析后**的值 —— 打原始 Option 的话, 日志里只会看到 None,
                // 而现场最想知道的恰恰是"它到底连了哪"。
                cf.embedding.remote.resolved_gateway_url()
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
    fn timeout_默认值不能是零() {
        // ★★★ `#[serde(default = "...")]` 只管反序列化缺字段, **不管
        // Default::default()**。8/14 第一版顺手 `#[derive(Default)]`,
        // 于是 timeout_seconds=0 (reqwest 当场超时) + embed_dim=0 (维度全乱)。
        //
        // 而 EmbeddingConfig::default() 正是"员工机上没有 embedding.yaml"
        // 那条路 —— 也就是绝大多数员工。
        let c = RemoteConfig::default();
        assert_ne!(c.timeout_seconds, 0, "timeout=0 → 每次请求当场超时");
    }

    #[test]
    fn 地址永远跟着_companion_yaml_走() {
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
    fn 配置里根本没有可用的模型名字段() {
        // ★★★ 员工端一旦存了模型名, 控制台换向量模型这边就不知道。
        // 现在连"能取出来用"的方法都不提供 —— RemoteConfig 上没有任何
        // 返回"要发给网关的模型名"的 API, 请求体里也就不可能带上它。
        let c = load("embedding:\n  remote:\n    timeout_seconds: 5\n");
        assert_eq!(c.legacy_model, None);
        assert_eq!(RemoteConfig::default().legacy_model, None);
    }

    #[test]
    fn 老_yaml_里显式配的那两行_一律不生效() {
        // ★★★ 第一版把它们做成了"员工显式 override"。鸿波追问之后改掉了:
        //
        // 向量模型是管道类 (catalog.py:48「不给员工选」), 而且维度一致性只靠
        // _meta 里一个 embed_dim 数字兜着 —— 两个同样 1024 维的不同向量模型,
        // 那道检查一点都看不出来: 缓存里新旧向量长度相同、来自不同的向量空间,
        // cosine 全是垃圾, 没有一处报错。
        //
        // 所以"这台机器用不一样的向量模型"这个场景本身就不成立, 口子不能留。
        let (_tmp, _g) = with_gateway("http://10.10.40.50:8999");
        let c = load(
            "embedding:\n  remote:\n    gateway_url: http://192.168.1.7:9000/\n\
             \x20   model: customer-x-embed\n",
        );
        assert_eq!(
            c.resolved_gateway_url(),
            "http://10.10.40.50:8999",
            "yaml 里写了别的地址也不算数, 真源是 companion.yaml 的 endpoints"
        );
        // 但要能被读出来 —— 读不出来就没法报警
        assert_eq!(c.legacy_gateway_url.as_deref(), Some("http://192.168.1.7:9000/"));
        assert_eq!(c.legacy_model.as_deref(), Some("customer-x-embed"));
    }

    #[test]
    fn 老字段必须解析得出来_否则报警无从谈起() {
        // ★★ 直接删字段的话 serde 会**静默忽略**, 员工改了值、重启、行为没变,
        // 也没有任何提示 —— 那正是今天一直在修的那种病。
        // 留着解析 + warn_legacy_keys() 才有得报。
        let c = load("embedding:\n  remote:\n    model: 随便什么\n    embed_dim: 768\n");
        assert_eq!(c.legacy_model.as_deref(), Some("随便什么"));
        assert_eq!(c.legacy_embed_dim, Some(768));
        c.warn_legacy_keys(); // 不 panic 即可 (内容进日志)
    }

    #[test]
    fn 空串和纯空白按没配处理() {
        // ★★ 员工把值删成空串是很常见的"我不想配了"。
        // 不当成没配的话, 会拼出 "/v1/embeddings" 这种打不出去的地址,
        // 而报错长得像网络问题, 查半天。
        let (_tmp, _g) = with_gateway("http://10.10.40.50:8999");
        let c = load("embedding:\n  remote:\n    gateway_url: \"\"\n    model: \"   \"\n");
        assert_eq!(c.resolved_gateway_url(), "http://10.10.40.50:8999");
    }

    #[test]
    fn 老员工_yaml_里那两行仍然读得进来() {
        // 6/16 起 embedding.yaml 的示例就写着这两行, 员工机上是有的。
        // 字段改成 legacy_* 之后, 老 yaml 仍然要能**解析**(否则没法报警),
        // 只是不再影响行为。
        //
        // 这条自己摆 HOME —— 地址现在一律走 endpoints, 不摆的话会读到上一个
        // 测试留下的 HOME (env 是进程全局的, 见 util/test_env 的长注释)。
        let (_tmp, _g) = with_gateway("http://127.0.0.1:8999");
        let c = load(
            "embedding:\n  remote:\n    gateway_url: http://127.0.0.1:8999\n\
             \x20   model: catfish-private-embed\n    timeout_seconds: 10\n    embed_dim: 1024\n",
        );
        assert_eq!(c.resolved_gateway_url(), "http://127.0.0.1:8999");
        assert_eq!(c.timeout_seconds, 10, "timeout 还是真配置");
        // embed_dim 也退成 legacy 了 —— 读得出来 (要报警), 但不作数
        assert_eq!(c.legacy_embed_dim, Some(1024));
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
