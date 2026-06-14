//! P3.4.1 (6/13 鸿波): mcp OAuth token 本机存储.
//!
//! # 为啥
//!
//! 老逻辑: mcp-registry oauth_callback 把员工 OAuth token POST 到中央
//! secret-broker (`:8995`) 集中存. 跟 manifesto 数据本地化红线冲突 — 中央
//! 拿到员工 token 等于能以员工身份操作 SaaS, 政企信安场景下不合规.
//!
//! 新逻辑: callback response 直接返 access_token + token_ref_local 给
//! Companion, Companion 落本机 `~/.catfish/mcp/oauth-tokens/<ref>` 文件
//! 0600. 中央对 token 0 持有, 0 看见.
//!
//! # 红线
//!
//! - token 文件权限 0600 (仅 owner 可读写)
//! - ref 名严格 safe_filename (防 path traversal)
//! - 不写 audit chain (token 值进 audit jsonl 等于 audit 也持有 — 不接受)

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenSaveResult {
    pub abs_path: String,
    pub bytes: usize,
}

fn oauth_tokens_dir() -> Result<PathBuf, String> {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home)
        .join(".catfish")
        .join("mcp")
        .join("oauth-tokens"))
}

/// ref 名校验. 必须是 `<connector>-<user>.token` 形式, 不含 / \ ..
fn safe_ref(token_ref_local: &str) -> Result<&str, String> {
    if token_ref_local.is_empty() || token_ref_local.len() > 200 {
        return Err(format!("token_ref_local 长度非法: {}", token_ref_local.len()));
    }
    if token_ref_local.contains('/')
        || token_ref_local.contains('\\')
        || token_ref_local.contains("..")
    {
        return Err(format!("token_ref_local 含非法字符: {token_ref_local}"));
    }
    Ok(token_ref_local)
}

/// 落 OAuth token 到 ~/.catfish/mcp/oauth-tokens/<token_ref_local>, 权限 0600.
///
/// 调用方: Companion McpRegistryCard 拿到 oauth_callback 的 response
/// (含 access_token + token_ref_local) 后立即调本 cmd. token 值绝不上 audit.
#[tauri::command]
pub async fn mcp_oauth_token_save(
    token_ref_local: String,
    access_token: String,
) -> Result<TokenSaveResult, String> {
    let r = safe_ref(&token_ref_local)?;
    let dir = oauth_tokens_dir()?;
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("创建 ~/.catfish/mcp/oauth-tokens/ 失败: {e}"))?;
    let path = dir.join(r);
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, &access_token)
        .map_err(|e| format!("写 tmp 失败: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("chmod 600 tmp 失败: {e}"))?;
    }
    std::fs::rename(&tmp, &path)
        .map_err(|e| format!("rename token 失败: {e}"))?;
    Ok(TokenSaveResult {
        abs_path: path.to_string_lossy().to_string(),
        bytes: access_token.len(),
    })
}

/// 删本机 OAuth token (取消订阅 / 重新授权时调). 不存在不报错.
#[tauri::command]
pub async fn mcp_oauth_token_delete(token_ref_local: String) -> Result<(), String> {
    let r = safe_ref(&token_ref_local)?;
    let dir = oauth_tokens_dir()?;
    let path = dir.join(r);
    if path.exists() {
        std::fs::remove_file(&path)
            .map_err(|e| format!("删 token 失败 ({}): {e}", path.display()))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn safe_ref_rejects_traversal() {
        assert!(safe_ref("jira-alice.token").is_ok());
        assert!(safe_ref("../escape.token").is_err());
        assert!(safe_ref("dir/file.token").is_err());
        assert!(safe_ref("with\\backslash").is_err());
        assert!(safe_ref("").is_err());
        assert!(safe_ref(&"x".repeat(201)).is_err());
    }

    #[tokio::test]
    async fn save_writes_file_and_chmod_600() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("HOME", tmp.path());

        let result = mcp_oauth_token_save(
            "jira-alice-at-test.dev.token".into(),
            "secret-token-value-123".into(),
        )
        .await
        .expect("save 成功");

        let expect_path = tmp
            .path()
            .join(".catfish")
            .join("mcp")
            .join("oauth-tokens")
            .join("jira-alice-at-test.dev.token");
        assert_eq!(result.abs_path, expect_path.to_string_lossy().to_string());

        // 文件存在 + 内容对
        let content = std::fs::read_to_string(&expect_path).expect("读");
        assert_eq!(content, "secret-token-value-123");

        // 权限 0600 (unix only)
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let meta = std::fs::metadata(&expect_path).expect("metadata");
            let mode = meta.permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "权限应该 0600, 实际 {mode:o}");
        }
    }

    #[tokio::test]
    async fn save_overwrites_existing() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("HOME", tmp.path());

        mcp_oauth_token_save("k.token".into(), "v1".into())
            .await
            .expect("save 1");
        mcp_oauth_token_save("k.token".into(), "v2-renewed".into())
            .await
            .expect("save 2");

        let path = tmp
            .path()
            .join(".catfish/mcp/oauth-tokens/k.token");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "v2-renewed");
    }

    #[tokio::test]
    async fn save_rejects_path_traversal() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("HOME", tmp.path());

        let err = mcp_oauth_token_save("../escape.token".into(), "x".into())
            .await
            .expect_err("应拒绝 ..");
        assert!(err.contains("非法字符"), "{err}");

        let err = mcp_oauth_token_save("a/b.token".into(), "x".into())
            .await
            .expect_err("应拒绝 /");
        assert!(err.contains("非法字符"), "{err}");
    }

    #[tokio::test]
    async fn delete_idempotent() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("HOME", tmp.path());

        // 删不存在的不报错
        mcp_oauth_token_delete("missing.token".into())
            .await
            .expect("idempotent");

        // 写 + 删
        mcp_oauth_token_save("k.token".into(), "v".into())
            .await
            .expect("save");
        let path = tmp
            .path()
            .join(".catfish/mcp/oauth-tokens/k.token");
        assert!(path.exists());
        mcp_oauth_token_delete("k.token".into())
            .await
            .expect("delete");
        assert!(!path.exists());
    }

    #[tokio::test]
    async fn delete_rejects_traversal() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("HOME", tmp.path());

        // 在 outputs 上一级放个 marker
        let evil = tmp.path().join("evil.txt");
        std::fs::write(&evil, "important").expect("write");

        let err = mcp_oauth_token_delete("../../evil.txt".into())
            .await
            .expect_err("应拒绝");
        assert!(err.contains("非法字符"), "{err}");
        assert!(evil.exists()); // 不能被删
    }
}
