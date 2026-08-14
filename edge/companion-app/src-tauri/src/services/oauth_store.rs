//! OAuth 凭证的本机存取。
//!
//! 2026-08-15 从 oauth.rs 切出来。**纯搬迁, 一行未改, 也没加任何日志。**
//!
//! 单独成文件是因为这是全项目最该被逐行看的几十行: 它决定 access_token /
//! id_token / refresh_token 落在哪、权限是多少。
//!
//! 现状请看下面那段历史注释 —— 它现在写的是文件不是 OS 钥匙串, 而"release
//! build 再切回 keyring"这件事至今没有做。这是既有状态, 不在这次搬迁里改动;
//! 要改是另一件事, 得单独评估。
//!
//! 已有的 log::debug 只打 username 和字节数, 不打值。搬迁保持原样。

use anyhow::{Context, Result};

// ============================================================
// Token storage — 文件系统 (BL-FIX32, 5/9)
// ============================================================
//
// 历史: 老实现用 macOS Keychain (keyring crate). 在 unsigned dev binary
// (cargo run target/debug, 没 codesign) 跑时, set_password 报 success 但实
// 际不写 login keychain — silent no-op. 用户从来不会看到那个"允许访问 Keychain"
// 的系统弹窗 (真写入 OAuth login OK 但 security CLI 一行都查不到 entry).
//
// 用户登录走完: log 打 'OAuth login OK: user=chenhongbo@ffcs.cn', 但 chat
// 调 invoke('auth_get_access_token') 永远返 None, 全链路 fallback dev_token,
// gateway 401. 中招过五六次都没看出是 keyring silent fail.
//
// 现在: 直接写 ~/.catfish/oauth/{access_token,id_token,user_info} 三个文件,
// chmod 600. dev 流程立即可用. 真 release build (cargo tauri build --release
// + Apple Developer ID sign) 后再切回 keyring (那时弹窗 + 真写入都正常).
//
// 函数名仍叫 _to_keyring/from_keyring/_keyring 不改, callers 全不动.

fn _oauth_storage_dir() -> Result<std::path::PathBuf> {
    // BL-WIN-HOME (7/17 · 达华): Windows 没有 $HOME (只有 %USERPROFILE%).
    // 老代码只查 HOME · SSO 回来 token 换成功但存文件时挂在 "登录失败: $HOME 未设置".
    // 全项目其他 service (email_config/agent_prefs/curator_config/pet_status/hermes_api_config)
    // 都写了 USERPROFILE fallback, 就这里漏了.
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .context("$HOME / %USERPROFILE% 均未设置")?;
    let dir = std::path::PathBuf::from(home).join(".catfish").join("oauth");
    std::fs::create_dir_all(&dir).context("建 ~/.catfish/oauth 目录失败")?;
    // 目录权限 0700, 防别的用户读
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700));
    }
    Ok(dir)
}

pub(crate) fn save_to_keyring(username: &str, value: &str) -> Result<()> {
    let path = _oauth_storage_dir()?.join(username);
    std::fs::write(&path, value)
        .with_context(|| format!("写 token 文件失败: {}", path.display()))?;
    // 单文件权限 0600
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))
            .with_context(|| format!("chmod 600 失败: {}", path.display()))?;
    }
    log::debug!("oauth token saved: {} ({} bytes)", username, value.len());
    Ok(())
}

pub(crate) fn load_from_keyring(username: &str) -> Result<Option<String>> {
    let path = _oauth_storage_dir()?.join(username);
    if !path.exists() {
        return Ok(None);
    }
    let s = std::fs::read_to_string(&path)
        .with_context(|| format!("读 token 文件失败: {}", path.display()))?;
    Ok(Some(s))
}

pub(crate) fn delete_from_keyring(username: &str) -> Result<()> {
    let path = _oauth_storage_dir()?.join(username);
    if path.exists() {
        std::fs::remove_file(&path)
            .with_context(|| format!("删 token 文件失败: {}", path.display()))?;
    }
    Ok(())
}
