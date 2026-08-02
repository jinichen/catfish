//! 教学流程的本地凭据保存。
//!
//! 密码只通过 Tauri IPC 到 Rust，写入操作系统凭据库。这里绝不记录密码，
//! 也不把密码放进 shell 参数、配置文件或聊天消息。

use keyring::Entry;

const ACCOUNT: &str = "catfish-teaching";
const SERVICE_PREFIX: &str = "catfish-teaching:";

fn service_name(label: &str) -> Result<String, String> {
    let label = label.trim();
    if label.is_empty() {
        return Err("请填写凭据名称".to_string());
    }
    if label.len() > 80 || label.chars().any(|c| c.is_control()) {
        return Err("凭据名称过长或包含不可用字符".to_string());
    }
    Ok(format!("{SERVICE_PREFIX}{label}"))
}

#[tauri::command]
pub fn teaching_credential_save(label: String, password: String) -> Result<String, String> {
    let target = service_name(&label)?;
    if password.is_empty() {
        return Err("请填写密码".to_string());
    }
    #[cfg(target_os = "windows")]
    let entry = Entry::new("catfish", &target);
    #[cfg(not(target_os = "windows"))]
    let entry = Entry::new(&target, ACCOUNT);
    let entry = entry
        .map_err(|e| format!("无法打开本机凭据库: {e}"))?;
    entry
        .set_password(&password)
        .map_err(|e| format!("保存到本机凭据库失败: {e}"))?;
    #[cfg(target_os = "windows")]
    let scheme = "wincred";
    #[cfg(not(target_os = "windows"))]
    let scheme = "keychain";
    Ok(format!("{scheme}://{target}"))
}

#[cfg(test)]
mod tests {
    use super::service_name;

    #[test]
    fn builds_namespaced_service_name() {
        assert_eq!(service_name("教学登录").unwrap(), "catfish-teaching:教学登录");
    }

    #[test]
    fn rejects_empty_name() {
        assert!(service_name("  ").is_err());
    }
}
