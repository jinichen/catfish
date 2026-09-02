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
    catfish_root().map(|r| r.join("edge").join("hermes-plugins").join("catfish-memory"))
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

/// 找 Chrome 二进制 (mac / Windows / Linux 全平台).
///
/// BL-WIN3 (5/8): Windows 候选扩到 7 条 — Per-user 安装 (%LOCALAPPDATA%\Google),
/// 标准 Program Files (双架构), Edge 作为最后备选 (Win11 自带, Chromium 内核
/// 兼容 Playwright). mac 候选不变. 通过环境变量动态查 (LOCALAPPDATA / ProgramFiles
/// / ProgramFiles(x86)) 免硬编盘符. 用 ``CATFISH_CHROME_BIN`` env 强制覆盖.
pub fn find_chrome() -> Option<PathBuf> {
    // 1. env 强制覆盖 (员工 / 开发者用)
    if let Ok(custom) = std::env::var("CATFISH_CHROME_BIN") {
        let p = PathBuf::from(custom);
        if p.exists() {
            return Some(p);
        }
    }

    // 2. 平台候选清单
    let mut candidates: Vec<PathBuf> = vec![
        // macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome".into(),
        "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta".into(),
        "/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev".into(),
        "/Applications/Chromium.app/Contents/MacOS/Chromium".into(),
    ];
    if let Some(home) = home_dir() {
        // mac per-user: ~/Applications/Google Chrome.app/...
        candidates.push(home.join("Applications/Google Chrome.app/Contents/MacOS/Google Chrome"));
    }

    // Windows
    if let Ok(local_appdata) = std::env::var("LOCALAPPDATA") {
        // Per-user 安装 (现在 Windows 主流)
        candidates.push(PathBuf::from(&local_appdata).join("Google/Chrome/Application/chrome.exe"));
        candidates.push(PathBuf::from(&local_appdata).join("Google/Chrome SxS/Application/chrome.exe")); // Canary
    }
    if let Ok(pf) = std::env::var("ProgramFiles") {
        candidates.push(PathBuf::from(&pf).join("Google/Chrome/Application/chrome.exe"));
        // Edge 作为 fallback (Win11 自带 Chromium 内核)
        candidates.push(PathBuf::from(&pf).join("Microsoft/Edge/Application/msedge.exe"));
    }
    if let Ok(pf86) = std::env::var("ProgramFiles(x86)") {
        candidates.push(PathBuf::from(&pf86).join("Google/Chrome/Application/chrome.exe"));
        candidates.push(PathBuf::from(&pf86).join("Microsoft/Edge/Application/msedge.exe"));
    }
    // Hard-coded fallback (env 没设的极端情况)
    candidates.push("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe".into());
    candidates.push("C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe".into());

    // Linux (开发用, 非主目标)
    candidates.push("/usr/bin/google-chrome".into());
    candidates.push("/usr/bin/google-chrome-stable".into());
    candidates.push("/usr/bin/chromium".into());
    candidates.push("/usr/bin/chromium-browser".into());

    candidates.into_iter().find(|p| p.exists())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    /// 造一个"能执行"的文件。Unix 上必须真的 chmod +x —— is_executable
    /// 查执行位, 只 write 出来的文件不算。
    fn touch_exe(dir: &Path, name: &str) -> PathBuf {
        let p = dir.join(name);
        std::fs::write(&p, b"#!/bin/sh\nexit 0\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        p
    }

    fn exts(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    // ── find_in_dirs: 核心查找逻辑 ────────────────────────────────

    #[test]
    fn finds_bare_name_unix_style() {
        let tmp = TempDir::new().unwrap();
        let want = touch_exe(tmp.path(), "catfish-email");
        let got = find_in_dirs("catfish-email", &[tmp.path().to_path_buf()], &[]);
        assert_eq!(got.as_deref(), Some(want.as_path()));
    }

    #[test]
    fn finds_extension_when_bare_name_is_missing() {
        // 这条是本次修复的重点: Windows 上 pip 装出来的是 catfish-email.exe,
        // 无后缀的名字根本不存在, 而原来的代码只找无后缀名 + shell out
        // `which`, 两条都不成立。后缀是参数传进来的, 所以这个纯 Windows 的
        // 行为在 mac 上也测得到。
        //
        // ── 8/10 这条第一版是挂的, 值得留个记录 ──────────────────────
        //
        // 那一版造的是小写 `catfish-email.exe`, 搜的是大写 `.EXE`
        // (真实 PATHEXT 就是大写)。mac 上跑出来:
        //     left:  ".../catfish-email.EXE"   ← 我们拼出来的
        //     right: ".../catfish-email.exe"   ← 磁盘上真实的
        //
        // 挂的原因**不是没找到**: APFS 大小写不敏感, `.EXE` 确实命中了那个
        // 文件; find_in_dirs 返回的是拼接出来的路径而不是磁盘上的真名。功能
        // 上没问题 —— NTFS 同样不敏感, Command::new 照跑 —— 是断言把"找到了"
        // 和"拼写跟我造的一致"混成了一件事。
        //
        // 真正的隐患在第二层: 那一版的结果取决于**文件系统区不区分大小写**。
        // mac 上(断言写对的话)过, Linux CI 上会返 None 直接挂。"换台机器就翻"
        // 的测试比没有还糟。
        //
        // 所以这里造和搜用同一个大小写, 结论跟文件系统无关。
        // 小写文件 + 大写 PATHEXT 那条路能通, 靠的是 Windows 文件系统的性质,
        // 不是我们的代码 —— 不该由我们的单测去断言别人的行为。
        let tmp = TempDir::new().unwrap();
        let want = touch_exe(tmp.path(), "catfish-email.EXE");
        let got = find_in_dirs(
            "catfish-email",
            &[tmp.path().to_path_buf()],
            &exts(&[".COM", ".EXE"]),
        );
        assert_eq!(
            got.as_deref(),
            Some(want.as_path()),
            "带后缀的没找到 —— Windows 上 CLI 就只有这一个名字"
        );
    }

    #[test]
    fn extension_order_is_respected() {
        // PATHEXT 的先后就是 Windows 的解析顺序。两个都在时必须挑排在前面的,
        // 不然行为跟 Windows 自己解析命令的结果对不上。
        let tmp = TempDir::new().unwrap();
        let first = touch_exe(tmp.path(), "catfish-email.COM");
        touch_exe(tmp.path(), "catfish-email.EXE");
        let got = find_in_dirs(
            "catfish-email",
            &[tmp.path().to_path_buf()],
            &exts(&[".COM", ".EXE"]),
        );
        assert_eq!(got.as_deref(), Some(first.as_path()), "后缀顺序没生效");
    }

    #[test]
    fn bare_name_beats_extensions() {
        // Unix 上装的是无后缀名。万一同目录还有个同名带后缀的, 也得挑无后缀那个
        // —— exts 是"补充尝试", 不该盖过原名。
        let tmp = TempDir::new().unwrap();
        let bare = touch_exe(tmp.path(), "catfish-email");
        touch_exe(tmp.path(), "catfish-email.COM");
        let got = find_in_dirs(
            "catfish-email",
            &[tmp.path().to_path_buf()],
            &exts(&[".COM", ".EXE"]),
        );
        assert_eq!(got.as_deref(), Some(bare.as_path()));
    }

    #[test]
    fn returns_none_when_nothing_matches() {
        let tmp = TempDir::new().unwrap();
        touch_exe(tmp.path(), "something-else");
        assert!(find_in_dirs("catfish-email", &[tmp.path().to_path_buf()], &exts(&[".EXE"])).is_none());
    }

    #[test]
    fn earlier_dir_wins() {
        let a = TempDir::new().unwrap();
        let b = TempDir::new().unwrap();
        let first = touch_exe(a.path(), "catfish-email");
        touch_exe(b.path(), "catfish-email");
        let got = find_in_dirs(
            "catfish-email",
            &[a.path().to_path_buf(), b.path().to_path_buf()],
            &[],
        );
        assert_eq!(got.as_deref(), Some(first.as_path()), "候选顺序没生效");
    }

    #[test]
    fn skips_empty_dir_entries() {
        // PATH 里的空段按 POSIX 是 cwd。不能从 cwd 捡二进制。
        let tmp = TempDir::new().unwrap();
        touch_exe(tmp.path(), "catfish-email");
        let dirs = [PathBuf::from(""), tmp.path().to_path_buf()];
        let got = find_in_dirs("catfish-email", &dirs, &[]);
        assert_eq!(got.as_deref(), Some(tmp.path().join("catfish-email").as_path()));
    }

    #[cfg(unix)]
    #[test]
    fn non_executable_file_is_not_picked() {
        let tmp = TempDir::new().unwrap();
        std::fs::write(tmp.path().join("catfish-email"), b"not executable").unwrap();
        assert!(
            find_in_dirs("catfish-email", &[tmp.path().to_path_buf()], &[]).is_none(),
            "没有执行位的同名文件被选中了 —— spawn 时才会报 Permission denied"
        );
    }

    // ── 环境相关 (要拿 env 锁, 见 util/test_env.rs) ────────────────

    /// 改一个环境变量, drop 时**恢复原值**。
    ///
    /// 第一版这几条测试是直接 `remove_var` 收尾的。问题是 HERMES_HOME 在
    /// 开发机上完全可能本来就设着 (hermes 装在非默认位置) —— 无脑 remove
    /// 等于把它对整个测试进程抹掉, 而后面 curator_config / curator_state /
    /// memory_provider_config / identity_bundle / hermes_plugin /
    /// codex_backend 六处都读这个变量, 会静静掉回 ~/.hermes。
    ///
    /// 顺带解决 panic 泄漏: 断言失败时 remove_var 那一行根本不会执行,
    /// 变量就留给后面所有测试了。Drop 在 unwind 时照跑。
    ///
    /// 这个仓库为跨测试的 env 污染写过 util/test_env.rs 整整一篇, 不该再添一个。
    struct EnvGuard {
        key: &'static str,
        old: Option<std::ffi::OsString>,
    }

    impl EnvGuard {
        fn set(key: &'static str, val: impl AsRef<std::ffi::OsStr>) -> Self {
            let old = std::env::var_os(key);
            std::env::set_var(key, val);
            Self { key, old }
        }

        fn unset(key: &'static str) -> Self {
            let old = std::env::var_os(key);
            std::env::remove_var(key);
            Self { key, old }
        }
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            match &self.old {
                Some(v) => std::env::set_var(self.key, v),
                None => std::env::remove_var(self.key),
            }
        }
    }

    #[test]
    fn catfish_email_bin_honors_explicit_env() {
        let _guard = crate::util::test_env::env_lock();
        let tmp = TempDir::new().unwrap();
        let bin = touch_exe(tmp.path(), "catfish-email");
        let _e = EnvGuard::set("CATFISH_EMAIL_BIN", &bin);
        assert_eq!(catfish_email_bin().as_deref(), Some(bin.as_path()));
    }

    #[test]
    fn catfish_email_bin_finds_it_in_hermes_venv() {
        // 复现 install.sh 的落点: <hermes>/hermes-agent/venv/bin/catfish-email
        let _guard = crate::util::test_env::env_lock();
        let tmp = TempDir::new().unwrap();
        let bin_dir = tmp.path().join("hermes-agent").join("venv").join("bin");
        std::fs::create_dir_all(&bin_dir).unwrap();
        let want = touch_exe(&bin_dir, "catfish-email");

        let _e1 = EnvGuard::unset("CATFISH_EMAIL_BIN");
        let _e2 = EnvGuard::set("HERMES_HOME", tmp.path());
        assert_eq!(catfish_email_bin().as_deref(), Some(want.as_path()));
    }

    #[test]
    fn hermes_home_prefers_localappdata_over_dot_hermes() {
        // Windows 是 %LOCALAPPDATA%\hermes, 不是 ~/.hermes。
        // 不加 cfg(windows), 所以这条在 mac 上就能验。
        let _guard = crate::util::test_env::env_lock();
        let tmp = TempDir::new().unwrap();
        let hermes = tmp.path().join("hermes");
        std::fs::create_dir_all(&hermes).unwrap();

        let _e1 = EnvGuard::unset("HERMES_HOME");
        let _e2 = EnvGuard::set("LOCALAPPDATA", tmp.path());
        assert_eq!(hermes_home().as_deref(), Some(hermes.as_path()));
    }

    #[test]
    fn hermes_home_env_wins_over_everything() {
        let _guard = crate::util::test_env::env_lock();
        let tmp = TempDir::new().unwrap();
        let _e = EnvGuard::set("HERMES_HOME", tmp.path());
        assert_eq!(hermes_home().as_deref(), Some(tmp.path()));
    }

    #[test]
    fn env_guard_restores_previous_value() {
        // 守卫本身也要有人守 —— 写错了(比如存旧值那步漏了)的表现是"测试照样
        // 全绿, 但环境被悄悄改了", 跟没写一样。
        let _guard = crate::util::test_env::env_lock();
        const K: &str = "CATFISH_ENVGUARD_SELFTEST";

        std::env::set_var(K, "原值");
        {
            let _e = EnvGuard::set(K, "临时值");
            assert_eq!(std::env::var(K).as_deref(), Ok("临时值"));
        }
        assert_eq!(std::env::var(K).as_deref(), Ok("原值"), "没恢复回原值");

        {
            let _e = EnvGuard::unset(K);
            assert!(std::env::var_os(K).is_none());
        }
        assert_eq!(std::env::var(K).as_deref(), Ok("原值"), "unset 后没恢复");

        std::env::remove_var(K);
        {
            let _e = EnvGuard::set(K, "临时值");
        }
        assert!(
            std::env::var_os(K).is_none(),
            "本来没有的变量, 用完应该还是没有, 而不是留个空值"
        );
    }

    // ── 回归闸: 别再 shell out 到 which ─────────────────────────────

    #[test]
    fn email_code_does_not_shell_out_to_which() {
        // 这个 bug 的形态是"在 mac 上跑得好好的, Windows 上静默失效"。
        // 没有 Windows CI, 所以靠源码闸盯住 —— 谁再写回 `which` 会直接红。
        //
        // 只盯邮件这两个文件。commands/calendar.rs 里也有 which, 但那个找的是
        // catfish-calendar —— 一个装在 .app/Contents/Resources 里的 Swift
        // 二进制, macOS 专有, 它的 PATH 兜底在 Windows 上本来就不可达, 不是
        // 同一个问题。
        for (name, src) in [
            ("commands/email.rs", include_str!("../commands/email.rs")),
            ("services/email_scheduler.rs", include_str!("email_scheduler.rs")),
        ] {
            assert!(
                !src.contains("Command::new(\"which\")"),
                "{name} 里又出现了 `which` —— 那在 Windows 上不存在, \
                 用 catfish_paths::catfish_email_bin() / find_on_path()"
            );
            assert!(
                !src.contains(".local/bin/catfish-email"),
                "{name} 里又写死了 .local/bin —— 那是 Unix 的路径约定, \
                 Windows 上 CLI 在 venv 的 Scripts 目录"
            );
        }
    }
}
