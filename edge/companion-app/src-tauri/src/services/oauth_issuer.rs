//! 中央地址变更检测 (10/9) —— 已登录的令牌是不是"旧中央"签的。
//!
//! # 为什么
//!
//! 10/8 Windows 现场: 中央重新配置, 身份服务的签发者从 `https://192.168.205.38`
//! 改成了 `https://127.0.0.1`, 网关跟着改。Companion 手里的员工令牌还是旧签发者签的,
//! 有效期还剩 44 分钟 —— 网关按签发者逐字验签, 一律 `401 invalid token`。
//! `ensure_fresh_access_token` 只看过期时间, 觉得令牌"还很新", 一直原样交出去;
//! 员工只看到聊天报 401, 看不出是中央地址变了。
//!
//! # 判据
//!
//! 不能拿令牌的 `iss` 跟 Companion 配置里的身份服务地址比: 那个地址只是"去哪儿
//! 登录" (可能是 `http://IP:8998` 直连, 而签发者是 `https://IP`), 两者合法地不同。
//! 正确的参照是**身份服务自己现在声明的签发者** —— 它的
//! `/.well-known/openid-configuration` 里的 `issuer`, 网关验签认的也是这一个。
//! 拿不到 (身份服务不可达) 就判"未知", 什么都不做, 绝不因为网络抖动把人踢下线。

use std::sync::Mutex;
use std::time::{Duration, Instant};

use base64::Engine as _;

use super::oauth_config::OidcConfig;

/// 发现文档缓存多久 —— 地址变更是低频事件, 10 分钟内发现足够, 省得每次取令牌都打一次网络
const DISCOVERY_TTL: Duration = Duration::from_secs(600);

/// (取到的时刻, 当时用的身份服务地址, 它声明的签发者)
static DISCOVERED: Mutex<Option<(Instant, String, String)>> = Mutex::new(None);
/// 被踢下线的原因, 留给登录页显示; 登录成功后清掉
static NOTICE: Mutex<Option<String>> = Mutex::new(None);

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum IssuerDrift {
    /// 一致, 或者没法判断 (令牌解不开 / 身份服务不可达)
    NoChange,
    /// 令牌是旧签发者签的
    Changed { token_iss: String, current: String },
}

fn norm(s: &str) -> String {
    s.trim().trim_end_matches('/').to_string()
}

/// 令牌里的 `iss` (不验签, 只读 claims)
pub(crate) fn token_issuer(token: &str) -> Option<String> {
    let mid = token.split('.').nth(1)?;
    let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(mid.trim_end_matches('=').as_bytes())
        .ok()?;
    let v: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
    v.get("iss")?.as_str().map(norm)
}

/// 纯判定, 方便测: 两边都有值且不同才算变更
pub(crate) fn compare(token_iss: Option<String>, current: Option<String>) -> IssuerDrift {
    match (token_iss, current) {
        (Some(t), Some(c)) if norm(&t) != norm(&c) => IssuerDrift::Changed {
            token_iss: norm(&t),
            current: norm(&c),
        },
        _ => IssuerDrift::NoChange,
    }
}

async fn discovered_issuer(cfg: &OidcConfig) -> Option<String> {
    if let Ok(guard) = DISCOVERED.lock() {
        if let Some((at, base, iss)) = guard.as_ref() {
            if base == &cfg.issuer && at.elapsed() < DISCOVERY_TTL {
                return Some(iss.clone());
            }
        }
    }
    let client = crate::util::http_client::central_client(Duration::from_secs(10)).ok()?;
    let url = format!("{}/.well-known/openid-configuration", cfg.issuer);
    let resp = client.get(&url).send().await.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let doc: serde_json::Value = resp.json().await.ok()?;
    let iss = norm(doc.get("issuer")?.as_str()?);
    if iss.is_empty() {
        return None;
    }
    if let Ok(mut guard) = DISCOVERED.lock() {
        *guard = Some((Instant::now(), cfg.issuer.clone(), iss.clone()));
    }
    Some(iss)
}

/// 当前令牌是不是旧中央签的。身份服务不可达 → NoChange (不动)。
pub(crate) async fn check(cfg: &OidcConfig, token: &str) -> IssuerDrift {
    let Some(token_iss) = token_issuer(token) else {
        return IssuerDrift::NoChange;
    };
    compare(Some(token_iss), discovered_issuer(cfg).await)
}

pub(crate) fn set_notice(msg: String) {
    if let Ok(mut n) = NOTICE.lock() {
        *n = Some(msg);
    }
}

/// 登录页要显示的提示 (不清除 —— 登录页 60 秒轮询一次, 清了提示就闪没了)
pub(crate) fn notice() -> Option<String> {
    NOTICE.lock().ok().and_then(|n| n.clone())
}

pub(crate) fn clear_notice() {
    if let Ok(mut n) = NOTICE.lock() {
        *n = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn jwt(payload: &str) -> String {
        let enc = |s: &str| base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(s.as_bytes());
        format!("{}.{}.sig", enc(r#"{"alg":"RS256"}"#), enc(payload))
    }

    #[test]
    fn reads_iss_from_token_and_normalizes_trailing_slash() {
        let t = jwt(r#"{"iss":"https://192.168.205.38/","sub":"a@b.c"}"#);
        assert_eq!(token_issuer(&t).as_deref(), Some("https://192.168.205.38"));
        assert_eq!(token_issuer("not-a-jwt"), None);
        assert_eq!(token_issuer(&jwt(r#"{"sub":"x"}"#)), None);
    }

    #[test]
    fn changed_only_when_both_known_and_different() {
        // 10/8 Windows 现场那一对
        assert_eq!(
            compare(Some("https://192.168.205.38".into()), Some("https://127.0.0.1".into())),
            IssuerDrift::Changed {
                token_iss: "https://192.168.205.38".into(),
                current: "https://127.0.0.1".into(),
            }
        );
        assert_eq!(
            compare(Some("https://127.0.0.1/".into()), Some("https://127.0.0.1".into())),
            IssuerDrift::NoChange
        );
        // 身份服务不可达 / 令牌解不开 → 不判变更, 不能把人踢下线
        assert_eq!(compare(Some("https://a".into()), None), IssuerDrift::NoChange);
        assert_eq!(compare(None, Some("https://a".into())), IssuerDrift::NoChange);
    }

    #[test]
    fn notice_persists_until_cleared() {
        set_notice("中央地址已变更".into());
        assert_eq!(notice().as_deref(), Some("中央地址已变更"));
        assert_eq!(notice().as_deref(), Some("中央地址已变更"), "读一次不能清掉");
        clear_notice();
        assert_eq!(notice(), None);
    }
}
