//! 上传文件解析用的 Python 解释器和脚本在哪 (9/28 从 file_parse.rs 拆出)。
//!
//! # 9/28 Windows: 聊天里一上传文件就是「Python 解释器找不到. 设 env CATFISH_PYTHON」
//!
//! 原来的查找只认开发机:
//!   · 解释器: 4 个写死的 Unix 路径 (`…/venv/bin/python`, `/opt/homebrew/…`,
//!     `/usr/bin/python3`)。Windows 的 hermes venv 在
//!     `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe`, 一个都对不上。
//!   · 脚本: 只在当前目录和 `~/person_task/catfish/…` 源码树里找 —— 安装包里
//!     根本没带 parse_file.py。装好的 mac 也一样, 开发机上有源码树才没暴露。
//!   · 依赖: pypdfium2 / python-docx 等只在开发机的网关 venv 里, hermes venv 没有。
//!
//! 修法 (两个平台同一套):
//!   · 解释器走 catfish_paths (hermes_home / hermes_venv_python), 跟 tool-bridge、
//!     local-search 找 Python 是同一个判据;
//!   · 脚本随 catfish-edge-runtime.tar.gz 解到 `~/.catfish/edge-runtime/file-parse/`;
//!   · 依赖进 hermes-deps (src-tauri/hermes-extra-packages.txt), 启动自检缺了就补装。

use std::path::{Path, PathBuf};

use crate::services::catfish_paths;

/// parse_file.py 三大依赖。都 import 得了的解释器优先。
const PARSE_DEPS_IMPORT: &str = "import pypdfium2, openpyxl, docx";

/// 候选解释器, 按优先级 (只列路径, 不判断在不在)。
///
/// 1. 开发机的中央网关 venv —— 5/6 起文档要求解析依赖装这里;
/// 2. hermes venv —— 安装包装的, 解析依赖随 hermes-deps 一起装;
/// 3. (仅 mac) Homebrew 的 python3。
///
/// 不再列 `/usr/bin/python3`: 没装命令行工具的 mac 上它是个占位程序, 一探测就弹
/// 「安装开发者工具」的系统对话框。Windows 同理不碰 PATH 上的 python (多半是
/// 应用商店的占位)。
fn python_candidates() -> Vec<PathBuf> {
    let mut out = Vec::new();
    // 网关 venv 跟 hermes venv 是同一种布局 (bin/python 或 Scripts\python.exe)
    if let Some(gateway) = catfish_paths::gateway_dir() {
        out.push(catfish_paths::hermes_venv_python(&gateway));
    }
    if let Some(hermes) = catfish_paths::hermes_home() {
        out.push(catfish_paths::hermes_venv_python(&hermes.join("hermes-agent")));
    }
    #[cfg(not(windows))]
    for p in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"] {
        out.push(PathBuf::from(p));
    }
    out
}

/// 先挑依赖齐的; 都不齐就用第一个存在的 (parse_file.py 会报具体缺哪个包)。
fn pick_python(
    candidates: &[PathBuf],
    exists: impl Fn(&Path) -> bool,
    has_deps: impl Fn(&Path) -> bool,
) -> Option<PathBuf> {
    let present: Vec<&PathBuf> = candidates.iter().filter(|p| exists(p)).collect();
    if let Some(p) = present.iter().find(|p| has_deps(p)) {
        log::info!("find_python: 依赖齐, 用 {}", p.display());
        return Some((*p).clone());
    }
    let first = present.first()?;
    log::warn!(
        "find_python: 没有依赖齐的解释器, 退而用 {} (PDF / Word 上传可能报缺依赖)",
        first.display()
    );
    Some((*first).clone())
}

fn has_parse_deps(py: &Path) -> bool {
    let out = crate::services::process::background_command(py)
        .arg("-c")
        .arg(PARSE_DEPS_IMPORT)
        .output();
    matches!(out, Ok(o) if o.status.success())
}

fn joined(paths: &[PathBuf]) -> String {
    paths.iter().map(|p| p.display().to_string()).collect::<Vec<_>>().join("; ")
}

/// 解析文件用的 Python。env `CATFISH_PYTHON` 指定且存在时直接用。
pub(crate) fn find_python() -> Result<PathBuf, String> {
    if let Some(custom) = std::env::var_os("CATFISH_PYTHON").map(PathBuf::from) {
        if custom.exists() {
            return Ok(custom);
        }
    }
    let candidates = python_candidates();
    pick_python(&candidates, |p| p.exists(), has_parse_deps).ok_or_else(|| {
        format!(
            "解析上传的文件要用鲶鱼自带的 Python 环境, 这台电脑上还没找到 —— \
             鲶鱼的首次安装 / 修复可能还没完成, 完成后再上传一次。(找过: {})",
            joined(&candidates)
        )
    })
}

/// 脚本候选: 源码树优先 (开发), 然后是安装包解出来的那份。
fn script_candidates(name: &str) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        out.push(cwd.join("scripts").join(name));
        out.push(cwd.join("src-tauri").join("scripts").join(name));
    }
    if let Some(root) = catfish_paths::catfish_root() {
        out.push(root.join("edge/companion-app/src-tauri/scripts").join(name));
    }
    if let Some(p) = crate::services::edge_runtime::file_parse_script(name) {
        out.push(p);
    }
    out
}

/// 找 parse_file.py / attachment_bm25.py 这类解析脚本。
pub(crate) fn find_script(name: &str) -> Result<PathBuf, String> {
    let candidates = script_candidates(name);
    candidates.iter().find(|p| p.is_file()).cloned().ok_or_else(|| {
        format!(
            "解析脚本 {name} 没找到 —— 安装包里的组件可能还没解压 (鲶鱼启动完成后会自动解压), \
             稍后再试或重启鲶鱼。(找过: {})",
            joined(&candidates)
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn paths(names: &[&str]) -> Vec<PathBuf> {
        names.iter().map(PathBuf::from).collect()
    }

    #[test]
    fn 依赖齐的优先_哪怕排在后面() {
        let c = paths(&["/gw/python", "/hermes/python"]);
        let got = pick_python(&c, |_| true, |p| p == Path::new("/hermes/python"));
        assert_eq!(got, Some(PathBuf::from("/hermes/python")));
    }

    #[test]
    fn 都不齐_用第一个存在的() {
        let c = paths(&["/gw/python", "/hermes/python"]);
        let got = pick_python(&c, |p| p == Path::new("/hermes/python"), |_| false);
        assert_eq!(got, Some(PathBuf::from("/hermes/python")));
    }

    #[test]
    fn 一个都不存在_返回空_不去探测依赖() {
        let c = paths(&["/gw/python", "/hermes/python"]);
        let got = pick_python(&c, |_| false, |_| panic!("不存在的解释器不该被启动"));
        assert_eq!(got, None);
    }

    /// Windows 装机的落点: `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe`
    /// (mac 是 `~/.hermes/hermes-agent/venv/bin/python`)。9/28 之前这里一个都对不上。
    #[test]
    fn 候选里有_hermes_venv_的解释器() {
        let _guard = crate::util::test_env::env_lock();
        let tmp = tempfile::TempDir::new().unwrap();
        let old = std::env::var_os("HERMES_HOME");
        std::env::set_var("HERMES_HOME", tmp.path());
        let candidates = python_candidates();
        match old {
            Some(v) => std::env::set_var("HERMES_HOME", v),
            None => std::env::remove_var("HERMES_HOME"),
        }
        let want = catfish_paths::hermes_venv_python(&tmp.path().join("hermes-agent"));
        assert!(candidates.contains(&want), "{candidates:?}");
        if cfg!(windows) {
            assert!(want.ends_with("venv/Scripts/python.exe"));
        } else {
            assert!(want.ends_with("venv/bin/python"));
            assert!(!candidates.contains(&PathBuf::from("/usr/bin/python3")));
        }
    }

    #[test]
    fn 脚本候选最后是安装包解出来的那份() {
        let _guard = crate::util::test_env::env_lock();
        let last = script_candidates("parse_file.py").pop().unwrap();
        assert!(last.ends_with(".catfish/edge-runtime/file-parse/parse_file.py"), "{}", last.display());
    }
}
