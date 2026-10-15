//! One lifecycle for automatic/manual Chrome startup. Never trust a PID alone.
use std::{path::Path, time::Duration};
use super::{catfish_paths, endpoints, process};

pub(crate) static LIFECYCLE: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn launch_arguments(profile: &Path, port: u16) -> Vec<String> {
    let args = vec![
        format!("--remote-debugging-port={port}"),
        format!("--user-data-dir={}", profile.display()),
        "--no-first-run".into(), "--no-default-browser-check".into(),
        "--disable-features=DialMediaRouteProvider".into(),
        "--remote-allow-origins=*".into(), "about:blank".into(),
    ];
    #[cfg(test)]
    let args = if std::env::var_os("CATFISH_TEST_CHROME_BIN").is_some() {
        let mut args = args;
        args.push("--headless=new".into()); // Hosted CI has no interactive desktop.
        args
    } else { args };
    args
}

#[derive(serde::Deserialize)]
struct BrowserProcess {
    #[serde(rename = "ProcessId")]
    pid: u32,
    #[serde(rename = "CommandLine")]
    command: Option<String>,
}

fn owns_profile(command: &str, profile: &Path) -> bool {
    if command.contains("--type=") { return false; } // Never adopt a renderer.
    let flag = format!("--user-data-dir={}", profile.display());
    let exact = command_arguments(command).iter().any(|arg| {
        #[cfg(windows)]
        { arg.eq_ignore_ascii_case(&flag) }
        #[cfg(not(windows))]
        { arg == &flag }
    });
    if exact { return true; }
    // Unix ps renders argv without quoting paths containing spaces. Our launch
    // always places --no-first-run immediately after the profile argument.
    #[cfg(not(windows))]
    return command.contains(&format!(" {flag} --no-first-run"));
    #[cfg(windows)]
    false
}

fn command_arguments(command: &str) -> Vec<String> {
    let mut args = Vec::new();
    let mut arg = String::new();
    let mut quoted = false;
    for ch in command.chars() {
        if ch == '"' { quoted = !quoted; }
        else if ch.is_whitespace() && !quoted {
            if !arg.is_empty() { args.push(std::mem::take(&mut arg)); }
        } else { arg.push(ch); }
    }
    if !arg.is_empty() { args.push(arg); }
    args
}

async fn profile_owner(profile: &Path) -> Result<Option<BrowserProcess>, String> {
    #[cfg(windows)]
    let mut cmd = {
        let mut cmd = process::background_tokio_command("powershell.exe");
        cmd.args(["-NoProfile", "-NonInteractive", "-Command",
            "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; $ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='msedge.exe' OR Name='chromium.exe'\" | Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress"]);
        cmd
    };
    #[cfg(not(windows))]
    let mut cmd = {
        let mut cmd = process::background_tokio_command("ps");
        cmd.args(["-ax", "-o", "pid=,command="]);
        cmd
    };
    cmd.kill_on_drop(true);
    let output = tokio::time::timeout(Duration::from_secs(10), cmd.output()).await
        .map_err(|_| "检查 Chrome 配置目录占用超时".to_string())?
        .map_err(|e| format!("检查 Chrome 进程失败: {e}"))?;
    if !output.status.success() { return Err("无法确认 Chrome 配置目录的占用进程".into()); }
    let text = String::from_utf8_lossy(&output.stdout);
    #[cfg(windows)]
    let processes: Vec<BrowserProcess> = if text.trim().is_empty() { vec![] } else {
        // PowerShell's pipeline unwraps singleton arrays.
        let value: serde_json::Value = serde_json::from_str(text.trim_start_matches('\u{feff}').trim())
            .map_err(|e| format!("Chrome 进程列表无效: {e}"))?;
        if value.is_null() { vec![] } else if value.is_array() {
            serde_json::from_value(value).map_err(|e| e.to_string())?
        } else { vec![serde_json::from_value(value).map_err(|e| e.to_string())?] }
    };
    #[cfg(not(windows))]
    let processes: Vec<BrowserProcess> = text.lines().filter_map(|line| {
        let line = line.trim();
        let (pid, command) = line.split_once(char::is_whitespace)?;
        if !command.contains("Chrome") && !command.contains("chrome") && !command.contains("chromium") {
            return None;
        }
        Some(BrowserProcess { pid: pid.parse().ok()?, command: Some(command.trim().into()) })
    }).collect();
    let owners: Vec<_> = processes.into_iter().filter(|p|
        p.command.as_ref().is_some_and(|c| owns_profile(c, profile))
    ).collect();
    if owners.len() > 1 { return Err("多个 Chrome 主进程占用鲶鱼配置目录，请先关闭重复的鲶鱼浏览器".into()); }
    Ok(owners.into_iter().next())
}

pub(crate) async fn cdp_url(base: &str) -> Result<String, String> {
    let client = reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(2))
        .build().map_err(|e| e.to_string())?;
    let response = client.get(format!("{base}/json/version")).send().await
        .map_err(|e| e.to_string())?.error_for_status().map_err(|e| e.to_string())?;
    let value: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    let ws = value["webSocketDebuggerUrl"].as_str().ok_or("CDP 响应缺少浏览器地址")?;
    let url = reqwest::Url::parse(ws).map_err(|e| e.to_string())?;
    let requested = reqwest::Url::parse(base).map_err(|e| e.to_string())?;
    if url.scheme() != "ws" || !matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
        || url.port_or_known_default() != requested.port_or_known_default()
        || !url.path().starts_with("/devtools/browser/") {
        return Err("CDP 响应不是本机浏览器端点".into());
    }
    Ok(ws.to_owned())
}

/// Chromium / Chrome 用 chrome-profile, Edge 单独一份 (两者不能共用配置目录)。
fn profile_for(binary: &Path) -> Option<std::path::PathBuf> {
    let chrome = catfish_paths::chrome_user_data_dir()?;
    if super::browser_paths::is_edge(binary) {
        Some(chrome.with_file_name("edge-profile"))
    } else {
        Some(chrome)
    }
}

fn known_profiles() -> Vec<std::path::PathBuf> {
    let Some(chrome) = catfish_paths::chrome_user_data_dir() else { return vec![] };
    vec![chrome.with_file_name("edge-profile"), chrome]
}

/// 上次真正起来的浏览器 (10/16)。下次先试它, 不用每次从头试。
fn chosen_browser_file() -> Option<std::path::PathBuf> {
    catfish_paths::companion_state_dir().map(|d| d.join("chrome-browser.txt"))
}

fn ordered_candidates(candidates: Vec<std::path::PathBuf>, chosen: Option<std::path::PathBuf>) -> Vec<std::path::PathBuf> {
    match chosen {
        Some(c) if candidates.contains(&c) => {
            let mut out = vec![c.clone()];
            out.extend(candidates.into_iter().filter(|x| *x != c));
            out
        }
        _ => candidates,
    }
}

enum NotReady {
    /// 进程一启动就没了 (被安全软件拦 / 跟这台机器不兼容) —— 换下一个浏览器试
    Exited(String),
    /// 进程还活着但没就绪 (慢机器首次启动) —— 不再另起一个, 免得两个抢端口
    Timeout(String),
}

async fn wait_ready(pid: u32, profile: &Path, base: &str) -> Result<String, NotReady> {
    // 10/9: 原来只是死等 30 秒 —— Chrome 一启动就崩 (配置目录被锁 / 被安全软件拦 /
    // 版本不兼容) 也要等满 30 秒, 最后只说"未就绪, 去看 chrome.log", 员工看不到原因。
    // 现在: 进程没了且没有别的进程接管这个配置目录 → 立刻失败; 失败时把 chrome.log
    // 里最近的真实报错带出来。慢机器首次启动 (Windows Defender 扫一遍 Chromium)
    // 30 秒常不够, 放宽到 45 秒。
    let log_path = catfish_paths::chrome_log_path();
    let ready = tokio::time::timeout(Duration::from_secs(READY_TIMEOUT_SECS), async {
        loop {
            if let Ok(ws) = cdp_url(base).await { return Ok(ws); }
            if !process::is_alive(pid) && matches!(profile_owner(profile).await, Ok(None)) {
                return Err(NotReady::Exited(format!(
                    "PID {pid} 启动后马上退出了{}", log_hint(log_path.as_deref())
                )));
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }).await;
    match ready {
        Ok(Ok(ws)) => {
            // Chrome can hand off to an existing process; save the actual owner.
            let actual = profile_owner(profile).await.map_err(NotReady::Timeout)?
                .ok_or_else(|| NotReady::Timeout("浏览器启动后无法确认配置目录所有者".into()))?;
            if let Some(pid_file) = catfish_paths::chrome_pid_file() {
                std::fs::write(&pid_file, actual.pid.to_string()).map_err(|e| NotReady::Timeout(e.to_string()))?;
            }
            Ok(ws)
        }
        Ok(Err(e)) => Err(e),
        Err(_) => Err(NotReady::Timeout(format!(
            "浏览器 (PID {pid}) {READY_TIMEOUT_SECS} 秒内未就绪；没有重复启动或删除配置目录{}",
            log_hint(log_path.as_deref())
        ))),
    }
}

pub async fn ensure_running() -> Result<String, String> {
    let _guard = LIFECYCLE.lock().await;
    let pid_file = catfish_paths::chrome_pid_file().ok_or("找不到 Chrome PID 路径")?;
    let base = endpoints::endpoints().chrome_base();
    if !matches!(endpoints::endpoints().chrome_host.as_str(), "127.0.0.1" | "localhost" | "::1") {
        return Err("远程 CDP 端点不由本机 Chrome 启停管理".into());
    }
    let mut owner = None;
    for profile in known_profiles() {
        if let Some(found) = profile_owner(&profile).await? {
            owner = Some((profile, found));
            break;
        }
    }
    if let Some((_, ref existing)) = owner {
        let port_flag = format!("--remote-debugging-port={}", endpoints::endpoints().chrome_port);
        if !existing.command.as_ref().is_some_and(|c| command_arguments(c).contains(&port_flag)) {
            return Err("鲶鱼浏览器已占用配置目录，但调试端口不同；请先停止该浏览器再启动".into());
        }
    }
    if let Ok(ws) = cdp_url(&base).await {
        let (_, pid) = owner.ok_or("调试端口被非鲶鱼浏览器占用；未接管该浏览器")?;
        std::fs::write(&pid_file, pid.pid.to_string()).map_err(|e| e.to_string())?;
        return Ok(ws);
    }
    // An existing profile owner may still be starting. Never launch over its lock.
    if let Some((profile, existing)) = owner {
        return wait_ready(existing.pid, &profile, &base).await.map_err(|e| match e {
            NotReady::Exited(m) | NotReady::Timeout(m) => m,
        });
    }

    // 10/16: 依次试本机的浏览器 (自带 Chromium → Chrome → Edge)。一台 Windows 上自带 Chromium
    // 一启动就退 (被安全软件拦), Edge 却能正常起 —— 原来只认第一个, 浏览器功能整个用不了。
    let chosen = chosen_browser_file().and_then(|f| std::fs::read_to_string(f).ok())
        .map(|s| std::path::PathBuf::from(s.trim()));
    let candidates = ordered_candidates(super::browser_paths::browser_candidates(), chosen);
    if candidates.is_empty() {
        return Err("找不到 Chrome / Chromium / Edge".into());
    }
    let log_path = catfish_paths::chrome_log_path().ok_or("找不到 Chrome 日志路径")?;
    let mut failures = Vec::new();
    for binary in candidates {
        let profile = profile_for(&binary).ok_or("找不到 Chrome 配置目录")?;
        let name = binary.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let handle = match process::spawn_detached(process::SpawnConfig {
            program: binary.clone(),
            args: launch_arguments(&profile, endpoints::endpoints().chrome_port),
            working_dir: profile.parent().ok_or("无效 Chrome 配置目录")?.to_path_buf(),
            log_path: log_path.clone(), env: vec![],
        }) {
            Ok(handle) => handle,
            Err(e) => { failures.push(format!("{name}: 启动失败 {e}")); continue; }
        };
        std::fs::write(&pid_file, handle.pid.to_string()).map_err(|e| e.to_string())?;
        match wait_ready(handle.pid, &profile, &base).await {
            Ok(ws) => {
                if let Some(f) = chosen_browser_file() {
                    let _ = std::fs::write(f, binary.to_string_lossy().as_bytes());
                }
                if !failures.is_empty() {
                    log::warn!("鲶鱼浏览器改用 {}: 前面的起不来 ({})", binary.display(), failures.join("; "));
                }
                return Ok(ws);
            }
            Err(NotReady::Exited(m)) => failures.push(format!("{name}: {m}")),
            Err(NotReady::Timeout(m)) => return Err(m),
        }
    }
    Err(format!(
        "本机的浏览器都启动后马上退出了 (常见原因: 被安全软件拦截):\n{}",
        failures.join("\n")
    ))
}

const READY_TIMEOUT_SECS: u64 = 45;

/// chrome.log 末尾最近几条**真正的**报错 —— Chrome 平时也往这里刷大量无害的
/// ERROR (推送通道连不上、更新器、证书握手), 那些不是起不来的原因, 滤掉。
fn log_hint(path: Option<&Path>) -> String {
    let Some(path) = path else { return String::new() };
    let lines = tail_errors(&std::fs::read(path).unwrap_or_default());
    if lines.is_empty() {
        format!("。详见 {}", path.display())
    } else {
        format!("。chrome.log 最近的报错:\n{}", lines.join("\n"))
    }
}

fn tail_errors(bytes: &[u8]) -> Vec<String> {
    const NOISE: [&str; 6] = ["gcm/engine", "ssl_client_socket", "chrome/updater", "connection_factory",
        "google_apis", "registration_request"];
    let start = bytes.len().saturating_sub(64 * 1024);
    let text = String::from_utf8_lossy(&bytes[start..]);
    let mut hits: Vec<String> = text.lines()
        .filter(|l| l.contains("ERROR") || l.contains("FATAL") || l.contains("in use") || l.contains("Failed"))
        .filter(|l| !NOISE.iter().any(|n| l.contains(n)))
        .map(|l| l.chars().take(240).collect())
        .collect();
    let keep = hits.len().saturating_sub(3);
    hits.drain(..keep);
    hits
}

pub async fn stop() -> Result<(), String> {
    let _guard = LIFECYCLE.lock().await;
    for profile in known_profiles() {
        if let Some(owner) = profile_owner(&profile).await? {
            let pid = owner.pid;
            process::kill(pid).map_err(|e| e.to_string())?;
            // Keep the lifecycle lock until the profile owner exits.
            tokio::time::timeout(Duration::from_secs(10), async {
                while process::is_alive(pid) { tokio::time::sleep(Duration::from_millis(100)).await; }
            }).await.map_err(|_| "Chrome 仍在退出，暂不重新启动".to_string())?;
        }
    }
    if let Some(path) = catfish_paths::chrome_pid_file() { let _ = std::fs::remove_file(path); }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn log_tail_keeps_real_errors_and_drops_chrome_noise() {
        let log = "\
[1:2:1009/073752.3:ERROR:google_apis/gcm/engine/connection_factory_impl.cc:484] ConnectionHandler failed\n\
[1:2:1009/081752.0:ERROR:net/socket/ssl_client_socket_impl.cc:949] handshake failed\n\
[9:9:1009/090000.0:ERROR:chrome/browser/process_singleton_posix.cc:353] The profile appears to be in use by another Chromium process\n\
plain info line\n";
        let hits = tail_errors(log.as_bytes());
        assert_eq!(hits.len(), 1, "{hits:?}");
        assert!(hits[0].contains("profile appears to be in use"));
        assert!(tail_errors(b"").is_empty());
        let many: String = (0..10).map(|i| format!("FATAL boom {i}\n")).collect();
        assert_eq!(tail_errors(many.as_bytes()), vec!["FATAL boom 7", "FATAL boom 8", "FATAL boom 9"]);
    }

    #[test]
    fn exact_profile_not_prefix_or_renderer() {
        let profile = Path::new("C:\\Users\\someone\\Catfish State\\chrome-profile");
        assert!(owns_profile(&format!("chrome.exe --user-data-dir=\"{}\" --remote-debugging-port=9222", profile.display()), profile));
        assert!(!owns_profile(&format!("chrome.exe --user-data-dir={}2", profile.display()), profile));
        assert!(!owns_profile(&format!("chrome.exe --type=renderer --user-data-dir={}", profile.display()), profile));
        assert!(!owns_profile("chrome.exe --user-data-dir=C:\\DailyBrowser", profile));
        assert!(!owns_profile(&format!("chrome.exe --user-data-dir=\"{} extra\"", profile.display()), profile));
        #[cfg(not(windows))]
        assert!(owns_profile(&format!("Chrome --user-data-dir={} --no-first-run", profile.display()), profile));
    }
    #[test]
    fn remembered_browser_is_tried_first_and_stale_choice_ignored() {
        use std::path::PathBuf;
        let all = vec![PathBuf::from("chromium"), PathBuf::from("chrome"), PathBuf::from("msedge")];
        assert_eq!(ordered_candidates(all.clone(), Some("msedge".into())),
                   vec![PathBuf::from("msedge"), PathBuf::from("chromium"), PathBuf::from("chrome")]);
        assert_eq!(ordered_candidates(all.clone(), Some("uninstalled".into())), all);
        assert_eq!(ordered_candidates(all.clone(), None), all);
    }

    #[test]
    fn edge_gets_its_own_profile() {
        let edge = profile_for(Path::new("msedge.exe")).unwrap();
        let chromium = profile_for(Path::new("chrome.exe")).unwrap();
        assert_ne!(edge, chromium);
        assert_eq!(edge.parent(), chromium.parent());
        assert!(known_profiles().contains(&edge) && known_profiles().contains(&chromium));
    }

    #[tokio::test]
    async fn lifecycle_serializes_launch_and_stop() {
        let guard = LIFECYCLE.lock().await;
        assert!(LIFECYCLE.try_lock().is_err());
        drop(guard);
        assert!(LIFECYCLE.try_lock().is_ok());
    }

    #[tokio::test]
    async fn cdp_probe_validates_browser_endpoint() {
        use std::io::{Read, Write};
        for valid in [true, false] {
            let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            let port = listener.local_addr().unwrap().port();
            let worker = std::thread::spawn(move || {
                let (mut conn, _) = listener.accept().unwrap();
                conn.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut buf = [0; 2048];
                let _ = conn.read(&mut buf).unwrap();
                let body = if valid {
                    format!(r#"{{"webSocketDebuggerUrl":"ws://127.0.0.1:{port}/devtools/browser/test"}}"#)
                } else { r#"{"webSocketDebuggerUrl":"ws://example.com:9222/devtools/browser/test"}"#.into() };
                write!(conn, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).unwrap();
            });
            assert_eq!(cdp_url(&format!("http://127.0.0.1:{port}")).await.is_ok(), valid);
            worker.join().unwrap();
        }
    }

    #[cfg(windows)]
    #[tokio::test]
    #[ignore = "requires packaged Chromium; explicitly run by MSI gate"]
    async fn packaged_browser_lifecycle() {
        // Only run in the disposable Windows build job with a supplied browser.
        let browser = std::env::var("CATFISH_TEST_CHROME_BIN").expect("packaged Chromium required");
        let root = tempfile::tempdir().unwrap();
        let port = std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        std::env::set_var("CATFISH_ROOT", root.path());
        std::env::set_var("CATFISH_CHROME_BIN", browser);
        std::env::set_var("CATFISH_CHROME_DEBUG_PORT", port.to_string());
        let (first, second) = tokio::join!(ensure_running(), ensure_running());
        let result = async {
            assert_eq!(first.as_ref().unwrap(), second.as_ref().unwrap());
            let profile = catfish_paths::chrome_user_data_dir().unwrap();
            let original = profile_owner(&profile).await.unwrap().unwrap().pid;
            let pid_file = catfish_paths::chrome_pid_file().unwrap();
            std::fs::remove_file(&pid_file).unwrap();
            assert_eq!(ensure_running().await.unwrap(), first.unwrap());
            assert_eq!(profile_owner(&profile).await.unwrap().unwrap().pid, original);
            // A reused/stale PID must never cause an unrelated process to be killed.
            std::fs::write(pid_file, std::process::id().to_string()).unwrap();
        };
        // Assertions above are expected to succeed; CI retains the profile on failure.
        result.await;
        stop().await.unwrap();
        assert!(profile_owner(&catfish_paths::chrome_user_data_dir().unwrap()).await.unwrap().is_none());
    }
}
