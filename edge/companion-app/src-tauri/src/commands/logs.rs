//! 实时日志 tail —— 把日志文件的新增行通过 Tauri event 流式推给前端。
//!
//! 协议:
//!     前端 invoke `tail({service, fromEnd})` → Rust 起 tokio task 轮询文件
//!         → 每读到一行 emit `log:<service>` 事件
//!     前端 invoke `stop_tail({service})` 停止该 service 的 tail task
//!     重复 invoke 同一 service 的 tail 会自动 abort 之前那个
//!
//! 实现:
//!     - 500ms 轮询一次文件 (简单,不引 notify 依赖跨平台 fsevent / inotify 抽象)
//!     - 文件不存在: 等 15 秒,还没出现就 emit 一条"日志没生成"提示
//!     - 文件 rotate / truncate: 探到 size < pos 就重置从头读
//!     - leftover: 跨轮次保留未结尾行,下轮拼接
//!
//! 跨平台: macOS / Linux / Windows 都用同样的轮询逻辑,无平台差异。

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncReadExt, AsyncSeekExt, SeekFrom};
use tokio::task::AbortHandle;

use crate::services::catfish_paths;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogLine {
    pub service: String,
    pub line: String,
    pub ts: String, // ISO-8601
}

fn log_path_for(service: &str) -> Option<PathBuf> {
    match service {
        "gateway" => catfish_paths::gateway_log_path(),
        "chrome" => catfish_paths::chrome_log_path(),
        "local_search" => catfish_paths::local_search_log_path(),
        "tool_bridge" => catfish_paths::tool_bridge_log_path(),
        _ => None,
    }
}

fn tail_tasks() -> &'static Mutex<HashMap<String, AbortHandle>> {
    static TASKS: OnceLock<Mutex<HashMap<String, AbortHandle>>> = OnceLock::new();
    TASKS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn iso_now() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}

/// rename_all = "camelCase" 让 JS 的 `fromEnd` 自动绑定到 Rust 的 `from_end`。
/// Tauri 2 默认严格 snake_case 匹配 —— 不加这个 attr,JS 的 `fromEnd` 进不来,
/// invoke 直接 422 失败,前端 `catch{}` 静默吃掉,日志面板永远空白。
#[tauri::command(rename_all = "camelCase")]
pub async fn tail(
    service: String,
    from_end: bool,
    app: AppHandle,
) -> Result<(), String> {
    let log_path = log_path_for(&service)
        .ok_or_else(|| format!("unknown service: {service}"))?;

    // 取消已有同 service 的 tail task (重复 invoke 自动接管)
    {
        let mut tasks = tail_tasks().lock().unwrap();
        if let Some(handle) = tasks.remove(&service) {
            handle.abort();
        }
    }

    let event_name = format!("log:{service}");
    let service_for_loop = service.clone();
    let app_for_loop = app.clone();

    let join = tokio::spawn(async move {
        tail_loop(log_path, service_for_loop, event_name, app_for_loop, from_end).await;
    });

    {
        let mut tasks = tail_tasks().lock().unwrap();
        tasks.insert(service, join.abort_handle());
    }

    Ok(())
}

#[tauri::command]
pub async fn stop_tail(service: String) -> Result<(), String> {
    let mut tasks = tail_tasks().lock().unwrap();
    if let Some(handle) = tasks.remove(&service) {
        handle.abort();
    }
    Ok(())
}

async fn tail_loop(
    log_path: PathBuf,
    service: String,
    event_name: String,
    app: AppHandle,
    from_end: bool,
) {
    // 等文件出现 (服务可能还没启动,最多等 15 秒)
    for _ in 0..30 {
        if log_path.exists() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }

    if !log_path.exists() {
        let _ = app.emit(
            &event_name,
            LogLine {
                service: service.clone(),
                line: format!(
                    "(日志文件 {} 还没出现 — 服务可能没启动,或者由 Companion 之外的方式起的)",
                    log_path.display()
                ),
                ts: iso_now(),
            },
        );
        return;
    }

    let mut pos: u64 = if from_end {
        std::fs::metadata(&log_path).map(|m| m.len()).unwrap_or(0)
    } else {
        0
    };
    let mut leftover = String::new();

    // 起手提示一行,让前端立刻看到 tail 已就位
    let _ = app.emit(
        &event_name,
        LogLine {
            service: service.clone(),
            line: format!(
                "── 开始 tail {} (从 {}{}) ──",
                log_path.display(),
                if from_end { "末尾" } else { "开头" },
                if from_end { "" } else { ", 0 字节" }
            ),
            ts: iso_now(),
        },
    );

    loop {
        // 重开文件每轮一次 —— 处理 logrotate / 文件被删
        let read_result: std::io::Result<()> = async {
            let mut file = tokio::fs::File::open(&log_path).await?;
            let metadata = file.metadata().await?;

            // size < pos: 文件被 truncate / rotate, 重置
            if metadata.len() < pos {
                pos = 0;
                leftover.clear();
                let _ = app.emit(
                    &event_name,
                    LogLine {
                        service: service.clone(),
                        line: "── 检测到日志 rotate, 从头开始 ──".to_string(),
                        ts: iso_now(),
                    },
                );
            }

            file.seek(SeekFrom::Start(pos)).await?;

            let mut buf = vec![0u8; 64 * 1024];
            let n = file.read(&mut buf).await?;
            if n == 0 {
                return Ok(());
            }
            pos += n as u64;

            let chunk = String::from_utf8_lossy(&buf[..n]).to_string();
            let combined = std::mem::take(&mut leftover) + &chunk;
            let mut parts: Vec<String> = combined.split('\n').map(String::from).collect();

            // 最后一段没换行符就 leftover (下轮再拼)
            if !combined.ends_with('\n') && !parts.is_empty() {
                leftover = parts.pop().unwrap_or_default();
            }

            for line in parts {
                if line.is_empty() {
                    continue;
                }
                let _ = app.emit(
                    &event_name,
                    LogLine {
                        service: service.clone(),
                        line,
                        ts: iso_now(),
                    },
                );
            }
            Ok(())
        }
        .await;

        // 读取异常(文件被删之类)就静默重试,下一轮重开
        let _ = read_result;

        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}
