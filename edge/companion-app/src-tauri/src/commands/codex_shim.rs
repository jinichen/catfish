//! `~/.hermes` 下那个把 codex 指向 Hermes venv 的 shim 的装 / 卸。
//!
//! 2026-08-15 从 codex_backend.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! unix / 非 unix 两套实现整组搬过来了 —— 非 unix 那两个是空壳, 但必须跟着,
//! 否则 Windows 上编译不过。

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

use super::codex_probe::{home_dir, hermes_python};

#[cfg(unix)]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ShimMarker {
    path: String,
    target: String,
}

/// Hermes 的 launchd PATH 稳定包含它自己的 venv/bin。GUI 应用却经常
/// 看不到 Homebrew/npm/ChatGPT.app 里的 Codex，因此在该目录创建一个
/// 可追踪的 symlink。绝不覆盖现有文件。
#[cfg(unix)]
pub(crate) fn ensure_hermes_codex_shim(codex_path: &Path) -> Result<Option<PathBuf>, String> {
    use std::os::unix::fs::symlink;

    let python = hermes_python()?;
    let bin_dir = python
        .parent()
        .ok_or_else(|| "Hermes venv 路径异常".to_string())?;
    let shim = bin_dir.join("codex");
    if shim.exists() || shim.symlink_metadata().is_ok() {
        return Ok(None);
    }
    symlink(codex_path, &shim).map_err(|error| format!("创建 Codex 兼容链接失败: {error}"))?;

    let catfish_dir = home_dir()?.join(".catfish");
    std::fs::create_dir_all(&catfish_dir)
        .map_err(|error| format!("创建 Catfish 状态目录失败: {error}"))?;
    let marker_path = catfish_dir.join("codex-backend-shim.json");
    let marker = ShimMarker {
        path: shim.to_string_lossy().to_string(),
        target: codex_path.to_string_lossy().to_string(),
    };
    let bytes = serde_json::to_vec_pretty(&marker).map_err(|error| error.to_string())?;
    if let Err(error) = std::fs::write(&marker_path, bytes) {
        let _ = std::fs::remove_file(&shim);
        return Err(format!("记录 Codex 兼容链接失败: {error}"));
    }
    Ok(Some(shim))
}

#[cfg(not(unix))]
pub(crate) fn ensure_hermes_codex_shim(_codex_path: &Path) -> Result<Option<PathBuf>, String> {
    Ok(None)
}

#[cfg(unix)]
pub(crate) fn remove_hermes_codex_shim() {
    let Ok(home) = home_dir() else {
        return;
    };
    let marker_path = home.join(".catfish/codex-backend-shim.json");
    let Ok(bytes) = std::fs::read(&marker_path) else {
        return;
    };
    let Ok(marker) = serde_json::from_slice::<ShimMarker>(&bytes) else {
        return;
    };
    let path = PathBuf::from(&marker.path);
    let target = PathBuf::from(&marker.target);
    let points_to_managed_target = std::fs::read_link(&path)
        .map(|value| value == target)
        .unwrap_or(false);
    if points_to_managed_target {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(marker_path);
    }
}

#[cfg(not(unix))]
pub(crate) fn remove_hermes_codex_shim() {}
