//! 把 tool-bridge / local-search 的 Python 源码从安装包解到 `~/.catfish/edge-runtime/` (9/23).
//!
//! # 为什么有这个文件
//!
//! Companion 起 tool-bridge / local-search 时找的是 `catfish_paths::catfish_root()`
//! —— 也就是 `~/person_task/catfish` 或 `~/catfish` 这种**源码树**。开发机上有,
//! 客户机器上没有: MSI 和 DMG 的资源清单里从来没带这两个组件。
//!
//! 9/23 鸿波第一台干净装的 Windows 上, watchdog 每 5 秒报一次 "tool-bridge dir
//! not found", 5 次进 backoff, 180 秒后再来 —— 一直循环。症状是 LLM 调不了
//! 工具、本机文件搜不到, 但不报错。mac 客户装的 DMG 是同一个情况, 只是没人
//! 在干净的 mac 上试过。
//!
//! # 做法
//!
//! 构建时 `scripts/build_edge_runtime.py` 把两份 `src/` 打成
//! `catfish-edge-runtime.tar.gz` 放进平台资源目录 (两个平台同一个脚本)。
//! 启动时这里比对归档的 sha256 和上次解压时记下的, 不同就重新解 (装新包 =
//! 换新代码, 不用员工做任何事)。
//!
//! 解到临时目录再整体换名 —— 解到一半失败时老版本还在, 不会留半套代码。
//!
//! 两个组件都是纯 Python, 跑在 hermes 的 venv 里 (tool-bridge 本来就这样;
//! local-search 只依赖 pyyaml, hermes venv 里有), 所以只需要源码, 不需要 wheel。

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

pub const ARCHIVE: &str = "catfish-edge-runtime.tar.gz";
const STAMP: &str = ".archive-sha256";

/// `~/.catfish/edge-runtime/` —— 注意不是 `~/.catfish/runtime/`, 那个是离线安装归档的目录。
pub fn runtime_root() -> Option<PathBuf> {
    let home = crate::util::paths::home_env().ok()?;
    Some(PathBuf::from(home).join(".catfish").join("edge-runtime"))
}

fn archive_in(resource_dir: &Path) -> PathBuf {
    let platform = if cfg!(windows) { "windows" } else { "mac" };
    resource_dir.join("resources").join(platform).join(ARCHIVE)
}

fn sha256_file(p: &Path) -> Result<String> {
    let bytes = fs::read(p).with_context(|| format!("读 {}", p.display()))?;
    Ok(hex::encode(Sha256::digest(&bytes)))
}

/// 启动时调一次。任何失败都只 warn —— 缺了这两个组件 Companion 照样能聊天。
pub fn ensure_extracted(resource_dir: &Path) {
    match ensure_extracted_at(&archive_in(resource_dir), runtime_root().as_deref()) {
        Ok(Some(dir)) => log::info!("[edge-runtime] 已解压新版 tool-bridge / local-search → {}", dir.display()),
        Ok(None) => log::debug!("[edge-runtime] 已是最新, 不动"),
        Err(e) => log::warn!(
            "[edge-runtime] 解压失败, tool-bridge / local-search 在这台机器上不可用: {e:#}"
        ),
    }
}

/// 返回 `Some(dir)` = 这次解了; `None` = 已是最新 / 包里没带 (开发构建)。
pub(crate) fn ensure_extracted_at(archive: &Path, root: Option<&Path>) -> Result<Option<PathBuf>> {
    let Some(root) = root else {
        anyhow::bail!("找不到家目录");
    };
    if !archive.exists() {
        // 开发构建不带这个资源, 走源码树 —— 不是错误
        return Ok(None);
    }
    let want = sha256_file(archive)?;
    let have = fs::read_to_string(root.join(STAMP)).unwrap_or_default();
    if have.trim() == want {
        return Ok(None);
    }

    let parent = root.parent().context("edge-runtime 没有父目录")?;
    fs::create_dir_all(parent)?;
    let tmp = parent.join("edge-runtime.new");
    let _ = fs::remove_dir_all(&tmp);
    fs::create_dir_all(&tmp)?;

    // tar: macOS 自带, Windows 10 1803+ 自带 (System32\tar.exe, bsdtar)。
    // hermes_install_steps 解其它归档也是这么做的。
    let out = crate::services::process::background_command("tar")
        .arg("-xzf")
        .arg(archive)
        .arg("-C")
        .arg(&tmp)
        .output()
        .context("起不了 tar")?;
    if !out.status.success() {
        let _ = fs::remove_dir_all(&tmp);
        anyhow::bail!("tar 解压失败: {}", String::from_utf8_lossy(&out.stderr).trim());
    }
    for must in ["tool-bridge/src/catfish_tool_bridge", "local-search/src"] {
        if !tmp.join(must).exists() {
            let _ = fs::remove_dir_all(&tmp);
            anyhow::bail!("归档里缺 {must} —— 打包脚本坏了?");
        }
    }
    fs::write(tmp.join(STAMP), &want)?;

    // 换名: 先挪走老的再挪进新的。Windows 上 rename 不能覆盖已存在的目录。
    let old = parent.join("edge-runtime.old");
    let _ = fs::remove_dir_all(&old);
    if root.exists() {
        fs::rename(root, &old).with_context(|| {
            format!("挪不走老版本 {} (tool-bridge 还在跑、文件被占用?)", root.display())
        })?;
    }
    if let Err(e) = fs::rename(&tmp, root) {
        // 换不进去就把老的挪回来, 别留一个空目录
        let _ = fs::rename(&old, root);
        return Err(e).context("新版本换名失败, 已回退到老版本");
    }
    let _ = fs::remove_dir_all(&old);
    Ok(Some(root.to_path_buf()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_archive(dir: &Path, marker: &str) -> PathBuf {
        let src = dir.join("src-tree");
        fs::create_dir_all(src.join("tool-bridge/src/catfish_tool_bridge")).unwrap();
        fs::create_dir_all(src.join("local-search/src/catfish_search")).unwrap();
        fs::write(src.join("tool-bridge/src/catfish_tool_bridge/__init__.py"), marker).unwrap();
        let archive = dir.join(format!("a-{marker}.tar.gz"));
        let ok = std::process::Command::new("tar")
            .arg("-czf").arg(&archive).arg("-C").arg(&src).arg(".")
            .status().unwrap().success();
        assert!(ok);
        fs::remove_dir_all(&src).unwrap();
        archive
    }

    #[test]
    fn 首次解压_再跑不动_换包重解() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path().join(".catfish/edge-runtime");
        let a1 = make_archive(tmp.path(), "v1");
        assert!(ensure_extracted_at(&a1, Some(&root)).unwrap().is_some());
        let init = root.join("tool-bridge/src/catfish_tool_bridge/__init__.py");
        assert_eq!(fs::read_to_string(&init).unwrap(), "v1");

        assert!(ensure_extracted_at(&a1, Some(&root)).unwrap().is_none(), "同一个包不该重解");

        let a2 = make_archive(tmp.path(), "v2");
        assert!(ensure_extracted_at(&a2, Some(&root)).unwrap().is_some());
        assert_eq!(fs::read_to_string(&init).unwrap(), "v2", "装新包要换新代码");
        assert!(!tmp.path().join(".catfish/edge-runtime.old").exists());
    }

    #[test]
    fn 包里没带_是开发构建_不算错() {
        let tmp = tempfile::TempDir::new().unwrap();
        let r = ensure_extracted_at(&tmp.path().join("nope.tar.gz"), Some(&tmp.path().join("x")));
        assert!(r.unwrap().is_none());
    }
}
