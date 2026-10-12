//! 「提醒同步到微信」开关 (10/12, tool-bridge wechat_push.py)。
//!
//! Windows 上小鲶的提醒在 Catfish 自己的任务库里, 手机看不到。员工本机的 hermes 已经是
//! 他自己的微信机器人, 开了这个开关后, 闹钟到点时 tool-bridge 除了系统通知再推一条到他
//! 绑定的微信 (本机 → 腾讯 iLink → 手机, 不经过中央端)。
//!
//! - 开关: `~/.catfish/notify_prefs.json` {"wechat_reminders": bool}, 默认关
//! - 上次推送结果: `~/.catfish/notify_status.json` (tool-bridge 写)
//! - 是否已绑定微信: hermes `.env` 里 WEIXIN_TOKEN / ACCOUNT_ID / HOME_CHANNEL 都有

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};

use crate::services::tool_bridge_rpc;

const KEY: &str = "wechat_reminders";

fn catfish_dir() -> Result<PathBuf, String> {
    crate::services::meeting_store::home().map(|h| h.join(".catfish"))
}

/// hermes .env 里三个 WEIXIN_* 都非空 = 已绑定、能推。
fn wechat_bound_in(env_file: &Path) -> bool {
    let Ok(text) = std::fs::read_to_string(env_file) else { return false };
    let has = |key: &str| {
        text.lines().any(|l| {
            let l = l.trim();
            let l = l.strip_prefix("export ").unwrap_or(l).trim_start();
            l.strip_prefix(key)
                .and_then(|rest| rest.trim_start().strip_prefix('='))
                .map(|v| !v.trim().trim_matches(|c| c == '"' || c == '\'').is_empty())
                .unwrap_or(false)
        })
    };
    has("WEIXIN_TOKEN") && has("WEIXIN_ACCOUNT_ID") && has("WEIXIN_HOME_CHANNEL")
}

#[derive(Debug, Serialize)]
pub struct NotifyPrefs {
    pub wechat_reminders: bool,
    pub wechat_bound: bool,
    /// tool-bridge 写的上次推送结果 {last_at, ok, error}; 没推过为 null
    pub last: Value,
}

fn read_json(p: &Path) -> Value {
    std::fs::read_to_string(p).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null)
}

#[tauri::command]
pub fn notify_prefs_get() -> Result<NotifyPrefs, String> {
    let dir = catfish_dir()?;
    let prefs = read_json(&dir.join("notify_prefs.json"));
    let bound = crate::services::catfish_paths::hermes_home().is_some_and(|h| wechat_bound_in(&h.join(".env")));
    Ok(NotifyPrefs {
        wechat_reminders: prefs.get(KEY).and_then(Value::as_bool).unwrap_or(false),
        wechat_bound: bound,
        last: read_json(&dir.join("notify_status.json")),
    })
}

#[tauri::command]
pub fn notify_prefs_set(wechat_reminders: bool) -> Result<(), String> {
    let dir = catfish_dir()?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("建 ~/.catfish 失败: {e}"))?;
    let path = dir.join("notify_prefs.json");
    let mut prefs = read_json(&path);
    if !prefs.is_object() {
        prefs = json!({});
    }
    prefs[KEY] = json!(wechat_reminders);
    let tmp = dir.join(".notify_prefs.json.tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(&prefs).map_err(|e| e.to_string())?)
        .map_err(|e| format!("写设置失败: {e}"))?;
    std::fs::rename(&tmp, &path).map_err(|e| format!("写设置失败: {e}"))
}

/// 发一条测试消息到绑定的微信, 返回 {ok, error?, last_at?}。
#[tauri::command]
pub async fn notify_wechat_test() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/wechat_push_test", json!({}), Duration::from_secs(30)).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bound_needs_all_three_non_empty_keys() {
        let d = tempfile::tempdir().unwrap();
        let env = d.path().join(".env");
        std::fs::write(&env, "export WEIXIN_TOKEN=\"t\"\nWEIXIN_ACCOUNT_ID=a\nWEIXIN_HOME_CHANNEL='c'\n").unwrap();
        assert!(wechat_bound_in(&env));
        std::fs::write(&env, "WEIXIN_TOKEN=t\nWEIXIN_ACCOUNT_ID=a\nWEIXIN_HOME_CHANNEL=\n").unwrap();
        assert!(!wechat_bound_in(&env));
        std::fs::write(&env, "WEIXIN_TOKEN_OLD=t\nWEIXIN_ACCOUNT_ID=a\nWEIXIN_HOME_CHANNEL=c\n").unwrap();
        assert!(!wechat_bound_in(&env), "前缀相同的别的 key 不算");
        assert!(!wechat_bound_in(&d.path().join("missing")));
    }
}
