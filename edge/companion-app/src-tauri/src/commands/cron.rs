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
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| "找不到 HOME".to_string())?;
    Ok(PathBuf::from(home).join(".hermes").join("cron"))
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
    entries.sort_by(|a, b| b.1.cmp(&a.1));
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
