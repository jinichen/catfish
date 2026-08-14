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
//! 3. **Auto** — 启动时 ping remote + 看 token, 都过才 remote, 否则 local.
//!    8/14 起**运行时也会切**: 远程连续失败到阈值 → 整体退回本机 ONNX
//!    (到重启为止)。启动探测只看得到"网关活没活", 看不到网关背后的上游 ——
//!    今晚现场就是网关活着而内网 10.10.40.102 不可达。
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
use std::sync::OnceLock;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use crate::services::embedding_config::{Backend, EmbeddingConfig, load_config};

// 观测到的向量身份 (dim + 模型) 挪到了 services/embed_cache_meta.rs ——
// 它跟"缓存该不该重建"是同一件事, 放一起才好写测试 (那边只依赖 std + rusqlite,
// 不像这个文件拖着 ort / reqwest / tokenizers, 在没有 GTK 的机器上编不了)。
// 两个 provider 各自一个文件 (8/14 拆的, 见各自文件头)
#[cfg(target_arch = "aarch64")]
use crate::services::embedding_local::LocalProvider;
use crate::services::embedding_remote::{RemoteProvider, probe_remote_alive};

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

    /// 是不是走远程. 只有远程才谈得上"失败了退回本地".
    fn is_remote(&self) -> bool {
        matches!(self, Provider::Remote(_))
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
// ─── 远程连续失败 → 整体退回本地 (8/14) ────────────────────
//
// 鸿波两次问「不会切？」。原来的行为是: auto 在**启动那一刻**探一次, 定下来就
// 到重启为止不动。于是"启动时网关是通的, 之后上游挂了"这种情况下, 每一次
// embed 都要等满 10 秒超时, 而本机那个 569MB 的模型就在旁边闲着。
//
// 今晚现场正是这样: 网关活着 (ping 通、token 有), 但网关背后的内网机
// 10.10.40.102 不可达 —— 启动探测**探不到**这一层。
//
// ## 为什么现在能做了
//
// 我今晚早些时候拒绝过这件事, 理由是"中途换 provider 会让两个来源的向量混进
// 同一个缓存, 而 _meta 只比维度、比不出来"。那个顾虑已经不成立了:
// embed_cache_meta 现在按 (维度, 模型标识) 对账, 切过去会检测到
// model 从 "catfish-private-embed" 变成 "local:~/.catfish/models/bge-m3.onnx",
// 自动清缓存重建。
//
// ## 为什么是"整体切"而不是"每次失败退一次"
//
// 逐请求回退的话, 每条候选都要先等满 10 秒超时再走本地 —— 一次早安几十条
// 就是几分钟。整体切只赔前 N 次。
//
// ## 阈值为什么是 3
//
// 1 次太敏感: 网络抖一下就切, 而切一次要清缓存重算几百条。
// 太大又要让员工干等好几轮 10 秒 (早安一轮只产生 2 次失败:
// 相关性筛选 1 次 + wiki 注入 1 次)。3 = 第二轮早安就切过去。
//
// 成功一次就清零 —— 判据是"连续", 不是"累计"。累计的话跑够久总会切。
const REMOTE_FAILURES_BEFORE_DEMOTE: usize = 3;

static CONSECUTIVE_REMOTE_FAILURES: AtomicUsize = AtomicUsize::new(0);
static DEMOTED_TO_LOCAL: AtomicBool = AtomicBool::new(false);
static FALLBACK_PROVIDER: OnceLock<Option<Provider>> = OnceLock::new();

/// 已经退回本地了吗.
fn demoted() -> bool {
    DEMOTED_TO_LOCAL.load(Ordering::Relaxed)
}

/// 记一次远程失败, 返回"这次是否达到降级阈值".
fn note_remote_failure() -> bool {
    let n = CONSECUTIVE_REMOTE_FAILURES.fetch_add(1, Ordering::Relaxed) + 1;
    n >= REMOTE_FAILURES_BEFORE_DEMOTE
}

fn note_remote_success() {
    CONSECUTIVE_REMOTE_FAILURES.store(0, Ordering::Relaxed);
}

/// 降级目标. 只有"主 provider 是远程 + 这个架构有本地 ONNX"才存在.
///
/// x86_64 (Intel Mac dmg / Windows msi) 上 LocalProvider 整个没编进来,
/// 所以那边是 None —— 远程挂了就是没有向量, 不是降级。这一点在日志里要说清楚,
/// 否则 Windows 上会以为"它会自己退回本地"。
fn fallback_provider() -> Option<&'static Provider> {
    FALLBACK_PROVIDER
        .get_or_init(|| {
            #[cfg(target_arch = "aarch64")]
            {
                let cfg = load_config();
                Some(Provider::Local(Box::new(LocalProvider::new(cfg.local))))
            }
            #[cfg(not(target_arch = "aarch64"))]
            {
                None
            }
        })
        .as_ref()
}

fn active_provider() -> &'static Provider {
    ACTIVE_PROVIDER.get_or_init(init_active_provider)
}

// ─── Public API (跟 P3.5.4 接口兼容, embed_text 加 async) ────────────

/// embed_text — text → L2-normalized f32 vector.
/// 失败返 None, caller 自己降级 (advisor 不筛全量注入 / wiki 返空).
///
/// 远程连续失败到阈值会**整体退回本地**, 见上面那段。
pub async fn embed_text(text: &str) -> Option<Vec<f32>> {
    if demoted() {
        // 已经切过去了, 不再碰远程
        return fallback_provider()?.embed_text(text).await;
    }

    let primary = active_provider();
    let out = primary.embed_text(text).await;

    if !primary.is_remote() {
        // 本机 ONNX 失败是另一回事 (文件坏 / 内存不够), 退无可退
        return out;
    }

    match out {
        Some(v) => {
            note_remote_success();
            Some(v)
        }
        None => {
            if !note_remote_failure() {
                return None;
            }
            match fallback_provider() {
                Some(fb) => {
                    // compare_exchange 而不是裸 store: 并发下只让第一个到达阈值的
                    // 打这条日志, 否则三路并发的早安会刷三条一样的。
                    if DEMOTED_TO_LOCAL
                        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                        .is_ok()
                    {
                        log::warn!(
                            "[embedding] 远程连续失败 {REMOTE_FAILURES_BEFORE_DEMOTE} 次 → \
                             整体退回本机 ONNX (到重启为止)。\
                             向量来源变了, 缓存会在下一次写入前自动重建 \
                             (见 services/embed_cache_meta.rs)。"
                        );
                    }
                    // 这一次立刻用兜底重算, 不让触发降级的那次调用白白失败
                    fb.embed_text(text).await
                }
                None => {
                    log::warn!(
                        "[embedding] 远程连续失败 {REMOTE_FAILURES_BEFORE_DEMOTE} 次, \
                         但当前架构没有本地 ONNX (x86_64 不编 ort) —— \
                         **没有降级目标, 向量功能不可用**, 不是'会自己退回本地'。"
                    );
                    None
                }
            }
        }
    }
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


#[cfg(test)]
mod demote_tests {
    //! 降级的**计数语义**. 逻辑很短但错一个字后果两极:
    //!   · 阈值算成"累计"而不是"连续" → 跑够久必然切到本地, 而远程明明是好的
    //!   · 成功不清零 → 同上
    //!   · 阈值算错一位 → 员工多等一整轮 10 秒超时
    //!
    //! 这里只测计数器 (纯 std)。真正的切换要连着 provider 一起看, 那部分靠
    //! 实机日志: "远程连续失败 3 次 → 整体退回本机 ONNX"。

    use super::*;

    fn reset() {
        CONSECUTIVE_REMOTE_FAILURES.store(0, Ordering::Relaxed);
        DEMOTED_TO_LOCAL.store(false, Ordering::Relaxed);
    }

    #[test]
    fn 连续失败到阈值才返回_true() {
        reset();
        for i in 1..REMOTE_FAILURES_BEFORE_DEMOTE {
            assert!(!note_remote_failure(), "第 {i} 次就想切, 太敏感了");
        }
        assert!(note_remote_failure(), "到阈值必须切");
    }

    #[test]
    fn 中间成功一次就清零() {
        // ★★★ 判据是"连续"不是"累计"。累计的话跑够久总会切到本地,
        // 而远程一直是好的 —— 那是纯粹的性能损失 + 一次无谓的缓存重建。
        reset();
        note_remote_failure();
        note_remote_failure();
        note_remote_success();
        for i in 1..REMOTE_FAILURES_BEFORE_DEMOTE {
            assert!(!note_remote_failure(), "清零之后第 {i} 次不该切");
        }
        assert!(note_remote_failure());
    }

    #[test]
    fn 阈值大于一() {
        // ★★ 1 次就切太敏感: 网络抖一下就换 provider, 而换一次要清缓存
        // 重算几百条。这条钉的是"别哪天顺手改成 1"。
        assert!(REMOTE_FAILURES_BEFORE_DEMOTE > 1);
    }

    #[test]
    fn demoted_默认是假的() {
        reset();
        assert!(!demoted(), "没失败过就不该是降级态");
    }
}
