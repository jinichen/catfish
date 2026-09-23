//! 微信历史记录分析授权。
//!
//! 本模块只保存授权元数据并校验独立读取器；不读取聊天、不保存密钥，也不调用模型。
//! 真读取走 Tool Bridge，分析沿用当前会话 Picker。
//!
//! 9/23: 数据源从「员工选一个 JSON/CSV 文件」改成「微信导出 ZIP 的导入库」
//! (`export_library`, 见 wechat_exports.rs)。入口在聊天框拖入 ZIP, 首次导入时
//! 在确认框里同意授权 —— 看板上单独选文件、单独授权的卡片去掉了。老的
//! `export_file` 配置仍然认 (Tool Bridge 也认), 员工第一次导入 ZIP 后切到导入库。

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

pub(crate) const CONFIG_VERSION: u32 = 2;
pub(crate) const SOURCE_LIBRARY: &str = "export_library";
const SOURCE_FILE: &str = "export_file";
const EXPORT_READER_NAME: &str = "catfish-wechat-reader";
const MAX_SOURCE_BYTES: u64 = 512 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct WeChatArchiveConfig {
    pub(crate) version: u32,
    pub(crate) enabled: bool,
    pub(crate) helper_path: String,
    pub(crate) source_type: String,
    pub(crate) source_path: String,
    pub(crate) source_size: u64,
    pub(crate) source_modified_ns: u64,
    pub(crate) consented_picker_model: String,
    pub(crate) consented_at: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WeChatArchiveStatus {
    supported: bool,
    helper_path: String,
    helper_installed: bool,
    source_type: Option<String>,
    source_path: Option<String>,
    source_ready: bool,
    enabled: bool,
    authorized: bool,
    current_picker_model: Option<String>,
    consented_picker_model: Option<String>,
    requires_reauthorization: bool,
    message: String,
}

pub(crate) fn catfish_home() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| "找不到用户目录".to_string())?;
    Ok(PathBuf::from(home).join(".catfish"))
}

fn config_path() -> Result<PathBuf, String> {
    if let Ok(path) = std::env::var("CATFISH_WECHAT_ARCHIVE_CONFIG") {
        if !path.trim().is_empty() {
            return Ok(PathBuf::from(path));
        }
    }
    Ok(catfish_home()?.join("wechat_archive.json"))
}

fn default_reader_path(name: &str, env_name: &str) -> Result<PathBuf, String> {
    if let Ok(path) = std::env::var(env_name) {
        if !path.trim().is_empty() {
            return Ok(PathBuf::from(path));
        }
    }
    #[cfg(target_os = "windows")]
    {
        let hermes_home = std::env::var("HERMES_HOME")
            .map(PathBuf::from)
            .or_else(|_| std::env::var("LOCALAPPDATA").map(|p| PathBuf::from(p).join("hermes")))
            .map_err(|_| "找不到 Windows Hermes 目录".to_string())?;
        return Ok(hermes_home
            .join("hermes-agent/venv/Scripts")
            .join(format!("{name}.exe")));
    }
    #[cfg(not(target_os = "windows"))]
    Ok(catfish_home()?.join("bin").join(name))
}

pub(crate) fn default_export_helper_path() -> Result<PathBuf, String> {
    default_reader_path(EXPORT_READER_NAME, "CATFISH_WECHAT_READER")
}

pub(crate) fn load_config() -> Option<WeChatArchiveConfig> {
    let text = std::fs::read_to_string(config_path().ok()?).ok()?;
    serde_json::from_str(&text).ok()
}

pub(crate) fn write_config(config: &WeChatArchiveConfig) -> Result<(), String> {
    let path = config_path()?;
    let parent = path.parent().ok_or_else(|| "授权文件路径无父目录".to_string())?;
    std::fs::create_dir_all(parent).map_err(|e| format!("创建授权目录失败: {e}"))?;
    let body = serde_json::to_vec_pretty(config).map_err(|e| format!("序列化授权失败: {e}"))?;
    std::fs::write(&path, body).map_err(|e| format!("写微信历史授权失败: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("收紧授权文件权限失败: {e}"))?;
    }
    Ok(())
}

pub(crate) fn helper_installed(path: &Path) -> bool {
    if !path.is_absolute() || !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        return path
            .metadata()
            .map(|meta| meta.permissions().mode() & 0o111 != 0)
            .unwrap_or(false);
    }
    #[cfg(not(unix))]
    true
}

pub(crate) fn supported_platform() -> bool {
    cfg!(any(target_os = "macos", target_os = "windows"))
}

fn export_snapshot(path: &Path) -> Result<(PathBuf, u64, u64), String> {
    let resolved = path
        .canonicalize()
        .map_err(|_| "导出文件已移动、删除或不可读取".to_string())?;
    let extension = resolved
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !matches!(extension.as_str(), "json" | "jsonl" | "csv") {
        return Err("只支持 JSON、JSONL、CSV 聊天导出文件".to_string());
    }
    let metadata = resolved
        .metadata()
        .map_err(|_| "无法读取导出文件信息".to_string())?;
    if !metadata.is_file() {
        return Err("选择的数据源不是文件".to_string());
    }
    if metadata.len() > MAX_SOURCE_BYTES {
        return Err("导出文件超过 512 MB 安全上限".to_string());
    }
    let modified_ns = metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos().min(u64::MAX as u128) as u64)
        .ok_or_else(|| "无法读取导出文件修改时间".to_string())?;
    Ok((resolved, metadata.len(), modified_ns))
}

fn source_ready(config: &WeChatArchiveConfig) -> bool {
    if config.version != CONFIG_VERSION {
        return false;
    }
    match config.source_type.as_str() {
        SOURCE_LIBRARY => Path::new(&config.source_path).is_dir(),
        SOURCE_FILE => export_snapshot(Path::new(&config.source_path))
            .map(|(_, size, modified)| {
                size == config.source_size && modified == config.source_modified_ns
            })
            .unwrap_or(false),
        _ => false,
    }
}

fn doctor_is_safe(report: &Value) -> bool {
    report.get("protocol_version").and_then(Value::as_u64) == Some(1)
        && report.get("read_only").and_then(Value::as_bool) == Some(true)
        && report.get("secure_key_store").and_then(Value::as_bool) == Some(true)
        && report
            .get("ephemeral_plaintext_cache")
            .and_then(Value::as_bool)
            == Some(true)
        && report.get("modifies_wechat_app").and_then(Value::as_bool) == Some(false)
}

/// 启动读取器用的最小环境。env_clear 之后只放回这些 —— 不透传 API key / 代理。
///
/// 9/23 补 SYSTEMROOT / WINDIR / USERPROFILE / TEMP: 以前只放 HOME/PATH/LANG,
/// Windows 上 Python 没有 SYSTEMROOT 连随机数都初始化不了, 跟 Tool Bridge 那边
/// (`wechat_archive._safe_env`) 也对不齐。
pub(crate) fn reader_command(helper: &Path) -> tokio::process::Command {
    let mut command = crate::services::process::background_tokio_command(helper);
    command.env_clear().kill_on_drop(true);
    for key in [
        "HOME", "USERPROFILE", "PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
    ] {
        if let Ok(value) = std::env::var(key) {
            command.env(key, value);
        }
    }
    command
}

pub(crate) async fn verify_helper(path: &Path) -> Result<(), String> {
    let mut command = reader_command(path);
    command.arg("doctor").arg("--json");
    let output = tokio::time::timeout(std::time::Duration::from_secs(10), command.output())
        .await
        .map_err(|_| "安全读取器自检超时".to_string())?
        .map_err(|e| format!("安全读取器无法启动: {e}"))?;
    if !output.status.success() {
        return Err("安全读取器自检失败".to_string());
    }
    let report: Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "安全读取器自检没有返回有效 JSON".to_string())?;
    if !doctor_is_safe(&report) {
        return Err("读取器不满足只读、系统凭据库、临时缓存和不修改微信的安全要求".to_string());
    }
    Ok(())
}

fn build_status() -> WeChatArchiveStatus {
    let supported = supported_platform();
    let config = load_config();
    let export_config = config.as_ref().filter(|item| {
        item.version == CONFIG_VERSION
            && (item.source_type == SOURCE_LIBRARY || item.source_type == SOURCE_FILE)
    });
    let helper = export_config
        .and_then(|item| {
            (!item.helper_path.trim().is_empty()).then(|| PathBuf::from(&item.helper_path))
        })
        .or_else(|| default_export_helper_path().ok())
        .unwrap_or_default();
    let installed = supported && helper_installed(&helper);
    let source_type = export_config.map(|item| item.source_type.clone());
    let source_path = export_config
        .map(|item| item.source_path.clone())
        .filter(|value| !value.is_empty());
    let ready = export_config.map(source_ready).unwrap_or(false);
    let current = crate::services::picker_config::current_model();
    let consented = export_config
        .map(|item| item.consented_picker_model.trim().to_string())
        .filter(|value| !value.is_empty());
    let enabled = export_config.map(|item| item.enabled).unwrap_or(false);
    let model_matches = current.is_some() && current == consented;
    let authorized = supported && installed && ready && enabled && model_matches;
    let requires_reauthorization = enabled && !model_matches;
    let message = if !supported {
        "当前系统不支持聊天导出文件分析".to_string()
    } else if !installed {
        "安全读取器尚未安装".to_string()
    } else if !ready {
        "还没有导入微信聊天记录：在聊天框拖入微信「合并转发」导出的 ZIP".to_string()
    } else if current.is_none() {
        "请先在聊天 Picker 中选择模型".to_string()
    } else if requires_reauthorization {
        "Picker 已变化，需要重新确认授权".to_string()
    } else if authorized {
        "已授权；每次读取仍会执行安全自检".to_string()
    } else {
        "读取器已就绪，等待员工授权".to_string()
    };
    WeChatArchiveStatus {
        supported,
        helper_path: helper.to_string_lossy().to_string(),
        helper_installed: installed,
        source_type,
        source_path,
        source_ready: ready,
        enabled,
        authorized,
        current_picker_model: current,
        consented_picker_model: consented,
        requires_reauthorization,
        message,
    }
}

#[tauri::command]
pub fn wechat_archive_status() -> WeChatArchiveStatus {
    build_status()
}

#[tauri::command]
pub async fn wechat_archive_enable(acknowledged: bool) -> Result<WeChatArchiveStatus, String> {
    if !supported_platform() {
        return Err("当前系统不支持聊天导出文件分析".to_string());
    }
    if !acknowledged {
        return Err("必须先确认微信正文将交给当前 Picker 模型分析".to_string());
    }
    let model = crate::services::picker_config::current_model()
        .ok_or_else(|| "请先在聊天 Picker 中选择模型".to_string())?;
    let mut config = load_config().ok_or_else(|| "还没有导入微信聊天记录".to_string())?;
    if config.source_type != SOURCE_LIBRARY && config.source_type != SOURCE_FILE {
        return Err("还没有导入微信聊天记录".to_string());
    }
    if !source_ready(&config) {
        return Err("微信聊天数据源已失效，请重新导入".to_string());
    }
    let configured_path = if config.helper_path.trim().is_empty() {
        default_export_helper_path()?
    } else {
        PathBuf::from(&config.helper_path)
    };
    let helper = configured_path
        .canonicalize()
        .map_err(|_| format!("安全读取器未安装: {}", configured_path.display()))?;
    if !helper_installed(&helper) {
        return Err(format!("安全读取器不可执行: {}", helper.display()));
    }
    verify_helper(&helper).await?;
    config.version = CONFIG_VERSION;
    config.enabled = true;
    config.helper_path = helper.to_string_lossy().to_string();
    config.consented_picker_model = model;
    config.consented_at = chrono::Utc::now().to_rfc3339();
    write_config(&config)?;
    Ok(build_status())
}

#[tauri::command]
pub fn wechat_archive_disable() -> Result<WeChatArchiveStatus, String> {
    let mut config = load_config().unwrap_or(WeChatArchiveConfig {
        version: CONFIG_VERSION,
        enabled: false,
        helper_path: default_export_helper_path()?.to_string_lossy().to_string(),
        source_type: String::new(),
        source_path: String::new(),
        source_size: 0,
        source_modified_ns: 0,
        consented_picker_model: String::new(),
        consented_at: String::new(),
    });
    config.enabled = false;
    config.consented_picker_model.clear();
    config.consented_at.clear();
    write_config(&config)?;
    Ok(build_status())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn safe_doctor_requires_every_capability() {
        let mut report = serde_json::json!({
            "protocol_version": 1,
            "read_only": true,
            "secure_key_store": true,
            "ephemeral_plaintext_cache": true,
            "modifies_wechat_app": false
        });
        assert!(doctor_is_safe(&report));
        report["modifies_wechat_app"] = Value::Bool(true);
        assert!(!doctor_is_safe(&report));
    }

    #[test]
    fn export_snapshot_rejects_unsupported_and_detects_changes() {
        let temp = tempfile::tempdir().unwrap();
        let bad = temp.path().join("chat.txt");
        std::fs::write(&bad, b"hello").unwrap();
        assert!(export_snapshot(&bad).is_err());

        let source = temp.path().join("chat.jsonl");
        std::fs::write(&source, b"{}\n").unwrap();
        let (resolved, size, modified) = export_snapshot(&source).unwrap();
        let config = WeChatArchiveConfig {
            version: CONFIG_VERSION,
            enabled: false,
            helper_path: String::new(),
            source_type: "export_file".to_string(),
            source_path: resolved.to_string_lossy().to_string(),
            source_size: size,
            source_modified_ns: modified,
            consented_picker_model: String::new(),
            consented_at: String::new(),
        };
        assert!(source_ready(&config));
        let mut retired = config.clone();
        retired.source_type = "authorized_database".to_string();
        retired.enabled = true;
        assert!(!source_ready(&retired));
        std::fs::write(&source, b"{}\n{}\n").unwrap();
        assert!(!source_ready(&config));
    }
}
