//! 解析 Catfish 各组件在当前平台上的路径。
//!
//! 优先级：
//!   1. 环境变量 `CATFISH_ROOT` / `CATFISH_GATEWAY_DIR` 显式指向（部署时用）
//!   2. `~/person_task/catfish` —— 当前开发约定位置
//!   3. `~/catfish` —— 退而求其次
//!
//! Companion 自己的运行时状态（PID 文件、log 文件）放在 `<root>/.companion-state/`
//! 跟 catfish 项目放一起，员工 `rm -rf catfish` 时一起干净。

use std::path::PathBuf;

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

pub fn catfish_root() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("CATFISH_ROOT") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Some(path);
        }
    }
    let home = home_dir()?;
    for sub in &["person_task/catfish", "catfish"] {
        let p = home.join(sub);
        if p.exists() {
            return Some(p);
        }
    }
    None
}

pub fn gateway_dir() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("CATFISH_GATEWAY_DIR") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Some(path);
        }
    }
    catfish_root().map(|r| r.join("central").join("llm-gateway"))
}

/// 优先用 gateway 自带的 venv，没 venv 就 fallback 到 system python3
pub fn gateway_python() -> Option<PathBuf> {
    let dir = gateway_dir()?;
    let venv_py = dir.join("venv").join("bin").join("python");
    if venv_py.exists() {
        return Some(venv_py);
    }
    // Windows venv 路径
    let venv_py_win = dir.join("venv").join("Scripts").join("python.exe");
    if venv_py_win.exists() {
        return Some(venv_py_win);
    }
    Some(PathBuf::from("python3"))
}

/// Companion 自己的运行时状态目录（PID/log）
pub fn companion_state_dir() -> Option<PathBuf> {
    let dir = catfish_root()?.join(".companion-state");
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

pub fn gateway_pid_file() -> Option<PathBuf> {
    companion_state_dir().map(|d| d.join("gateway.pid"))
}

pub fn chrome_pid_file() -> Option<PathBuf> {
    companion_state_dir().map(|d| d.join("chrome.pid"))
}

pub fn local_search_pid_file() -> Option<PathBuf> {
    companion_state_dir().map(|d| d.join("local-search.pid"))
}

pub fn gateway_log_path() -> Option<PathBuf> {
    companion_state_dir().map(|d| d.join("gateway.log"))
}

pub fn chrome_log_path() -> Option<PathBuf> {
    companion_state_dir().map(|d| d.join("chrome.log"))
}

pub fn local_search_log_path() -> Option<PathBuf> {
    companion_state_dir().map(|d| d.join("local-search.log"))
}

// —————————————— tool-bridge ——————————————

pub fn tool_bridge_dir() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("CATFISH_TOOL_BRIDGE_DIR") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Some(path);
        }
    }
    catfish_root().map(|r| r.join("edge").join("tool-bridge"))
}

pub fn tool_bridge_pid_file() -> Option<PathBuf> {
    companion_state_dir().map(|d| d.join("tool-bridge.pid"))
}

pub fn tool_bridge_log_path() -> Option<PathBuf> {
    companion_state_dir().map(|d| d.join("tool-bridge.log"))
}

/// tool-bridge 的 endpoint 文件路径 (与 Python 端 default 对齐).
///
/// Unix: 这个路径就是 unix domain socket 文件本体.
/// Windows: BL-WIN8 (5/8) — Windows 没 unix socket, 文件内容改成 ASCII 端口号
///   (e.g. "54321"), 客户端读出来 connect("127.0.0.1:<port>"). 命名仍叫
///   .sock 以保持 mac/win 行为一致, Python 端 default 也是这条.
pub fn tool_bridge_socket() -> Option<PathBuf> {
    home_dir().map(|h| h.join(".catfish").join("tool-bridge.sock"))
}

/// tool-bridge 必须复用 hermes-agent 的 venv（tool 依赖都在那）
pub fn tool_bridge_python() -> Option<PathBuf> {
    // hermes-agent 在 ~/.hermes/hermes-agent 有自己的 venv
    let h = home_dir()?;
    let venv_py = h
        .join(".hermes")
        .join("hermes-agent")
        .join("venv")
        .join("bin")
        .join("python");
    if venv_py.exists() {
        return Some(venv_py);
    }
    let venv_py_win = h
        .join(".hermes")
        .join("hermes-agent")
        .join("venv")
        .join("Scripts")
        .join("python.exe");
    if venv_py_win.exists() {
        return Some(venv_py_win);
    }
    Some(PathBuf::from("python3"))
}

// —————————————— local-search ——————————————

pub fn local_search_dir() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("CATFISH_LOCAL_SEARCH_DIR") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Some(path);
        }
    }
    catfish_root().map(|r| r.join("edge").join("local-search"))
}

/// 找 catfish-search 用的 Python：
///   1. local-search 自带 venv
///   2. fallback 到 gateway 的 venv（一般员工只装 gateway 的 venv，
///      装上 watchdog 后能复用）
///   3. 系统 python3
pub fn local_search_python() -> Option<PathBuf> {
    let dir = local_search_dir()?;
    let local_venv = dir.join("venv").join("bin").join("python");
    if local_venv.exists() {
        return Some(local_venv);
    }
    let local_venv_win = dir.join("venv").join("Scripts").join("python.exe");
    if local_venv_win.exists() {
        return Some(local_venv_win);
    }
    if let Some(gw_py) = gateway_python() {
        return Some(gw_py);
    }
    Some(PathBuf::from("python3"))
}

/// Companion 自己启动 Chrome 时用的隔离 user-data-dir
/// —— 不复用员工日常 Chrome profile，避免污染 cookies / 误退他们的浏览器
pub fn chrome_user_data_dir() -> Option<PathBuf> {
    companion_state_dir().map(|d| d.join("chrome-profile"))
}

/// hermes 主 config 路径 (~/.hermes/config.yaml)
pub fn hermes_config_path() -> Option<PathBuf> {
    home_dir().map(|h| h.join(".hermes").join("config.yaml"))
}

/// 找 Chrome 二进制（Mac 优先，Windows 备选）
pub fn find_chrome() -> Option<PathBuf> {
    let candidates: &[&str] = &[
        // macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        "/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        // Windows（开发期主要 Mac 跑）
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    ];
    candidates
        .iter()
        .map(PathBuf::from)
        .find(|p| p.exists())
}
