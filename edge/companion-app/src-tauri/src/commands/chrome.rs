//! Catfish Chrome 的启停 + 状态查询。
//!
//! Catfish Chrome 是 Companion 起的**独立**调试 Chrome 实例（隔离 user-data-dir），
//! 不复用员工日常浏览器，避免污染 cookies / 误退他们的 Chrome 主窗口。
//!
//! 启动：`<chrome-bin> --remote-debugging-port=9222 --user-data-dir=<隔离 profile>`
//! kill：重新核验隔离 profile 的实际占用进程，不信任残留 PID 文件。
//!
//! cdp_url 自动同步 (#50): chrome_launch 等待 Chrome 的 /json/version 就绪,
//! 拿到 webSocketDebuggerUrl 自动写到 ~/.hermes/config.yaml 的 browser.cdp_url。
//! 替代员工每次手动跑 catfish-browser-attach.sh 的辛苦活。

use crate::commands::types::ServiceStatus;
use crate::services::{catfish_paths, endpoints};


#[tauri::command]
pub async fn chrome_launch() -> Result<(), String> {
    let ws_url = crate::services::chrome_runtime::ensure_running().await?;
    match update_hermes_config_cdp_url(&ws_url) {
        Ok(true) => log::info!("已更新浏览器 CDP 地址；配置 watcher 将加载新配置"),
        Ok(false) => {},
        Err(e) => return Err(format!("浏览器已就绪，但同步 CDP 配置失败: {e}")),
    }
    Ok(())
}


/// 更新 ~/.hermes/config.yaml 的 browser.cdp_url。
/// 返回 Ok(true) 表示真的写了, Ok(false) 表示无操作 (config 不存在 / 已经是同值)。
fn update_hermes_config_cdp_url(ws_url: &str) -> Result<bool, String> {
    let config_path = catfish_paths::hermes_config_path()
        .ok_or_else(|| "找不到 home 目录".to_string())?;
    if !config_path.exists() {
        return Ok(false);
    }
    let content = std::fs::read_to_string(&config_path)
        .map_err(|e| format!("读 hermes config 失败: {e}"))?;
    let mut data: serde_yaml::Value = serde_yaml::from_str(&content)
        .map_err(|e| format!("yaml 解析失败: {e}"))?;

    // 走到 data["browser"]["cdp_url"] 路径, 不存在则按需建
    let mapping = data
        .as_mapping_mut()
        .ok_or_else(|| "config.yaml 顶层不是 mapping".to_string())?;
    let browser_key = serde_yaml::Value::String("browser".to_string());
    let browser = mapping
        .entry(browser_key)
        .or_insert_with(|| serde_yaml::Value::Mapping(serde_yaml::Mapping::new()));
    let browser_map = browser
        .as_mapping_mut()
        .ok_or_else(|| "config.browser 字段不是 mapping".to_string())?;

    let cdp_key = serde_yaml::Value::String("cdp_url".to_string());
    // 已经是同值就不动 (避免无意义写文件触发 inotify)
    if let Some(serde_yaml::Value::String(existing)) = browser_map.get(&cdp_key) {
        if existing == ws_url {
            return Ok(false);
        }
    }
    browser_map.insert(cdp_key, serde_yaml::Value::String(ws_url.to_string()));

    let new_content = serde_yaml::to_string(&data)
        .map_err(|e| format!("yaml 序列化失败: {e}"))?;
    std::fs::write(&config_path, new_content)
        .map_err(|e| format!("写 hermes config 失败: {e}"))?;
    Ok(true)
}

#[tauri::command]
pub async fn chrome_kill() -> Result<(), String> {
    crate::services::chrome_runtime::stop().await
}

#[tauri::command]
pub async fn chrome_status() -> Result<ServiceStatus, String> {
    let ep = endpoints::endpoints();
    // Test the actual CDP protocol; a preliminary TCP timeout can reject working
    // localhost IPv4 services on Windows before the HTTP client's fallback runs.
    crate::util::service_probe::http_health(
        &ep.chrome_base(), "/json/version", Some(ep.chrome_port), true,
    ).await
}
