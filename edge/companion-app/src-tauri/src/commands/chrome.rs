//! Catfish Chrome 的启停 + 状态查询。
//!
//! Catfish Chrome 是 Companion 起的**独立**调试 Chrome 实例（隔离 user-data-dir），
//! 不复用员工日常浏览器，避免污染 cookies / 误退他们的 Chrome 主窗口。
//!
//! 启动：`<chrome-bin> --remote-debugging-port=9222 --user-data-dir=<隔离 profile>`
//! kill：只 kill PID 文件里记的那个进程，绝不动员工日常 Chrome。
//!
//! cdp_url 自动同步 (#50): chrome_launch 后台 poll Chrome 的 /json/version,
//! 拿到 webSocketDebuggerUrl 自动写到 ~/.hermes/config.yaml 的 browser.cdp_url。
//! 替代员工每次手动跑 catfish-browser-attach.sh 的辛苦活。

use std::time::Duration;

use crate::commands::types::ServiceStatus;
use crate::services::{catfish_paths, endpoints, process};

const TCP_TIMEOUT: Duration = Duration::from_millis(800);
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);

#[tauri::command]
pub async fn chrome_launch() -> Result<(), String> {
    if let Some(pid_file) = catfish_paths::chrome_pid_file() {
        if let Some(pid) = process::read_pid_file_alive(&pid_file) {
            return Err(format!("Chrome 已在跑（PID {pid}）"));
        }
    }

    let chrome_bin = catfish_paths::find_chrome().ok_or_else(|| {
        "找不到 Chrome — 请先安装 Google Chrome / Chromium".to_string()
    })?;
    let user_data_dir = catfish_paths::chrome_user_data_dir()
        .ok_or_else(|| "找不到 Chrome 隔离 profile 路径".to_string())?;
    let log_path = catfish_paths::chrome_log_path()
        .ok_or_else(|| "找不到日志路径".to_string())?;
    let pid_file = catfish_paths::chrome_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;
    let working_dir = user_data_dir
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| std::env::temp_dir());

    let chrome_port = endpoints::endpoints().chrome_port;
    let cfg = process::SpawnConfig {
        program: chrome_bin,
        args: vec![
            format!("--remote-debugging-port={chrome_port}"),
            format!("--user-data-dir={}", user_data_dir.display()),
            "--no-first-run".into(),
            "--no-default-browser-check".into(),
            "--disable-features=DialMediaRouteProvider".into(), // 减少后台噪音
            "about:blank".into(), // 默认开空白页
        ],
        log_path,
        working_dir,
        env: vec![],
    };

    let handle = process::spawn_detached(cfg).map_err(|e| format!("启动失败: {e}"))?;

    std::fs::write(&pid_file, handle.pid.to_string())
        .map_err(|e| format!("写 PID 文件失败: {e}"))?;

    // 后台等 Chrome 真正 ready (CDP /json/version 可达) 后刷新 hermes config 的 cdp_url。
    // 不阻塞 chrome_launch 返回, 员工立刻看到"启动成功", UUID 同步在背后做。
    //
    // 为啥要做: 每次 Chrome 重启 webSocketDebuggerUrl 的 UUID 变, 但 hermes config
    //         里的 cdp_url 是写死的, 不刷新员工敲 'browser_navigate xxx' 会 404.
    //         以前要靠 catfish-browser-attach.sh 手动跑, 现在 Companion 包办.
    let chrome_base = endpoints::endpoints().chrome_base();
    tokio::spawn(async move {
        refresh_cdp_url_when_ready(&chrome_base).await;
    });

    Ok(())
}

/// 后台 poll Chrome 的 /json/version, 拿到 webSocketDebuggerUrl 后写到
/// ~/.hermes/config.yaml 的 browser.cdp_url 字段。
///
/// 容错:
///   - Chrome 没起来 → 60 次 retry 各等 500ms, 总 30s 超时, 静默放弃
///   - hermes config 不存在 → skip (员工没装 hermes 也不该报错)
///   - yaml 解析 / 写失败 → log warn, 不抛
async fn refresh_cdp_url_when_ready(chrome_base: &str) {
    let ws_url = match fetch_ws_debugger_url(chrome_base, 60).await {
        Ok(u) => u,
        Err(e) => {
            log::warn!(
                "Chrome 起来了但 30s 内拿不到 webSocketDebuggerUrl: {e}. \
                 hermes config 的 cdp_url 没自动刷, 员工要手动 \
                 catfish-browser-attach.sh"
            );
            return;
        }
    };
    match update_hermes_config_cdp_url(&ws_url) {
        Ok(true) => {
            log::info!("已刷新 hermes config cdp_url -> {ws_url}");
            // 配置文件刷新只是第一步 — tool-bridge 这个常驻 Python daemon
            // 在 import hermes 时已把 cdp_url 缓存进内存, config 改了它也不知道.
            // 顺手 kill 一下, Companion autostart 1-2s 内 respawn 起新进程读新 config,
            // browser_navigate 立刻就通最新 UUID, 员工无感.
            //
            // 小代价: 刚 kill 时如果有 in-flight 工具调用会失败一次, 但 Chrome 重启
            // 本来就是低频事件 (30 分钟才一次), 比让员工反复撞 cdp 404 强多了.
            kill_tool_bridge_for_respawn();
        }
        Ok(false) => log::debug!("hermes config 不存在或无变化, 跳过"),
        Err(e) => log::warn!("刷新 hermes config cdp_url 失败: {e}"),
    }
}

/// kill tool-bridge daemon, 让 Companion autostart 接管重启, 新进程读最新 config.
/// 找不到 PID 文件 / 进程已死 → 静默 skip (autostart 会拉一个新的就行).
fn kill_tool_bridge_for_respawn() {
    let pid_file = match catfish_paths::tool_bridge_pid_file() {
        Some(p) => p,
        None => {
            log::debug!("tool-bridge PID 文件路径找不到, 跳过 respawn");
            return;
        }
    };
    let pid = match process::read_pid_file_alive(&pid_file) {
        Some(p) => p,
        None => {
            log::debug!("tool-bridge 没在跑, 不需要 respawn");
            return;
        }
    };
    match process::kill(pid) {
        Ok(_) => log::info!(
            "已 kill tool-bridge {pid}, autostart 1-2s 内会用新 cdp_url 拉起新进程"
        ),
        Err(e) => log::warn!("kill tool-bridge {pid} 失败: {e}, 员工可能要手动 pkill"),
    }
}

async fn fetch_ws_debugger_url(
    chrome_base: &str,
    max_attempts: u32,
) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .no_proxy()
        .build()
        .map_err(|e| format!("HTTP client: {e}"))?;
    let url = format!("{chrome_base}/json/version");
    for attempt in 1..=max_attempts {
        match client.get(&url).send().await {
            Ok(resp) if resp.status().is_success() => {
                if let Ok(json) = resp.json::<serde_json::Value>().await {
                    if let Some(ws) = json
                        .get("webSocketDebuggerUrl")
                        .and_then(|v| v.as_str())
                    {
                        return Ok(ws.to_string());
                    }
                    return Err("响应里没 webSocketDebuggerUrl 字段".into());
                }
            }
            _ => {} // 等下次 retry, 不 log 减噪音 (Chrome 冷启动时前几秒 connection refused 是正常的)
        }
        if attempt < max_attempts {
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }
    Err(format!("超时: {max_attempts} 次 retry 都拿不到 /json/version"))
}

/// 更新 ~/.hermes/config.yaml 的 browser.cdp_url。
/// 返回 Ok(true) 表示真的写了, Ok(false) 表示无操作 (config 不存在 / 已经是同值)。
fn update_hermes_config_cdp_url(ws_url: &str) -> Result<bool, String> {
    let config_path = catfish_paths::hermes_config_path()
        .ok_or_else(|| "找不到 home 目录".to_string())?;
    if !config_path.exists() {
        return Ok(false);
    }
    let content = std::fs::read_to_string(&config_path)
        .map_err(|e| format!("读 hermes config 失败: {e}"))?;
    let mut data: serde_yaml::Value = serde_yaml::from_str(&content)
        .map_err(|e| format!("yaml 解析失败: {e}"))?;

    // 走到 data["browser"]["cdp_url"] 路径, 不存在则按需建
    let mapping = data
        .as_mapping_mut()
        .ok_or_else(|| "config.yaml 顶层不是 mapping".to_string())?;
    let browser_key = serde_yaml::Value::String("browser".to_string());
    let browser = mapping
        .entry(browser_key)
        .or_insert_with(|| serde_yaml::Value::Mapping(serde_yaml::Mapping::new()));
    let browser_map = browser
        .as_mapping_mut()
        .ok_or_else(|| "config.browser 字段不是 mapping".to_string())?;

    let cdp_key = serde_yaml::Value::String("cdp_url".to_string());
    // 已经是同值就不动 (避免无意义写文件触发 inotify)
    if let Some(serde_yaml::Value::String(existing)) = browser_map.get(&cdp_key) {
        if existing == ws_url {
            return Ok(false);
        }
    }
    browser_map.insert(cdp_key, serde_yaml::Value::String(ws_url.to_string()));

    let new_content = serde_yaml::to_string(&data)
        .map_err(|e| format!("yaml 序列化失败: {e}"))?;
    std::fs::write(&config_path, new_content)
        .map_err(|e| format!("写 hermes config 失败: {e}"))?;
    Ok(true)
}

#[tauri::command]
pub async fn chrome_kill() -> Result<(), String> {
    let pid_file = catfish_paths::chrome_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;

    if !pid_file.exists() {
        return Err("Catfish Chrome 没有由 Companion 启动过（无 PID 文件）".into());
    }

    let pid: u32 = std::fs::read_to_string(&pid_file)
        .map_err(|e| format!("读 PID 文件失败: {e}"))?
        .trim()
        .parse()
        .map_err(|e| format!("PID 文件格式错误: {e}"))?;

    process::kill(pid).map_err(|e| format!("kill 失败: {e}"))?;

    let _ = std::fs::remove_file(&pid_file);
    Ok(())
}

#[tauri::command]
pub async fn chrome_status() -> Result<ServiceStatus, String> {
    let ep = endpoints::endpoints();
    let host = ep.chrome_host.clone();
    let port = ep.chrome_port;
    let tcp_alive = tokio::time::timeout(
        TCP_TIMEOUT,
        tokio::net::TcpStream::connect((host.as_str(), port)),
    )
    .await
    .map(|r| r.is_ok())
    .unwrap_or(false);

    if !tcp_alive {
        return Ok(ServiceStatus::down(
            Some(port),
            "未启动 — 点 \"启动\" 拉起 Catfish Chrome",
        ));
    }

    // 9222 通了再 GET /json/version 确认是 DevTools 协议
    let chrome_base = ep.chrome_base();
    let healthy = match reqwest::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .no_proxy()
        .build()
    {
        Ok(c) => c
            .get(format!("{chrome_base}/json/version"))
            .send()
            .await
            .map(|r| r.status().is_success())
            .unwrap_or(false),
        Err(_) => false,
    };

    let pid = catfish_paths::chrome_pid_file()
        .and_then(|p| process::read_pid_file_alive(&p));

    Ok(ServiceStatus {
        running: true,
        healthy,
        pid,
        port: Some(port),
        message: Some(if healthy {
            format!("DevTools 已就绪 :{port}")
        } else {
            "TCP 通但非 Chrome DevTools — 端口被别的进程占了？".into()
        }),
    })
}
