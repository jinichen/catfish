//! 「接入 MCP」的两件事 (10/31 鸿波接 12306-mcp 踩的坑):
//!
//! 1. **npx / npm / node 找不到**: Hermes 只认它自己包管理目录 (`<hermes>/tools/node-*`) 里的 Node,
//!    老安装放在 `<hermes>/node/` 的那份它不认, 又默认禁止现场下载 —— 填 `npx` 的 MCP 直接起不来,
//!    小鲶看不到工具就去网页上抓, 员工完全不知道没接上。这里接入前先替员工找好 Node:
//!    Hermes 管得了就原样交给它; 管不了就换成本机现有 Node 的绝对路径, 并把它的目录放进这个
//!    MCP 的 PATH (npm 装依赖时的脚本还要再找 `node`)。哪儿都没有就直接说, 不写配置。
//! 2. **接上没有**: 写完配置跑一次 `hermes mcp test <名字>` (真连上去列工具)。连不上就撤掉
//!    这条配置、把原因原样告诉员工; 连上了再让 Hermes 重新加载, 小鲶这一轮之后就能用。

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use serde::Serialize;

/// 一条 MCP 的启动方式 (写进 `mcp_servers.<名字>`)
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Launch {
    pub command: String,
    /// 需要额外放进这个 MCP 环境的 PATH (None = 不加 env)
    pub path_env: Option<String>,
}

/// npx / npm / node 这三个要 Node 的启动器 (带不带 .cmd / .exe 都算)
fn node_launcher(command: &str) -> Option<String> {
    let base = Path::new(command).file_name()?.to_string_lossy().to_ascii_lowercase();
    let stem = base.trim_end_matches(".cmd").trim_end_matches(".exe").to_string();
    matches!(stem.as_str(), "npx" | "npm" | "node").then_some(stem)
}

fn base_path() -> String {
    if cfg!(windows) {
        let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
        format!(r"{root}\System32;{root}")
    } else {
        "/usr/bin:/bin".into()
    }
}

fn with_dir(dir: &Path) -> String {
    let sep = if cfg!(windows) { ";" } else { ":" };
    format!("{}{sep}{}", dir.display(), base_path())
}

/// Hermes 自己的包管理里有没有 Node (有就让 Hermes 管, 别插手)
fn hermes_manages_node(hermes_home: &Path) -> bool {
    std::fs::read_dir(hermes_home.join("tools"))
        .map(|rd| rd.flatten().any(|e| {
            let n = e.file_name().to_string_lossy().to_ascii_lowercase();
            n.starts_with("node-") || n.starts_with("npm-")
        }))
        .unwrap_or(false)
}

/// 老安装把 Node 放在 `<hermes>/node/`: mac / linux 在 bin/ 下, Windows 直接在根下 (npx.cmd)
fn legacy_hermes_node(hermes_home: &Path, launcher: &str) -> Option<PathBuf> {
    let root = hermes_home.join("node");
    let candidates = if cfg!(windows) {
        vec![root.join(format!("{launcher}.cmd")), root.join(format!("{launcher}.exe"))]
    } else {
        vec![root.join("bin").join(launcher)]
    };
    candidates.into_iter().find(|p| p.exists())
}

/// 员工填的命令 → 实际写进配置的启动方式。`which` 查系统 PATH (测试里换成假的)。
pub(crate) fn resolve_launch(command: &str, hermes_home: &Path, which: impl Fn(&str) -> Option<PathBuf>) -> Result<Launch, String> {
    let Some(launcher) = node_launcher(command) else {
        return Ok(Launch { command: command.to_string(), path_env: None });
    };
    let p = Path::new(command);
    if p.is_absolute() {
        // 绝对路径的 npx: 它的目录得在 PATH 上 (npx 脚本和 npm 装依赖的脚本都要再找 node)
        let dir = p.parent().ok_or("命令路径不对")?;
        return Ok(Launch { command: command.to_string(), path_env: Some(with_dir(dir)) });
    }
    if hermes_manages_node(hermes_home) {
        return Ok(Launch { command: command.to_string(), path_env: None });
    }
    let found = legacy_hermes_node(hermes_home, &launcher).or_else(|| which(&launcher));
    match found {
        Some(abs) => {
            let dir = abs.parent().map(Path::to_path_buf).unwrap_or_default();
            Ok(Launch { command: abs.to_string_lossy().into_owned(), path_env: Some(with_dir(&dir)) })
        }
        None => Err(format!(
            "这台电脑上找不到 Node.js, `{command}` 类的 MCP 起不来。先装 Node.js (nodejs.org, 18 以上), 再来接入"
        )),
    }
}

/// 在系统 PATH 里找 (外加 Homebrew 两个常见目录: 从 Dock 启动的 mac 应用 PATH 里没有它们)
pub(crate) fn find_on_path(name: &str) -> Option<PathBuf> {
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH").map(|p| std::env::split_paths(&p).collect()).unwrap_or_default();
    if cfg!(target_os = "macos") {
        dirs.extend(["/opt/homebrew/bin", "/usr/local/bin"].map(PathBuf::from));
    }
    let names: Vec<String> = if cfg!(windows) {
        vec![format!("{name}.cmd"), format!("{name}.exe"), name.to_string()]
    } else {
        vec![name.to_string()]
    };
    dirs.iter().flat_map(|d| names.iter().map(move |n| d.join(n))).find(|p| p.is_file())
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct McpTestResult {
    pub ok: bool,
    /// 连上后发现的工具数
    pub tools: u32,
    /// 给员工看的原因 / 摘要 (去掉颜色码)
    pub detail: String,
}

fn strip_ansi(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut it = s.chars().peekable();
    while let Some(c) = it.next() {
        if c == '\u{1b}' {
            // ESC [ ... 字母
            if it.peek() == Some(&'[') {
                it.next();
                for d in it.by_ref() {
                    if d.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        out.push(c);
    }
    out
}

/// 从 `hermes mcp test` 的输出里摘结果
pub(crate) fn parse_test_output(code: Option<i32>, out: &str) -> McpTestResult {
    let text = strip_ansi(out);
    let tools = text
        .lines()
        .find_map(|l| l.split("Tools discovered:").nth(1))
        .and_then(|n| n.trim().parse().ok())
        .unwrap_or(0);
    let reason = text
        .lines()
        .map(str::trim)
        .filter(|l| l.contains("Connection failed") || l.starts_with("✗") || l.to_ascii_lowercase().contains("error"))
        .collect::<Vec<_>>()
        .join("\n");
    let ok = code == Some(0);
    McpTestResult {
        ok,
        tools,
        detail: if ok {
            format!("连上了, 有 {tools} 个工具")
        } else if reason.is_empty() {
            text.trim().chars().take(600).collect()
        } else {
            reason
        },
    }
}

/// 跑 `hermes mcp test <名字>`。第一次跑 npx 要现场下载, 给足 3 分钟。
pub(crate) fn hermes_mcp_test(name: &str) -> Result<McpTestResult, String> {
    let home = crate::services::catfish_paths::hermes_home().ok_or("找不到 Hermes 目录")?;
    let cli = crate::services::catfish_paths::hermes_venv_tool(&home.join("hermes-agent"), "hermes");
    if !cli.exists() {
        return Err(format!("Hermes 还没装好 ({} 不在)", cli.display()));
    }
    let mut child = crate::services::process::python_command(&cli)
        .args(["mcp", "test", name])
        .env("HERMES_HOME", &home)
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("起不了 hermes ({e})"))?;
    let (mut so, mut se) = (child.stdout.take().unwrap(), child.stderr.take().unwrap());
    let rd_out = std::thread::spawn(move || { let mut s = String::new(); let _ = so.read_to_string(&mut s); s });
    let rd_err = std::thread::spawn(move || { let mut s = String::new(); let _ = se.read_to_string(&mut s); s });
    let deadline = Instant::now() + Duration::from_secs(180);
    let code = loop {
        match child.try_wait() {
            Ok(Some(st)) => break st.code(),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(300)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Ok(McpTestResult { ok: false, tools: 0, detail: "3 分钟还没连上 (下载太慢或程序卡住了)".into() });
            }
        }
    };
    let out = format!("{}\n{}", rd_out.join().unwrap_or_default(), rd_err.join().unwrap_or_default());
    Ok(parse_test_output(code, &out))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 不是_node_类的命令原样() {
        let t = tempfile::tempdir().unwrap();
        let l = resolve_launch("uvx", t.path(), |_| None).unwrap();
        assert_eq!(l, Launch { command: "uvx".into(), path_env: None });
    }

    #[test]
    fn hermes_自己管_node_就交给它() {
        let t = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(t.path().join("tools/node-22.1.0-darwin-arm64")).unwrap();
        assert_eq!(resolve_launch("npx", t.path(), |_| None).unwrap().path_env, None);
    }

    #[cfg(unix)]
    #[test]
    fn hermes_不管_用老位置的_node_并放进_path() {
        let t = tempfile::tempdir().unwrap();
        let bin = t.path().join("node/bin");
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(bin.join("npx"), "").unwrap();
        let l = resolve_launch("npx", t.path(), |_| None).unwrap();
        assert_eq!(l.command, bin.join("npx").to_string_lossy());
        assert!(l.path_env.unwrap().starts_with(&*bin.to_string_lossy()));
        // 哪儿都没有 → 不写配置, 直接说
        let empty = tempfile::tempdir().unwrap();
        assert!(resolve_launch("npx", empty.path(), |_| None).unwrap_err().contains("Node.js"));
        // 系统里有 → 用系统的
        let l = resolve_launch("npx", empty.path(), |_| Some(PathBuf::from("/opt/homebrew/bin/npx"))).unwrap();
        assert_eq!(l.command, "/opt/homebrew/bin/npx");
        assert!(l.path_env.unwrap().starts_with("/opt/homebrew/bin:"));
    }

    #[test]
    fn 读_hermes_mcp_test_的结果() {
        let ok = parse_test_output(Some(0), "\u{1b}[36m  Testing '12306'...\u{1b}[0m\n  ✓ Connected (812ms)\n  ✓ Tools discovered: 10\n");
        assert!(ok.ok && ok.tools == 10, "{ok:?}");
        let bad = parse_test_output(Some(1), "  Testing 'x'...\n  ✗ Connection failed (3.1s): npm: not installed\n");
        assert!(!bad.ok && bad.detail.contains("npm: not installed"), "{bad:?}");
    }
}
