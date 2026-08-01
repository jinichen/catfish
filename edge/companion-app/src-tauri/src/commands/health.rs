//! 直接调 gateway HTTP 的命令（不是探进程，是探"业务面活着"）。
//!
//! 给前端的仪表盘 / 控制台用 —— "进程在跑" ≠ "服务能用"，
//! 比如 gateway 进程活着但 .env 没加载、dev token 错配，/healthz 仍可能 200。

use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::services::{endpoints, hermes_api_config};

const HTTP_TIMEOUT: Duration = Duration::from_secs(2);

/// BL-AUTH-DECOUPLE-A5 Phase 2 (5/19): 返 Companion 真"后端 API 入口" base URL.
/// hermes_api.enabled + has_key 时 = hermes 8642 (hermes Phase 1 proxy 转 gateway),
/// 否则 = gateway 8999 (灰度回退). 跟前端 config.backendUrl 同语义.
fn backend_base() -> String {
    let h = hermes_api_config::hermes_api_config();
    if h.enabled && h.key.is_some() {
        return h.url.trim_end_matches('/').to_string();
    }
    endpoints::endpoints().gateway_base()
}

/// BL-AUTH-DECOUPLE-A5 Phase 2 closure (5/19): 算这次请求该带的 Authorization header.
///
/// - 走 hermes proxy (enabled + has_key) → `Some("Bearer <API_SERVER_KEY>")`,
///   因为 hermes `_handle_companion_proxy` 一律 require API_SERVER_KEY, 不区分
///   哪个 path (即使 gateway 端 `/v1/catalog` 是匿名公开的, hermes 这层入口
///   永远要 key — proxy 本身的入口鉴权, 跟下游业务鉴权是两件事).
/// - 走老 gateway 直连 (灰度回退) → `None`, gateway `/v1/catalog` 接受匿名.
///
/// `health.rs` 的 `healthz` / `catalog` 调用都该带这个, 不带会被 hermes 401.
/// 这是 A5 Phase 2 部署后 model selector 退化到 raw model ID 的根因.
fn backend_auth_header() -> Option<String> {
    let h = hermes_api_config::hermes_api_config();
    if h.enabled {
        h.key.as_ref().map(|k| format!("Bearer {}", k))
    } else {
        None
    }
}

/// 既要 Deserialize（reqwest 解 gateway 返回）也要 Serialize（送给前端）
#[derive(Debug, Serialize, Deserialize)]
pub struct HealthzResp {
    pub status: String,
    pub service: String,
}

/// GET /healthz —— 标准健康检查
#[tauri::command]
pub async fn healthz() -> Result<HealthzResp, String> {
    let client = build_client()?;
    let base = backend_base();
    let mut req = client.get(format!("{base}/healthz"));
    if let Some(auth) = backend_auth_header() {
        req = req.header("Authorization", auth);
    }
    let resp = req.send().await.map_err(|e| format!("连接失败：{e}"))?;

    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status()));
    }

    resp.json::<HealthzResp>()
        .await
        .map_err(|e| format!("解析失败：{e}"))
}

/// GET /v1/catalog —— 公开的模型清单（匿名也能调）.
///
/// 注意: 虽然 gateway 端 `/v1/catalog` 接受匿名 (`get_current_user_optional`),
/// 但走 hermes proxy (8642) 时一律要带 API_SERVER_KEY — hermes 这一跳的入口
/// 鉴权独立于下游业务鉴权 (proxy `_check_auth` 不区分 path).
///
/// 不带 Authorization → hermes 401 → catalog `null` → ChatTab 落回 raw
/// "catfish-private-main" 字符串, 丢掉 friendly display_name 和 tier 描述.
/// 这是 BL-AUTH-DECOUPLE-A5 Phase 2 部署后 model selector 退化的根因.
#[tauri::command]
pub async fn catalog() -> Result<Value, String> {
    // 6/1 BL-CATALOG-DIRECT-GATEWAY (鸿波"模型列表又出不来了"):
    // 原走 backend_base() = hermes 8642 (BL-AUTH-DECOUPLE-A5 5/19 设计走 P7
    // catch-all proxy 透传 gateway). 但 hermes 0.15.1 升级后 P7 middleware
    // Application.__init__ 注入时机**晚于 Application 创建** — Application 是
    // 主线程 import gateway.platforms.api_server 时就实例化, plugin install 后台
    // 0.2s 跑时 Application 已创建完, _patched_app_init 来不及注入 mw.
    // → hermes 8642 /v1/catalog 真返 404 (无 P7 catch), 客户端退化到默认硬编码
    // catfish-private-main 单 model.
    //
    // 真修要重构 P7 让 Application.__init__ patch 在 register 时立刻跑 (无 hermes
    // 模块依赖, 可立刻 monkey-patch). 但这是 architectural 改造, 留周一.
    // 临时绕开: catalog 直接走 gateway 8999 (catalog 是 catfish 概念, hermes 没,
    // gateway /v1/catalog 公开匿名 OK). 1 行修立刻见效.
    let client = build_client()?;
    let base = endpoints::endpoints().gateway_base();
    let req = client.get(format!("{base}/v1/catalog"));
    // Codex 是本机模型源，不应被中央 gateway 的短暂不可达拖累。
    // gateway 失败时仍返回一个可用 catalog，只是附上 gateway_error。
    let mut catalog = match req.send().await {
        Ok(resp) if resp.status().is_success() => resp
            .json::<Value>()
            .await
            .unwrap_or_else(|e| empty_catalog(format!("解析失败：{e}"))),
        Ok(resp) => empty_catalog(format!("gateway HTTP {}", resp.status())),
        Err(e) => empty_catalog(format!("gateway 连接失败：{e}")),
    };

    // 中央 gateway 不知道员工本机的 Codex 登录与模型。在
    // Companion catalog 层合并，让聊天 picker 直接把两类模型分组展示。
    if let Some(models) = catalog.get_mut("models").and_then(Value::as_array_mut) {
        let existing: std::collections::HashSet<String> = models
            .iter()
            .filter_map(|item| item.get("id").and_then(Value::as_str).map(str::to_string))
            .collect();
        let codex_models =
            tokio::task::spawn_blocking(crate::commands::codex_backend::catalog_models)
                .await
                .map_err(|e| format!("Codex catalog 失败: {e}"))?;
        models.extend(codex_models.into_iter().filter(|item| {
            item.get("id")
                .and_then(Value::as_str)
                .map(|id| !existing.contains(id))
                .unwrap_or(false)
        }));
    }
    Ok(catalog)
}

fn empty_catalog(error: String) -> Value {
    serde_json::json!({
        "authenticated": false,
        "models": [],
        "default": null,
        "your_dept_default": null,
        "gateway_error": error,
    })
}

fn build_client() -> Result<reqwest::Client, String> {
    // P3.5.80 (7/28): 中央端可能是自签 HTTPS, 挂上 ~/.catfish/server-ca.pem 的信任.
    crate::util::http_client::trust_central(
        reqwest::Client::builder()
            .timeout(HTTP_TIMEOUT)
            // 关键：localhost 永远不走代理，避免员工设了 HTTPS_PROXY 把 127.0.0.1 也劫了
            .no_proxy(),
    )
    .build()
    .map_err(|e| format!("HTTP client 构造失败：{e}"))
}

#[cfg(test)]
mod tests {
    //! BL-AUTH-DECOUPLE-A5 Phase 2 闭口回归 (5/19).
    //!
    //! 防回归: model selector 退化到 raw `catfish-private-main` 是因为
    //! `catalog()` / `healthz()` 不带 Authorization 走 hermes proxy → 401.
    //! 这组测试锁死: hermes_api 开启时 `backend_auth_header()` 必须返
    //! Some("Bearer <key>"), 关闭时 None.
    //!
    //! 注意: `hermes_api_config()` 用 `OnceLock` 启动时算一次, 单进程跑测
    //! 共享一个全局实例 — 没法直接覆盖. 这里只测 `build()` (private 但
    //! 通过 backend_auth_header 间接走). 为了不污染共享状态, 我们改测
    //! 一个等价的 pure-fn 副本逻辑, 跟生产代码 lock-step.
    //!
    //! 真正端到端的覆盖在 hermes 侧 `tests/gateway/test_api_server_companion_proxy.py`
    //! 的 `test_proxy_rejects_without_api_key` (确认没 key 401) + 这里的
    //! "带 key 时拼 Authorization" 加起来形成闭环.
    use crate::services::hermes_api_config::HermesApiConfig;

    /// 跟 `super::backend_auth_header` 同算法的 pure 版本, 接受任意 cfg
    /// 避免触发 OnceLock 全局.
    fn compute_auth_header(cfg: &HermesApiConfig) -> Option<String> {
        if cfg.enabled {
            cfg.key.as_ref().map(|k| format!("Bearer {}", k))
        } else {
            None
        }
    }

    #[test]
    fn returns_bearer_when_hermes_enabled_with_key() {
        // 生产路径: companion.yaml hermes_api.enabled=true + key=<api_server_key>
        // → catalog() / healthz() 必须带 Authorization, 否则 hermes 401.
        let cfg = HermesApiConfig {
            enabled: true,
            url: "http://localhost:8642".to_string(),
            key: Some("test-api-server-key".to_string()),
        };
        assert_eq!(
            compute_auth_header(&cfg).as_deref(),
            Some("Bearer test-api-server-key"),
            "hermes_api 启用+有 key 时必须输出 Bearer header, 不然 model selector \
             会退化到 raw model ID (A5 Phase 2 回归)"
        );
    }

    #[test]
    fn returns_none_when_hermes_disabled() {
        // 灰度回退路径: hermes_api 关闭 → 走 gateway 8999 直连, gateway
        // /v1/catalog 接受匿名, 不该塞 Authorization (gateway 看到非法
        // token 会拒, 而我们这里压根没 token 业务概念).
        let cfg = HermesApiConfig {
            enabled: false,
            url: "http://localhost:8642".to_string(),
            key: Some("present-but-disabled".to_string()),
        };
        assert!(
            compute_auth_header(&cfg).is_none(),
            "hermes_api.enabled=false 时不该带 Authorization"
        );
    }

    #[test]
    fn returns_none_when_hermes_enabled_but_no_key() {
        // 不该发生 (hermes_api_config::build 会 force enabled=false), 但
        // 防御性测一遍: 没 key 就没法拼 Bearer.
        let cfg = HermesApiConfig {
            enabled: true,
            url: "http://localhost:8642".to_string(),
            key: None,
        };
        assert!(
            compute_auth_header(&cfg).is_none(),
            "enabled=true 但 key 缺失时不该 panic 也不该拼半截 header"
        );
    }
}
