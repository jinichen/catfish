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
//! 见 hermes_install_gateway::stop_everything_for_deps)。装到一半留下的空壳安装记录 (dist-info 没有
//! METADATA) 先删掉、那几个包点名重装, 已经装坏的机器升级后自己修好。同一份清单补装失败过的,
//! 之后启动不再停网关重来 (见 FAILED_FILE)。

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

/// 够不够用; 不够时带上 Python 报的最后一行 (缺哪个包 / 哪个版本低), 写进日志好查。
fn check(python: &Path) -> Result<(), String> {
    match crate::services::process::background_command(python).arg("-c").arg(runtime_check()).output() {
        Ok(o) if o.status.success() => Ok(()),
        Ok(o) => {
            let err = String::from_utf8_lossy(&o.stderr);
            Err(err.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("").trim().to_string())
        }
        Err(e) => Err(format!("起不来: {e}")),
    }
}

/// 列出环境里只剩空壳的安装记录 (dist-info 没有 METADATA)。
///
/// 10/26 鸿波 Windows: 1.1.81 换 multidict 时文件被占, 装到一半, 只留下
/// `multidict-6.7.1.dist-info/licenses`。importlib 读出的版本是 None, 版本检查次次不过;
/// uv 不认它是坏的, 普通补装修不好 —— 于是每次启动都「缺包 → 补装 → 还是缺」。
const BROKEN_DISTS: &str = "import pathlib, sysconfig\n\
for d in {sysconfig.get_paths()[k] for k in ('purelib', 'platlib')}:\n\
\x20   for x in sorted(pathlib.Path(d).glob('*.dist-info')):\n\
\x20       if x.is_dir() and not (x / 'METADATA').is_file(): print(x)";

fn broken_dists(python: &Path) -> Vec<PathBuf> {
    let Ok(out) = crate::services::process::background_command(python).arg("-I").arg("-c").arg(BROKEN_DISTS).output() else {
        return Vec::new();
    };
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .map(|l| PathBuf::from(l.trim()))
        .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().ends_with(".dist-info")))
        .collect()
}

/// `multidict-6.7.1.dist-info` → `multidict` (dist-info 目录名里包名的 `-` 已经换成 `_`)。
fn dist_name(dist_info: &Path) -> Option<String> {
    let name = dist_info.file_name()?.to_string_lossy().into_owned();
    Some(name.split('-').next()?.to_ascii_lowercase().replace('_', "-"))
}

/// 删掉空壳, 返回要点名重装的包。删不掉的也照样点名 (uv 重装时会覆盖)。
fn clear_broken_dists(python: &Path) -> Vec<String> {
    let mut names = Vec::new();
    for dist in broken_dists(python) {
        match fs::remove_dir_all(&dist) {
            Ok(()) => log::warn!("[hermes-deps] 删掉只剩空壳的安装记录: {}", dist.display()),
            Err(e) => log::warn!("[hermes-deps] 空壳安装记录删不掉 {}: {e}", dist.display()),
        }
        if let Some(name) = dist_name(&dist) {
            if !names.contains(&name) {
                names.push(name);
            }
        }
    }
    names
}

/// 「这份依赖清单在这个环境里补装过、没成」的记录 (10/26)。
///
/// 补装要停网关 (Windows 还要结束所有 hermes 进程), 一次几分钟聊天不可用。修不好的情况下每次启动
/// 都来一遍, 等于每次开 Companion 都断一次 —— 10/26 鸿波 Windows 就是这样。所以同一份清单
/// 补装失败过的不再自动重来; 清单变了或 Companion 升级了才再试。
const FAILED_FILE: &str = ".catfish-deps-repair-failed.json";

fn attempt_key(paths: &BootstrapPaths) -> String {
    use sha2::{Digest, Sha256};
    let text = fs::read(paths.install_dir.join(hermes_install_native::REQUIREMENTS_FILE)).unwrap_or_default();
    // 带上 Companion 版本: 新版可能带着新修法, 升级后再试一次
    let extra = format!("{}\n{}", env!("CARGO_PKG_VERSION"), runtime_check());
    hex::encode(Sha256::digest([text.as_slice(), extra.as_bytes()].concat()))
}

fn failed_attempts(paths: &BootstrapPaths) -> serde_json::Map<String, serde_json::Value> {
    fs::read_to_string(paths.hermes_home.join(FAILED_FILE))
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

fn failed_before(paths: &BootstrapPaths, python: &Path, key: &str) -> bool {
    failed_attempts(paths).get(&python.display().to_string()).and_then(|v| v.as_str()) == Some(key)
}

fn remember_attempt(paths: &BootstrapPaths, python: &Path, key: &str, ok: bool) {
    let mut all = failed_attempts(paths);
    let id = python.display().to_string();
    if ok {
        if all.remove(&id).is_none() {
            return;
        }
    } else {
        all.insert(id, serde_json::Value::from(key));
    }
    let file = paths.hermes_home.join(FAILED_FILE);
    let result = if all.is_empty() {
        fs::remove_file(&file).or_else(|e| if e.kind() == std::io::ErrorKind::NotFound { Ok(()) } else { Err(e) })
    } else {
        fs::write(&file, serde_json::Value::Object(all).to_string())
    };
    if let Err(e) = result {
        log::warn!("[hermes-deps] 记不下补装结果 {}: {e}", file.display());
    }
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

/// 补装一个环境: 先删空壳安装记录, 照清单装, 空壳的那几个点名重装。
fn repair(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths, python: &Path) -> Result<(), String> {
    let broken = clear_broken_dists(python);
    if !broken.is_empty() {
        hermes_install_native::reinstall_packages(artifacts, paths, python, &broken).map_err(|e| format!("{e:#}"))?;
    }
    let result = if python == hermes_venv_python(&paths.install_dir) {
        // hermes 自己的 venv: 依赖 + hermes 本体 (editable) 一起, 跟首次安装同一步
        hermes_install_native::install_python_deps(artifacts, paths)
    } else {
        hermes_install_native::install_requirements(artifacts, paths, python)
    };
    result.map_err(|e| format!("{e:#}"))?;
    check(python).map_err(|why| format!("装完还不够用: {why}"))
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
    let key = attempt_key(paths);
    let mut missing = Vec::new();
    for python in runtime_pythons(paths) {
        let Err(why) = check(&python) else {
            remember_attempt(paths, &python, &key, true);
            continue;
        };
        if failed_before(paths, &python, &key) {
            log::error!(
                "[hermes-deps] {} 还是不够用 ({why}); 这份依赖清单上次补装没成, 这次不再停网关重装 —— \
                 升级到新版 Companion 会再试, 或删掉 {} 后重启 Companion",
                python.display(),
                paths.hermes_home.join(FAILED_FILE).display()
            );
            continue;
        }
        log::warn!(
            "[hermes-deps] {} 缺鲶鱼要的包 (分词/浏览器/文件解析/网关) 或依赖版本低于安全要求 ({why}) —— 离线补装, 不重装 hermes",
            python.display()
        );
        missing.push(python);
    }
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
        match repair(&artifacts, paths, &python) {
            Ok(()) => {
                log::info!("[hermes-deps] 补装完成: {}", python.display());
                remember_attempt(paths, &python, &key, true);
                fixed += 1;
            }
            Err(e) => {
                log::error!("[hermes-deps] 补装没成 {}: {e}", python.display());
                remember_attempt(paths, &python, &key, false);
            }
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

    /// 真建一个 venv (不带 pip, 一秒内), 往里放一个空壳安装记录 —— 跟 10/26 Windows 那台一样。
    fn venv_with_broken_multidict(tmp: &Path) -> Option<(PathBuf, PathBuf, PathBuf)> {
        let env = tmp.join("venv");
        let ok = std::process::Command::new("python3")
            .args(["-m", "venv", "--without-pip"])
            .arg(&env)
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if !ok {
            eprintln!("没有 python3, 跳过");
            return None;
        }
        let python = env_python(&env);
        let out = std::process::Command::new(&python)
            .args(["-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"])
            .output()
            .unwrap();
        let site = PathBuf::from(String::from_utf8_lossy(&out.stdout).trim());
        let broken = site.join("multidict-6.7.1.dist-info");
        fs::create_dir_all(broken.join("licenses")).unwrap();
        let good = site.join("python_docx-1.1.2.dist-info");
        fs::create_dir_all(&good).unwrap();
        fs::write(good.join("METADATA"), "Metadata-Version: 2.1\nName: python-docx\nVersion: 1.1.2\n").unwrap();
        Some((python, broken, good))
    }

    #[test]
    fn 空壳安装记录_版本检查报出来不崩_删掉后点名重装() {
        let tmp = TempDir::new().unwrap();
        let Some((python, broken, good)) = venv_with_broken_multidict(tmp.path()) else { return };
        let out = std::process::Command::new(&python).arg("-c").arg(hermes_dep_overrides::version_floor_check()).output().unwrap();
        assert!(!out.status.success());
        let err = String::from_utf8_lossy(&out.stderr);
        assert!(err.contains("multidict 安装记录坏了"), "{err}");

        assert_eq!(broken_dists(&python), vec![broken.clone()]);
        assert_eq!(clear_broken_dists(&python), vec!["multidict".to_string()]);
        assert!(!broken.exists() && good.join("METADATA").is_file(), "只删空壳");
        assert!(broken_dists(&python).is_empty());
    }

    #[test]
    fn 安装记录目录名换回包名() {
        assert_eq!(dist_name(Path::new("/x/python_docx-1.1.2.dist-info")).as_deref(), Some("python-docx"));
        assert_eq!(dist_name(Path::new("/x/MultiDict-6.7.1.dist-info")).as_deref(), Some("multidict"));
    }

    #[test]
    fn 同一份清单补装没成过_下次不再停网关重来_清单变了或修好了就放行() {
        let tmp = TempDir::new().unwrap();
        let _env = crate::util::test_env::env_lock();
        let paths = BootstrapPaths::new(tmp.path().to_path_buf());
        fs::create_dir_all(&paths.hermes_home).unwrap();
        let py = Path::new("/h/venv/python");
        let other = Path::new("/h/other/python");
        assert!(!failed_before(&paths, py, "k1"));
        remember_attempt(&paths, py, "k1", false);
        remember_attempt(&paths, other, "k1", false);
        assert!(failed_before(&paths, py, "k1"));
        assert!(!failed_before(&paths, py, "k2"), "清单变了再试");
        remember_attempt(&paths, py, "k1", true);
        assert!(!failed_before(&paths, py, "k1"));
        assert!(failed_before(&paths, other, "k1"), "各环境分开记");
        remember_attempt(&paths, other, "k1", true);
        assert!(!paths.hermes_home.join(FAILED_FILE).exists(), "都好了就不留文件");
    }

    #[test]
    fn 清单内容进指纹() {
        let tmp = TempDir::new().unwrap();
        let _env = crate::util::test_env::env_lock();
        let paths = BootstrapPaths::new(tmp.path().to_path_buf());
        fs::create_dir_all(&paths.install_dir).unwrap();
        let file = paths.install_dir.join(hermes_install_native::REQUIREMENTS_FILE);
        fs::write(&file, "a==1\n").unwrap();
        let k1 = attempt_key(&paths);
        fs::write(&file, "a==2\n").unwrap();
        assert_ne!(attempt_key(&paths), k1);
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
