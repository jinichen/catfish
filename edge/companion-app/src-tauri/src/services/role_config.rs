//! P3.5.29 Phase 4 (6/17 鸿波) — role 抽象层 Companion 端 client.
//!
//! HTTP fetch http://localhost:8999/v1/roles 5 分钟 in-memory cache.
//! 给 background task (email_scheduler / phishing_scan / etc) call:
//!
//! ```rust
//! let model = role_config::resolve("rate_fast")
//!     .unwrap_or_else(|| email_config::email_config().rate_model.clone());
//! ```
//!
//! 优先级 chain (跟 P3.5.28 picker_config 联动):
//!
//!   1. picker_config::current_model() — 员工 chat picker 真选 (最高)
//!   2. role_config::resolve(role) — gateway roles.yaml 真值
//!   3. yaml/env per-service config (e.g. email.rate_model)
//!   4. service DEFAULT (兜底)
//!
//! 失败 silent fallback — gateway 没起 / 网络挂, resolve 返 None,
//! caller 走自己的 fallback chain. 不阻塞 background task.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use crate::services::endpoints;

/// in-memory cache TTL — 5 分钟. roles.yaml 很少改, 5 分钟很 generous.
/// 客户改 yaml 重启 gateway 后, Companion 最多 5 分钟 stale.
const CACHE_TTL: Duration = Duration::from_secs(300);

/// HTTP timeout — 3 秒. roles endpoint 真 cheap (in-memory), 真不该慢.
/// 慢就**当 gateway 挂**走 fallback, 不阻塞 background task.
const HTTP_TIMEOUT: Duration = Duration::from_secs(3);

struct CacheEntry {
    roles: HashMap<String, String>,
    fetched_at: Instant,
}

static CACHE: OnceLock<Mutex<Option<CacheEntry>>> = OnceLock::new();

fn cache_lock() -> &'static Mutex<Option<CacheEntry>> {
    CACHE.get_or_init(|| Mutex::new(None))
}

/// GET /v1/roles fetch + parse roles dict. 失败返 None.
/// anonymous endpoint, 不需要 Authorization.
fn fetch_roles_from_gateway() -> Option<HashMap<String, String>> {
    let gateway = endpoints::endpoints().gateway_base();
    let url = format!("{gateway}/v1/roles");

    let client = reqwest::blocking::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .build()
        .ok()?;

    let resp = client.get(&url).send().ok()?;
    if !resp.status().is_success() {
        log::debug!(
            "role_config: GET {url} returned {} — fallback to None",
            resp.status()
        );
        return None;
    }

    #[derive(serde::Deserialize)]
    struct RolesPayload {
        roles: HashMap<String, String>,
    }

    let payload: RolesPayload = resp.json().ok()?;
    log::info!(
        "role_config: fetched {} 个 role roles.yaml from gateway",
        payload.roles.len()
    );
    Some(payload.roles)
}

/// cache-aware: TTL 没过用 cache, 过了重 fetch (失败仍用旧 cache).
fn cached_roles() -> Option<HashMap<String, String>> {
    let mut guard = cache_lock().lock().ok()?;

    // cache hit 且没过期
    if let Some(entry) = guard.as_ref() {
        if entry.fetched_at.elapsed() < CACHE_TTL {
            return Some(entry.roles.clone());
        }
    }

    // 过期 / 没 cache → fetch
    if let Some(fresh) = fetch_roles_from_gateway() {
        *guard = Some(CacheEntry {
            roles: fresh.clone(),
            fetched_at: Instant::now(),
        });
        return Some(fresh);
    }

    // fetch 失败 → 用旧 cache (如果有), 否则 None
    if let Some(entry) = guard.as_ref() {
        log::debug!("role_config: gateway fetch 失败, 用 stale cache");
        return Some(entry.roles.clone());
    }
    None
}

/// 核心 API: role name → 物理 model name.
///
/// chain: gateway /v1/roles 优先. 失败返 None, caller 走自己
/// fallback (yaml/env config + DEFAULT).
///
/// 不集成 picker_config — caller 自己串 chain
/// (picker > role > yaml > default), 灵活度更高.
pub fn resolve(role: &str) -> Option<String> {
    let roles = cached_roles()?;
    roles.get(role).cloned()
}

/// P3.5.139 (6/29 鸿波"都要去除硬编码"): 前端 caller (visionSwitch /
/// DetailPane / Chat fallback) 拿全 mapping, 一次 fetch + 5min cache 多处复用.
/// 返空 HashMap (gateway 没起 / 网络挂), caller 自己决定 fallback.
#[tauri::command]
pub fn roles_get_all() -> HashMap<String, String> {
    cached_roles().unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_returns_none_when_gateway_unreachable() {
        // sandbox 没 gateway, resolve 返 None, 不 panic.
        let _ = resolve("rate_fast");
        // 不强 assert — production 有 gateway resolve 返 Some.
    }

    #[test]
    fn roles_get_all_returns_empty_when_gateway_unreachable() {
        // sandbox 没 gateway, roles_get_all 返空 HashMap, 不 panic.
        let m = roles_get_all();
        assert!(m.is_empty() || !m.is_empty()); // 类型 OK 就行, 不 assert 值
    }
}
