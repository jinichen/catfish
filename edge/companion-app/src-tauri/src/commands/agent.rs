//! BL-E11 命名权 — 前端调用读/写鲶鱼名字 + 人设.
//!
//! 前端 `useAgent()` hook 调 `get_agent_prefs` / `set_agent_prefs`.
//! 写入后下一次 chat 请求 `lib/chat.ts` 会带 X-Catfish-Agent-* header.

use serde::Serialize;

use crate::services::agent_prefs::{self, AgentPrefs, PERSONALITIES};

#[derive(Debug, Serialize)]
pub struct AgentPrefsView {
    pub name: String,
    pub personality: String,
    /// 白名单, 给前端渲染人设选择 UI 用
    pub personality_options: Vec<&'static str>,
}

#[tauri::command]
pub fn get_agent_prefs() -> Result<AgentPrefsView, String> {
    let p = agent_prefs::load().map_err(|e| e.to_string())?;
    Ok(AgentPrefsView {
        name: p.name,
        personality: p.personality,
        personality_options: PERSONALITIES.to_vec(),
    })
}

#[tauri::command]
pub fn set_agent_prefs(name: String, personality: String) -> Result<AgentPrefsView, String> {
    let prefs = AgentPrefs { name, personality };
    agent_prefs::save(&prefs).map_err(|e| e.to_string())?;
    // 写完读回, 防各种 trim/normalize 后跟前端不一致
    let saved = agent_prefs::load().map_err(|e| e.to_string())?;
    Ok(AgentPrefsView {
        name: saved.name,
        personality: saved.personality,
        personality_options: PERSONALITIES.to_vec(),
    })
}
