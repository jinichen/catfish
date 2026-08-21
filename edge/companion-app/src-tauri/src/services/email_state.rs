//! 邮件调度的进程级状态: 评级缓存、push 历史、以及它们的落盘。
//!
//! 2026-08-15 从 email_scheduler.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! 两个 OnceLock (URGENCY_CACHE / PUSH_HISTORY) 加上 APP_HANDLE 都在这里 ——
//! 它们是"这个进程记住了什么", 跟调度循环本身是两回事。
//! push 历史落盘是为了让"这封急件今天已经吵过你一次"在重启 Companion 之后
//! 仍然成立。

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tauri::AppHandle;

pub(crate) const DEDUP_WINDOW_SECS: u64 = 24 * 3600; // 24h
const URGENCY_CACHE_FILE: &str = "email_urgency.json";
const PUSH_HISTORY_FILE: &str = "email_push_history.json";

/// 解 ~/.catfish/<file>. None = HOME 找不到 (不发声明跳过持久化).
fn catfish_state_file(name: &str) -> Option<PathBuf> {
    let home = std::env::var_os("HOME")?;
    let mut p = PathBuf::from(home);
    p.push(".catfish");
    p.push(name);
    Some(p)
}

pub(crate) fn now_epoch_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// 急邮件 push history: id → 上次 push 的 epoch 秒. 24h 内不重 push.
static PUSH_HISTORY: OnceLock<Mutex<HashMap<String, u64>>> = OnceLock::new();
pub(crate) fn push_history() -> &'static Mutex<HashMap<String, u64>> {
    PUSH_HISTORY.get_or_init(|| Mutex::new(HashMap::new()))
}

/// 启动时调一次 — load urgency_cache + push_history 从 disk.
/// 找不到文件 / 解析失败 → 静默, 当 empty (跟 5/18 老行为兼容).
pub(crate) fn load_persisted_state() {
    if let Some(path) = catfish_state_file(URGENCY_CACHE_FILE) {
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Ok(parsed) = serde_json::from_str::<HashMap<String, String>>(&content) {
                if let Ok(mut cache) = urgency_cache().lock() {
                    *cache = parsed;
                    log::info!(
                        "email_scheduler: loaded {} urgency entries from {}",
                        cache.len(),
                        path.display()
                    );
                }
            }
        }
    }
    if let Some(path) = catfish_state_file(PUSH_HISTORY_FILE) {
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Ok(parsed) = serde_json::from_str::<HashMap<String, u64>>(&content) {
                if let Ok(mut hist) = push_history().lock() {
                    // 启动时顺手 GC 24h 之前的, 防 file 越涨越大
                    let cutoff = now_epoch_secs().saturating_sub(DEDUP_WINDOW_SECS);
                    *hist = parsed.into_iter().filter(|(_, ts)| *ts >= cutoff).collect();
                    log::info!(
                        "email_scheduler: loaded {} push history entries (GC 后)",
                        hist.len()
                    );
                }
            }
        }
    }
}

/// atomic write JSON map to ~/.catfish/<file>. 失败 log.debug, 不抛.
/// .tmp + rename 防中途崩坏 — 半写文件比丢全部新增 cache 更糟.
fn persist_json_atomic<T: Serialize>(file: &str, data: &T) {
    let Some(path) = catfish_state_file(file) else { return };
    let Some(parent) = path.parent() else { return };
    let _ = std::fs::create_dir_all(parent);
    let json = match serde_json::to_string_pretty(data) {
        Ok(s) => s,
        Err(e) => {
            log::debug!("persist_json {file}: serialize 失败 {e}");
            return;
        }
    };
    let tmp = path.with_extension("json.tmp");
    if let Err(e) = std::fs::write(&tmp, json) {
        log::debug!("persist_json {file}: 写 tmp 失败 {e}");
        return;
    }
    if let Err(e) = std::fs::rename(&tmp, &path) {
        log::debug!("persist_json {file}: rename 失败 {e}");
    }
}

pub(crate) fn persist_urgency_cache() {
    if let Ok(cache) = urgency_cache().lock() {
        persist_json_atomic(URGENCY_CACHE_FILE, &*cache);
    }
}

pub(crate) fn persist_push_history() {
    if let Ok(hist) = push_history().lock() {
        persist_json_atomic(PUSH_HISTORY_FILE, &*hist);
    }
}

/// app handle 句柄, 给 background task 用来 emit Tauri 事件给前端.
/// schedule_email_scheduler() 启动时存进来.
pub(crate) static APP_HANDLE: OnceLock<AppHandle> = OnceLock::new();

/// urgency 评级缓存 — scheduler 评完一封, 写这里给 Tauri command email_urgency_map() 读.
/// id -> "急" | "中" | "低" 字符串 (跟 SerializableUrgency 同套). 已读 → tick 时自然
/// 从 baseline 移除 → 下次评级也不会再被加进来. 缓存上限 200 防内存涨, 老的先丢.
static URGENCY_CACHE: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();

pub(crate) fn urgency_cache() -> &'static Mutex<HashMap<String, String>> {
    URGENCY_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

// ── 8/21 分诊升级: action 缓存 (知/回/办 + 截止日) ──────────────────────
//
// **平行新缓存, 不动 urgency 的形状** —— urgency 的 `HashMap<String,String>`
// 穿过 email_urgency.json / 前端 store / localStorage / ListItem badge 整条链,
// 而且 8/15 那次烧 2470 万 token 的永动机三处修复 (ratedRef / 上限 600 /
// store merge) 全是围着它做的。改值形状 = 全链重来, 收益为零。
//
// 上限跟 urgency 同一个常量 —— 8/15 教训: 缓存上限 (200) 低于列表上限 (500)
// 是永动机燃料之一 ("评完 → 缓存丢一半 → unrated 变多 → 再评")。

/// 一封邮件的行动分诊。serde 双向: 进 email_action.json / 出 Tauri command。
#[derive(Clone, Debug, Serialize, serde::Deserialize)]
pub(crate) struct ActionEntry {
    /// "知" / "回" / "办"
    pub(crate) action: String,
    /// YYYY-MM-DD, 只有 回/办 且邮件里有明确日期才有
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) deadline: Option<String>,
}

const ACTION_CACHE_FILE: &str = "email_action.json";
static ACTION_CACHE: OnceLock<Mutex<HashMap<String, ActionEntry>>> = OnceLock::new();

pub(crate) fn action_cache() -> &'static Mutex<HashMap<String, ActionEntry>> {
    ACTION_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

pub(crate) fn persist_action_cache() {
    if let Ok(cache) = action_cache().lock() {
        persist_json_atomic(ACTION_CACHE_FILE, &*cache);
    }
}

/// 启动时 load (跟 load_persisted_state 同款, 单独一个函数免得改老函数签名)。
pub(crate) fn load_persisted_action_cache() {
    if let Some(path) = catfish_state_file(ACTION_CACHE_FILE) {
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Ok(parsed) = serde_json::from_str::<HashMap<String, ActionEntry>>(&content) {
                if let Ok(mut cache) = action_cache().lock() {
                    *cache = parsed;
                    log::info!(
                        "email_scheduler: loaded {} action entries from {}",
                        cache.len(),
                        path.display()
                    );
                }
            }
        }
    }
}
