//! local-search 后台 watcher 的启停 + 状态查询。
//!
//! 区分两个独立场景：
//!
//! 1. **stdio MCP (`catfish-search-mcp`)** —— Hermes 在对话里要查文件时，
//!    自己 fork 一个 MCP 进程，stdin/stdout 用完就退。这里 Companion 不管。
//!
//! 2. **后台 watcher (`catfish-search watch`)** —— 监听员工文件系统事件，
//!    增量更新 SQLite FTS 索引。这是真长期跑的服务，Companion 管启停。
//!
//! 后台 watcher 没有端口可探，状态完全靠 PID 文件 + is_alive 判断。

use serde::Serialize;

use crate::commands::types::ServiceStatus;
use crate::services::{autostart, catfish_paths, process};

#[tauri::command]
pub async fn local_search_start() -> Result<(), String> {
    // P3.4.2 (6/15 鸿波): UI 点"启动" 时也清孤儿后重起.
    //
    // 老逻辑撞错: pid_file 那个进程在跑就 reject. 但常见场景是员工撞 yaml /
    // 老进程跑的代码版本不对, 想重起 — 老逻辑要先点"停止"才让"启动" work,
    // UX 差. 新逻辑: 直接 pkill 所有 catfish_search.cli watch (含 pid_file 没记
    // 的孤儿) 再 spawn, 等价"重启" 语义.
    //
    // 等价行为: 跟 autostart::ensure_local_search_running 同款, 调同一个 helper.
    autostart::pkill_local_search_watchers();

    let dir = catfish_paths::local_search_dir().ok_or_else(|| {
        "找不到 local-search 目录 — 设 CATFISH_LOCAL_SEARCH_DIR 或确保 \
         ~/person_task/catfish/edge/local-search 存在"
            .to_string()
    })?;
    let python = catfish_paths::local_search_python()
        .ok_or_else(|| "找不到 Python 解释器".to_string())?;
    let log_path = catfish_paths::local_search_log_path()
        .ok_or_else(|| "找不到日志路径".to_string())?;
    let pid_file = catfish_paths::local_search_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;

    // 把 src/ 加到 PYTHONPATH，免去员工跑 `pip install -e .` 的额外步骤
    // （catfish-local-search 只有 pyyaml + watchdog 这两个核心 runtime 依赖）
    let pythonpath = dir.join("src").to_string_lossy().to_string();

    let cfg = process::SpawnConfig {
        program: python,
        args: vec![
            "-m".into(),
            "catfish_search.cli".into(),
            "watch".into(),
        ],
        log_path,
        working_dir: dir,
        env: vec![("PYTHONPATH".into(), pythonpath)],
    };

    let handle = process::spawn_detached(cfg).map_err(|e| format!("启动失败: {e}"))?;

    std::fs::write(&pid_file, handle.pid.to_string())
        .map_err(|e| format!("写 PID 文件失败: {e}"))?;

    Ok(())
}

#[tauri::command]
pub async fn local_search_stop() -> Result<(), String> {
    let pid_file = catfish_paths::local_search_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;

    if !pid_file.exists() {
        return Err("Local Search watcher 没有由 Companion 启动过（无 PID 文件）".into());
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

/// BL-SEARCH-NO-BOOTSTRAP (7/27 鸿波实盘): 前台跑一次索引，等它跑完再返回。
///
/// # 为什么需要这个命令
///
/// Companion 一直只 spawn `catfish_search.cli watch`，而 watcher 只吃**文件变化
/// 事件** —— 存量文件永远不会自己进索引。鸿波 yaml 里配了 ~/Documents、
/// ~/Desktop、~/Downloads、~/.catfish/uploads，索引库里这四个各 0 条，
/// 60332 条全来自 ~/person_task（活跃开发目录，文件天天变，被 watcher 逐个吃进去）。
///
/// 现在三处补齐：
///   1. `watch` 启动时库为空 → 自己先做一次全量（watcher.py:_bootstrap_if_empty）
///   2. 员工在面板加完目录 → 前端调本命令带 `only`，只补新加的那一个
///   3. 面板"重建索引"按钮 → 不带 `only`，整库重来
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct IndexRunResult {
    /// 索引器的完整 stdout（含各目录条数 / 读不了的目录提示），直接显给员工
    pub output: String,
    /// 退出码 0 才算成功
    pub ok: bool,
}

#[tauri::command]
pub async fn local_search_index(only: Option<String>) -> Result<IndexRunResult, String> {
    let dir = catfish_paths::local_search_dir()
        .ok_or_else(|| "找不到 local-search 目录".to_string())?;
    let python = catfish_paths::local_search_python()
        .ok_or_else(|| "找不到 Python 解释器".to_string())?;
    let pythonpath = dir.join("src").to_string_lossy().to_string();

    let mut args: Vec<String> = vec![
        "-m".into(),
        "catfish_search.cli".into(),
        "index".into(),
        "--quiet".into(),
    ];
    if let Some(p) = only {
        let p = p.trim().to_string();
        if p.is_empty() {
            return Err("目录不能空".into());
        }
        args.push("--only".into());
        args.push(p);
    }

    // 索引是同步 subprocess 且可能跑几十秒，丢进 spawn_blocking，
    // 不占 tokio runtime 线程（跟 tts.rs:304 同款处理）。
    tokio::task::spawn_blocking(move || -> Result<IndexRunResult, String> {
        let out = std::process::Command::new(&python)
            .args(&args)
            .current_dir(&dir)
            .env("PYTHONPATH", &pythonpath)
            .env("PYTHONUNBUFFERED", "1")
            .output()
            .map_err(|e| format!("跑索引失败: {e}"))?;

        let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
        let err = String::from_utf8_lossy(&out.stderr);
        if !err.trim().is_empty() {
            text.push('\n');
            text.push_str(&err);
        }
        Ok(IndexRunResult {
            output: text.trim().to_string(),
            ok: out.status.success(),
        })
    })
    .await
    .map_err(|e| format!("索引任务崩了: {e}"))?
}

#[tauri::command]
pub async fn local_search_status() -> Result<ServiceStatus, String> {
    let pid = catfish_paths::local_search_pid_file()
        .and_then(|p| process::read_pid_file_alive_strict(&p, "catfish_search"));

    match pid {
        Some(pid) => Ok(ServiceStatus {
            running: true,
            healthy: true, // 进程活着就算健康；FS watcher 没业务面探测点
            pid: Some(pid),
            port: None,
            message: Some("Watcher 在跑（FS 事件 → 增量索引）".into()),
        }),
        None => Ok(ServiceStatus::down(
            None,
            "未启动 — 点 \"启动\" 拉起 watcher（监听文件变化更新索引）",
        )),
    }
}
