//! hermes-agent 首次装机 (BL-CATFISH-MAC-OFFLINE-INSTALL 7/15).
//!
//! # 为啥独立 module (不塞 hermes_plugin.rs)
//!
//! `hermes_plugin.rs` 装 **catfish-xcatfish-user plugin** (9 py + 1 yaml, ~189KB
//! `include_str!` baked 进 binary), 目标是 `~/.hermes/plugins/`. 假设 hermes-agent
//! 本体**已装**.
//!
//! 这个 module 补齐前置 · 装 **hermes-agent 本体** (~/.hermes/hermes-agent/), 走
//! offline install.sh + tauri bundle 里的 4 artifacts (install.sh + uv +
//! cpython.tar.gz + hermes-agent-bundle.tar.gz).
//!
//! # 平台分工
//!
//! - **Windows**: msi CustomAction (`wix/catfish-postinstall.wxs`) 装机时跑
//!   install.ps1 -OfflineSourceDir ... -OfflineUvExe ... -OfflinePythonZip ...
//!   → Companion Rust **不参与**, msi 装完 hermes 已就位.
//! - **macOS**: dmg 只 rsync app, 无 CustomAction. Companion 首启检测 hermes 未
//!   装 → 沿用 tauri App resources 里的 4 artifacts, spawn install.sh --offline-*
//!   → 员工看 dialog 进度.
//! - **Linux**: 同 mac 路径 (若未来支持).
//!
//! # 触发点
//!
//! `lib.rs` setup hook (macOS only branch) 调 `ensure_hermes_installed()`:
//! - 已装 (~/.hermes/hermes-agent/pyproject.toml 存在) → no-op
//! - 未装 → spawn install.sh (block wait), 装完 log_info, 失败 emit dialog

use anyhow::{Context, Result};
use std::path::Path;
use std::process::Command;

/// hermes 是否已装 (检测 ~/.hermes/hermes-agent/pyproject.toml 存在).
pub fn hermes_agent_installed() -> bool {
    let Ok(home) = crate::util::paths::home_env() else {
        return false;
    };
    Path::new(&home)
        .join(".hermes/hermes-agent/pyproject.toml")
        .exists()
}

/// macOS 首启 · 用 dmg bundle 里的 4 artifacts 装 hermes-agent 到 ~/.hermes/.
///
/// # Args
///
/// - `resource_dir`: tauri App resources 目录 (含 `resources/mac/*` 4 artifacts)
///
/// # Returns
///
/// - Ok(()) : 装机成功 (或已装, no-op)
/// - Err   : install.sh spawn 挂 / bundle artifacts 缺 / hermes install 挂
pub fn ensure_hermes_installed(resource_dir: &Path) -> Result<()> {
    if hermes_agent_installed() {
        log::info!("hermes-agent 已装, 跳过 offline install");
        return Ok(());
    }

    log::info!("hermes-agent 未装, 开始 offline install ...");

    let mac_res = resource_dir.join("resources").join("mac");
    let install_sh = mac_res.join("install.sh");
    let uv_bin = mac_res.join("uv");
    let py_tar = mac_res.join("cpython-3.11.15-embed.tar.gz");
    let hermes_tar = mac_res.join("hermes-agent-bundle.tar.gz");

    // sanity: 4 artifacts 都要 non-empty 才启动 install
    for p in [&install_sh, &uv_bin, &py_tar, &hermes_tar] {
        let meta = std::fs::metadata(p)
            .with_context(|| format!("bundle 缺文件 (resources/mac/): {}", p.display()))?;
        if meta.len() < 1024 {
            anyhow::bail!(
                "bundle artifact 太小 (< 1KB, 可能 build 时 placeholder 未替换): {}",
                p.display()
            );
        }
    }

    // 解压 hermes-agent-bundle.tar.gz 到 tmp, install.sh 用 --offline-source-dir 拿
    let tmp_dir = std::env::temp_dir().join("catfish-hermes-install");
    std::fs::create_dir_all(&tmp_dir).context("建 tmp dir")?;
    let hermes_extract = tmp_dir.join("hermes-src");
    if hermes_extract.exists() {
        std::fs::remove_dir_all(&hermes_extract).context("清老 hermes-src")?;
    }
    std::fs::create_dir(&hermes_extract).context("建 hermes-src")?;

    log::info!("解压 hermes bundle 到 {}", hermes_extract.display());
    let status = Command::new("tar")
        .arg("-xzf")
        .arg(&hermes_tar)
        .arg("-C")
        .arg(&hermes_extract)
        .arg("--strip-components=1")   // tar 里第一层是 hermes-agent/, strip 掉
        .status()
        .context("spawn tar")?;
    if !status.success() {
        anyhow::bail!("tar 解压 hermes-agent-bundle.tar.gz 挂: {}", status);
    }

    // 跑 install.sh --offline-source-dir ... --offline-uv ... --offline-python-tar ...
    log::info!("跑 install.sh (offline mode)...");
    let status = Command::new("bash")
        .arg(&install_sh)
        .arg("--no-venv")           // 用 uv 装 venv, 不用 bash venv
        .arg("--skip-setup")         // 跳 interactive setup wizard
        .arg("--skip-browser")       // 跳 Playwright/Chromium (Companion 走 catfish-in-chrome 或 dev tools)
        .arg("--offline-source-dir")
        .arg(&hermes_extract)
        .arg("--offline-uv")
        .arg(&uv_bin)
        .arg("--offline-python-tar")
        .arg(&py_tar)
        .status()
        .context("spawn install.sh")?;

    if !status.success() {
        anyhow::bail!("install.sh 挂: {}", status);
    }

    log::info!("hermes-agent 装到 ~/.hermes/hermes-agent/ 成功");

    // 清 tmp (成功后)
    let _ = std::fs::remove_dir_all(&tmp_dir);

    Ok(())
}

/// Public tauri command · 员工 dashboard 手工重装用 (若首启 auto install 挂).
#[tauri::command]
pub fn reinstall_hermes_agent(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("拿不到 resource_dir: {e}"))?;
    ensure_hermes_installed(&resource_dir).map_err(|e| format!("装 hermes 挂: {e:#}"))
}
