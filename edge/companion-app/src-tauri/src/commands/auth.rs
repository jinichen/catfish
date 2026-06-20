//! 鉴权 Tauri 命令: login / logout / whoami / get_access_token.
//!
//! 给前端 React 调.
//! 内部委托给 services::oauth.

use serde::{Deserialize, Serialize};

use crate::services::oauth::{self, AuthSession, OidcConfig};

/// 返回给前端的 user info. AuthSession 子集, 不暴露 expires_at 之外的敏感字段.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthState {
    pub authenticated: bool,
    pub email: String,
    pub name: String,
    pub department: String,
    pub tier: String,
    /// 'oidc' / 'dev_token' / ''. UI 用来决定要不要显 warning banner.
    pub auth_method: String,
    pub expires_at: i64,
}

impl From<AuthSession> for AuthState {
    fn from(s: AuthSession) -> Self {
        Self {
            authenticated: true,
            email: s.email,
            name: s.name,
            department: s.department,
            tier: s.tier,
            auth_method: s.auth_method,
            expires_at: s.expires_at,
        }
    }
}

impl AuthState {
    fn anonymous() -> Self {
        Self {
            authenticated: false,
            email: String::new(),
            name: String::new(),
            department: String::new(),
            tier: String::new(),
            auth_method: String::new(),
            expires_at: 0,
        }
    }
}

/// `whoami` — Companion 启动 + useAuth 60s poll 调, 看现在登录了没.
///
/// 优先级:
///   1. CATFISH_DEV_TOKEN env 设了 → 返 dev_token 身份 (auth_method='dev_token')
///   2. Keychain 有未过期 token → 返 已登录
///   3. 都没 → 返 anonymous (前端弹登录)
///
/// P3.5.42.10 (鸿波 6/20 catch '反复出现登录'): 调用前先跑一次 silent refresh.
///
/// 老逻辑只 try_load_session 读盘 — access_token 过期就返 None → anonymous.
/// useAuth.ts 60s poll 调这函数, 每次过期都给前端 setState(authenticated:false)
/// → LoginGate 立刻显登录, 哪怕 refresh_token 完全有效能续 (30 天 TTL).
///
/// 修: 先调 ensure_fresh_access_token (内部带 mutex 防并发), 它会用 refresh_token
/// 跟 IdP 换新 access + refresh, 写新 user_info 到盘. 再 try_load_session 读盘看到
/// 新 expires_at, 返 authenticated. refresh 也挂 (refresh_token 也过期 / IdP 不可达
/// / 网络挂) 才走老路 → anonymous → 弹登录, 这是真过期场景, 合理.
#[tauri::command]
pub async fn auth_whoami() -> Result<AuthState, String> {
    // silent refresh 副作用: 写新 expires_at 到 ~/.catfish/oauth/user_info.
    // 这里不用返回值, 只要它写完盘.
    let _ = oauth::ensure_fresh_access_token().await;
    Ok(oauth::try_load_session()
        .map(AuthState::from)
        .unwrap_or_else(AuthState::anonymous))
}

/// `login` — 启动 OAuth flow.
///
/// 这个调用会:
///   1. 弹默认浏览器到 IdP authorize URL
///   2. 临时起 HTTP server 监听 callback (60 秒超时)
///   3. 拿 code → 换 token → 存 Keychain
///   4. 返登录后的 AuthState
///
/// 前端调用时 await: 用户在浏览器输密码 + 跳回 callback 完成 = 这个 promise resolve.
/// 如果 60 秒没完成 (员工没登录) 抛 timeout 错.
#[tauri::command]
pub async fn auth_login() -> Result<AuthState, String> {
    // load() 优先级: ~/.catfish/companion.yaml > env > 自动生成默认 yaml
    // macOS .app 不继承 terminal env 的坑由 yaml 路径解决
    let cfg = OidcConfig::load().map_err(|e| format!("OIDC 配置错: {e}"))?;
    let session = oauth::run_login_flow(&cfg)
        .await
        .map_err(|e| format!("登录失败: {e}"))?;
    Ok(session.into())
}

/// `logout` — 清 Keychain. dev_token 模式下返成功但没真清 (env 是员工自己设的).
#[tauri::command]
pub async fn auth_logout() -> Result<(), String> {
    oauth::logout().map_err(|e| format!("登出失败: {e}"))
}

/// `auth_get_access_token` — 给前端调 gateway 时用.
/// 注意: 这个不暴露 id_token (没必要), 只返 access_token.
/// 前端拿到后直接 `Authorization: Bearer <token>` 调 gateway.
///
/// BL-COMPANION-SILENT-REFRESH (5/23): 走 ensure_fresh_access_token 而不是
/// current_access_token. 区别:
///   - current_access_token: 纯读盘, token 即使过期也照返 → gateway 401 → me.ts
///     fetchWithAuth 撞 401 → invoke('auth_login') 弹浏览器走完整 OAuth.
///   - ensure_fresh_access_token: 读盘前先看 expires_at, 快过期 / 已过期就用
///     refresh_token 跟 catfish-identity 换一对新的, 员工无感. refresh 也挂时
///     才 fallback 到老路径 (返旧 token → 401 → 弹浏览器, 跟用户 5/23 选定的
///     fallback 一致).
///
/// 历史: 函数名沿用 OAuth 习惯叫 access_token, 实际语义是"给 gateway 当 Bearer
/// 的那个 token" — BL-FIX31 5/9 改成 id_token 后名字没改, 现在仍是.
#[tauri::command]
pub async fn auth_get_access_token() -> Result<Option<String>, String> {
    Ok(oauth::ensure_fresh_access_token().await)
}
