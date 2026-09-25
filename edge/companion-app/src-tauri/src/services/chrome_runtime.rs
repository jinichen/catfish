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

pub async fn ensure_running() -> Result<String, String> {
    let _guard = LIFECYCLE.lock().await;
    let profile = catfish_paths::chrome_user_data_dir().ok_or("找不到 Chrome 配置目录")?;
    let pid_file = catfish_paths::chrome_pid_file().ok_or("找不到 Chrome PID 路径")?;
    let base = endpoints::endpoints().chrome_base();
    if !matches!(endpoints::endpoints().chrome_host.as_str(), "127.0.0.1" | "localhost" | "::1") {
        return Err("远程 CDP 端点不由本机 Chrome 启停管理".into());
    }
    let owner = profile_owner(&profile).await?;
    if let Some(ref existing) = owner {
        let port_flag = format!("--remote-debugging-port={}", endpoints::endpoints().chrome_port);
        if !existing.command.as_ref().is_some_and(|c| command_arguments(c).contains(&port_flag)) {
            return Err("鲶鱼浏览器已占用配置目录，但调试端口不同；请先停止该浏览器再启动".into());
        }
    }
    if let Ok(ws) = cdp_url(&base).await {
        let pid = owner.ok_or("调试端口被非鲶鱼浏览器占用；未接管该浏览器")?;
        std::fs::write(&pid_file, pid.pid.to_string()).map_err(|e| e.to_string())?;
        return Ok(ws);
    }
    // An existing profile owner may still be starting. Never launch over its lock.
    let pid = if let Some(owner) = owner { owner.pid } else {
        let binary = catfish_paths::find_chrome().ok_or("找不到 Chrome / Chromium / Edge")?;
        let log_path = catfish_paths::chrome_log_path().ok_or("找不到 Chrome 日志路径")?;
        let handle = process::spawn_detached(process::SpawnConfig {
            program: binary,
            args: launch_arguments(&profile, endpoints::endpoints().chrome_port),
            working_dir: profile.parent().ok_or("无效 Chrome 配置目录")?.to_path_buf(),
            log_path, env: vec![],
        }).map_err(|e| format!("启动 Chrome 失败: {e}"))?;
        std::fs::write(&pid_file, handle.pid.to_string()).map_err(|e| e.to_string())?;
        handle.pid
    };
    let ready = tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            if let Ok(ws) = cdp_url(&base).await { return ws; }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }).await;
    match ready {
        Ok(ws) => {
            // Chrome can hand off to an existing process; save the actual owner.
            let actual = profile_owner(&profile).await?.ok_or("Chrome 启动后无法确认配置目录所有者")?;
            std::fs::write(&pid_file, actual.pid.to_string()).map_err(|e| e.to_string())?;
            Ok(ws)
        }
        Err(_) => Err(format!("Chrome (PID {pid}) 30 秒内未就绪；没有重复启动或删除配置目录。请查看 chrome.log")),
    }
}

pub async fn stop() -> Result<(), String> {
    let _guard = LIFECYCLE.lock().await;
    let profile = catfish_paths::chrome_user_data_dir().ok_or("找不到 Chrome 配置目录")?;
    if let Some(owner) = profile_owner(&profile).await? {
        let pid = owner.pid;
        process::kill(pid).map_err(|e| e.to_string())?;
        // Keep the lifecycle lock until the profile owner exits.
        tokio::time::timeout(Duration::from_secs(10), async {
            while process::is_alive(pid) { tokio::time::sleep(Duration::from_millis(100)).await; }
        }).await.map_err(|_| "Chrome 仍在退出，暂不重新启动".to_string())?;
    }
    if let Some(path) = catfish_paths::chrome_pid_file() { let _ = std::fs::remove_file(path); }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
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
