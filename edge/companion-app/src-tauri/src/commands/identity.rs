//! 身份信息 —— 当前员工 / SOUL 来源 / Hermes skin / 当前活动会话。
//!
//! Hermes auth.json 没有真"用户"概念（只有 credential pool），
//! 所以"员工"行用 system `$USER` 兜底。
//!
//! SOUL 来源判断：
//!   - ~/.hermes/SOUL.md 是符号链接 → "鲶鱼定制" + 显示 link 目标
//!   - 是普通文件 → "本地 SOUL" 或 "默认"

use std::path::PathBuf;

use serde::Serialize;

use crate::services::catfish_paths;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct IdentityInfo {
    /// 系统用户名（macOS / Linux $USER, Windows $USERNAME）
    pub system_user: String,
    /// SOUL 来源描述（"鲶鱼定制" / "默认" / "本地 SOUL"）
    pub soul_source: String,
    /// SOUL 文件实际路径或符号链接目标
    pub soul_target: Option<String>,
    /// 当前 skin（Hermes 不持久化 skin 到 config.yaml，
    /// 所以一般是 "default"——员工通过 /skin daylight 等命令切换是运行时的）
    pub skin: String,
    /// config.yaml 里的 model.default
    pub default_model: Option<String>,
    /// 当前活动会话 id（state.db 里 ended_at IS NULL 的最新一条）
    pub active_session_id: Option<String>,
    pub active_session_model: Option<String>,
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn system_username() -> String {
    std::env::var("USER")
        .or_else(|_| std::env::var("USERNAME"))
        .unwrap_or_else(|_| "(unknown)".into())
}

fn resolve_soul() -> (String, Option<String>) {
    let Some(home) = home_dir() else {
        return ("(找不到 home)".into(), None);
    };
    let soul_path = home.join(".hermes").join("SOUL.md");
    if !soul_path.exists() {
        return ("(无 SOUL.md)".into(), None);
    }

    // 是符号链接 → 看目标
    if let Ok(meta) = std::fs::symlink_metadata(&soul_path) {
        if meta.file_type().is_symlink() {
            if let Ok(target) = std::fs::read_link(&soul_path) {
                let target_str = target.display().to_string();
                let source = if target_str.contains("catfish/edge/identity") {
                    "鲶鱼定制 (symlink)"
                } else {
                    "本地 symlink"
                };
                return (source.to_string(), Some(target_str));
            }
        }
    }
    // 普通文件
    ("默认 (本地文件)".into(), Some(soul_path.display().to_string()))
}

/// 从 ~/.hermes/config.yaml 读 skin + model.default。
///
/// Hermes 不持久化 skin 到 config.yaml（员工 /skin daylight 是运行时的，
/// 重启 hermes 就回 default），所以 skin 字段大概率返回 "default"。
fn read_config_yaml() -> (String, Option<String>) {
    let Some(home) = home_dir() else {
        return ("default".into(), None);
    };
    let cfg_path = home.join(".hermes").join("config.yaml");
    let Ok(text) = std::fs::read_to_string(&cfg_path) else {
        return ("default".into(), None);
    };
    let Ok(value) = serde_yaml::from_str::<serde_yaml::Value>(&text) else {
        return ("default".into(), None);
    };

    // skin —— 容忍多种 key 路径，最终大多数情况落到 "default"
    let skin = ["skin", "current_skin", "theme"]
        .iter()
        .find_map(|k| value.get(*k).and_then(|v| v.as_str()).map(String::from))
        .or_else(|| {
            value
                .get("ui")
                .and_then(|v| v.get("skin"))
                .and_then(|v| v.as_str())
                .map(String::from)
        })
        .unwrap_or_else(|| "default".into());

    let default_model = value
        .get("model")
        .and_then(|m| m.get("default"))
        .and_then(|v| v.as_str())
        .map(String::from);

    (skin, default_model)
}

fn read_active_session() -> (Option<String>, Option<String>) {
    let Some(home) = home_dir() else {
        return (None, None);
    };
    let db_path = home.join(".hermes").join("state.db");
    if !db_path.exists() {
        return (None, None);
    }
    let Ok(conn) = rusqlite::Connection::open(&db_path) else {
        return (None, None);
    };
    let row: rusqlite::Result<(String, Option<String>)> = conn.query_row(
        "SELECT id, model FROM sessions
         WHERE ended_at IS NULL
         ORDER BY started_at DESC
         LIMIT 1",
        [],
        |row| Ok((row.get(0)?, row.get(1)?)),
    );
    match row {
        Ok((id, model)) => (Some(id), model),
        Err(_) => (None, None),
    }
}

#[tauri::command]
pub async fn identity_info() -> Result<IdentityInfo, String> {
    tokio::task::spawn_blocking(|| {
        let (soul_source, soul_target) = resolve_soul();
        let (skin, default_model) = read_config_yaml();
        let (active_session_id, active_session_model) = read_active_session();
        // 摸下 catfish 路径（不是 identity 关键，只是检查存在性，免得打印警告）
        let _ = catfish_paths::catfish_root();

        Ok::<IdentityInfo, String>(IdentityInfo {
            system_user: system_username(),
            soul_source,
            soul_target,
            skin,
            default_model,
            active_session_id,
            active_session_model,
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}
