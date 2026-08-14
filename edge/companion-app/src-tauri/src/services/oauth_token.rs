//! 授权码换 token, 以及 id_token claims 的解码。
//!
//! 2026-08-15 从 oauth.rs 切出来。纯搬迁, 逻辑一行未改。
//! P3.5.82 那 53 行测试 (refresh 响应缺 id_token 时不能整体失败) 跟着过来了。
//!
//! ⚠ `decode_id_token_claims_unverified` 名字里的 unverified 是认真的:
//! 它**不验签**, 只把 claims 解出来取 sub/email 显示用。授权判断不能靠它 ——
//! 真正的校验在中央那边。搬迁没动这个语义, 也别让它悄悄变成"验过了"。

use anyhow::{anyhow, bail, Context, Result};
use base64::Engine as _;
use serde::Deserialize;

use super::oauth_config::OidcConfig;

#[derive(Deserialize)]
pub(crate) struct TokenResponse {
    pub(crate) access_token: String,
    /// P3.5.82 (7/29): 改成 Option —— **服务端可能不返**。
    ///
    /// 原来是必填 `String`。identity 的 refresh_token grant 响应体里没有
    /// 这个字段 (只有 access_token / refresh_token / expires_in / scope),
    /// 于是整个响应反序列化失败:
    ///     "解析 refresh /token 响应失败: missing field `id_token`"
    ///
    /// 而失败发生在**服务端已经消费掉旧 refresh_token 并 rotation 出新的之后** ——
    /// 新 token 随着这次解析失败一起被丢掉, 本地留着的还是那个已作废的。
    /// 下一次刷新拿旧的去换, 服务端判"已用过 / 已吊销", 客户端据此删掉本地
    /// 凭证 → 员工被弹回登录页。
    ///
    /// 7/29 实测: 登录后**恰好一小时**必现一次, 一整天被弹了七八次。且第一现场
    /// (missing field) 跟最终报错 (已用过/已吊销) 文案完全不同、相隔一分钟,
    /// 只看后者会误判成"token 被盗用"。
    ///
    /// 服务端已同步修成返回 id_token (routes_token.py 两个 refresh 返回点)。
    /// 这里保持 Option 是为了**对着老版本 identity 也能自愈** —— 客户端先于
    /// 服务端升级是常态, 不能要求两边同时更新。
    #[serde(default)]
    pub(crate) id_token: Option<String>,
    pub(crate) expires_in: Option<i64>,
    /// BL-COMPANION-SILENT-REFRESH (5/23): catfish-identity routes_token.py:150
    /// 在 refresh_token_store 配了的情况下会带这个字段. 没配 → None, 保持原行为
    /// (员工 1h 后重登).
    /// rotation: 每次 refresh 后服务端发新 refresh_token, 老的标 revoked.
    #[serde(default)]
    pub(crate) refresh_token: Option<String>,
}

pub(crate) async fn exchange_code_for_tokens(
    cfg: &OidcConfig,
    code: &str,
    redirect_uri: &str,
) -> Result<TokenResponse> {
    let token_url = format!("{}/token", cfg.issuer);
    // P3.5.80 (7/28): 同上 —— 自签 HTTPS issuer 要靠 trust_central 才连得上.
    let client = crate::util::http_client::central_client(std::time::Duration::from_secs(30))
        .map_err(|e| anyhow!(e))?;
    let resp = client
        .post(&token_url)
        .form(&[
            ("grant_type", "authorization_code"),
            ("code", code),
            ("redirect_uri", redirect_uri),
            ("client_id", cfg.client_id.as_str()),
        ])
        .send()
        .await
        .context("调 /token 失败")?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        bail!("/token 返回 {status}: {body}");
    }
    let token_resp: TokenResponse = resp.json().await.context("解析 /token 响应失败")?;
    Ok(token_resp)
}

#[derive(Deserialize)]
pub(crate) struct IdTokenClaims {
    pub(crate) sub: String,
    pub(crate) name: Option<String>,
    pub(crate) department: Option<String>,
    pub(crate) tier: Option<String>,
}

/// 解 id_token middle segment (base64 JSON), 不验签.
/// gateway 才负责验签, Companion 这里只是为了显 user info.
pub(crate) fn decode_id_token_claims_unverified(id_token: &str) -> Result<IdTokenClaims> {
    let mid = id_token
        .split('.')
        .nth(1)
        .ok_or_else(|| anyhow!("id_token 格式错"))?;
    let padded = format!(
        "{}{}",
        mid,
        "=".repeat((4 - mid.len() % 4) % 4)
    );
    let bytes = base64::engine::general_purpose::URL_SAFE
        .decode(padded.as_bytes())
        .context("id_token base64 解码失败")?;
    serde_json::from_slice(&bytes).context("id_token claims JSON 解析失败")
}

/// P3.5.82 (7/29): refresh 响应缺 `id_token` 时不能整体失败。
///
/// ── 这组测试在防什么 ────────────────────────────────────────────────
///
/// 7/29 实测的完整因果链:
///   1. Companion 发起 silent refresh
///   2. identity **消费掉旧 refresh_token**, rotation 出新的, 返 200
///   3. 但响应体没有 `id_token`, 而客户端把它当必填 → 反序列化整体失败
///   4. 新 refresh_token 随之丢弃, 本地留着已作废的那个
///   5. 下次刷新 → "已用过 / 已吊销" → 删本地凭证 → 员工被弹回登录页
///
/// 表现是**登录后恰好一小时**必弹一次, 而日志里第一眼看到的错 ("已用过")
/// 跟真因 ("missing field `id_token`") 文案完全不同、相隔一分钟。
#[cfg(test)]
mod tests_token_response_id_token_optional {
    use super::TokenResponse;

    #[test]
    fn parses_refresh_response_without_id_token() {
        // identity 老版本 refresh 分支的真实响应体形状
        let body = r#"{
            "access_token": "acc.jwt.sig",
            "refresh_token": "rt-new",
            "refresh_expires_in": 2592000,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid email profile"
        }"#;
        let r: TokenResponse = serde_json::from_str(body).expect("缺 id_token 不该解析失败");
        assert!(r.id_token.is_none());
        // 最关键的一条: 新的 refresh_token 必须能拿到 —— 拿不到就是会话之死
        assert_eq!(r.refresh_token.as_deref(), Some("rt-new"));
        assert_eq!(r.access_token, "acc.jwt.sig");
    }

    #[test]
    fn parses_response_with_id_token() {
        // 服务端修好之后的形状, 不能因为改成 Option 就读不到了
        let body = r#"{
            "access_token": "acc",
            "id_token": "idt",
            "refresh_token": "rt",
            "token_type": "Bearer",
            "expires_in": 3600
        }"#;
        let r: TokenResponse = serde_json::from_str(body).unwrap();
        assert_eq!(r.id_token.as_deref(), Some("idt"));
        assert_eq!(r.refresh_token.as_deref(), Some("rt"));
    }

    #[test]
    fn access_token_still_required() {
        // 只有 id_token 放宽, access_token 缺了仍要 fail-loud ——
        // 那是真的拿不到凭证, 静默降级只会把问题推到更远的地方。
        let body = r#"{"id_token":"i","token_type":"Bearer","expires_in":3600}"#;
        assert!(serde_json::from_str::<TokenResponse>(body).is_err());
    }

    #[test]
    fn missing_refresh_token_is_none_not_error() {
        // 服务端没配 refresh_token_store 时的老行为, 不能退化成解析失败
        let body = r#"{"access_token":"a","id_token":"i","token_type":"Bearer","expires_in":3600}"#;
        let r: TokenResponse = serde_json::from_str(body).unwrap();
        assert!(r.refresh_token.is_none());
    }
}
