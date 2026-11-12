//! 会议纪要 / 转写的修正和分享 (11/12, 鸿波「是不是应该有个分享或者邮件; 内容是不是还应该可以修正」)。
//!
//! - 纪要: 改了存回去、存成 Word、放进群聊暂存区 —— 都在 tool-bridge (meeting_minutes_edit.py), 这里注入会议根目录。
//! - 转写: 点一句改错字。原话留在 `orig_text` (改回去时对照); 改过的转写下次「重新生成」纪要就按改后的写。
//! - 常用热词: 改错字时顺手把人名 / 术语记下, 新建会议的热词框自动带上 (~/.catfish/meetings/hotwords.json)。
//!
//! 发邮件 / 发到群都是员工在界面上点了才发, 这里没有直接发送的口子。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::meeting_store as store;
use crate::services::tool_bridge_rpc;

const RPC_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_SEGMENT_CHARS: usize = 2000;

async fn rpc(method: &str, id: &str, extra: Value) -> Result<Value, String> {
    let home = store::home()?;
    let mut params = json!({ "meeting_id": id, "catfish_home": home.join(".catfish").display().to_string() });
    if let (Some(p), Some(e)) = (params.as_object_mut(), extra.as_object()) {
        for (k, v) in e {
            p.insert(k.clone(), v.clone());
        }
    }
    tool_bridge_rpc::call_with_timeout(method, params, RPC_TIMEOUT)
        .await
        .map_err(|e| e.split_once("]: ").map(|(_, m)| m.to_string()).unwrap_or(e))
}

/// 改过的纪要存回去: `fields` = 逐项 (摘要 / 决议 / 待办 / 待定问题 / 上次遗留), `markdown` = 按文字模版写的全文。
#[tauri::command]
pub async fn meeting_minutes_save(id: String, fields: Option<Value>, markdown: Option<String>) -> Result<Value, String> {
    rpc("meeting/minutes_save", &id, json!({ "fields": fields, "markdown": markdown })).await
}

/// 存成 Word (会议目录里), 返回 {path, name, size} —— 导出、邮件附件都用它。
#[tauri::command]
pub async fn meeting_minutes_docx(id: String) -> Result<Value, String> {
    rpc("meeting/minutes_docx", &id, json!({})).await
}

/// 纪要文件放进群聊暂存区, 返回 {stage_id, name}; 界面再走群聊的「发送」。
#[tauri::command]
pub async fn meeting_minutes_stage(id: String) -> Result<Value, String> {
    rpc("meeting/minutes_stage", &id, json!({})).await
}

/// 改转写里的一句 (第 index 段)。空字符串 = 改回原话。
#[tauri::command]
pub fn meeting_transcript_edit(id: String, index: usize, text: String) -> Result<Value, String> {
    let root = store::meetings_root(&store::home()?);
    let p = store::transcript_path(&root, &id)?;
    edit_segment(&p, index, &text)
}

fn edit_segment(p: &std::path::Path, index: usize, text: &str) -> Result<Value, String> {
    let raw = std::fs::read_to_string(p).map_err(|_| "这场会议还没有转写结果".to_string())?;
    let mut doc: Value = serde_json::from_str(&raw).map_err(|e| format!("transcript.json 格式不对: {e}"))?;
    let seg = doc
        .get_mut("segments")
        .and_then(|s| s.as_array_mut())
        .and_then(|a| a.get_mut(index))
        .and_then(|s| s.as_object_mut())
        .ok_or("找不到这一句 (转写可能刚重新做过, 刷新一下)")?;
    let current = seg.get("text").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    let orig = seg.get("orig_text").and_then(|v| v.as_str()).map(str::to_string).unwrap_or_else(|| current.clone());
    let new: String = text.trim().chars().take(MAX_SEGMENT_CHARS).collect();
    if new.is_empty() || new == orig {
        seg.insert("text".into(), json!(orig));
        seg.remove("orig_text");
    } else {
        seg.insert("text".into(), json!(new));
        seg.insert("orig_text".into(), json!(orig));
    }
    let out = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?;
    let tmp = p.with_extension("json.tmp");
    std::fs::write(&tmp, out).map_err(|e| format!("保存转写失败: {e}"))?;
    std::fs::rename(&tmp, p).map_err(|e| format!("保存转写失败: {e}"))?;
    Ok(doc["segments"][index].clone())
}

fn hotwords_file() -> Result<std::path::PathBuf, String> {
    Ok(store::meetings_root(&store::home()?).join("hotwords.json"))
}

fn read_hotwords(p: &std::path::Path) -> Vec<String> {
    std::fs::read_to_string(p)
        .ok()
        .and_then(|t| serde_json::from_str::<Vec<String>>(&t).ok())
        .unwrap_or_default()
}

/// 常用热词 (改转写时记下的人名 / 术语), 新建会议时带进热词框。
#[tauri::command]
pub fn meeting_hotwords_saved() -> Result<Vec<String>, String> {
    Ok(read_hotwords(&hotwords_file()?))
}

/// 记下几个常用热词; `remove` 给了就是去掉。返回最新的整份。
#[tauri::command]
pub fn meeting_hotwords_remember(words: Vec<String>, remove: Option<bool>) -> Result<Vec<String>, String> {
    let p = hotwords_file()?;
    update_hotwords(&p, &words, remove.unwrap_or(false))
}

fn update_hotwords(p: &std::path::Path, words: &[String], remove: bool) -> Result<Vec<String>, String> {
    let mut cur = read_hotwords(p);
    let add = store::normalize_hotwords(words);
    if remove {
        cur.retain(|w| !add.contains(w));
    } else {
        // 新记的放前面: 超过上限时先挤掉最早记的
        let mut merged = add.clone();
        merged.extend(cur.into_iter().filter(|w| !add.contains(w)));
        cur = store::normalize_hotwords(&merged);
    }
    if let Some(dir) = p.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("保存热词失败: {e}"))?;
    }
    std::fs::write(p, serde_json::to_string_pretty(&cur).map_err(|e| e.to_string())?)
        .map_err(|e| format!("保存热词失败: {e}"))?;
    Ok(cur)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 改一句留原话_改回去去掉原话() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("transcript.json");
        std::fs::write(&p, r#"{"segments":[{"spk":0,"start":0,"end":1,"text":"西西RC末次"},{"spk":1,"start":1,"end":2,"text":"好"}]}"#).unwrap();
        let s = edit_segment(&p, 0, " CC RC末次 ").unwrap();
        assert_eq!(s["text"], "CC RC末次");
        assert_eq!(s["orig_text"], "西西RC末次");
        // 再改一次: 原话还是最初那句
        let s = edit_segment(&p, 0, "CCRC 末次").unwrap();
        assert_eq!(s["orig_text"], "西西RC末次");
        // 清空 = 改回原话
        let s = edit_segment(&p, 0, "").unwrap();
        assert_eq!(s["text"], "西西RC末次");
        assert!(s.get("orig_text").is_none());
        assert!(edit_segment(&p, 9, "x").is_err());
        let doc: Value = serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(doc["segments"][1]["text"], "好");
    }

    #[test]
    fn 常用热词_新的在前_去重_能删() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("meetings").join("hotwords.json");
        assert_eq!(update_hotwords(&p, &["CCRC".into(), "栾总".into()], false).unwrap(), vec!["CCRC", "栾总"]);
        assert_eq!(update_hotwords(&p, &["福富，CCRC".into()], false).unwrap(), vec!["福富", "CCRC", "栾总"]);
        assert_eq!(update_hotwords(&p, &["CCRC".into()], true).unwrap(), vec!["福富", "栾总"]);
        assert_eq!(read_hotwords(&p), vec!["福富", "栾总"]);
    }
}
