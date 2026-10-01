//! 可选组件 (会议纪要组件包等) 的列表 / 下载 / 取消 / 校验 —— 前端入口。
//! 逻辑在 services::components, 这里只做 Tauri 命令和进度事件。
//!
//! 进度事件 `component_progress`, payload = services::components::Progress。
//! 下载在后台 tokio 任务里跑, 命令立即返回; 关掉界面不影响下载。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use serde::Serialize;
use tauri::Emitter;

use crate::services::components::{
    self as comp, ComponentEntry, LocalStatus, Progress,
};

pub const PROGRESS_EVENT: &str = "component_progress";

#[derive(Debug, Serialize)]
pub struct ComponentInfo {
    #[serde(flatten)]
    pub entry: ComponentEntry,
    pub status: LocalStatus,
    pub busy: bool,
}

#[derive(Debug, Serialize)]
pub struct ComponentsList {
    pub platform: &'static str,
    pub components: Vec<ComponentInfo>,
}

fn web_base() -> String {
    crate::services::endpoints::endpoints().web_base()
}

#[tauri::command]
pub async fn components_list() -> Result<ComponentsList, String> {
    let client = comp::download_client()?;
    let manifest = comp::fetch_manifest(&client, &web_base()).await?;
    let dir = comp::runtime_dir()?;
    let platform = comp::current_platform();
    let busy = comp::active().lock().map_err(|e| e.to_string())?.clone();
    let components = manifest
        .components
        .into_iter()
        .filter(|c| c.platform == platform)
        .map(|entry| ComponentInfo {
            status: comp::local_status(&dir, &entry),
            busy: busy.contains_key(&entry.name),
            entry,
        })
        .collect();
    Ok(ComponentsList { platform, components })
}

/// 占住下载槽; 已有同名任务在跑就报错。
fn claim(name: &str) -> Result<Arc<AtomicBool>, String> {
    let mut map = comp::active().lock().map_err(|e| e.to_string())?;
    if map.contains_key(name) {
        return Err(format!("{name} 已经在下载/校验中"));
    }
    let flag = Arc::new(AtomicBool::new(false));
    map.insert(name.to_string(), flag.clone());
    Ok(flag)
}

fn release(name: &str) {
    if let Ok(mut map) = comp::active().lock() {
        map.remove(name);
    }
}

fn emit(app: &tauri::AppHandle, entry: &ComponentEntry, phase: &'static str, downloaded: u64, error: Option<String>) {
    let p = Progress { name: entry.name.clone(), phase, downloaded, total: entry.size, error };
    if let Err(e) = app.emit(PROGRESS_EVENT, p) {
        log::warn!("[components] 发进度事件失败: {e}");
    }
}

async fn resolve_entry(name: &str) -> Result<(reqwest::Client, String, ComponentEntry), String> {
    let client = comp::download_client()?;
    let base = web_base();
    let manifest = comp::fetch_manifest(&client, &base).await?;
    let entry = comp::find_entry(&manifest, name, comp::current_platform())
        .cloned()
        .ok_or_else(|| format!("中央没有发布 {name} 的 {} 版本", comp::current_platform()))?;
    Ok((client, base, entry))
}

#[tauri::command]
pub async fn components_download(app: tauri::AppHandle, name: String) -> Result<(), String> {
    let (client, base, entry) = resolve_entry(&name).await?;
    let dir = comp::runtime_dir()?;
    let cancel = claim(&name)?;
    log::info!("[components] 开始下载 {} {} ({} 字节)", entry.name, entry.version, entry.size);
    tokio::spawn(async move {
        let progress_app = app.clone();
        let progress_entry = entry.clone();
        let result = comp::download(&client, &base, &dir, &entry, cancel.clone(), move |phase, n| {
            emit(&progress_app, &progress_entry, phase, n, None)
        })
        .await;
        match result {
            Ok(()) => log::info!("[components] {} 下载并校验完成", entry.name),
            Err(e) => {
                let phase = if cancel.load(Ordering::Relaxed) { "cancelled" } else { "error" };
                log::warn!("[components] {} 下载失败: {e}", entry.name);
                emit(&app, &entry, phase, 0, Some(e));
            }
        }
        release(&entry.name);
    });
    Ok(())
}

/// IT 手动放进 ~/.catfish/runtime/ 的包 (状态 unverified): 补算一次 sha256。
#[tauri::command]
pub async fn components_verify(app: tauri::AppHandle, name: String) -> Result<(), String> {
    let (_, _, entry) = resolve_entry(&name).await?;
    let dir = comp::runtime_dir()?;
    let cancel = claim(&name)?;
    tokio::task::spawn_blocking(move || {
        emit(&app, &entry, "verifying", entry.size, None);
        match comp::verify_existing(&dir, &entry, &cancel) {
            Ok(()) => emit(&app, &entry, "done", entry.size, None),
            Err(e) => emit(&app, &entry, "error", 0, Some(e)),
        }
        release(&entry.name);
    });
    Ok(())
}

/// 取消; 已下的部分留在 .part, 下次接着下。幂等。
#[tauri::command]
pub fn components_cancel(name: String) -> Result<(), String> {
    if let Some(flag) = comp::active().lock().map_err(|e| e.to_string())?.get(&name) {
        flag.store(true, Ordering::Relaxed);
    }
    Ok(())
}
