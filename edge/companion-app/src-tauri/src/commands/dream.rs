//! P3.5.1 (6/15 鸿波 Dream Engine): 员工主动触发 long-term memory 蒸馏.
//!
//! # 设计 (方案 D, 6/15 鸿波拍)
//!
//! Companion **不重新实现蒸馏算法** — 复用 `hermes-plugins/catfish-memory/` 现有
//! `_call_distill_llm` + `_write_distilled` + `_mark_distill_run`. plugin 已经有完整
//! 24h cooldown state, Dream Engine 跑完写同款 state, plugin auto path 24h 内自然 skip.
//! **零冲突, 不撞** (相对方案 C 独立 cooldown 的优势).
//!
//! # 流程
//!
//! 1. UI 点 "现在重蒸" → invoke `dream_distill_run(model)`
//! 2. Rust spawn `python -m catfish_memory.dream_cli --model X`
//!    - python: `catfish_paths::tool_bridge_python()` (复用 hermes venv, 有 httpx 等依赖)
//!    - PYTHONPATH: `catfish_memory_plugin_parent_dir()` (让 `import catfish_memory` 工作)
//! 3. tokio BufReader.read_line stdout 逐行解析 JSON event → emit Tauri event
//! 4. UI 听 `dream:progress` event 更新 progress bar / 终态
//!
//! # stdout JSON 协议 (跟 dream_cli.py 对齐)
//!
//! ```json
//! {"event":"start","total":12}
//! {"event":"chunk","done":3,"total":12}
//! {"event":"done","ok":true,"chunks":12,"bytes":4523,"model":"...","took_seconds":42.1}
//! {"event":"done","ok":false,"reason":"empty_journal","took_seconds":0.1}
//! {"event":"error","msg":"..."}
//! ```
//!
//! Rust 端透传整 line 给前端 (`Emitter.emit("dream:progress", json_line)`), 不解析字段
//! — 前端 TS 自己 JSON.parse, 减少 Rust 端 schema 维护负担.

use std::path::PathBuf;
use std::process::Stdio;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, BufReader};

use crate::services::{catfish_paths, process};

const DREAM_EVENT: &str = "dream:progress";

/// 调用 dream_cli 的返回值. Tauri 直接 await — UI 等 invoke 完才知道是否成功 spawn.
/// 真实进度走 Tauri event (不阻塞 invoke).
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DreamRunOutput {
    /// 是否成功 spawn + 完整跑完 CLI (含 exit code = 0).
    pub spawn_ok: bool,
    /// 实际 spawn 的命令 (debug 用).
    pub command: String,
    /// 退出码 (None = 还没退或被 kill).
    pub exit_code: Option<i32>,
    /// 错误信息 (spawn 失败 / 进程崩 等).
    pub error: Option<String>,
    /// 8/4: CLI 自己报的终态 —— {ok, reason, chunks_total, bytes_written, model}。
    ///
    /// 为什么补这个: 定时蒸馏上线当晚就发现日志分不出"真跑了"和"cooldown 跳过了"
    /// —— 两种情况都是 exit_code=0, 长得一模一样。而这两天查的一堆 bug, 病根
    /// 全是"报了成功但没说到底发生了什么"。自己刚写的代码又犯一次, 当场补上。
    ///
    /// None = CLI 没输出可解析的终态行 (老版本 / 崩在中途)。
    pub result: Option<serde_json::Value>,
}

/// P3.5.1.4 (6/15 鸿波): 触发 Dream Engine 蒸馏.
///
/// model 必须是 companion picker 当前选的 (前端 `useChatStore.model` 传入).
/// 不读 yaml/env model — Dream Engine 跟 plugin auto 用同 distill 算法但 model 来源不同.
///
/// invoke 立即返回 (spawn ok), 真实进度走 Tauri event `dream:progress`.
/// invoke 一直 await 直到 CLI 退出 — UI 知道终态. 进度走 event.
/// 把 CLI 终态说成人话 —— exit_code=0 分不出"跑了"和"跳过了", 这行能。
pub(crate) fn describe(result: &Option<serde_json::Value>) -> String {
    let Some(v) = result else {
        return "CLI 没报终态 (老版本? 中途崩了?)".to_string();
    };
    let reason = v.get("reason").and_then(|x| x.as_str()).unwrap_or("");
    if v.get("ok").and_then(|x| x.as_bool()) == Some(true) {
        format!(
            "已重蒸: {} 段 → {} 字节 (model {})",
            v.get("chunks_total").and_then(|x| x.as_u64()).unwrap_or(0),
            v.get("bytes_written").and_then(|x| x.as_u64()).unwrap_or(0),
            v.get("model").and_then(|x| x.as_str()).unwrap_or("?"),
        )
    } else if reason == "cooldown" {
        "跳过: 24h 内已经蒸过".to_string()
    } else {
        format!("没跑成: {reason}")
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn dream_distill_run(
    app: AppHandle,
    model: String,
) -> Result<DreamRunOutput, String> {
    // 员工点按钮 = 现在就想跑, 跳 24h cooldown
    run_dream(app, model, /* force */ true).await
}

/// 真正的 spawn。force=false 时带 --no-force, 24h 内跑过就直接返 cooldown。
///
/// 8/4: 抽出来给后台定时蒸馏用 (services::distill_scheduler)。
/// 之前定时器写在 catfish-memory 的 Python 侧 (CatfishMemoryProvider.__init__),
/// 但实测 `~/.hermes/config.yaml` 的 plugins.enabled 里**根本没有 catfish-memory**
/// —— 这个 plugin 在当前架构下从来不是被 hermes 加载的 plugin, 它只以
/// dream_cli.py 子进程的形式活着。挂在 provider 构造里的定时器是死代码。
///
/// 所以定时器必须在 Companion 这边 —— 这里本来就是唯一会执行到蒸馏的地方。
pub(crate) async fn run_dream(
    app: AppHandle,
    model: String,
    force: bool,
) -> Result<DreamRunOutput, String> {
    let model = model.trim().to_string();
    if model.is_empty() {
        return Err("Dream Engine: model 参数为空, 不跑".into());
    }

    let python = catfish_paths::tool_bridge_python()
        .ok_or_else(|| "Dream Engine: 找不到 hermes venv python (tool_bridge_python None)".to_string())?;
    // P3.5.1.7 (6/15 鸿波): 改 spawn 绝对路径 dream_cli.py — 不再依赖 `python -m
    // catfish_memory.dream_cli` (因为目录名 catfish-memory 带横线非法 Python module).
    // dream_cli.py 内部 sys.path.insert(0, __file__ dirname) 让同目录 catfish_memory.py
    // 作 module 被 import.
    let cli_path = catfish_paths::dream_cli_path()
        .ok_or_else(|| {
            "Dream Engine: 找不到 dream_cli.py (CATFISH_ROOT 没配 / catfish-memory 没装?)"
                .to_string()
        })?;
    if !cli_path.exists() {
        return Err(format!(
            "Dream Engine: dream_cli.py 不存在: {}. 检查 CATFISH_ROOT 或 catfish-memory plugin 是否在源码目录.",
            cli_path.display(),
        ));
    }

    let mut args = vec![
        "-u".to_string(),  // unbuffered, 跟 PYTHONUNBUFFERED 双保险
        cli_path.to_string_lossy().to_string(),
        "--model".to_string(),
        model.clone(),
    ];
    if !force {
        // dream_cli 默认 force=True (员工主动触发场景); 后台定时必须尊重 24h
        // cooldown, 否则每 15 分钟重蒸一次 journal, 白烧 LLM。
        args.push("--no-force".to_string());
    }
    let cmd_display = format!("{} {}", python.display(), args.join(" "));
    log::info!("[dream] spawn: {}", cmd_display);

    // P3.5.1.9 (6/15 鸿波): subprocess 必须传 CATFISH_INTERNAL_DEV_TOKEN, 否则 dream_cli
    //   走 _call_distill_llm → _gateway_dev_token() 返空 → 直接返 None → UI llm_fail.
    //   真因: dream_cli subprocess 不继承 hermes/gateway 进程的 .env. catfish-memory
    //   plugin 在 hermes 进程内时, hermes 启动 source ~/.hermes/.env, env 就有 token;
    //   Rust spawn 不走 source 路径, 必须显式传.
    let (hermes_token, hermes_gateway_url) = read_hermes_dev_env();
    if hermes_token.is_none() {
        log::warn!(
            "[dream] CATFISH_INTERNAL_DEV_TOKEN 未在 ~/.hermes/.env 找到, dream_cli 会撞 llm_fail. \
             检查 ~/.hermes/.env 是否含此行."
        );
    }

    let mut cmd = process::background_tokio_command(&python);
    cmd.args(&args)
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(token) = &hermes_token {
        cmd.env("CATFISH_INTERNAL_DEV_TOKEN", token);
    }
    if let Some(url) = &hermes_gateway_url {
        cmd.env("CATFISH_GATEWAY_URL", url);
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Dream Engine: spawn dream_cli 失败: {e}"))?;

    let stdout = child.stdout.take().ok_or("Dream Engine: stdout 取不到")?;
    let stderr = child.stderr.take().ok_or("Dream Engine: stderr 取不到")?;

    // 后台读 stderr 写 log (不阻塞主路径 — stderr 量小, 错误诊断用)
    let app_for_stderr = app.clone();
    tokio::spawn(async move {
        let mut reader = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = reader.next_line().await {
            log::warn!("[dream] stderr: {}", line);
            let payload = serde_json::json!({
                "event": "stderr",
                "msg": line,
            })
            .to_string();
            let _ = app_for_stderr.emit(DREAM_EVENT, payload);
        }
    });

    // 主路径: 读 stdout 逐行 emit
    let mut final_result: Option<serde_json::Value> = None;
    let mut reader = BufReader::new(stdout).lines();
    while let Some(line) = reader
        .next_line()
        .await
        .map_err(|e| format!("Dream Engine: stdout read 错: {e}"))?
    {
        // 透传整行给前端 — 前端 JSON.parse. Rust 不解析协议字段.
        log::debug!("[dream] stdout: {}", line);
        if let Err(e) = app.emit(DREAM_EVENT, &line) {
            log::warn!("[dream] emit 失败: {} (line={})", e, line);
        }
        // 终态行 (带 ok 字段) 留一份 —— 进度行没有 ok, 不会误判。
        // 取最后一条而不是第一条: 中途万一有别的带 ok 的行, 最后那条才是结论。
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) {
            if v.get("ok").is_some() {
                final_result = Some(v);
            }
        }
    }

    let status = child
        .wait()
        .await
        .map_err(|e| format!("Dream Engine: wait child 失败: {e}"))?;
    let code = status.code();
    log::info!("[dream] CLI 退出, code={:?} · {}", code, describe(&final_result));

    Ok(DreamRunOutput {
        spawn_ok: true,
        command: cmd_display,
        exit_code: code,
        error: None,
        result: final_result,
    })
}

/// 读 ~/.catfish/memory_distill_state.json 拿上次蒸馏时间.
/// UI "上次蒸馏: X 小时前" 显示用.
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DreamStatus {
    /// 上次蒸馏 unix epoch 秒. None = 从未跑过.
    pub last_run_ts: Option<f64>,
    /// 上次蒸馏 ISO 时间字符串 (state file 自带).
    pub last_run_iso: Option<String>,
    /// distilled_facts.md 当前字节数. 0 = 文件不存在.
    pub distilled_bytes: u64,
    /// employee_journal.md 当前字节数 (蒸馏的输入). 0 = 文件不存在.
    pub journal_bytes: u64,
}

#[tauri::command(rename_all = "camelCase")]
pub async fn dream_distill_status() -> Result<DreamStatus, String> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .ok_or("HOME 未设")?;
    let catfish_home = PathBuf::from(home).join(".catfish");

    let state_path = catfish_home.join("memory_distill_state.json");
    let (last_run_ts, last_run_iso) = if state_path.exists() {
        match std::fs::read_to_string(&state_path) {
            Ok(text) => match serde_json::from_str::<serde_json::Value>(&text) {
                Ok(v) => (
                    v.get("last_run_ts").and_then(|x| x.as_f64()),
                    v.get("last_run_iso").and_then(|x| x.as_str()).map(String::from),
                ),
                Err(e) => {
                    log::warn!("[dream] state 文件 JSON parse 失败 (返默认): {e}");
                    (None, None)
                }
            },
            Err(_) => (None, None),
        }
    } else {
        (None, None)
    };

    let distilled_bytes = std::fs::metadata(catfish_home.join("distilled_facts.md"))
        .map(|m| m.len())
        .unwrap_or(0);
    let journal_bytes = std::fs::metadata(catfish_home.join("employee_journal.md"))
        .map(|m| m.len())
        .unwrap_or(0);

    Ok(DreamStatus {
        last_run_ts,
        last_run_iso,
        distilled_bytes,
        journal_bytes,
    })
}

// P3.5.1.7 (6/15 鸿波): 老 append_pythonpath helper 删 — 现在 spawn 绝对路径 dream_cli.py,
// dream_cli.py 内部 sys.path.insert 自适应, 不再依赖 PYTHONPATH 环境变量.

/// P3.5.1.9 (6/15 鸿波): 读 ~/.hermes/.env 抽 CATFISH_INTERNAL_DEV_TOKEN + CATFISH_GATEWAY_URL.
///
/// 真因: dream_cli subprocess 不继承 hermes 进程 env (hermes 启动 source .env, 我们 Rust spawn
/// 不走 shell). catfish-memory plugin `_gateway_dev_token()` 读 `CATFISH_INTERNAL_DEV_TOKEN` env,
/// 没拿到就返空 → `_call_distill_llm` 返 None → UI llm_fail.
///
/// 简单解析 KEY=VALUE 行 (忽略注释 / 空行). 不依赖 dotenv crate (新增依赖太重).
fn read_hermes_dev_env() -> (Option<String>, Option<String>) {
    let home = match std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
        Some(h) => PathBuf::from(h),
        None => return (None, None),
    };
    let env_path = home.join(".hermes").join(".env");
    let text = match std::fs::read_to_string(&env_path) {
        Ok(s) => s,
        Err(e) => {
            log::debug!("[dream] 读 {} 失败: {} (token 不传 subprocess)", env_path.display(), e);
            return (None, None);
        }
    };

    let mut token: Option<String> = None;
    let mut url: Option<String> = None;
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(rest) = line.strip_prefix("CATFISH_INTERNAL_DEV_TOKEN=") {
            let val = strip_env_quotes(rest);
            if !val.is_empty() {
                token = Some(val);
            }
        } else if let Some(rest) = line.strip_prefix("CATFISH_GATEWAY_URL=") {
            let val = strip_env_quotes(rest);
            if !val.is_empty() {
                url = Some(val);
            }
        }
    }
    (token, url)
}

/// 剥 .env 值的引号 + 行末注释. KEY="value" 和 KEY=value # comment 都处理.
fn strip_env_quotes(raw: &str) -> String {
    let mut s = raw.trim().to_string();
    // 切掉行末 ` # comment`
    if let Some(idx) = s.find(" #") {
        s.truncate(idx);
        s = s.trim().to_string();
    }
    // 剥单/双引号
    if (s.starts_with('"') && s.ends_with('"') && s.len() >= 2)
        || (s.starts_with('\'') && s.ends_with('\'') && s.len() >= 2)
    {
        s = s[1..s.len() - 1].to_string();
    }
    s
}

#[cfg(test)]
mod describe_tests {
    use super::*;
    use serde_json::json;

    // 8/4: 定时蒸馏上线当晚就发现 exit_code=0 分不出"真跑了"和"cooldown 跳过了",
    // 两种情况日志长得一模一样。这组测试钉住它们必须能被区分开。

    #[test]
    fn ran_and_skipped_are_distinguishable() {
        let ran = describe(&Some(json!({
            "ok": true, "chunks_total": 30, "bytes_written": 54129,
            "model": "catfish-public-qwen-flash"
        })));
        let skipped = describe(&Some(json!({"ok": false, "reason": "cooldown"})));
        assert_ne!(ran, skipped, "两种终态说出来是一样的话, 等于没说");
        assert!(ran.contains("30") && ran.contains("54129"), "{ran}");
        assert!(skipped.contains("24h"), "{skipped}");
    }

    #[test]
    fn other_failures_carry_the_reason() {
        let s = describe(&Some(json!({"ok": false, "reason": "llm_fail"})));
        assert!(s.contains("llm_fail"), "{s}");
    }

    #[test]
    fn missing_result_says_so_instead_of_pretending() {
        // 没终态时不能装作成功 —— 那正是这次要修的毛病
        let s = describe(&None);
        assert!(s.contains("没报终态"), "{s}");
    }
}
