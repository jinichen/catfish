//! 对话卡片 (A2UI, 11/15): 按编号读 tool-bridge 存下的卡片。
//!
//! 照 A2UI 上游 subagent_tool 的「out-of-band」: 小鲶调 `catfish_form` 的 `card` 动作, tool-bridge 的专员写卡片、
//! 官方 SDK 校验过后存 `~/.catfish/cards/<编号>.json` (tool-bridge/a2ui_card.py), 只把编号还给小鲶;
//! 对话里那次工具调用的结果带着编号, 前端拿编号来这里读出 A2UI 消息再画。
//! 编号格式固定 (`c_YYYYMMDD_HHMMSS_6位十六进制`), 不认的一律拒 —— 不让任何字符串拼进路径。

use std::fs;
use std::path::PathBuf;

fn valid_id(id: &str) -> bool {
    let b = id.as_bytes();
    b.len() == 24
        && id.starts_with("c_")
        && b[2..10].iter().all(u8::is_ascii_digit)
        && b[10] == b'_'
        && b[11..17].iter().all(u8::is_ascii_digit)
        && b[17] == b'_'
        && b[18..].iter().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(c))
}

fn cards_dir() -> Option<PathBuf> {
    if let Ok(h) = std::env::var("CATFISH_HOME") {
        if !h.trim().is_empty() {
            return Some(PathBuf::from(h.trim()).join("cards"));
        }
    }
    crate::services::catfish_paths::catfish_home().map(|h| h.join("cards"))
}

/// 读一张卡片, 返回它的 A2UI 消息 (JSON 数组)。过期清掉了 / 编号不对 → Err (前端显示「卡片已过期」)。
#[tauri::command]
pub fn a2ui_card_read(card_id: String) -> Result<serde_json::Value, String> {
    if !valid_id(&card_id) {
        return Err(format!("卡片编号不对: {card_id}"));
    }
    let path = cards_dir().ok_or("找不到用户目录")?.join(format!("{card_id}.json"));
    let text = fs::read_to_string(&path).map_err(|_| "卡片已过期 (本机只留 90 天) 或不在这台电脑上".to_string())?;
    let body: serde_json::Value = serde_json::from_str(&text).map_err(|e| format!("卡片文件坏了: {e}"))?;
    match body.get("messages") {
        Some(m) if m.is_array() => Ok(m.clone()),
        _ => Err("卡片文件里没有内容".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids() {
        assert!(valid_id("c_20261115_093012_a1b2c3"));
        assert!(!valid_id("c_20261115_093012_A1B2C3"));
        assert!(!valid_id("../../etc/passwd"));
        assert!(!valid_id("c_20261115_093012_a1b2c3/../x"));
        assert!(!valid_id(""));
    }
}
