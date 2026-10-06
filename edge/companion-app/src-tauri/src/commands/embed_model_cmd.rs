//! 向量模型组件包: 状态 + 安装 —— 前端 (知识体系 · 语义检索) 入口。
//! 下载 / 校验走通用的 commands::components (组件名 embed-model, 平台 any)。
//! 进度事件 `embed_model_progress`: { step, done, error? }。
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use serde::Serialize;
use serde_json::json;
use tauri::Emitter;

use crate::services::components as comp;
use crate::services::embed_model::{self as em, Installed};
use crate::services::embedding;
use crate::services::embedding_config::load_config;

static INSTALLING: AtomicBool = AtomicBool::new(false);

#[derive(Debug, Serialize)]
pub struct EmbedModelStatus {
    /// 本机构建带本地向量 (aarch64 / Windows x64, 见 build.rs local_embedding) —— false 时只能靠中央网关
    pub local_supported: bool,
    /// 当前实际在用的向量 provider 能不能产出向量 (本地 or 远程)
    pub provider_ready: bool,
    pub provider_remote: bool,
    pub not_ready_reason: Option<String>,
    pub installed: Option<Installed>,
    /// ~/.catfish/runtime/ 里已下载 + 校验过、且比已装版本新的包文件名
    pub pack_ready: Option<String>,
    pub installing: bool,
}

fn version_key(v: &str) -> Vec<u32> {
    v.split('.').map(|x| x.parse().unwrap_or(0)).collect()
}

/// runtime 目录里本组件最高版本的、校验过的包 (平台 any 或本平台都认)。
pub fn ready_pack(runtime: &Path) -> Option<(PathBuf, String)> {
    let prefix = format!("{}-", em::COMPONENT_NAME);
    // Windows 的包多带 onnxruntime.dll, 平台无关的 any 包装不上 → 只认 windows-x64
    let suffixes: Vec<String> = if cfg!(windows) {
        vec![format!("-{}.tar.gz", comp::current_platform())]
    } else {
        vec![format!("-{}.tar.gz", comp::ANY_PLATFORM), format!("-{}.tar.gz", comp::current_platform())]
    };
    std::fs::read_dir(runtime)
        .ok()?
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            let rest = name.strip_prefix(&prefix)?;
            let version = suffixes.iter().find_map(|s| rest.strip_suffix(s.as_str()))?.to_string();
            let stamp = std::fs::read_to_string(runtime.join(format!("{name}.verified"))).ok()?;
            (stamp.trim().len() == 64).then(|| (e.path(), version))
        })
        .max_by_key(|(_, v)| version_key(v))
}

#[tauri::command]
pub fn embed_model_status() -> Result<EmbedModelStatus, String> {
    let cfg = load_config();
    let installed = em::installed(&cfg.local);
    let pack_ready = ready_pack(&comp::runtime_dir()?)
        .filter(|(_, v)| match installed.as_ref().and_then(|i| i.version.clone()) {
            Some(cur) => version_key(v) > version_key(&cur),
            // 没版本戳 (手动 curl 的) 或没装 → 有包就算可装
            None => true,
        })
        .map(|(p, _)| p.file_name().unwrap_or_default().to_string_lossy().to_string());
    let not_ready_reason = embedding::provider_not_ready_reason();
    Ok(EmbedModelStatus {
        local_supported: cfg!(local_embedding),
        provider_ready: not_ready_reason.is_none(),
        provider_remote: embedding::active_is_remote(),
        not_ready_reason,
        installed,
        pack_ready,
        installing: INSTALLING.load(Ordering::Relaxed),
    })
}

#[tauri::command]
pub fn embed_model_install(app: tauri::AppHandle) -> Result<(), String> {
    let (pack, version) = ready_pack(&comp::runtime_dir()?).ok_or("向量模型包还没下载 (或没校验), 先下载")?;
    if INSTALLING.swap(true, Ordering::SeqCst) {
        return Err("正在安装中".into());
    }
    let cfg = load_config();
    log::info!("[embed-model] 安装 {version} ← {}", pack.display());
    tauri::async_runtime::spawn_blocking(move || {
        let emit_app = app.clone();
        let r = em::install(&pack, &cfg.local, |step| {
            let _ = emit_app.emit("embed_model_progress", json!({ "step": step, "done": false }));
        });
        match &r {
            Ok(i) => log::info!("[embed-model] 装好 {:?} → {}", i.version, i.model_path.display()),
            Err(e) => log::warn!("[embed-model] 安装失败: {e}"),
        }
        let _ = app.emit("embed_model_progress", json!({ "step": "finished", "done": true, "error": r.err() }));
        INSTALLING.store(false, Ordering::SeqCst);
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn ready_pack_prefers_highest_verified_any_pack() {
        let tmp = TempDir::new().unwrap();
        for (v, verified) in [("1.0.0", true), ("1.2.0", true), ("2.0.0", false)] {
            let name = format!("embed-model-{v}-any.tar.gz");
            std::fs::write(tmp.path().join(&name), b"x").unwrap();
            if verified {
                std::fs::write(tmp.path().join(format!("{name}.verified")), "a".repeat(64)).unwrap();
            }
        }
        // 别的组件的包不算
        std::fs::write(tmp.path().join("meeting-asr-9.0.0-any.tar.gz"), b"x").unwrap();
        std::fs::write(tmp.path().join("meeting-asr-9.0.0-any.tar.gz.verified"), "a".repeat(64)).unwrap();
        let (p, v) = ready_pack(tmp.path()).unwrap();
        assert_eq!(v, "1.2.0");
        assert!(p.ends_with("embed-model-1.2.0-any.tar.gz"));
    }
}
