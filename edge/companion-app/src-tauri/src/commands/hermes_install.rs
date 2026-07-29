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

/// 解析 `.catfish-hermes-version` 的内容, 取出 commit SHA。
///
/// 格式契约 —— **写方和读方在两个不同的文件里**:
///   写: `scripts/build-mac-resources.sh`
///       `printf '%s\n%s\n' "$ACTUAL_DESC" "$ACTUAL_SHA"`   ← 第 1 行 tag, 第 2 行 SHA
///   读: 这里
///
/// 顺序写反的后果很阴: 读到的"SHA"其实是 tag, 长度不是 40 → 返 None →
/// 上层当成"版本未知"→ **每次启动都重装一遍 hermes**。所以这个格式必须有测试
/// 钉着, 不能只靠两边注释对齐。
fn parse_version_file(s: &str) -> Option<String> {
    s.lines()
        .map(str::trim)
        .find(|l| l.len() == 40 && l.chars().all(|c| c.is_ascii_hexdigit()))
        .map(str::to_string)
}

/// 读**已装的** hermes 是哪个 commit。读不出来返 None。
///
/// 两个来源, 对应两条安装路径:
///   - `.catfish-hermes-version` —— 离线包路径写的 (打包时记进去)。离线安装是
///     `cp -R` + `git init`, 没有真实 git 历史, 所以必须靠这个文件。
///   - `git rev-parse HEAD` —— 联网安装路径。install.sh 真 clone, 有完整历史。
fn installed_hermes_commit() -> Option<String> {
    let home = crate::util::paths::home_env().ok()?;
    let dir = Path::new(&home).join(".hermes").join("hermes-agent");

    // 1) 打包时记进去的版本文件
    if let Ok(s) = std::fs::read_to_string(dir.join(".catfish-hermes-version")) {
        if let Some(sha) = parse_version_file(&s) {
            return Some(sha);
        }
    }

    // 2) 真 git 仓库
    let out = Command::new("git")
        .arg("-C")
        .arg(&dir)
        .arg("rev-parse")
        .arg("HEAD")
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let sha = String::from_utf8_lossy(&out.stdout).trim().to_string();
    (sha.len() == 40).then_some(sha)
}

/// hermes-agent 钉死的 commit —— 联网安装时传给 install.sh 的 `--commit`。
///
/// 编译期从 `.hermes-git-commit` 读, 跟打包脚本用同一个文件, 不会两处不一致。
///
/// **为什么必须钉死**: install.sh 默认装 main 分支。catfish 的 19 个 monkey-patch
/// patch 的是 hermes 内部函数, 上游一改就加载失败, 而失败是 silent skip ——
/// 界面正常, 只是多租户 / RBAC / 审批 / picker 联动悄悄没了。
///
/// 升级流程: 改 `.hermes-git-commit` + `.hermes-git-tag` → 在**干净机器**上装一遍 →
/// 确认 `~/.hermes/logs/` 里 P15/P20/P21/P23 等 patch 都有成功日志、没有 skip。
/// 不做这一步就升级, 等于把没验证过的组合发给 500 个员工。
/// 注: 这里不在 const 里 trim —— `str::trim_ascii_end` 的 const 版本要 Rust 1.80,
/// 而本 crate 声明的 MSRV 是 1.77。用取值函数在运行时 trim, 不抬 MSRV。
const HERMES_PINNED_COMMIT_RAW: &str = include_str!("../../../.hermes-git-commit");
const HERMES_PINNED_TAG_RAW: &str = include_str!("../../../.hermes-git-tag");

fn hermes_pinned_commit() -> &'static str {
    HERMES_PINNED_COMMIT_RAW.trim()
}

fn hermes_pinned_tag() -> &'static str {
    HERMES_PINNED_TAG_RAW.trim()
}

/// 离线运行时的四个大包 —— 它们**不再打进 .app**。
///
/// ── 为什么挪出去 (P3.5.85 · 7/29) ────────────────────────────────────
///
/// Apple 公证会**递归解开资源里的归档**并检查里面的 Mach-O。这四个包里装着
/// 上千个二进制 (cpython 的扩展模块、hermes venv 的 .so、node 原生模块、
/// 以及一整个 Google Chrome for Testing.app), 其中大部分只有 ad-hoc 签名,
/// 公证一律判 Invalid。
///
/// 要让它们过, 得把每个归档解开、按由内到外的顺序逐个重签、再原样打回去 ——
/// 而且**每次重新生成资源都要全部重签一遍**。chromium 那个尤其麻烦: 它是
/// 完整的 app bundle (1362 个条目, 含 Framework 和 Helper 子包), 重签等于
/// 用我们的证书为 Google 的二进制背书。
///
/// 所以改成: `.app` 里只留 install.sh 和 uv (两个裸文件, 签起来是分钟级),
/// 这四个包有就用 (离线场景放 ~/.catfish/runtime/), 没有就让 install.sh
/// 联网装。公证扫描范围缩到极小, 一次就过。
const RUNTIME_ARCHIVES: [&str; 4] = [
    "cpython-3.11.15-embed.tar.gz",
    "hermes-agent-bundle.tar.gz",
    "node-embed.tar.gz",
    "chromium-embed.tar.gz",
];

/// 找到一个"齐活"的运行时目录, 按优先级依次尝试。
///
/// 1. `.app` 内的 `resources/mac/` —— 老形态。仍然支持: 开发机和历史 dmg 都是
///    这个布局, 不能因为改了打包方式就让老版本装不上。
/// 2. `~/.catfish/runtime/` —— 新形态。由 Companion 首次启动时下载解压, 或者
///    离线场景下由 IT 手工放进去。
///
/// 判据是 **install.sh 和 uv 在不在**, 不看那四个大包 —— 它们是可选的:
/// install.sh 打过 offline patch, 缺哪个就 fallback 到联网装哪个。这样"公网
/// 能通但没预下载"的机器也能装, 只是慢一点。
fn resolve_runtime_dir(resource_dir: &Path) -> Result<std::path::PathBuf> {
    let in_bundle = resource_dir.join("resources").join("mac");
    let external = crate::util::paths::home_env()
        .ok()
        .map(|h| std::path::PathBuf::from(h).join(".catfish").join("runtime"));

    let mut tried: Vec<String> = Vec::new();
    for cand in [Some(in_bundle), external].into_iter().flatten() {
        let sh = cand.join("install.sh");
        let uv = cand.join("uv");
        let ok = std::fs::metadata(&sh).map(|m| m.len() > 1024).unwrap_or(false)
            && std::fs::metadata(&uv).map(|m| m.len() > 1024).unwrap_or(false);
        if ok {
            let n = RUNTIME_ARCHIVES
                .iter()
                .filter(|f| {
                    std::fs::metadata(cand.join(f)).map(|m| m.len() > 1024).unwrap_or(false)
                })
                .count();
            log::info!(
                "[runtime] 用 {} · 四个运行时包到位 {}/4{}",
                cand.display(),
                n,
                if n == 4 { "" } else { " (缺的会联网装, 慢但不挂)" }
            );
            return Ok(cand);
        }
        tried.push(cand.display().to_string());
    }

    anyhow::bail!(
        "找不到运行时目录 (install.sh + uv). 已尝试:\n  {}\n\
         解决: 让 Companion 联网自动下载, 或把运行时包解压到 ~/.catfish/runtime/",
        tried.join("\n  ")
    )
}

/// macOS 首启 · 装 hermes-agent 到 ~/.hermes/.
///
/// # Args
///
/// - `resource_dir`: tauri App resources 目录 (含 `resources/mac/install.sh` + `uv`)
///
/// # Returns
///
/// - Ok(()) : 装机成功 (或已装, no-op)
/// - Err   : 找不到 install.sh / uv, 或 install.sh 执行失败
///
/// P3.5.85/87 起四个运行时大包不再随 `.app` 分发: 有就用 (离线), 没有就让
/// install.sh 联网装, 而联网装时 **commit 是钉死的** (见 `hermes_pinned_commit`)。
pub fn ensure_hermes_installed(resource_dir: &Path) -> Result<()> {
    // P3.5.87 (7/29): 已装也要**核对版本**, 不一致就重装对齐。
    //
    // 原来这里只问"装了没" —— 装了就返回。于是员工机上留着旧 Companion 装的
    // hermes, 升级 Companion 之后**什么都不会发生**: 新客户端 + 旧 hermes,
    // 而 catfish 的 19 个 monkey-patch 是按特定 hermes 版本写的, 对不上就
    // warning + silent skip —— 多租户 header / RBAC / 审批 / picker 联动全部
    // 悄悄不工作, 聊天和界面一切正常, 没有任何人会发现。
    //
    // 版本读不出来 (老版本装的, 没有版本文件也没有 git 历史) 一律当成不一致:
    // 不知道装的是什么 = 没验证过, 该对齐。代价是这些机器升级时会重装一次
    // hermes, 一次性的。
    if hermes_agent_installed() {
        let want = hermes_pinned_commit();
        match installed_hermes_commit() {
            Some(got) if got == want => {
                log::info!(
                    "hermes-agent 已装且版本一致 ({} · {}), 跳过",
                    &want[..12.min(want.len())],
                    hermes_pinned_tag()
                );
                return Ok(());
            }
            Some(got) => {
                log::warn!(
                    "hermes-agent 版本不一致 · 已装 {} · 本客户端要 {} ({}) —— 重装对齐",
                    &got[..12.min(got.len())],
                    &want[..12.min(want.len())],
                    hermes_pinned_tag()
                );
            }
            None => {
                log::warn!(
                    "hermes-agent 已装但读不出版本 (老版本装的) · 本客户端要 {} ({}) —— 重装对齐",
                    &want[..12.min(want.len())],
                    hermes_pinned_tag()
                );
            }
        }
    } else {
        log::info!("hermes-agent 未装, 开始安装 ...");
    }

    let mac_res = resolve_runtime_dir(resource_dir)?;
    let install_sh = mac_res.join("install.sh");
    let uv_bin = mac_res.join("uv");
    let py_tar = mac_res.join("cpython-3.11.15-embed.tar.gz");
    let hermes_tar = mac_res.join("hermes-agent-bundle.tar.gz");
    // BL-MAC-INSTALL-NODE-BUNDLE + CHROMIUM-BUNDLE (7/17): 用户拍板 · 打 Node + chromium 进 dmg,
    // 达华 3 台 POC mac 无 VPN, 100% offline. 若这 2 个 tar 是空/缺 · installer 会 log_warn 但不挂
    // (patch_install_sh_offline.py PATCH_6/7 里 fallback 老逻辑).
    let node_tar = mac_res.join("node-embed.tar.gz");
    let chromium_tar = mac_res.join("chromium-embed.tar.gz");

    // sanity: install.sh + uv 必须在 (resolve_runtime_dir 已经查过, 这里是二次确认);
    // 四个大包 P3.5.85 起改成**可选** —— 缺哪个 install.sh 就联网装哪个
    // (patch_install_sh_offline.py 的 fallback 路径), 慢但不挂。
    for p in [&install_sh, &uv_bin] {
        let meta = std::fs::metadata(p)
            .with_context(|| format!("bundle 缺文件 (resources/mac/): {}", p.display()))?;
        if meta.len() < 1024 {
            anyhow::bail!(
                "bundle artifact 太小 (< 1KB, 可能 build 时 placeholder 未替换): {}",
                p.display()
            );
        }
    }
    // P3.5.85: 四个大包全部改成可选后, 这两个也要显式判在不在 —— 尤其
    // hermes_tar: 下面那段无条件 `tar -xzf` 它, 缺了会挂在一个只说
    // "tar 解压挂" 的错误上, 现场根本看不出是**包没下下来**。
    let has_py_tar = std::fs::metadata(&py_tar).map(|m| m.len() > 1024).unwrap_or(false);
    let has_hermes_tar = std::fs::metadata(&hermes_tar).map(|m| m.len() > 1024).unwrap_or(false);
    // Node + chromium 是可选 · 空 placeholder 时 install.sh 会 fallback 老代码 (需公网)
    let has_node_tar = std::fs::metadata(&node_tar).map(|m| m.len() > 1024).unwrap_or(false);
    let has_chromium_tar = std::fs::metadata(&chromium_tar).map(|m| m.len() > 1024).unwrap_or(false);
    if !has_hermes_tar {
        // 不给 --offline-source-dir 时, patched install.sh 的
        // `_catfish_offline_done=false` 分支会接着走原来的 git clone
        // (SSH 优先, 回退 HTTPS) —— 所以这里不是硬失败, 只是慢。
        //
        // 代价说清楚: clone 60MB 源码 + npm ci 装 1296 个包, 全程要公网,
        // 而且装出来的是**未瘦身**的 node_modules (我们的构建流程会从 1.0G
        // 删到 579M, 那步只在打包时做)。网络差的机器可能十几分钟起步。
        log::warn!(
            "[runtime] 没有 hermes-agent-bundle.tar.gz · install.sh 会从 GitHub clone \
             并 npm ci (需公网, 慢, node_modules 不瘦身)"
        );
    }
    log::info!(
        "offline bundle 状态: node_tar={} ({}B), chromium_tar={} ({}B)",
        has_node_tar,
        std::fs::metadata(&node_tar).map(|m| m.len()).unwrap_or(0),
        has_chromium_tar,
        std::fs::metadata(&chromium_tar).map(|m| m.len()).unwrap_or(0)
    );

    // 解压 hermes-agent-bundle.tar.gz 到 tmp, install.sh 用 --offline-source-dir 拿。
    // P3.5.85: 包不在时整段跳过 —— 不传 --offline-source-dir, install.sh 自己
    // 去 clone (见上面 has_hermes_tar 处的说明)。
    let hermes_extract = if has_hermes_tar {
        let tmp_dir = std::env::temp_dir().join("catfish-hermes-install");
        std::fs::create_dir_all(&tmp_dir).context("建 tmp dir")?;
        let dir = tmp_dir.join("hermes-src");
        if dir.exists() {
            std::fs::remove_dir_all(&dir).context("清老 hermes-src")?;
        }
        std::fs::create_dir(&dir).context("建 hermes-src")?;

        log::info!("解压 hermes bundle 到 {}", dir.display());
        let status = Command::new("tar")
            .arg("-xzf")
            .arg(&hermes_tar)
            .arg("-C")
            .arg(&dir)
            .arg("--strip-components=1")   // tar 里第一层是 hermes-agent/, strip 掉
            .status()
            .context("spawn tar")?;
        if !status.success() {
            anyhow::bail!("tar 解压 hermes-agent-bundle.tar.gz 挂: {}", status);
        }
        Some(dir)
    } else {
        None
    };

    // 跑 install.sh (offline mode) · BL-MAC-INSTALL-NODE/CHROMIUM-BUNDLE (7/17):
    // 移除 --skip-browser, 让 install.sh 走 Playwright chromium 装机段.
    // patch_install_sh_offline.py PATCH_7 会短路整个 install_node_deps, 用 offline tar 解压 chromium.
    log::info!("跑 install.sh (offline mode)...");
    let mut cmd = Command::new("bash");
    cmd.arg(&install_sh)
        .arg("--no-venv")            // 用 uv 装 venv, 不用 bash venv
        .arg("--skip-setup")          // 跳 interactive setup wizard
        .arg("--offline-uv")
        .arg(&uv_bin);
    if let Some(ref dir) = hermes_extract {
        cmd.arg("--offline-source-dir").arg(dir);
    } else {
        // P3.5.87 (7/29): 走联网安装时**必须钉死 commit**。
        //
        // install.sh 默认装 main 分支 —— 那意味着员工装到的是"装机当天上游
        // 是什么样"。catfish 的 19 个 monkey-patch 是 patch hermes 内部函数的
        // (gateway.run._resolve_gateway_model 这类), 上游改个签名就加载失败,
        // 而失败方式是 warning + silent skip: 多租户 header、picker 联动、
        // RBAC、审批全部悄悄不工作, 界面上一切正常。
        //
        // 所以联网装不等于放任版本。这个 SHA 是**实际验证过的那份代码**
        // (v2026.7.20, 开发机上 P15/P20 等 patch 有成功日志为证)。
        // 升级 hermes = 改这个文件 + 在干净机器上重验 patch 加载, 不是自动跟随。
        cmd.arg("--commit").arg(hermes_pinned_commit());
        log::info!(
            "[runtime] 联网安装 hermes · 钉死 commit {} ({})",
            hermes_pinned_commit(),
            hermes_pinned_tag()
        );
    }
    // P3.5.85: python 包也改成可选 —— 缺了就不传这个参数, install.sh 走
    // 联网装 python 的老路径。以前是无条件传, 包不在时传进去一个不存在的
    // 路径, install.sh 那边报的是"解压失败", 看不出是**根本没这个文件**。
    if has_py_tar {
        cmd.arg("--offline-python-tar").arg(&py_tar);
    } else {
        log::warn!("[runtime] 没有 cpython 包 · install.sh 会联网装 python (需公网, 慢)");
    }
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

    // 清 tmp (成功后)。P3.5.85: 只有走离线包路径才有临时解压目录;
    // 联网安装那条路没解压任何东西, 没什么可清。
    if let Some(dir) = hermes_extract {
        // 删它的父目录 (catfish-hermes-install), 连壳一起清掉
        let _ = std::fs::remove_dir_all(dir.parent().unwrap_or(&dir));
    }

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

/// P3.5.87 (7/29): hermes 版本钉死的回归测试。
///
/// ── 在防什么 ────────────────────────────────────────────────────────
///
/// 改成从公网装之后, 版本控制权只剩 `--commit` 这一个参数。这个文件写错、
/// 写空、或者哪次重构把它丢了, 后果是 install.sh 回落到 **main 分支** ——
/// 员工装到的变成"装机当天上游是什么样"。
///
/// 而 catfish 的 19 个 monkey-patch 是 patch hermes 内部函数的, 上游一改就
/// 加载失败, 失败方式是 warning + silent skip: 多租户 header / RBAC / 审批 /
/// picker 联动全部悄悄不工作, 界面和聊天一切正常, 现场根本发现不了。
///
/// 所以这几条不是形式检查 —— 它们守的是"版本必须是被验证过的那一个"。
#[cfg(test)]
mod tests_hermes_pin {
    use super::{hermes_pinned_commit, hermes_pinned_tag};

    #[test]
    fn commit_is_full_sha() {
        let c = hermes_pinned_commit();
        assert_eq!(c.len(), 40, "必须是完整 40 位 SHA, 收到: {c:?}");
        assert!(
            c.chars().all(|ch| ch.is_ascii_hexdigit()),
            "含非十六进制字符: {c:?}"
        );
        // 短 SHA 也能用, 但完整的才不会有歧义 —— 上游仓库大了之后短 SHA 可能撞
    }

    #[test]
    fn tag_looks_like_a_version() {
        let t = hermes_pinned_tag();
        assert!(t.starts_with('v'), "tag 应以 v 开头, 收到: {t:?}");
        assert!(t.len() > 1);
    }

    #[test]
    fn no_stray_whitespace() {
        // 文件末尾必然有换行, 取值函数要负责 trim —— 不 trim 的话
        // `--commit "3ef6...\n"` 传给 git, 报的是含糊的 "unknown revision"
        for v in [hermes_pinned_commit(), hermes_pinned_tag()] {
            assert_eq!(v, v.trim(), "取值没 trim: {v:?}");
            assert!(!v.contains('\n'), "含换行: {v:?}");
        }
    }

    #[test]
    fn version_file_parsed_regardless_of_line_order() {
        // 写方 (build-mac-resources.sh) 是 tag 在前、SHA 在后。
        let sha = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a";
        let normal = format!("v2026.7.20\n{sha}\n");
        assert_eq!(super::parse_version_file(&normal).as_deref(), Some(sha));

        // 顺序写反也要能读出来 —— 不然那次改动会变成"每次启动重装 hermes",
        // 而且没有任何报错, 只是每次开 Companion 都卡几分钟。
        let swapped = format!("{sha}\nv2026.7.20\n");
        assert_eq!(super::parse_version_file(&swapped).as_deref(), Some(sha));
    }

    #[test]
    fn version_file_rejects_non_sha() {
        // 只有 tag 没有 SHA → None (上层据此判"版本未知", 去对齐)
        assert!(super::parse_version_file("v2026.7.20\n").is_none());
        // 短 SHA 不收 —— 长度不对说明格式变了, 宁可当未知也不要猜
        assert!(super::parse_version_file("3ef6bbd\n").is_none());
        // 40 位但含非十六进制
        assert!(super::parse_version_file(&"z".repeat(40)).is_none());
        assert!(super::parse_version_file("").is_none());
    }
}
