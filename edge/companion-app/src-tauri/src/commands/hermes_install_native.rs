//! hermes v0.22 起 Companion 自己装 hermes 的各步 (macOS / Windows 共用; 10/14)。
//!
//! 原来是调上游 install.sh 的 `--stage xxx` + 我们打的离线补丁。v0.22.0 上游安装器整个重写成
//! "拿 uv + Python → 交给 hermes pm 联网按 uv.lock 装", 只认 Python 3.14, 补丁锚点全部对不上
//! (docs/HERMES-022-UPGRADE-ASSESSMENT.md)。现在每一步都在这里, 只用安装包里带的东西, 不碰网络:
//!
//! | 步骤 | 做什么 | 原来 install.sh 的哪一段 |
//! |---|---|---|
//! | `ensure_embedded_python` | 解 cpython-3.14.7-embed 到 `$HERMES_HOME/python-3.14` | check_python 的离线补丁 |
//! | `create_venv` | `uv venv <install>/venv --python <上一步>` | setup_venv |
//! | `install_python_deps` | wheelhouse 离线装 (钉版本 + sha256), 再 editable 装 hermes | install_deps |
//! | `install_chromium` | 解 chromium-embed 到 Playwright 默认目录 | node-deps 的离线补丁 |
//! | `prepare_config` | ~/.hermes 目录骨架 + .env / config.yaml 模板 | stage_config |
//! | `link_cli` | `~/.local/bin/hermes` 启动脚本 (仅 macOS / Linux; Windows 用 venv\Scripts\hermes.exe) | setup_path |
//! | `finish` | `.install_method` | complete |
//!
//! wheelhouse / catfish-requirements.txt / install-stamp.json (hermes_install_health::INSTALL_STAMP) 在打包时写进源码树
//! (scripts/build_hermes_wheelhouse.py、build-mac-resources.sh [5.5])。

use anyhow::{Context, Result};
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::services::catfish_paths::hermes_venv_python;

use super::hermes_install_artifacts::RuntimeArtifacts;
#[cfg(not(target_os = "windows"))]
use super::hermes_install_base::INSTALL_METHOD_MARKER;
use super::hermes_install_state::{remove_any, BootstrapPaths};

/// 安装包里钉的 Python 小版本 (cpython-3.14.7-embed)。上游 v0.22 的依赖只在 3.14 上装。
pub(crate) const PYTHON_MINOR: (u32, u32) = (3, 14);
/// 打包时写进源码树的两样东西 (见文件头)
pub(crate) const WHEELHOUSE_DIR: &str = "wheelhouse";
pub(crate) const REQUIREMENTS_FILE: &str = "catfish-requirements.txt";

fn run(mut command: Command, description: &str) -> Result<()> {
    let output = command.output().with_context(|| format!("启动 {description}"))?;
    if !output.status.success() {
        let tail = |b: &[u8]| {
            let s = String::from_utf8_lossy(b);
            let lines: Vec<&str> = s.lines().collect();
            lines[lines.len().saturating_sub(15)..].join("\n")
        };
        anyhow::bail!(
            "{description} 失败 ({})\n{}\n{}",
            output.status,
            tail(&output.stderr),
            tail(&output.stdout)
        );
    }
    Ok(())
}

/// uv 只看我们给的东西: 不读用户的 uv 配置, 不下 Python, 不联网。
fn uv(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths) -> Command {
    let mut command = crate::services::process::background_command(&artifacts.uv);
    #[cfg(not(target_os = "windows"))]
    command.env("HOME", &paths.home);
    #[cfg(target_os = "windows")]
    {
        // 9/26 现场: uv 缓存撞过 Windows error 4390 (硬链接 / 重解析点) —— 一律复制, 缓存放自己的目录
        command
            .env("UV_LINK_MODE", "copy")
            .env("UV_CACHE_DIR", paths.hermes_home.join(".catfish-uv-cache"));
    }
    command
        .env("UV_NO_CONFIG", "1")
        .env("UV_PYTHON_DOWNLOADS", "never")
        .env("UV_OFFLINE", "1")
        .env_remove("VIRTUAL_ENV")
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME");
    command
}

/// 10/14: 放 `python-3.14`, **不复用** 0.21 时代的 `$HERMES_HOME/python` (3.11)。
/// 会议转写组件包的 venv 是用那个 3.11 建的 (meeting_asr::install 取 hermes venv 的基础解释器),
/// 原地换成 3.14 会让已经装好的会议转写当场坏掉; 老目录留着, 由会议组件自己的升级处理。
pub(crate) fn embedded_python_root(paths: &BootstrapPaths) -> PathBuf {
    paths.hermes_home.join("python-3.14")
}

pub(crate) fn embedded_python(paths: &BootstrapPaths) -> PathBuf {
    python_in(&embedded_python_root(paths))
}

/// python-build-standalone 的布局: Windows 是根目录下 python.exe, 其它是 bin/python3
fn python_in(root: &Path) -> PathBuf {
    if cfg!(target_os = "windows") {
        root.join("python.exe")
    } else {
        root.join("bin").join("python3")
    }
}

fn python_is_wanted(python: &Path) -> bool {
    let check = format!(
        "import sys; raise SystemExit(0 if sys.version_info[:2] == ({}, {}) else 1)",
        PYTHON_MINOR.0, PYTHON_MINOR.1
    );
    crate::services::process::background_command(python)
        .arg("-c")
        .arg(check)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// `$HERMES_HOME/python-3.14` 是 3.14 就复用; 不是 (没有 / 坏了) 就解包里那份换上 (坏的挪成 `.broken-<时间>`)。
pub(crate) fn ensure_embedded_python(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths) -> Result<PathBuf> {
    let root = embedded_python_root(paths);
    let python = embedded_python(paths);
    if python_is_wanted(&python) {
        return Ok(python);
    }
    let tar = artifacts
        .python_tar
        .as_ref()
        .context("安装包里没有内嵌 Python (cpython-3.14.7-embed.tar.gz)")?;
    let stage = paths.hermes_home.join(format!(".catfish-python-stage-{}", std::process::id()));
    remove_any(&stage)?;
    std::fs::create_dir_all(&stage).with_context(|| format!("创建 {}", stage.display()))?;
    let mut untar = crate::services::process::background_command("tar");
    untar.arg("-xzf").arg(tar).arg("-C").arg(&stage).arg("--strip-components=1");
    if let Err(e) = untar_python(untar, &stage, paths) {
        let _ = remove_any(&stage);
        return Err(e);
    }
    if !python_is_wanted(&python_in(&stage)) {
        let _ = remove_any(&stage);
        anyhow::bail!("内嵌 Python 不是 {}.{}: {}", PYTHON_MINOR.0, PYTHON_MINOR.1, tar.display());
    }
    if root.exists() {
        let stamp = chrono::Utc::now().format("%Y%m%d-%H%M%S");
        let old = paths.hermes_home.join(format!("python-3.14.broken-{stamp}"));
        std::fs::rename(&root, &old).with_context(|| format!("挪开坏的 Python {}", root.display()))?;
        log::warn!("[hermes-install] {} 不可用, 挪到 {}", root.display(), old.display());
    }
    std::fs::rename(&stage, &root).with_context(|| format!("放置内嵌 Python {}", root.display()))?;
    Ok(python)
}

/// 解内嵌 Python。Windows 上安全软件只拦了 VC 运行库两个 DLL、而 System32 里有它们时继续 (10/21,
/// 见 hermes_python_dlls); System32 也没有就报一句说得清的; 别的失败原样报。
fn untar_python(mut untar: Command, stage: &Path, paths: &BootstrapPaths) -> Result<()> {
    let output = untar.output().context("启动 解压内嵌 Python")?;
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr);
    if cfg!(target_os = "windows") {
        if let Some(dlls) = super::hermes_python_dlls::blocked_runtime_dlls(&stderr) {
            let system32 = super::hermes_python_dlls::system32();
            if python_in(stage).is_file() && super::hermes_python_dlls::all_present_in(&system32, &dlls) {
                log::warn!(
                    "[hermes-install] {} 没写进内嵌 Python 目录 (多半被安全软件拦了), System32 里有, 用系统那份继续",
                    dlls.join(", ")
                );
                return Ok(());
            }
            anyhow::bail!(super::hermes_python_dlls::blocked_message(&dlls, &paths.hermes_home, &stderr));
        }
    }
    let tail: Vec<&str> = stderr.lines().collect();
    anyhow::bail!(
        "解压内嵌 Python 失败 ({})\n{}",
        output.status,
        tail[tail.len().saturating_sub(15)..].join("\n")
    )
}

pub(crate) fn create_venv(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths, python: &Path) -> Result<()> {
    let venv = paths.install_dir.join("venv");
    remove_any(&venv)?;
    let mut command = uv(artifacts, paths);
    command.arg("venv").arg("--quiet").arg(&venv).arg("--python").arg(python);
    run(command, "创建 Hermes Python 环境")
}

/// 只用源码树里的 wheelhouse 装, 版本和 sha256 都钉死 (--require-hashes)。
/// 修复路径 (hermes 健康但额外包缺) 也走这里 —— 同一份清单, 装过的包 uv 秒过。
pub(crate) fn install_python_deps(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths) -> Result<()> {
    let dir = &paths.install_dir;
    let wheelhouse = dir.join(WHEELHOUSE_DIR);
    let requirements = dir.join(REQUIREMENTS_FILE);
    if !wheelhouse.is_dir() || !requirements.is_file() {
        anyhow::bail!(
            "Hermes 源码里没有离线依赖 ({WHEELHOUSE_DIR}/ 或 {REQUIREMENTS_FILE}) —— 安装包不完整, \
             检查 build-mac-resources.sh [5.5]"
        );
    }
    let venv_python = hermes_venv_python(dir);
    install_requirements(artifacts, paths, &venv_python)?;
    let mut project = uv(artifacts, paths);
    project
        .arg("pip")
        .arg("install")
        .arg("--quiet")
        .arg("--python")
        .arg(&venv_python)
        .arg("--no-index")
        .arg("--find-links")
        .arg(&wheelhouse)
        .arg("--no-deps")
        .arg("-e")
        .arg(dir);
    run(project, "安装 Hermes 本体")?;
    let mut check = crate::services::process::background_command(&venv_python);
    check
        .arg("-c")
        .arg(format!("import hermes_cli.main; {}", super::hermes_install_artifacts::hermes_extra_import_check()));
    run(check, "验证 Hermes 和额外依赖可导入")
}

/// 把源码树里那份钉版本 + sha256 的依赖清单离线装进指定 Python 环境。
/// hermes 自己的 venv 和 hermes 另建的运行环境 (hermes_runtime_deps.rs) 共用。
pub(crate) fn install_requirements(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths, python: &Path) -> Result<()> {
    let dir = &paths.install_dir;
    let mut deps = uv(artifacts, paths);
    deps.arg("pip")
        .arg("install")
        .arg("--quiet")
        .arg("--python")
        .arg(python)
        .arg("--no-index")
        .arg("--find-links")
        .arg(dir.join(WHEELHOUSE_DIR))
        .arg("--require-hashes")
        .arg("--compile-bytecode") // 首次启动不用现编; jieba 那几句 SyntaxWarning 也只出现在这里
        .arg("-r")
        .arg(dir.join(REQUIREMENTS_FILE));
    run(deps, "安装 Hermes 依赖 (离线)")
}

/// Playwright 默认找浏览器的地方。跟 Python playwright 的 registry 一致
/// (macOS: ~/Library/Caches, Windows: %LOCALAPPDATA%, Linux: ~/.cache)。
fn playwright_browsers_dir(paths: &BootstrapPaths) -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        let _ = paths;
        std::env::var_os("LOCALAPPDATA").map(|d| PathBuf::from(d).join("ms-playwright"))
    }
    #[cfg(target_os = "macos")]
    {
        Some(paths.home.join("Library").join("Caches").join("ms-playwright"))
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        Some(paths.home.join(".cache").join("ms-playwright"))
    }
}

/// 解 chromium-embed (打包时那版 playwright 下的)。浏览器是附加功能: 失败只告警, 不拦装机。
pub(crate) fn install_chromium(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths) {
    let Some(tar) = artifacts.chromium_tar.as_ref() else {
        log::warn!("[hermes-install] 安装包里没有 chromium-embed.tar.gz, 鲶鱼浏览器工具不可用");
        return;
    };
    let Some(dest) = playwright_browsers_dir(paths) else {
        log::warn!("[hermes-install] 找不到 Playwright 浏览器目录 (LOCALAPPDATA 未设), 鲶鱼浏览器工具不可用");
        return;
    };
    let result = std::fs::create_dir_all(&dest)
        .with_context(|| format!("创建 {}", dest.display()))
        .and_then(|()| {
            let mut untar = crate::services::process::background_command("tar");
            untar.arg("-xzf").arg(tar).arg("-C").arg(&dest);
            run(untar, "解压 Chromium")
        });
    match result {
        Ok(()) => log::info!("[hermes-install] Chromium 解到 {}", dest.display()),
        Err(e) => log::warn!("[hermes-install] 解 Chromium 失败, 鲶鱼浏览器工具不可用: {e:#}"),
    }
}

/// 上游 stage_config 的同款: 目录骨架 + .env (600) + config.yaml 模板 (已有的一律不动)。
pub(crate) fn prepare_config(paths: &BootstrapPaths) -> Result<()> {
    let home = &paths.hermes_home;
    for sub in [
        "cron", "sessions", "logs", "pairing", "hooks", "image_cache", "audio_cache", "memories", "skills",
    ] {
        std::fs::create_dir_all(home.join(sub)).with_context(|| format!("创建 {}/{sub}", home.display()))?;
    }
    let env = home.join(".env");
    if !env.exists() {
        let example = paths.install_dir.join(".env.example");
        if example.is_file() {
            std::fs::copy(&example, &env).with_context(|| format!("写 {}", env.display()))?;
        } else {
            std::fs::write(&env, b"").with_context(|| format!("写 {}", env.display()))?;
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&env, std::fs::Permissions::from_mode(0o600));
    }
    let config = home.join("config.yaml");
    let template = paths.install_dir.join("cli-config.yaml.example");
    if !config.exists() && template.is_file() {
        std::fs::copy(&template, &config).with_context(|| format!("写 {}", config.display()))?;
    }
    // 启动时的 ensure 在装机之前就跑过了 (那会儿还没有 config.yaml), 这里当场补上,
    // 免得新装的机器第一次起 hermes 用的是 v0.22 出厂默认 (自动删会话、轮数不限、插件没授权 ...)
    if let Err(e) = crate::services::hermes_language::ensure_default_at(&config) {
        log::warn!("装机写 display.language 失败 (不阻塞): {e}");
    }
    match crate::services::hermes_policy_config::ensure_at(&config) {
        Ok(changed) => log::info!("装机写入 hermes 规定配置: {}", changed.join(", ")),
        Err(e) => log::warn!("装机写 hermes 规定配置失败 (不阻塞, 下次启动再补): {e}"),
    }
    Ok(())
}

/// `~/.local/bin/hermes` —— 员工 / IT 在终端里敲 hermes 用 (Companion 自己直接调 venv)。
#[cfg(not(target_os = "windows"))]
pub(crate) fn link_cli(paths: &BootstrapPaths) -> Result<()> {
    let bin_dir = paths.home.join(".local").join("bin");
    std::fs::create_dir_all(&bin_dir).with_context(|| format!("创建 {}", bin_dir.display()))?;
    let launcher = bin_dir.join("hermes");
    let script = format!(
        "#!/bin/sh\nunset PYTHONPATH\nunset PYTHONHOME\nexec \"{}\" \"{}\" \"$@\"\n",
        hermes_venv_python(&paths.install_dir).display(),
        paths.install_dir.join("hermes").display()
    );
    let _ = remove_any(&launcher);
    std::fs::write(&launcher, script).with_context(|| format!("写 {}", launcher.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&launcher, std::fs::Permissions::from_mode(0o755))
            .with_context(|| format!("chmod {}", launcher.display()))?;
    }
    Ok(())
}

/// Windows 用 hermes_install_windows::write_windows_install_markers (同时写版本标记)。
#[cfg(not(target_os = "windows"))]
pub(crate) fn finish(paths: &BootstrapPaths) -> Result<()> {
    std::fs::write(paths.install_dir.join(INSTALL_METHOD_MARKER), b"catfish\n").context("写 .install_method")
}

#[cfg(all(test, not(target_os = "windows")))]
mod e2e {
    use super::*;

    /// 真装一遍 (隔离 HOME, 不碰 ~/.hermes), 用 build-mac-resources.sh 打出来的资源。默认跳过:
    ///   CATFISH_TEST_RUNTIME=src-tauri/resources/mac-aarch64 \
    ///   cargo test --lib hermes_install_native::e2e -- --ignored --nocapture
    /// 跑两遍装机: 第二遍必须走"已完整安装"的快路径 (健康检查认这个安装)。
    #[test]
    #[ignore]
    fn installs_offline_from_bundle_and_is_healthy() {
        let runtime = std::path::PathBuf::from(std::env::var("CATFISH_TEST_RUNTIME").unwrap())
            .canonicalize()
            .unwrap();
        let resource = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(resource.path().join("resources")).unwrap();
        std::os::unix::fs::symlink(&runtime, resource.path().join("resources").join("mac")).unwrap();
        let home = tempfile::tempdir().unwrap();
        let paths = BootstrapPaths::new(home.path().to_path_buf());
        let steps = std::sync::Mutex::new(Vec::new());
        let reporter = |p: super::super::hermes_install_base::HermesBootstrapProgress| {
            println!("{:?}", p);
            steps.lock().unwrap().push(format!("{:?}", p));
        };
        let started = std::time::Instant::now();
        super::super::hermes_install::bootstrap_locked(resource.path(), &paths, &reporter).unwrap();
        println!("首装 {:.1}s", started.elapsed().as_secs_f64());
        let problems = super::super::hermes_install_health::core_health_problems(&paths, true);
        assert!(problems.is_empty(), "{problems:?}");
        assert!(!paths.install_dir.join(".git").exists());
        // hermes 本身能跑, 而且就是 pin 的那个版本 + external (10/17: 不再写死版本号, 升 pin 不用改这里)
        let out = crate::services::process::background_command(paths.install_dir.join("venv/bin/hermes"))
            .arg("--version")
            .env("HOME", &paths.home)
            .env("HERMES_HOME", &paths.hermes_home)
            .output()
            .unwrap();
        let text = String::from_utf8_lossy(&out.stdout);
        println!("{text}");
        assert!(text.contains(super::super::hermes_install_base::hermes_pinned_tag()), "{text}");
        // 工具桥 (catfish-tools MCP) 在员工机上就是用这个 venv + PYTHONPATH 跑的: 全部模块都要导得进
        let tool_bridge = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tool-bridge/src");
        let probe = r#"
import pkgutil, importlib, catfish_tool_bridge as t
bad = []
# Intel Mac 上这几个是打包时现编的 (cryptography 带 Rust 二进制), 真加载一次
for m in ("cryptography.hazmat.bindings._rust", "greenlet", "watchdog.observers", "yaml"):
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append(f"{m}: {e!r}")
for m in pkgutil.walk_packages(t.__path__, "catfish_tool_bridge."):
    try:
        importlib.import_module(m.name)
    except Exception as e:
        bad.append(f"{m.name}: {e!r}")
print("\n".join(bad))
raise SystemExit(1 if bad else 0)
"#;
        let out = crate::services::process::background_command(paths.install_dir.join("venv/bin/python"))
            .args(["-c", probe])
            .env("PYTHONPATH", &tool_bridge)
            .env("HOME", &paths.home)
            .env("HERMES_HOME", &paths.hermes_home)
            .output()
            .unwrap();
        assert!(out.status.success(), "工具桥导入失败:\n{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
        // 第二遍: 快路径
        let started = std::time::Instant::now();
        super::super::hermes_install::bootstrap_locked(resource.path(), &paths, &reporter).unwrap();
        println!("第二遍 {:.1}s", started.elapsed().as_secs_f64());
        assert!(steps.lock().unwrap().last().unwrap().contains("Skipped"));
    }
}
