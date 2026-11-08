//! P3.5.105 (6/25 鸿波 catch "定时任务跑没跑结果如何都看不到") — cron 监控 + 操作.
//!
//! # 真因 audit
//!
//! hermes 真有完整 cron 监控数据:
//!   ~/.hermes/cron/jobs.json     ← jobs[] 含 last_run_at / last_status / last_error
//!   ~/.hermes/cron/output/<id>/  ← 每次跑 .md 真完整输出
//! 但 Companion UI 真 0 处展示. 鸿波 daily-morning-brief 6/24 streaming error, 自己不知道.
//!
//! # 真路径设计 (A + D 混合)
//!
//! - **读** (list / outputs / output_read): A 直读 ~/.hermes/cron/ 文件 (jobs.lock 只写时锁)
//! - **写** (pause / resume / delete): D 走 P26 RESTful endpoint (hermes daemon 内 cron.jobs
//!   public function), 自动触发 scheduler 重新 load + 加锁安全
//!
//! 鉴权: services::hermes_api_config 真现成 (url + key 跟 ~/.hermes/.env API_SERVER_KEY 共享).
//!
//! # 7 个 Tauri command
//!
//!   cron_jobs_list()                    → Vec<CronJob>       (A 直读)
//!   cron_job_outputs(id, limit)          → Vec<OutputMeta>   (A readdir)
//!   cron_job_output_read(id, ts)         → String            (A 读 .md)
//!   cron_job_pause(id, reason?)          → ()                (D HTTP POST)
//!   cron_job_resume(id)                  → ()                (D HTTP POST)
//!   cron_job_delete(id)                  → ()                (D HTTP DELETE)

use std::cmp::Reverse;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::services::hermes_api_config::hermes_api_config;

// ── 数据结构 ──

/// 跟 ~/.hermes/cron/jobs.json 真 schema 一致 (25 字段). 字段 nullable 严格按真数据.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CronJob {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub prompt: Option<String>,
    #[serde(default)]
    pub skills: Vec<String>,
    #[serde(default)]
    pub skill: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub schedule: Option<Value>, // {kind, expr, display} 真嵌套, 透传给前端
    #[serde(default)]
    pub schedule_display: Option<String>,
    #[serde(default)]
    pub repeat: Option<Value>, // {times, completed}
    pub enabled: bool,
    pub state: String, // 'scheduled' / 'paused' / 等
    #[serde(default)]
    pub paused_at: Option<String>,
    #[serde(default)]
    pub paused_reason: Option<String>,
    #[serde(default)]
    pub created_at: Option<String>,
    #[serde(default)]
    pub next_run_at: Option<String>,
    #[serde(default)]
    pub last_run_at: Option<String>,
    #[serde(default)]
    pub last_status: Option<String>, // 'ok' / 'error'
    #[serde(default)]
    pub last_error: Option<String>,
    #[serde(default)]
    pub last_delivery_error: Option<String>,
    #[serde(default)]
    pub deliver: Option<String>,
    // P3.5.106 P27 (6/25 鸿波 catch "失败不重试"): cron 失败 5/10/15 三档自动重试
    // 真状态字段. catfish-xcatfish-user plugin.py:_patch_p27_cron_auto_retry 真写.
    // 老 jobs.json (P27 ship 前) 真无这俩字段, serde(default) None 真兼容.
    #[serde(default)]
    pub catfish_retry_attempt: Option<u32>, // 0/1/2/3 — 真当前重试次数
    #[serde(default)]
    pub catfish_retry_exhausted: Option<bool>, // true = 3 次都失败, 按 schedule 等下次
}

#[derive(Debug, Clone, Serialize)]
pub struct OutputMeta {
    /// 文件名 timestamp 部分 (e.g. "2026-06-24_09-00-11")
    pub timestamp: String,
    /// 文件大小字节
    pub size_bytes: u64,
    /// 前 200 字 snippet (head preview)
    pub snippet: String,
}

// ── 路径 helpers ──

fn cron_home() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| "找不到 HOME".to_string())?;
    Ok(crate::services::catfish_paths::hermes_home_for(&PathBuf::from(home)).join("cron"))
}

fn jobs_json_path() -> Result<PathBuf, String> {
    Ok(cron_home()?.join("jobs.json"))
}

fn job_output_dir(job_id: &str) -> Result<PathBuf, String> {
    // 防 path traversal — job_id 严格只允许字母数字 + 下划线 + 短横
    if job_id.is_empty()
        || job_id.contains('/')
        || job_id.contains('\\')
        || job_id.contains("..")
    {
        return Err(format!("非法 job_id: {job_id:?}"));
    }
    Ok(cron_home()?.join("output").join(job_id))
}

// ── A 路径: 直读文件 ──

/// 读 ~/.hermes/cron/jobs.json 返 jobs[]. 文件不存在返空 list (hermes 还没跑过任何 cron).
#[tauri::command]
pub async fn cron_jobs_list() -> Result<Vec<CronJob>, String> {
    let path = jobs_json_path()?;
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(&path)
        .map_err(|e| format!("读 jobs.json 失败: {e}"))?;
    let parsed: Value = serde_json::from_str(&content)
        .map_err(|e| format!("解析 jobs.json 失败: {e}"))?;
    let jobs_arr = parsed.get("jobs").and_then(|v| v.as_array());
    let Some(jobs_arr) = jobs_arr else {
        return Ok(Vec::new());
    };
    let mut result = Vec::with_capacity(jobs_arr.len());
    for item in jobs_arr {
        match serde_json::from_value::<CronJob>(item.clone()) {
            Ok(job) => result.push(job),
            Err(e) => {
                // 单 entry 解析失败不阻塞整列表 (hermes 升级加字段时兼容)
                log::warn!("cron_jobs_list: 跳过解析失败 entry: {e}");
            }
        }
    }
    Ok(result)
}

/// 列 ~/.hermes/cron/output/<id>/*.md, 按 mtime 倒序, 最多 limit (默认 10), 含 snippet.
#[tauri::command]
pub async fn cron_job_outputs(
    job_id: String,
    limit: Option<usize>,
) -> Result<Vec<OutputMeta>, String> {
    let dir = job_output_dir(&job_id)?;
    if !dir.is_dir() {
        return Ok(Vec::new());
    }
    let limit = limit.unwrap_or(10);
    let mut entries: Vec<(PathBuf, std::time::SystemTime)> = Vec::new();
    for entry in fs::read_dir(&dir).map_err(|e| format!("read_dir 失败: {e}"))? {
        let Ok(entry) = entry else { continue };
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("md") {
            continue;
        }
        // 跳隐藏文件 (tmp)
        if path
            .file_name()
            .and_then(|s| s.to_str())
            .map_or(true, |n| n.starts_with('.'))
        {
            continue;
        }
        let mtime = entry
            .metadata()
            .and_then(|m| m.modified())
            .unwrap_or(std::time::SystemTime::UNIX_EPOCH);
        entries.push((path, mtime));
    }
    entries.sort_by_key(|e| Reverse(e.1));
    entries.truncate(limit);

    let mut out = Vec::with_capacity(entries.len());
    for (path, _mtime) in entries {
        let stem = path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();
        let size_bytes = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        // 读前 200 字 snippet
        let snippet = read_snippet(&path, 200).unwrap_or_default();
        out.push(OutputMeta {
            timestamp: stem,
            size_bytes,
            snippet,
        });
    }
    Ok(out)
}

fn read_snippet(path: &Path, max_chars: usize) -> Result<String, String> {
    let content = fs::read_to_string(path)
        .map_err(|e| format!("读 snippet 失败 {}: {e}", path.display()))?;
    // 按字符截 (不按字节, 防中文截一半)
    let snippet: String = content.chars().take(max_chars).collect();
    Ok(snippet)
}

/// 读单个 output .md 完整内容. timestamp 真是文件 stem (不含 .md).
#[tauri::command]
pub async fn cron_job_output_read(
    job_id: String,
    timestamp: String,
) -> Result<String, String> {
    // 防 path traversal — timestamp 严格只允许 数字 / 短横 / 下划线
    if timestamp.is_empty()
        || timestamp.contains('/')
        || timestamp.contains('\\')
        || timestamp.contains("..")
    {
        return Err(format!("非法 timestamp: {timestamp:?}"));
    }
    let dir = job_output_dir(&job_id)?;
    let path = dir.join(format!("{timestamp}.md"));
    if !path.exists() {
        return Err(format!(
            "output 文件不存在: {} (job_id={}, ts={})",
            path.display(),
            job_id,
            timestamp
        ));
    }
    fs::read_to_string(&path).map_err(|e| format!("读 output 失败: {e}"))
}

// ── D 路径: HTTP 调 P26 endpoint ──

fn hermes_url_with_path(path: &str) -> Result<String, String> {
    let cfg = hermes_api_config();
    if cfg.key.is_none() {
        return Err(
            "hermes API key 没配 (~/.catfish/companion.yaml hermes_api.key \
             或 env CATFISH_HERMES_API_KEY). 检查跟 ~/.hermes/.env API_SERVER_KEY 是否同值."
                .to_string(),
        );
    }
    let base = cfg.url.trim_end_matches('/');
    Ok(format!("{}{}", base, path))
}

fn hermes_auth_header() -> Result<String, String> {
    let cfg = hermes_api_config();
    let key = cfg
        .key
        .as_ref()
        .ok_or_else(|| "hermes API key 没配".to_string())?;
    Ok(format!("Bearer {}", key))
}

async fn http_post_json(
    url: &str,
    auth: &str,
    body: serde_json::Value,
) -> Result<(), String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(url)
        .header("Authorization", auth)
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("POST {url} 失败: {e}"))?;
    handle_resp(resp, url).await
}

async fn http_delete(url: &str, auth: &str) -> Result<(), String> {
    let client = reqwest::Client::new();
    let resp = client
        .delete(url)
        .header("Authorization", auth)
        .send()
        .await
        .map_err(|e| format!("DELETE {url} 失败: {e}"))?;
    handle_resp(resp, url).await
}

async fn handle_resp(resp: reqwest::Response, url: &str) -> Result<(), String> {
    let status = resp.status();
    if status.is_success() {
        return Ok(());
    }
    let body_text = resp.text().await.unwrap_or_default();
    // hermes P26 返 {"ok": false, "error": "..."}, 透传 error 给前端 UI
    let err_msg = serde_json::from_str::<Value>(&body_text)
        .ok()
        .and_then(|v| v.get("error").and_then(|e| e.as_str()).map(String::from))
        .unwrap_or_else(|| body_text.clone());
    Err(format!("HTTP {} {url}: {err_msg}", status.as_u16()))
}

#[tauri::command]
pub async fn cron_job_pause(
    job_id: String,
    reason: Option<String>,
) -> Result<(), String> {
    let url = hermes_url_with_path(&format!(
        "/api/cron/jobs/{}/pause",
        urlencoding::encode(&job_id)
    ))?;
    let auth = hermes_auth_header()?;
    let body = if let Some(r) = reason {
        serde_json::json!({ "reason": r })
    } else {
        serde_json::json!({})
    };
    http_post_json(&url, &auth, body).await
}

#[tauri::command]
pub async fn cron_job_resume(job_id: String) -> Result<(), String> {
    let url = hermes_url_with_path(&format!(
        "/api/cron/jobs/{}/resume",
        urlencoding::encode(&job_id)
    ))?;
    let auth = hermes_auth_header()?;
    http_post_json(&url, &auth, serde_json::json!({})).await
}

#[tauri::command]
pub async fn cron_job_delete(job_id: String) -> Result<(), String> {
    let url = hermes_url_with_path(&format!(
        "/api/cron/jobs/{}",
        urlencoding::encode(&job_id)
    ))?;
    let auth = hermes_auth_header()?;
    http_delete(&url, &auth).await
}

// ── 11/09 小鲶秘书: 提醒条上的「重跑」+ 托管周期性事务 / 会前准备 建任务 ──
//
// 走 hermes 原生 /api/jobs (api_server.py _http_route_table), 不是 P26 的 /api/cron/jobs:
// 原生的 create / run 本来就有, 不用再打补丁。审计锚点见 audit_hermes_compat.sh Section 22。

/// POST /api/jobs/{id}/run —— 马上跑一次 (不动排期, hermes 记成 extra fire)
#[tauri::command]
pub async fn cron_job_run(job_id: String) -> Result<(), String> {
    let url = hermes_url_with_path(&format!("/api/jobs/{}/run", urlencoding::encode(&job_id)))?;
    let auth = hermes_auth_header()?;
    http_post_json(&url, &auth, serde_json::json!({})).await
}

/// 建任务的参数 —— 只放我们用得到的: hermes 其余字段 (model / skills ...) 走它自己的默认,
/// 模型由 P21 跟 Companion 里选的走。
#[derive(Debug, Clone, Deserialize)]
pub struct CronJobCreateArgs {
    pub name: String,
    /// cron 表达式 ("0 8 * * 1") 或本地时间 ISO ("2026-11-10T16:00") —— hermes parse_schedule 两种都认
    pub schedule: String,
    pub prompt: String,
    /// 一次性任务传 1; 周期任务不传
    #[serde(default)]
    pub repeat: Option<u32>,
}

/// POST /api/jobs —— 建一条定时任务, 返回 hermes 存下的那条 (含 id / next_run_at)。
/// deliver 固定 "local": 结果由 Companion 提醒条报 (useCronWatcher), 不往微信推 ——
/// 微信那条线要员工先给机器人发过话才通 (iLink session not ready), 推不到还算一次失败。
#[tauri::command]
pub async fn cron_job_create(args: CronJobCreateArgs) -> Result<Value, String> {
    let url = hermes_url_with_path("/api/jobs")?;
    let auth = hermes_auth_header()?;
    let mut body = serde_json::json!({
        "name": args.name,
        "schedule": args.schedule,
        "prompt": args.prompt,
        "deliver": "local",
    });
    if let Some(n) = args.repeat {
        body["repeat"] = serde_json::json!(n);
    }
    let resp = reqwest::Client::new()
        .post(&url)
        .header("Authorization", &auth)
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("POST {url} 失败: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    let v: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    if !status.is_success() {
        let msg = v.get("error").and_then(|e| e.as_str()).map(String::from).unwrap_or(text);
        return Err(format!("HTTP {} {url}: {msg}", status.as_u16()));
    }
    v.get("job").cloned().ok_or_else(|| format!("hermes 没返回 job: {text}"))
}

/// 对真 hermes 跑一遍小鲶秘书用到的整条链: 建一次性任务 → 立即跑 → jobs.json 记下这次 → 存档读得到。
/// 默认跳过; 手动跑 (临时 HOME 里起一个 hermes gateway, 不碰员工真数据):
///   HOME=<临时> CATFISH_HERMES_API_URL=http://127.0.0.1:<port> CATFISH_HERMES_API_KEY=<key> \
///   CATFISH_E2E_ARCHIVE_OUT=<存档拷到哪> cargo test --lib cron_secretary_e2e -- --ignored --nocapture
#[cfg(test)]
mod secretary_e2e {
    use super::*;

    #[tokio::test]
    #[ignore]
    async fn cron_secretary_e2e() {
        let at = (chrono::Local::now() + chrono::Duration::hours(3)).format("%Y-%m-%dT%H:%M").to_string();
        let job = cron_job_create(CronJobCreateArgs {
            name: "会前准备：e2e 周例会（11/10 10:00）".into(),
            schedule: at.clone(),
            prompt: "请为我下面这个会提前准备一份会务安排。只查阅和起草：不要发会议通知、不要回邮件、不要改日程。".into(),
            repeat: Some(1),
        })
        .await
        .expect("建任务");
        let id = job["id"].as_str().expect("job.id").to_string();
        assert_eq!(job["deliver"], "local");
        assert_eq!(job["schedule"]["kind"], "once", "本地 ISO 要被认成一次性: {job}");
        assert!(job["next_run_at"].as_str().unwrap().starts_with(&at[..16]), "排在 {at}: {job}");

        cron_job_run(id.clone()).await.expect("立即跑");
        let mut ran: Option<CronJob> = None;
        for _ in 0..120 {
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            let jobs = cron_jobs_list().await.expect("jobs.json");
            if let Some(j) = jobs.into_iter().find(|j| j.id == id && j.last_run_at.is_some()) {
                ran = Some(j);
                break;
            }
        }
        let ran = ran.expect("两分钟内跑完");
        println!("last_status={:?} last_run_at={:?} error={:?}", ran.last_status, ran.last_run_at, ran.last_error);
        assert_eq!(ran.last_status.as_deref(), Some("ok"));
        let outs = cron_job_outputs(id.clone(), Some(1)).await.expect("存档列表");
        assert_eq!(outs.len(), 1, "跑完有一份存档");
        let archive = cron_job_output_read(id.clone(), outs[0].timestamp.clone()).await.expect("读存档");
        assert!(archive.contains("## Response"), "{archive}");
        if let Ok(p) = std::env::var("CATFISH_E2E_ARCHIVE_OUT") {
            std::fs::write(&p, &archive).unwrap();
            println!("存档 {} → {p} (runAt={:?})", outs[0].timestamp, ran.last_run_at);
        }
        cron_job_delete(id).await.expect("清掉");
    }
}
