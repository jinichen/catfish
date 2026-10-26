//! hermes 实际在用的 Python 环境里, 鲶鱼要的包都得在 (10/21)。
//!
//! # 病
//!
//! 10/21 鸿波本机: 在命令行跑了一次 `hermes plugins enable catfish-policy`, hermes 顺手
//! ("Installing Python dependencies") 在 `~/.hermes/installs/<id>/environments/<id>/venv`
//! 另建了一套环境, 只装它自己的基础依赖, 并记进同目录的 `facts.json`。之后网关的启动器按
//! facts.json 用这套环境 —— 里面没有 aiohttp / cryptography 等鲶鱼的包, 下次重启网关:
//! 聊天端口 8642 起不来、鲶鱼插件加载失败、微信连不上。
//!
//! Companion 原来的「hermes 健康但额外包缺」修复只看 `hermes-agent/venv` (那边一切正常),
//! 于是只显示「检测失败」, 不会自己修好。
//!
//! # 做法
//!
//! 每次启动 (hermes 判定健康之后) 把 hermes 可能在用的环境都查一遍: 自己的 venv + facts.json
//! 里登记的每个环境。import 不全的, 用安装包里同一份钉版本清单离线补装 (装过的 uv 秒过)。
//! 不删 hermes 建的环境、不改 facts.json —— 那是 hermes 自己的状态, 只保证里面东西够用。
//!
//! 10/25 起「够用」还包括版本: hermes-dep-overrides.txt 里那几个有漏洞的依赖, 装着的低于覆盖版本
//! 也按缺包补装 (先从安装包把已装目录里的旧依赖清单换新, 见 hermes_dep_overrides.rs; 额外包清单后加的包
//! 也是这一步才到得了老机器)。
//!
//! 10/26 起 Windows 上换之前先停网关、结束所有用着 hermes 环境的进程, 换完再起 (文件被占会装坏,
//! 见 hermes_install_gateway::stop_everything_for_deps); 装完还 import 不了的强制重装一遍,
//! 已经装坏的机器升级后自己修好。

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use super::hermes_dep_overrides;
use super::hermes_install_artifacts::{hermes_extra_import_check, resolve_runtime_dir, RuntimeArtifacts};
use super::hermes_install_native;
use super::hermes_install_state::BootstrapPaths;
use crate::services::catfish_paths::hermes_venv_python;

/// 额外包 + 网关本身要的两个 (API Server / 微信适配器靠它们; hermes 基础依赖不带)。
fn runtime_import_check() -> String {
    format!("{}, aiohttp, cryptography", hermes_extra_import_check())
}

/// import 得了, 而且安全覆盖的那几个包不低于覆盖版本, 才算够用。
fn runtime_check() -> String {
    format!("{}\n{}", runtime_import_check(), hermes_dep_overrides::version_floor_check())
}

fn env_python(env: &Path) -> PathBuf {
    if cfg!(windows) {
        env.join("Scripts").join("python.exe")
    } else {
        env.join("bin").join("python")
    }
}

/// facts.json (`{"packages": {<名>: {"environment": "<目录>", ...}}}`) 里登记、且还在磁盘上的环境。
fn managed_env_pythons(hermes_home: &Path) -> Vec<PathBuf> {
    let Ok(stores) = fs::read_dir(hermes_home.join("installs")) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for store in stores.flatten() {
        let Ok(text) = fs::read_to_string(store.path().join("facts.json")) else {
            continue;
        };
        let Ok(facts) = serde_json::from_str::<serde_json::Value>(&text) else {
            continue;
        };
        let Some(packages) = facts.get("packages").and_then(|p| p.as_object()) else {
            continue;
        };
        for pkg in packages.values() {
            if let Some(env) = pkg.get("environment").and_then(|e| e.as_str()) {
                let python = env_python(Path::new(env));
                if python.is_file() && !out.contains(&python) {
                    out.push(python);
                }
            }
        }
    }
    out
}

/// hermes 可能在用的全部 Python: 自己的 venv 在前, hermes 另建的在后。
fn runtime_pythons(paths: &BootstrapPaths) -> Vec<PathBuf> {
    let mut out = vec![hermes_venv_python(&paths.install_dir)];
    for python in managed_env_pythons(&paths.hermes_home) {
        if !out.contains(&python) {
            out.push(python);
        }
    }
    out
}

fn imports_ok(python: &Path) -> bool {
    crate::services::process::background_command(python)
        .arg("-c")
        .arg(runtime_check())
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// 正在往 hermes 的环境里换依赖 (10/26)。这期间 watchdog / autostart 不拉 tool-bridge、local-search
/// —— 拉起来的进程会再把文件占住 (Windows), 或者 import 到装了一半的包。
static SWAPPING: AtomicBool = AtomicBool::new(false);

pub(crate) fn swapping() -> bool {
    SWAPPING.load(Ordering::SeqCst)
}

/// 换依赖期间的「静场」: 立起 SWAPPING; Windows 上再停网关、结束所有用着 hermes 环境的进程。
/// 离开作用域 (装成、装败、panic 都一样) 时放下标志, Windows 上把网关起回来。
struct Quiet<'a> {
    #[cfg_attr(not(windows), allow(dead_code))]
    paths: &'a BootstrapPaths,
    #[cfg(windows)]
    stopped: Option<super::hermes_install_gateway::StoppedGateway>,
}

impl<'a> Quiet<'a> {
    fn begin(paths: &'a BootstrapPaths) -> Self {
        SWAPPING.store(true, Ordering::SeqCst);
        Quiet {
            paths,
            #[cfg(windows)]
            stopped: Some(super::hermes_install_gateway::stop_everything_for_deps(paths)),
        }
    }

    /// 网关是不是已经由这里起回来了 (调用方就不用再重启一遍)。
    fn restarts_gateway(&self) -> bool {
        cfg!(windows)
    }
}

impl Drop for Quiet<'_> {
    fn drop(&mut self) {
        #[cfg(windows)]
        if let Some(stopped) = self.stopped.take() {
            super::hermes_install_gateway::resume_after_install(self.paths, stopped);
        }
        SWAPPING.store(false, Ordering::SeqCst);
    }
}

/// 补装一个环境: 先照常装; 装完还 import 不了, 再强制重装一遍 (10/26: 修装到一半的机器)。
fn repair(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths, python: &Path) -> bool {
    let first = if python == hermes_venv_python(&paths.install_dir) {
        // hermes 自己的 venv: 依赖 + hermes 本体 (editable) 一起, 跟首次安装同一步
        hermes_install_native::install_python_deps(artifacts, paths)
    } else {
        hermes_install_native::install_requirements(artifacts, paths, python)
    };
    match first {
        Ok(()) if imports_ok(python) => return true,
        Ok(()) => log::warn!("[hermes-deps] 补装后仍有包 import 不了或版本不够, 强制重装一遍: {}", python.display()),
        Err(e) => log::warn!("[hermes-deps] 补装失败, 强制重装一遍 {}: {e:#}", python.display()),
    }
    match hermes_install_native::reinstall_requirements(artifacts, paths, python) {
        Ok(()) if imports_ok(python) => true,
        Ok(()) => {
            log::error!("[hermes-deps] 强制重装后仍有包 import 不了或版本不够: {}", python.display());
            false
        }
        Err(e) => {
            log::error!("[hermes-deps] 强制重装失败 {}: {e:#}", python.display());
            false
        }
    }
}

/// 查一遍, 缺了就补。返回 true = 补装过、网关还得重启才用得上 (Windows 上这里已经把网关起回来, 返回 false)。
/// 失败只记日志。
pub(crate) fn ensure_runtime_deps(resource_dir: &Path, paths: &BootstrapPaths) -> bool {
    let artifacts = resolve_runtime_dir(resource_dir).map(RuntimeArtifacts::from_dir);
    // 先把已装目录里的离线依赖换成安装包里的 —— 下面的补装读的是已装目录那份
    if let Ok(a) = &artifacts {
        if let Err(e) = hermes_dep_overrides::refresh_offline_deps_if_outdated(a, paths) {
            log::error!("[hermes-deps] 离线依赖换新失败, 有漏洞的旧版本还在用: {e:#}");
        }
    }
    let missing: Vec<PathBuf> = runtime_pythons(paths).into_iter().filter(|p| !imports_ok(p)).collect();
    if missing.is_empty() {
        return false;
    }
    let artifacts = match artifacts {
        Ok(a) => a,
        Err(e) => {
            log::warn!("[hermes-deps] {} 个环境缺包, 但找不到安装包里的离线依赖: {e:#}", missing.len());
            return false;
        }
    };
    let quiet = Quiet::begin(paths);
    let mut fixed = 0;
    for python in missing {
        log::warn!(
            "[hermes-deps] {} 缺鲶鱼要的包 (分词/浏览器/文件解析/网关) 或依赖版本低于安全要求 —— 离线补装, 不重装 hermes",
            python.display()
        );
        if repair(&artifacts, paths, &python) {
            log::info!("[hermes-deps] 补装完成: {}", python.display());
            fixed += 1;
        }
    }
    fixed > 0 && !quiet.restarts_gateway()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn fake_env(root: &Path) -> PathBuf {
        let python = env_python(root);
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, "").unwrap();
        python
    }

    #[test]
    fn 认出_hermes_另建并登记的环境() {
        let tmp = TempDir::new().unwrap();
        let home = tmp.path();
        let env = home.join("installs/abc/environments/x/venv");
        let python = fake_env(&env);
        let gone = home.join("installs/abc/environments/gone/venv");
        fs::write(
            home.join("installs/abc/facts.json"),
            serde_json::json!({"packages": {
                "venv": {"environment": env, "extras": []},
                "old": {"environment": gone},
                "python": {"entry": "tools/py"}
            }})
            .to_string(),
        )
        .unwrap();
        fs::create_dir_all(home.join("installs/broken")).unwrap();
        fs::write(home.join("installs/broken/facts.json"), "{not json").unwrap();

        assert_eq!(managed_env_pythons(home), vec![python], "只认登记了且还在磁盘上的");
    }

    #[test]
    fn 没有_installs_目录时只查自己的_venv() {
        let tmp = TempDir::new().unwrap();
        // BootstrapPaths::new 读 HERMES_HOME (进程全局), 别的测试会临时改它 —— 排队。
        let _env = crate::util::test_env::env_lock();
        let paths = BootstrapPaths::new(tmp.path().to_path_buf());
        assert_eq!(runtime_pythons(&paths), vec![hermes_venv_python(&paths.install_dir)]);
    }

    #[test]
    fn 静场期间标着在换依赖_出了作用域就放下() {
        let tmp = TempDir::new().unwrap();
        let _env = crate::util::test_env::env_lock();
        let paths = BootstrapPaths::new(tmp.path().to_path_buf());
        assert!(!swapping());
        {
            let quiet = Quiet::begin(&paths);
            assert!(swapping(), "watchdog / autostart 这时不拉 tool-bridge");
            assert_eq!(quiet.restarts_gateway(), cfg!(windows));
        }
        assert!(!swapping());
        let r = std::panic::catch_unwind(|| {
            let _q = Quiet::begin(&paths);
            panic!("装到一半出错");
        });
        assert!(r.is_err());
        assert!(!swapping(), "出错也要放下, 不然 tool-bridge 再也不起");
    }

    #[test]
    fn 检查含安全覆盖的版本下限() {
        let check = runtime_check();
        assert!(check.starts_with(&runtime_import_check()), "{check}");
        assert!(check.contains("\"pyjwt\""), "{check}");
    }

    #[test]
    fn 检查项含网关要的包() {
        let check = runtime_import_check();
        assert!(check.starts_with("import jieba"), "{check}");
        assert!(check.ends_with(", aiohttp, cryptography"), "{check}");
    }
}
