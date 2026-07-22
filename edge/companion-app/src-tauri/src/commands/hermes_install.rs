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
use std::path::{Path, PathBuf};
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
    // BL-MAC-INSTALL-NODE-BUNDLE + CHROMIUM-BUNDLE (7/17): 用户拍板 · 打 Node + chromium 进 dmg,
    // 达华 3 台 POC mac 无 VPN, 100% offline. 若这 2 个 tar 是空/缺 · installer 会 log_warn 但不挂
    // (patch_install_sh_offline.py PATCH_6/7 里 fallback 老逻辑).
    let node_tar = mac_res.join("node-embed.tar.gz");
    let chromium_tar = mac_res.join("chromium-embed.tar.gz");

    // sanity: 4 核心 artifacts 都要 non-empty 才启动 install (Node + chromium 是可选 · 空也允许)
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
    // Node + chromium 是可选 · 空 placeholder 时 install.sh 会 fallback 老代码 (需公网)
    let has_node_tar = std::fs::metadata(&node_tar).map(|m| m.len() > 1024).unwrap_or(false);
    let has_chromium_tar = std::fs::metadata(&chromium_tar).map(|m| m.len() > 1024).unwrap_or(false);
    log::info!(
        "offline bundle 状态: node_tar={} ({}B), chromium_tar={} ({}B)",
        has_node_tar,
        std::fs::metadata(&node_tar).map(|m| m.len()).unwrap_or(0),
        has_chromium_tar,
        std::fs::metadata(&chromium_tar).map(|m| m.len()).unwrap_or(0)
    );

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

    // 跑 install.sh (offline mode) · BL-MAC-INSTALL-NODE/CHROMIUM-BUNDLE (7/17):
    // 移除 --skip-browser, 让 install.sh 走 Playwright chromium 装机段.
    // patch_install_sh_offline.py PATCH_7 会短路整个 install_node_deps, 用 offline tar 解压 chromium.
    log::info!("跑 install.sh (offline mode)...");
    let mut cmd = Command::new("bash");
    cmd.arg(&install_sh)
        .arg("--no-venv")            // 用 uv 装 venv, 不用 bash venv
        .arg("--skip-setup")          // 跳 interactive setup wizard
        .arg("--offline-source-dir")
        .arg(&hermes_extract)
        .arg("--offline-uv")
        .arg(&uv_bin)
        .arg("--offline-python-tar")
        .arg(&py_tar);
    // BL-MAC-INSTALL-NODE-BUNDLE (7/17): Node.js darwin binary tar 传 install.sh 解压到 $HERMES_HOME/node/
    if has_node_tar {
        cmd.arg("--offline-node-tar").arg(&node_tar);
    }
    // BL-MAC-INSTALL-CHROMIUM-BUNDLE (7/17): Playwright chromium tar 传 install.sh 解压到 ~/Library/Caches/ms-playwright/
    if has_chromium_tar {
        cmd.arg("--offline-chromium-tar").arg(&chromium_tar);
    }
    let status = cmd.status().context("spawn install.sh")?;

    if !status.success() {
        anyhow::bail!("install.sh 挂: {}", status);
    }

    log::info!("hermes-agent 装到 ~/.hermes/hermes-agent/ 成功");

    // BL-CATFISH-EMAIL-LINK (7/18 鸿波 catch Task #8): hermes-agent venv 里已 pip
    // 装 catfish-email (pyproject.toml 拉的), 但 Companion email.rs:23 找
    // ~/.local/bin/catfish-email 软链. install.sh 不建这软链, 需 hermes install 完
    // Companion 端补建 · 员工零手工. 缺则 UI 邮件 tab 挂 · 提示 "CLI 没装 · 装:
    // bash install.sh". 员工无源码 · 无法自装.
    if let Err(e) = link_catfish_email_bin() {
        // 失败 silent · 不阻塞 hermes install 成功流. 邮件 tab 挂 UI 会提示.
        log::warn!("[catfish-email-link] 建软链挂 (邮件 tab 会挂): {e:#}");
    }

    // 清 tmp (成功后)
    let _ = std::fs::remove_dir_all(&tmp_dir);

    Ok(())
}

/// BL-CATFISH-EMAIL-LINK (7/18): 建 ~/.local/bin/catfish-email → hermes-agent venv 的软链.
///
/// hermes-agent bundle build 时 pyproject.toml 拉的 catfish-email 装到
/// ~/.hermes/hermes-agent/venv/bin/catfish-email · 但 · Companion email.rs 找的是
/// ~/.local/bin/catfish-email. install.sh 不建 · 我们补建 · 员工零手工.
///
/// 幂等: 软链已存在 (unlink) 或指向对再建都 OK. macOS/Linux 用 std::os::unix::fs::symlink.
fn link_catfish_email_bin() -> Result<()> {
    let home = crate::util::paths::home_env().context("拿 HOME 挂")?;
    let venv_bin = PathBuf::from(&home).join(".hermes/hermes-agent/venv/bin/catfish-email");
    if !venv_bin.exists() {
        log::warn!(
            "[catfish-email-link] {} 不存在 · 跳过软链 (可能 hermes-agent-bundle \
             pyproject 未含 catfish-email 依赖 · 或 install 挂)",
            venv_bin.display()
        );
        return Ok(());
    }
    let local_bin_dir = PathBuf::from(&home).join(".local/bin");
    std::fs::create_dir_all(&local_bin_dir)
        .context(format!("建 {} 挂", local_bin_dir.display()))?;
    let link = local_bin_dir.join("catfish-email");
    // 幂等: 若已存在 (老软链或真文件) · 先 remove
    if link.symlink_metadata().is_ok() {
        let _ = std::fs::remove_file(&link);
    }
    #[cfg(unix)]
    std::os::unix::fs::symlink(&venv_bin, &link).context(format!(
        "ln -s {} {} 挂",
        venv_bin.display(),
        link.display()
    ))?;
    #[cfg(not(unix))]
    {
        // Windows 走 msi install.ps1 · 不该走到这; 保底 warn.
        log::warn!("[catfish-email-link] 非 unix 平台 · 跳过 symlink (Windows msi 自建)");
    }
    log::info!(
        "[catfish-email-link] ✓ {} → {}",
        link.display(),
        venv_bin.display()
    );
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
