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
//! - `vector_to_blob(v)` / `vector_from_blob(blob, expected_dim)` (sync, 跟 cache 互通)
//! - `provider_not_ready_reason() -> Option<String>` 没就绪时给出**具体原因**
//!   (8/14: 原来是 `is_provider_ready() -> bool`, 只说 yes/no 的话界面只能
//!    把两条分支的排查步骤都列出来, 反而把人往错方向带)
//! - `observed_identity() -> Option<EmbedIdentity>` 实际产出的维度 + 模型标识
//!   (8/14: 原来是 `embed_dim() -> usize`, 值来自员工机 yaml —— 而维度是**中央
//!    那个模型的属性**。现在量出来, 见下面 EmbedIdentity 那段)

// 7/16 BL-INTEL-DMG: ort + tokenizers 只 aarch64 依赖 (Intel Mac / Windows msi
// 走 Remote provider). LocalProvider 相关 code 也全部 cfg-guard.
#[cfg(target_arch = "aarch64")]
use ort::session::{Session, builder::GraphOptimizationLevel};
#[cfg(target_arch = "aarch64")]
use ort::value::Value;
use serde::Deserialize;
#[cfg(target_arch = "aarch64")]
use std::path::PathBuf;
use std::sync::OnceLock;
#[cfg(target_arch = "aarch64")]
use std::sync::Mutex;
use std::time::Duration;
#[cfg(target_arch = "aarch64")]
use tokenizers::Tokenizer;

use crate::services::embedding_config::{
    Backend, EmbeddingConfig, RemoteConfig, load_config,
};
// expand_home 只 Local provider (init_session/tokenizer) 用, cfg-guard 免 x86_64 unused warn
#[cfg(target_arch = "aarch64")]
use crate::services::embedding_config::{LocalConfig, expand_home};

// 观测到的向量身份 (dim + 模型) 挪到了 services/embed_cache_meta.rs ——
// 它跟"缓存该不该重建"是同一件事, 放一起才好写测试 (那边只依赖 std + rusqlite,
// 不像这个文件拖着 ort / reqwest / tokenizers, 在没有 GTK 的机器上编不了)。
use crate::services::embed_cache_meta::{EmbedIdentity, record_identity};

// ─── ACTIVE_PROVIDER — 全 process 唯一, 启动 lazy init ────────────

static ACTIVE_PROVIDER: OnceLock<Provider> = OnceLock::new();

/// Provider enum — 不用 trait + dyn 避 async_trait 依赖, 直接 match 路由.
///
/// P39 (clippy large_enum_variant): LocalProvider 装 ONNX Session (1000+ bytes),
/// RemoteProvider 只 72 bytes — 差 16x. Box LocalProvider 让 enum 变紧, 内存
/// 只在 Local 场景多一次堆分配 (整个 process 只 init 一次, 无损).
enum Provider {
    #[cfg(target_arch = "aarch64")]
    Local(Box<LocalProvider>),
    Remote(RemoteProvider),
}

impl Provider {
    fn is_ready(&self) -> bool {
        match self {
            #[cfg(target_arch = "aarch64")]
            Provider::Local(p) => p.is_ready(),
            Provider::Remote(p) => p.is_ready(),
        }
    }

    /// 没就绪的话, **具体是哪个 provider、卡在什么地方**.
    ///
    /// 8/14: 原来 wiki 搜索那条提示是个静态字符串, 把 local 和 remote 两条路的
    /// 排查步骤一起列出来 —— 鸿波看到之后的第一反应是"本地模型失效?", 而实际
    /// 选中的是 remote、缺的是 token, 本机模型压根没被碰过。
    ///
    /// 一条不指向真因的提示, 比没有提示更费时间。
    fn not_ready_reason(&self) -> Option<String> {
        if self.is_ready() {
            return None;
        }
        match self {
            #[cfg(target_arch = "aarch64")]
            Provider::Local(p) => Some(p.not_ready_reason()),
            Provider::Remote(p) => Some(p.not_ready_reason()),
        }
    }

    async fn embed_text(&self, text: &str) -> Option<Vec<f32>> {
        match self {
            #[cfg(target_arch = "aarch64")]
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
            #[cfg(target_arch = "aarch64")]
            {
                log::info!("[embedding] backend=local (yaml 显式)");
                Provider::Local(Box::new(LocalProvider::new(cfg.local)))
            }
            // 7/16 BL-INTEL-DMG: x86_64 (Intel Mac dmg / Windows msi) 无 ort → 强制 fallback Remote
            #[cfg(not(target_arch = "aarch64"))]
            {
                log::warn!(
                    "[embedding] backend=local 配置但当前架构无 ONNX ort → fallback remote {}",
                    cfg.remote.resolved_gateway_url()
                );
                Provider::Remote(RemoteProvider::new(cfg.remote))
            }
        }
        Backend::Remote => {
            log::info!(
                "[embedding] backend=remote (yaml 显式) → {}",
                cfg.remote.resolved_gateway_url()
            );
            Provider::Remote(RemoteProvider::new(cfg.remote))
        }
        Backend::Auto => {
            log::info!("[embedding] backend=auto, 先看 remote 可不可用, 再看通不通");
            let remote = RemoteProvider::new(cfg.remote.clone());

            // ★ 8/14 现场那次的真因就在这两行的**顺序和条件**上。
            //
            // 老逻辑只问一句 `probe_remote_alive`(网关连不连得上), 连得上就定
            // Remote —— 但"连得上"不等于"用得了": RemoteProvider::is_ready() 还
            // 要求 CATFISH_INTERNAL_DEV_TOKEN 非空。
            //
            // 于是鸿波机器上出现了最别扭的组合: 网关就在本机跑着(ping 通),
            // token 没 export → 选了 Remote → is_ready() 永远 false →
            // **而它不会再退回 local**。那台机器上 569MB 的 bge-m3.onnx 好端端
            // 躺在 ~/.catfish/models/ 里, 一次都用不上, 界面只说"provider 未就绪",
            // 看着像本地模型坏了。
            //
            // 按定下来的设计, "远程失效就用本地"里的**失效包括"根本用不了"**,
            // 不只是"ping 不通"。所以这里两个条件都要过。
            //
            // 顺序也有意: is_ready() 只读一个 env var, probe 要发 HTTP 等最多
            // 3 秒。没 token 的时候连 ping 都不必发。
            if !remote.is_ready() {
                log::warn!(
                    "[embedding] auto → remote 不可用 (CATFISH_INTERNAL_DEV_TOKEN 未配), 转本地"
                );
            }
            if remote.is_ready() && probe_remote_alive(&remote) {
                log::info!("[embedding] auto → remote OK (gateway 通 + token 有)");
                Provider::Remote(remote)
            } else {
                #[cfg(target_arch = "aarch64")]
                {
                    log::info!("[embedding] auto → local ONNX (remote 不通或不可用)");
                    Provider::Local(Box::new(LocalProvider::new(cfg.local)))
                }
                // 7/16 BL-INTEL-DMG: x86_64 (Intel Mac dmg / Windows msi) 无 ort,
                // 连 LocalProvider 都没编进来 → 只能 Remote 兜底, is_ready() 返 false,
                // caller 走 no-op fallback (不筛全量注入)。
                // Windows 上这意味着**没有向量**, 不是"降级到本地"。
                #[cfg(not(target_arch = "aarch64"))]
                {
                    log::warn!(
                        "[embedding] auto → remote 不通或不可用, 且当前架构无本地 ONNX, embed 会返 None"
                    );
                    Provider::Remote(remote)
                }
            }
        }
    }
}

/// 把 reqwest 错误的**整条 source 链**摊平成一行.
///
/// # 为什么必须有这个 (8/14)
///
/// `log::warn!("...失败: {e}")` 打的是 Display, 而 reqwest 的 Display 只有最外层:
///
///     error sending request for url (http://127.0.0.1:8999/v1/embeddings)
///
/// 这一句**分不出来**是连不上、被代理劫了、还是超时 —— 而这三种的处置完全不同。
/// 今晚就卡在这里: 我先按"被代理劫了"改了一版 (那个 asymmetry 确实是 bug),
/// 但看时间戳才发现两次失败都正好是 10 秒 = 客户端超时, 也就是请求其实到了网关,
/// 是网关那头在等一台不可达的内网机。
///
/// util/http_client.rs 里那段注释早就写着"关键词是 tunnel error" —— 而那个词
/// 就在 source 链的第三层, 默认永远不会被打出来。
///
/// 判断依据不该靠猜, 也不该靠数时间戳。
fn err_chain(e: &(dyn std::error::Error + 'static)) -> String {
    let mut parts = vec![e.to_string()];
    let mut cur = e.source();
    while let Some(s) = cur {
        parts.push(s.to_string());
        cur = s.source();
    }
    parts.join(" ← ")
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
    let url = format!("{}/v1/catalog", remote.config.resolved_gateway_url());
    let probe_timeout = Duration::from_secs(remote.config.timeout_seconds.min(3));

    // ── 8/8: 整个 blocking client 的生死都挪进一条独立 OS 线程 ──────────────
    //
    // 上面那句「启动期用 (在 init_active_provider 内, sync context)」的前提**不成立**。
    // active_provider() 是 `ACTIVE_PROVIDER.get_or_init(init_active_provider)` —— 懒初始化,
    // 真正跑的时机是第一次有人用 embedding, 而 embed_text 是 `async fn`, 调用方
    // (advisor_relevance / wiki_embed) 都是异步 Tauri command。所以它实际跑在
    // tokio 的 worker 线程上。
    //
    // `reqwest::blocking::Client` 自己内部揣着一个 tokio runtime, 而**在异步上下文里
    // drop 一个 runtime 是 panic**:
    //
    //   thread 'tokio-rt-worker' panicked at tokio/runtime/blocking/shutdown.rs:51
    //   Cannot drop a runtime in a context where blocking is not allowed.
    //   This happens when a runtime is dropped from within an asynchronous context.
    //
    // 后果比 panic 本身严重得多: Tauri command 里 panic → invoke 的 promise
    // **永远不 resolve**。8/8 鸿波实盘: 早安页 advisor 在发 fetch 之前先跑
    // applyRelevanceFilter → embed → 卡死在这里, 十分钟后客户端超时, 而
    // hermes 的 agent.log 里连一条 advisor 请求都没有 —— 因为请求压根没发出去。
    // 排查时看起来像"模型慢 / hermes 挂了", 实际两者都是好的。
    //
    // 而且 OnceLock 的 init 闭包 panic 之后 cell 仍是未初始化的, 下次调用**重跑重panic**,
    // 所以这不是"偶发一次", 是每次都挂。
    //
    // 修法: 让 client 在一条普通 OS 线程上创建、使用、析构 —— 那里没有 tokio
    // runtime 在跑, drop 合法。join 会阻塞调用方最多 probe_timeout (≤3s),
    // 跟原来 blocking send 的阻塞时长一致, 没有新增等待。
    std::thread::spawn(move || {
        // 8/9: 原来这里是裸 `Client::builder()`, **没走 trust_central_blocking**。
        //
        // 后果是这条探测不吃中央服务的证书/代理策略:
        //   · 自签证书环境 (CATFISH_ALLOW_SELF_SIGNED=1) → 握手失败
        //   · 没有 no_proxy → 系统代理开着时它走代理, 而其余所有中央调用都是绕过的
        // 而失败只表现为下面那个 matches! 返 false → **静默退回 fallback provider**。
        // 没日志没报错, 员工只会觉得"语义搜索不准"。
        //
        // trust_central_blocking 本来就是给这里写的 (它的注释原话: "blocking 版
        // (embedding / role_config 用的是 blocking client)"), 只是一直没接上 ——
        // 8/9 删 role_config 之后它变成 0 caller, cargo 报 dead_code 才暴露出来。
        let client = match crate::util::http_client::trust_central_blocking(
            reqwest::blocking::Client::builder().timeout(probe_timeout),
        )
        .build()
        {
            Ok(c) => c,
            Err(_) => return false,
        };
        // /v1/catalog 不需 auth, 不再带 bearer_auth.
        matches!(client.get(&url).send(), Ok(r) if r.status().is_success())
    })
    .join()
    // 线程自己 panic 了也只当"探测失败"往下走 fallback, 不把 panic 传回异步上下文。
    .unwrap_or(false)
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


/// 没就绪时的**具体原因**; 就绪返 None. 给界面直接显示用.
pub fn provider_not_ready_reason() -> Option<String> {
    active_provider().not_ready_reason()
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

/// 反序列化 little-endian f32 BLOB. 长度跟 `expected_dim` 不符返 None,
/// caller 拿到 None 视为 cache miss → 重 embed.
///
/// 8/14: 维度改成**参数**。原来它自己去读 `embed_dim()`, 而那个值来自员工机
/// yaml 里写死的 1024 —— 一个中央模型的属性写在员工机上。现在由调用方从
/// 各自缓存库的 `_meta` 里取 (见 services/embed_cache_meta.rs), 那里存的是
/// **实际观测到**的维度。
pub fn vector_from_blob(blob: &[u8], expected_dim: usize) -> Option<Vec<f32>> {
    let dim = expected_dim;
    if dim == 0 || blob.len() != dim * 4 {
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
// 7/16 BL-INTEL-DMG: LocalProvider 用 ort::Session + tokenizers::Tokenizer, 只 aarch64
// 编. Intel Mac dmg / Windows msi 上整个 struct + impl 不存在, 前面 Provider enum 里
// Local variant 也 cfg-guard, 保证 x86_64 build 通.

#[cfg(target_arch = "aarch64")]
struct LocalProvider {
    config: LocalConfig,
    session: OnceLock<Option<Mutex<Session>>>,
    tokenizer: OnceLock<Option<Tokenizer>>,
}

#[cfg(target_arch = "aarch64")]
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

    /// 卡在哪一步. **先报文件不在**, 因为那是绝大多数情况且一句话能修好.
    fn not_ready_reason(&self) -> String {
        let onnx = PathBuf::from(expand_home(&self.config.model_path));
        let tok = PathBuf::from(expand_home(&self.config.tokenizer_path));
        let missing: Vec<String> = [(&onnx, "ONNX 模型"), (&tok, "tokenizer.json")]
            .iter()
            .filter(|(p, _)| !p.exists())
            .map(|(p, what)| format!("{what} 不在: {}", p.display()))
            .collect();
        if !missing.is_empty() {
            return format!(
                "本机向量模型没装齐 —— {}\n\n下载:\n  \
                 mkdir -p ~/.catfish/models\n  \
                 curl -L -o ~/.catfish/models/bge-m3.onnx \
                 https://huggingface.co/Xenova/bge-m3/resolve/main/onnx/model_quantized.onnx\n  \
                 curl -L -o ~/.catfish/models/tokenizer.json \
                 https://huggingface.co/Xenova/bge-m3/resolve/main/tokenizer.json",
                missing.join("; ")
            );
        }
        format!(
            "本机向量模型文件都在 ({}), 但加载失败 —— 多半是文件损坏或架构不符, \
             详情看 Companion 日志里 [embedding/local] 那几行。",
            onnx.display()
        )
    }

    async fn embed_text(&self, text: &str) -> Option<Vec<f32>> {
        // 注: ONNX 推理 sync 长跑 (10-50ms). 主线程跑可接受 (P3.5.4 实测 OK).
        //     真 spawn_blocking 跨线程要 Arc<Session>, 复杂度 / 收益不平衡, 保持当前.
        let session_mutex = self.init_session()?;
        let tokenizer = self.init_tokenizer()?;
        // dim 不再从 config 拿 —— 见下面 ONNX 输出 shape 那段
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
        // 8/14: shape 原来被 `let (_, ...)` 丢掉了, 维度改从 yaml 读 —— 而
        // hidden size 就明明白白在 shape 的最后一维上。
        // ONNX last_hidden_state 是 [batch, seq, hidden], 数出来比配置准, 也不用配。
        let (shape, output_data) = outputs[0].try_extract_tensor::<f32>().ok()?;
        let dim = match shape.last().copied().filter(|d| *d > 0) {
            Some(d) => d as usize,
            None => {
                log::warn!(
                    "[embedding/local] ONNX 输出 shape={shape:?} 取不出 hidden 维, 放弃这条"
                );
                return None;
            }
        };
        if output_data.len() < dim || output_data.len() % dim != 0 {
            log::warn!(
                "[embedding/local] ONNX 输出长度 {} 跟 shape {shape:?} 对不上 (hidden={dim})",
                output_data.len()
            );
            return None;
        }
        record_identity(EmbedIdentity {
            dim,
            // 本机这条路的"模型身份"就是那个文件 —— 换了文件就该重建缓存
            model: Some(format!("local:{}", self.config.model_path)),
        });
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
        // ★ 8/14 现场: 探测说"gateway 通", 真发请求却
        //   `error sending request for url (http://127.0.0.1:8999/v1/embeddings)`。
        //
        // 因为**探测和真请求用的是两个不同的 client**:
        //   · probe_remote_alive 走 trust_central_blocking (8/9 修过) → 绕代理 → 通
        //   · 这里是裸 Client::builder() → reqwest 默认读系统代理和 HTTP(S)_PROXY
        //
        // 鸿波机器上系统代理指向 127.0.0.1:7890 (Clash), 而 Clash 没开 —— 网关就在
        // 本机 8999, 直连一步就到, 却被塞进一条不存在的隧道。
        //
        // 这个坑 P3.5.80 (7/28 达华现场) 查了一整轮, trust_central 的注释里连报错
        // 长什么样都写下来了。8/9 又因为清死代码发现"探测一直没接上"修了探测那一半
        // —— 唯独真正发请求的这一半留到了今天。
        //
        // 检查和真事用不同的 client, 本身就是个 bug 生成器: 检查永远比真事宽松。
        let builder = crate::util::http_client::trust_central(
            reqwest::Client::builder().timeout(Duration::from_secs(config.timeout_seconds)),
        );
        let client = builder.build().unwrap_or_else(|e| {
            // 兜底的 Client::new() **不绕代理** —— 真走到这里等于回到上面那个 bug,
            // 所以必须留声, 不能静默。
            log::warn!(
                "[embedding/remote] 带证书/绕代理的 client 构建失败 ({e}), \
                 退回默认 client —— 系统代理开着的话请求可能连不出去"
            );
            reqwest::Client::new()
        });
        Self { config, client }
    }

    /// is_ready: remote 不 lazy load model, 只要 auth token 拿得到就算 ready.
    /// 真连通靠每次 embed_text 时的 HTTP, 失败 caller 拿 None 走 fallback.
    fn is_ready(&self) -> bool {
        !auth_token().is_empty()
    }

    fn not_ready_reason(&self) -> String {
        format!(
            "远程向量模型不可用 —— 环境变量 CATFISH_INTERNAL_DEV_TOKEN 没配, \
             Companion 拿不到调网关的凭据。\n\n\
             网关地址: {}\n\
             (注: 这跟本机 ONNX 模型无关, 那条路没被走到。想强制走本机, \
             把 ~/.catfish/embedding.yaml 的 backend 改成 local 再重启。)",
            self.config.resolved_gateway_url()
        )
    }

    async fn embed_text(&self, text: &str) -> Option<Vec<f32>> {
        if text.is_empty() {
            return None;
        }
        let url = format!("{}/v1/embeddings", self.config.resolved_gateway_url());
        // **不带 model** —— 网关按 roles.yaml 的 embedding 角色解析
        // (app.py 的 /v1/embeddings)。
        //
        // 不放 null 而是整个不放: 那边判的是 `body.get("model")`, null 和缺省
        // 等价没错, 但显式写 null 会让人以为"客户端是有主张的" ——
        // 而这里的主张恰恰是"我不该有主张"。
        let body = serde_json::json!({ "input": text });

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
            .map_err(|e| {
                log::warn!(
                    "[embedding/remote] POST {url} 失败: {}{}",
                    err_chain(&e),
                    if e.is_timeout() {
                        format!(
                            " · 这是**客户端超时** ({}s) —— 请求多半已经到了网关, \
                             是上游在等。去看网关日志, 别在这边找网络问题。",
                            self.config.timeout_seconds
                        )
                    } else if e.is_connect() {
                        " · 连不上 —— 网关没起? 或者被系统代理劫了 (见 util/http_client.rs)".to_string()
                    } else {
                        String::new()
                    }
                )
            })
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
            .map_err(|e| log::warn!("[embedding/remote] 解析 response JSON 失败: {}", err_chain(&e)))
            .ok()?;

        let model_id = parsed.model.clone();
        let first = parsed.data.into_iter().next()?;

        // 8/14: 原来这里是 `if len != yaml.remote.embed_dim { return None }` ——
        // 中央换成 768 维的模型, 员工端就把每一次拿回来的向量都丢掉, 只留一行
        // warn。表现是"向量整个不能用", 而根因只是员工机 yaml 里那个 1024 没跟着改
        // (他也无从知道中央换了)。
        //
        // 维度是**中央模型的属性**, 不是员工能配的东西。现在数出来就是了。
        if first.embedding.is_empty() {
            log::warn!("[embedding/remote] 网关返回了空向量");
            return None;
        }
        record_identity(EmbedIdentity {
            dim: first.embedding.len(),
            model: model_id,
        });

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
    /// OpenAI 兼容响应里的模型名 (网关透传 litellm 的). 拿它当"这批向量是谁产的"
    /// 的身份 —— 中央换模型时缓存要靠它失效。
    ///
    /// Option: 上游不返也不能让整个响应解析失败。拿不到就不参与失效判断。
    #[serde(default)]
    model: Option<String>,
}

#[derive(Debug, Deserialize)]
struct EmbeddingApiDatum {
    embedding: Vec<f32>,
}

// P3.5.15 (6/16 鸿波 cargo check 通过后清理): 老 P3.5.4 init_session / init_tokenizer
// 两个 pub fn 已删. caller 全部迁到 provider_not_ready_reason() / embed_text() — 看 provider
// (Local + Remote) 是否就绪不再绑死本机 ONNX 文件存在. 删后 cargo check 0 warning.
