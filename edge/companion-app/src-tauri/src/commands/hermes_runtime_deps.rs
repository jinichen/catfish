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

use std::fs;
use std::path::{Path, PathBuf};

use super::hermes_install_artifacts::{hermes_extra_import_check, resolve_runtime_dir, RuntimeArtifacts};
use super::hermes_install_native;
use super::hermes_install_state::BootstrapPaths;
use crate::services::catfish_paths::hermes_venv_python;

/// 额外包 + 网关本身要的两个 (API Server / 微信适配器靠它们; hermes 基础依赖不带)。
fn runtime_import_check() -> String {
    format!("{}, aiohttp, cryptography", hermes_extra_import_check())
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
        .arg(runtime_import_check())
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// 查一遍, 缺了就补。返回补装过的环境数 (调用方据此决定要不要重启网关)。失败只 warn。
pub(crate) fn ensure_runtime_deps(resource_dir: &Path, paths: &BootstrapPaths) -> usize {
    let missing: Vec<PathBuf> = runtime_pythons(paths).into_iter().filter(|p| !imports_ok(p)).collect();
    if missing.is_empty() {
        return 0;
    }
    let artifacts = match resolve_runtime_dir(resource_dir).map(RuntimeArtifacts::from_dir) {
        Ok(a) => a,
        Err(e) => {
            log::warn!("[hermes-deps] {} 个环境缺包, 但找不到安装包里的离线依赖: {e:#}", missing.len());
            return 0;
        }
    };
    let primary = hermes_venv_python(&paths.install_dir);
    let mut fixed = 0;
    for python in missing {
        log::warn!(
            "[hermes-deps] {} 缺鲶鱼要的包 (分词/浏览器/文件解析/网关) —— 离线补装, 不重装 hermes",
            python.display()
        );
        let result = if python == primary {
            // hermes 自己的 venv: 依赖 + hermes 本体 (editable) 一起, 跟首次安装同一步
            hermes_install_native::install_python_deps(&artifacts, paths)
        } else {
            hermes_install_native::install_requirements(&artifacts, paths, &python)
        };
        match result {
            Ok(()) if imports_ok(&python) => {
                log::info!("[hermes-deps] 补装完成: {}", python.display());
                fixed += 1;
            }
            Ok(()) => log::warn!("[hermes-deps] 补装后仍有包 import 不了: {}", python.display()),
            Err(e) => log::warn!("[hermes-deps] 补装失败 {}: {e:#}", python.display()),
        }
    }
    fixed
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
    fn 检查项含网关要的包() {
        let check = runtime_import_check();
        assert!(check.starts_with("import jieba"), "{check}");
        assert!(check.ends_with(", aiohttp, cryptography"), "{check}");
    }
}
