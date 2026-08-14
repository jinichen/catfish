//! 远程向量 provider —— 打 catfish-gateway 的 /v1/embeddings.
//!
//! 8/14 从 services/embedding.rs 拆出来, 同 embedding_local.rs 的理由 (800 行红线)。
//!
//! 这条路**不传模型名**: 网关按中央 roles.yaml 的 embedding 角色自己解析。
//! 维度也不配, 从响应里数出来。两件事的来龙去脉见 embedding_config.rs 顶部。

use std::time::Duration;

use serde::Deserialize;

use crate::services::embed_cache_meta::{EmbedIdentity, record_identity};
use crate::services::embedding_config::RemoteConfig;

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
pub(crate) fn probe_remote_alive(remote: &RemoteProvider) -> bool {
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

// ─── Remote API provider — catfish-gateway /v1/embeddings ────────────

pub(crate) struct RemoteProvider {
    config: RemoteConfig,
    client: reqwest::Client,
}

impl RemoteProvider {
    pub(crate) fn new(config: RemoteConfig) -> Self {
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
    pub(crate) fn is_ready(&self) -> bool {
        !auth_token().is_empty()
    }

    pub(crate) fn not_ready_reason(&self) -> String {
        format!(
            "远程向量模型不可用 —— 环境变量 CATFISH_INTERNAL_DEV_TOKEN 没配, \
             Companion 拿不到调网关的凭据。\n\n\
             网关地址: {}\n\
             (注: 这跟本机 ONNX 模型无关, 那条路没被走到。想强制走本机, \
             把 ~/.catfish/embedding.yaml 的 backend 改成 local 再重启。)",
            self.config.resolved_gateway_url()
        )
    }

    pub(crate) async fn embed_text(&self, text: &str) -> Option<Vec<f32>> {
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

