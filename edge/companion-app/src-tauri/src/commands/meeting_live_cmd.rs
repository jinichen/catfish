//! 会中小鲶 (11/15, docs/MEETING-MINUTES-PLAN.md §8) —— 前端入口。
//!
//! - 实时文字: services::meeting_live 起的进程写会议目录的 live_transcript.jsonl / live_state.json, 这里只读
//! - 问小鲶 / 主动提示: tool-bridge `meeting/live_assist` (meeting_live.py), 跟纪要一样注入员工自己的
//!   登录令牌和网关地址; 结果存会议目录的 live_notes.json
//! - 记到纪要 / 忽略 / 删: tool-bridge `meeting/live_note` (只动本机文件)

use std::path::Path;
use std::time::Duration;

use serde_json::{json, Value};

use crate::services::meeting_store::{self as store, MeetingMeta};
use crate::services::{endpoints, meeting_asr as asr, meeting_live as live, oauth, tool_bridge_rpc};

const ASSIST_TIMEOUT: Duration = Duration::from_secs(150);

fn dir_of(id: &str) -> Result<std::path::PathBuf, String> {
    store::meeting_dir(&store::meetings_root(&store::home()?), id)
}

/// 开始录音时调 (commands::meeting)。没装会议组件包 / 找不到脚本 / 起不来: 只记日志, 录音照常。
#[cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
pub fn start_live(root: &Path, meta: &MeetingMeta, audio: &Path) {
    let Ok(home) = store::home() else { return };
    let Some(inst) = asr::installed(&home) else {
        log::info!("[meeting-live] {} 没装会议组件包, 不做实时转写", meta.id);
        return;
    };
    let r = super::file_parse_env::find_script("meeting_asr_live.py").and_then(|script| {
        let dir = store::meeting_dir(root, &meta.id)?;
        live::start(&inst, &script, audio, &dir, &meta.hotwords, &asr::root(&home).join(".empty-cache"))
    });
    if let Err(e) = r {
        log::warn!("[meeting-live] {} 实时转写没起来 (录音不受影响): {e}", meta.id);
    }
}

/// 第 `since` 行起的会中文字 + 状态 (loading / running / lagging / finishing / done / error)。
#[tauri::command]
pub fn meeting_live_read(id: String, since: Option<usize>) -> Result<Value, String> {
    let dir = dir_of(&id)?;
    let (lines, total) = live::read_lines(&dir, since.unwrap_or(0));
    Ok(json!({ "lines": lines, "total": total, "state": live::read_state(&dir) }))
}

async fn rpc(method: &str, id: &str, extra: Value, with_token: bool) -> Result<Value, String> {
    let home = store::home()?;
    let mut params = json!({ "meeting_id": id, "catfish_home": home.join(".catfish").display().to_string() });
    if with_token {
        let token = oauth::ensure_fresh_access_token().await.ok_or("没登录 (拿不到访问令牌), 登录后再问小鲶")?;
        params["auth_token"] = token.into();
        params["gateway_url"] = endpoints::endpoints().gateway_base().into();
    }
    if let (Some(p), Some(e)) = (params.as_object_mut(), extra.as_object()) {
        p.extend(e.clone());
    }
    tool_bridge_rpc::call_with_timeout(method, params, ASSIST_TIMEOUT).await
}

/// kind = "ask" (question 必填) | "hint" (主动提示, 新说的话不多就不调模型)。
#[tauri::command]
pub async fn meeting_live_assist(id: String, kind: String, question: Option<String>) -> Result<Value, String> {
    if kind != "ask" && kind != "hint" {
        return Err(format!("小鲶不会这个: {kind}"));
    }
    rpc("meeting/live_assist", &id, json!({ "kind": kind, "question": question.unwrap_or_default() }), true).await
}

#[tauri::command]
pub async fn meeting_live_notes(id: String) -> Result<Value, String> {
    rpc("meeting/live_notes", &id, json!({}), false).await
}

/// 某条提示 / 问答: state = new | pinned (记到纪要) | ignored; delete = 删掉 (item_id 空 = 整张卡)。
#[tauri::command]
pub async fn meeting_live_note(id: String, note_id: String, item_id: Option<String>, state: Option<String>, delete: Option<bool>) -> Result<Value, String> {
    rpc("meeting/live_note", &id,
        json!({ "note_id": note_id, "item_id": item_id.unwrap_or_default(), "state": state.unwrap_or_default(), "delete": delete.unwrap_or(false) }),
        false).await
}

/// 录音中途开 / 关主动提示。
#[tauri::command]
pub fn meeting_live_hints_set(id: String, on: bool) -> Result<MeetingMeta, String> {
    let root = store::meetings_root(&store::home()?);
    let mut meta = store::load(&root, &id)?;
    meta.live_hints = Some(on);
    store::save(&root, &meta)?;
    Ok(meta)
}
