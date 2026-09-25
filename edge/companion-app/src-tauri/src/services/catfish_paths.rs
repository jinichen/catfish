//! 解析 Catfish 各组件在当前平台上的路径。
//!
//! 优先级：
//!   1. 环境变量 `CATFISH_ROOT` / `CATFISH_GATEWAY_DIR` 显式指向（部署时用）
//!   2. `~/person_task/catfish` —— 当前开发约定位置
//!   3. `~/catfish` —— 退而求其次
//!
//! Companion 自己的运行时状态（PID 文件、log 文件）放在 `<root>/.companion-state/`
//! 跟 catfish 项目放一起，员工 `rm -rf catfish` 时一起干净。

use std::path::{Path, PathBuf};

pub(super) fn home_dir() -> Option<PathBuf> {
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
///
/// 9/23: 原来只有 `catfish_root()?/.companion-state` —— 客户机器上没有源码树,
/// 于是这里是 None, tool-bridge / local-search / chrome 的 pid 和日志路径全是
/// None, autostart 直接 return (而且不说为什么)。没有源码树时落 `~/.catfish/`。
pub fn companion_state_dir() -> Option<PathBuf> {
    let dir = match catfish_root() {
        Some(r) => r.join(".companion-state"),
        None => home_dir()?.join(".catfish").join("companion-state"),
    };
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

/// 源码树里的组件目录; 没有源码树 (客户机器) 时退到安装包解出来的那份 (9/23)。
///
/// 顺序: 源码树 > `~/.catfish/edge-runtime/<name>` (services::edge_runtime 解的)。
/// 两个都不存在 → None, watchdog 据此判定"没装"而不是"死了"。
fn edge_component_dir(name: &str) -> Option<PathBuf> {
    if let Some(p) = catfish_root().map(|r| r.join("edge").join(name)) {
        if p.exists() {
            return Some(p);
        }
    }
    let p = crate::services::edge_runtime::runtime_root()?.join(name);
    p.exists().then_some(p)
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
    edge_component_dir("tool-bridge")
}

// —————————————— catfish-memory plugin (P3.5.1 Dream Engine) ——————————————

/// catfish-memory plugin 目录 — 含 catfish_memory.py + dream_cli.py.
///
/// P3.5.1.7 (6/15 鸿波撞 chunk 0/?): catfish-memory/ 目录名带横线, 不是合法 Python
/// module 名. 老 `python -m catfish_memory.dream_cli` 找不到 module → import error
/// → UI 卡 "chunk 0/?". 改 spawn 绝对路径 dream_cli.py, 它内部 sys.path 加自己 dir,
/// `from catfish_memory import ...` 拿同目录 catfish_memory.py 文件作 module.
///
/// 优先级: env CATFISH_MEMORY_PLUGIN_DIR > catfish_root()/edge/hermes-plugins/catfish-memory
pub fn catfish_memory_plugin_dir() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("CATFISH_MEMORY_PLUGIN_DIR") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Some(path);
        }
    }
    if let Some(p) = catfish_root().map(|r| r.join("edge").join("hermes-plugins").join("catfish-memory")) {
        if p.exists() {
            return Some(p);
        }
    }
    // 9/23: 客户机器没有源码树。安装包把 catfish-memory 装进了 hermes-agent 的
    // plugins/memory/ (build-windows-msi.yml "Copy catfish plugins" 那步; mac 同)。
    let p = hermes_home()?.join("hermes-agent").join("plugins").join("memory").join("catfish-memory");
    p.exists().then_some(p)
}

/// Dream Engine CLI 绝对路径 — companion Rust spawn 用.
pub fn dream_cli_path() -> Option<PathBuf> {
    catfish_memory_plugin_dir().map(|d| d.join("dream_cli.py"))
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

/// 教学凭据取值通道 (8/19). 跟上面那条**方向相反** — 这条是 tool-bridge 问、
/// Companion 答, server 在 Companion 这边 (commands/teaching_credentials/socket.rs).
///
/// macOS only: 存在的理由是钥匙串按二进制授权, Windows 凭据管理器没这回事.
/// Python 端同一个路径写死在 companion_secrets.py::socket_path().
///
/// ⚠ 用 $HOME 不用 CATFISH_HOME — 跟 tool_bridge_socket / __main__.py:18 一致.
///   两端必须指同一个文件, 多认一个 env 只会让设了它的机器上两端错开.
#[cfg(target_os = "macos")]
pub fn companion_secrets_socket() -> Option<PathBuf> {
    home_dir().map(|h| h.join(".catfish").join("companion-secrets.sock"))
}

/// tool-bridge 必须复用 hermes-agent 的 venv（tool 依赖都在那）
///
/// 9/22: 走 hermes_home()。原来写死 `~/.hermes/hermes-agent/venv/...`, 而 Windows
/// 安装器装在 `%LOCALAPPDATA%\hermes` —— 于是 Windows 上永远找不到, 退回裸
/// `python3` (Windows 上要么不存在, 要么是 Store 的占位 stub), tool-bridge 起不来。
///
/// 这是鸿波 9/22 截图里「四个本地服务全红」的直接原因: 不是四个 bug, 是
/// 同一个路径假设。local-search / chrome 同款。
pub fn tool_bridge_python() -> Option<PathBuf> {
    let venv = hermes_home()?.join("hermes-agent").join("venv");
    let venv_py = venv.join("bin").join("python");
    if venv_py.exists() {
        return Some(venv_py);
    }
    let venv_py_win = venv.join("Scripts").join("python.exe");
    if venv_py_win.exists() {
        return Some(venv_py_win);
    }
    // ⚠ 找不到时**出声**再退回。原来静默退回 python3, 症状是 tool-bridge
    //   "未启动"三个字, 为什么没启动一个字都不说 —— 排查靠猜。
    log::warn!(
        "hermes venv 里找不到 python ({}), 退回 PATH 上的 python3 —— \
         tool-bridge 多半起不来。hermes 装完了吗?",
        venv.display()
    );
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
    edge_component_dir("local-search")
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
    // 9/23: hermes venv 放在 gateway venv 前面 —— 客户机器上没有 gateway venv
    // (那是中央端的东西), 而 hermes venv 一定在, 且带 pyyaml (local-search 唯一依赖)。
    // Windows 上最后那个 "python3" 基本不存在, 走不到那一步才对。
    if let Some(hermes_py) = hermes_home().map(|h| hermes_venv_python(&h.join("hermes-agent"))) {
        if hermes_py.exists() {
            return Some(hermes_py);
        }
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
    hermes_home().map(|h| h.join("config.yaml"))
}

// —————————————— 可执行文件查找 (跨平台) ——————————————
//
// 8/10: commands/email.rs 和 services/email_scheduler.rs 里各有一份一模一样的
// find_catfish_email(), 都是这个写法:
//
//     home.join(".local/bin/catfish-email")          // Unix 的路径约定
//     Command::new("which").arg("catfish-email")     // Windows 上没有 which
//
// 两条候选在 Windows 上都不成立, 于是 Companion 的 14 个邮件命令和后台扫描器
// 全部拿不到 CLI。而 scheduler 的红线写着"失败静默", 所以员工看到的是: 邮件页
// 完整显示、没有任何报错、什么都不工作。
//
// 对照组: tool-bridge 的 Python 版用 shutil.which(), 那个本来就是跨平台的
// (会处理 PATHEXT)。同一个功能, Rust 那条路死、Python 那条路活。
//
// 这里不是把 `which` 换成 `where` —— 那还是在 shell out, 依赖外部命令存在、
// 依赖它的输出格式。直接用 std::env::split_paths 自己走一遍 PATH, 没有子进程,
// 顺带能被单测覆盖。

/// 这个路径是不是一个能执行的文件。
///
/// Unix 上光 is_file() 不够 —— 同名但没有执行位的文件会被选中, 然后在
/// spawn 时才报 Permission denied, 那时已经离查找逻辑很远了。
#[cfg(unix)]
fn is_executable(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p)
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

#[cfg(not(unix))]
fn is_executable(p: &Path) -> bool {
    p.is_file()
}

/// Windows 上要试的可执行后缀; 非 Windows 返空。
///
/// pip 装的 console_script 在 Windows 上是 `catfish-email.exe`, 在 Unix 上是
/// 无后缀的 `catfish-email` —— 同一个包, 两个名字。
fn path_exts() -> Vec<String> {
    if !cfg!(windows) {
        return Vec::new();
    }
    std::env::var("PATHEXT")
        .unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".to_string())
        .split(';')
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .collect()
}

/// 在给定目录列表里找可执行文件。先试原名, 再依次试各后缀。
///
/// 目录和后缀都是**参数**而不是从环境里读 —— 这样 Windows 才有的行为
/// (`.exe` 后缀) 在 mac 上也能被单测覆盖。不然这段代码只有 Windows 员工
/// 能验证, 而那正是当初出问题的原因。
///
/// 注: 返回的是**拼接出来**的路径, 不是磁盘上的规范名。在大小写不敏感的
/// 文件系统上 (NTFS / 默认的 APFS), 拼出来的 `x.EXE` 会命中磁盘上的
/// `x.exe`, 返回值里保留的是前者。对 Command::new 没影响, 日志里看着可能
/// 跟 `dir` 的输出对不上。这里刻意不 canonicalize —— 那会把 ~/.local/bin
/// 下那个指向 venv 的软链解析掉, 而那条软链是 install.sh 特意做的。
fn find_in_dirs(name: &str, dirs: &[PathBuf], exts: &[String]) -> Option<PathBuf> {
    for dir in dirs {
        // PATH 里的空段按 POSIX 是"当前目录"。不接受 —— 从 cwd 捡二进制
        // 是个安全问题, 而且 Companion 的 cwd 是什么完全不可控。
        if dir.as_os_str().is_empty() {
            continue;
        }
        let bare = dir.join(name);
        if is_executable(&bare) {
            return Some(bare);
        }
        for ext in exts {
            let cand = dir.join(format!("{name}{ext}"));
            if is_executable(&cand) {
                return Some(cand);
            }
        }
    }
    None
}

/// 在真实 PATH 里找可执行文件。跨平台, 不 shell out。
pub fn find_on_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let dirs: Vec<PathBuf> = std::env::split_paths(&path).collect();
    find_in_dirs(name, &dirs, &path_exts())
}

/// hermes 安装根目录。
///
/// Windows 是 per-user 装到 `%LOCALAPPDATA%\hermes`, **不是** `~/.hermes`
/// —— 见 resources/windows/_phase1_win_install_hermes.ps1:99
/// (`$HermesHome = if ($env:HERMES_HOME) {...} else { "$env:LOCALAPPDATA\hermes" }`)。
///
/// 注: 本文件其它地方 (tool_bridge_python / hermes_config_path) 仍写死
/// `~/.hermes`, 全 Rust 代码里这样的还有 40 多处。那是同一类 Windows 缺口,
/// 但面比邮件大得多, 没在这次一起动。
pub fn hermes_home() -> Option<PathBuf> {
    Some(hermes_home_for(&home_dir()?))
}

/// Hermes 的用户数据目录。Windows 安装器使用 `%LOCALAPPDATA%\hermes`，
/// Unix 使用 `~/.hermes`；显式 HERMES_HOME 始终优先。
pub fn hermes_home_for(home: &Path) -> PathBuf {
    if let Some(p) = std::env::var_os("HERMES_HOME") {
        let path = PathBuf::from(p);
        if !path.as_os_str().is_empty() {
            return path;
        }
    }
    // 不加 cfg(windows)：测试可以在 macOS 上复现 Windows 的路径选择；正常
    // macOS 环境没有 LOCALAPPDATA，因此仍然回落到 ~/.hermes。
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        return PathBuf::from(local).join("hermes");
    }
    home.join(".hermes")
}

/// Hermes venv 的 Python 路径。
pub fn hermes_venv_python(install_dir: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        return install_dir.join("venv").join("Scripts").join("python.exe");
    }
    #[cfg(not(windows))]
    install_dir.join("venv").join("bin").join("python")
}

/// Hermes venv 内 console script 的路径。
pub fn hermes_venv_tool(install_dir: &Path, name: &str) -> PathBuf {
    #[cfg(windows)]
    {
        return install_dir
            .join("venv")
            .join("Scripts")
            .join(format!("{name}.exe"));
    }
    #[cfg(not(windows))]
    install_dir.join("venv").join("bin").join(name)
}

/// Hermes state.db 的统一路径，避免 Companion 在 Windows 读错到 `~/.hermes`。
pub fn hermes_state_db_path() -> Option<PathBuf> {
    hermes_home().map(|path| path.join("state.db"))
}

/// catfish-email CLI 的绝对路径。
///
/// 候选顺序 —— 每一条都对应一个**实际存在的安装路径**, 不是猜的:
///   1. env `CATFISH_EMAIL_BIN`  —— 跟本模块其它 env-first 约定一致
///   2. hermes venv             —— email-agent/install.sh 就是 pip install 到这
///   3. `~/.catfish/venv`       —— Windows 的 onboarding/install-catfish.ps1 建的
///   4. `~/.local/bin`          —— install.sh 额外做的软链 (Unix)
///   5. PATH                    —— 上面都没有时的兜底
pub fn catfish_email_bin() -> Option<PathBuf> {
    const NAME: &str = "catfish-email";

    if let Some(p) = std::env::var_os("CATFISH_EMAIL_BIN") {
        let path = PathBuf::from(p);
        if path.is_file() {
            return Some(path);
        }
    }

    let exts = path_exts();

    if let Some(h) = hermes_home() {
        #[cfg(windows)]
        if let Some(scripts) = super::email_runtime::scripts(&h) {
            return Some(scripts.join("catfish-email.exe"));
        }
        let venv = h.join("hermes-agent").join("venv");
        let dirs = [venv.join("bin"), venv.join("Scripts")];
        if let Some(p) = find_in_dirs(NAME, &dirs, &exts) {
            return Some(p);
        }
    }

    if let Some(home) = home_dir() {
        let venv = home.join(".catfish").join("venv");
        let dirs = [
            venv.join("bin"),
            venv.join("Scripts"),
            home.join(".local").join("bin"),
        ];
        if let Some(p) = find_in_dirs(NAME, &dirs, &exts) {
            return Some(p);
        }
    }

    find_on_path(NAME)
}

pub use super::browser_paths::find_chrome;

#[cfg(test)]
#[path = "catfish_paths_tests.rs"]
mod tests;
