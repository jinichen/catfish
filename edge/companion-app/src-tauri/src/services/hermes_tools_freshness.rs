//! 鲶鱼工具 (catfish-tools) 代码变了就重启一次 hermes 网关 (11/4)。
//!
//! # 病
//!
//! 鸿波 11/4 装了 1.2.52 让小鲶转发邮件, 草稿里还是只有「原文见下方」。查进程: hermes 网关
//! (`ai.hermes.gateway`) 和它拉起来的 `catfish_tool_bridge.mcp_server` 都是**前一天 07:32** 起的。
//! 小鲶能用哪些工具、每个工具有哪些参数, 是 hermes 启动那一刻从 mcp_server 拿的 —— 新版的
//! `forward_of` 参数小鲶根本看不见。Companion 每次启动会重启 socket 上那个 tool-bridge, 但 mcp_server
//! 是 hermes 的子进程, 只有 hermes 重启才换新; 而原来只在**插件文件**变了时才重启 hermes
//! (hermes_plugin.rs), 工具代码变了不算。结果: 升级后聊天里的新工具 / 新参数一律不生效, 而且静默。
//!
//! # 做法
//!
//! 启动时给 tool-bridge 的 Python 源码算一个内容指纹 (services::addon_fingerprint), 跟上次让 hermes
//! 重启时记下的比; 不一样就请 hermes 重启一次 (hermes_plugin::restart_hermes_gateway: 先等手头的回答
//! 写完), 再记下新指纹。代码没变就什么都不做 —— 不白白打断在跑的对话。

use std::path::{Path, PathBuf};

fn python_files(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                if path.file_name().is_some_and(|n| n != "__pycache__") {
                    stack.push(path);
                }
            } else if path.extension().is_some_and(|e| e == "py") {
                out.push(path);
            }
        }
    }
    out.sort(); // 遍历顺序跟文件系统有关, 排序后指纹才稳定
    out
}

/// tool-bridge 源码 (`<dir>/src/**/*.py`) 的内容指纹; 一个文件都没有返回 None。
pub(crate) fn tools_fingerprint(tool_bridge_dir: &Path) -> Option<String> {
    let files = python_files(&tool_bridge_dir.join("src"));
    if files.is_empty() {
        return None;
    }
    let refs: Vec<&Path> = files.iter().map(PathBuf::as_path).collect();
    crate::services::addon_fingerprint::fingerprint(&refs).ok()
}

/// 指纹跟记下的不一样 → 要重启。记号文件不存在也算不一样 (第一次: 不知道 hermes 加载的是哪版)。
pub(crate) fn needs_restart(marker: &Path, current: &str) -> bool {
    !crate::services::addon_fingerprint::matches(marker, current, true)
}

fn marker_path() -> Option<PathBuf> {
    crate::util::paths::home_env().ok().map(|h| PathBuf::from(h).join(".catfish").join("hermes_tools_fingerprint"))
}

/// Companion 启动 (tool-bridge 起好之后) 调一次。
pub fn restart_hermes_if_tools_changed() {
    let (Some(dir), Some(marker)) = (crate::services::catfish_paths::tool_bridge_dir(), marker_path()) else {
        return;
    };
    let Some(current) = tools_fingerprint(&dir) else {
        return;
    };
    if !needs_restart(&marker, &current) {
        return;
    }
    log::info!("[11/4] 鲶鱼工具代码变了 → 请 hermes 重启一次, 让小鲶拿到新的工具和参数");
    crate::commands::hermes_plugin::restart_hermes_gateway();
    if let Some(parent) = marker.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Err(e) = std::fs::write(&marker, &current) {
        log::warn!("[11/4] 记不下工具指纹 ({e}), 下次启动会再重启一次 hermes");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 工具代码一改就要重启_没改不动() {
        let dir = tempfile::tempdir().unwrap();
        let pkg = dir.path().join("src/catfish_tool_bridge");
        std::fs::create_dir_all(pkg.join("__pycache__")).unwrap();
        std::fs::write(pkg.join("schemas.py"), "TOOLS = ['create_draft']").unwrap();
        std::fs::write(pkg.join("__pycache__/schemas.cpython-314.pyc"), "x").unwrap();
        let marker = dir.path().join("marker");

        let first = tools_fingerprint(dir.path()).unwrap();
        assert!(needs_restart(&marker, &first), "第一次 (没有记号) 要重启");
        std::fs::write(&marker, &first).unwrap();
        assert!(!needs_restart(&marker, &tools_fingerprint(dir.path()).unwrap()), "没改不该重启");

        std::fs::write(pkg.join("__pycache__/schemas.cpython-314.pyc"), "changed").unwrap();
        assert!(!needs_restart(&marker, &tools_fingerprint(dir.path()).unwrap()), "缓存文件不算代码");

        std::fs::write(pkg.join("schemas.py"), "TOOLS = ['create_draft(forward_of)']").unwrap();
        assert!(needs_restart(&marker, &tools_fingerprint(dir.path()).unwrap()), "工具参数改了要重启");
    }

    #[test]
    fn 没有源码就不判() {
        let dir = tempfile::tempdir().unwrap();
        assert!(tools_fingerprint(dir.path()).is_none());
    }
}
