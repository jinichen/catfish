//! 会议说话人改名 + 纪要生成 / 读取 —— 前端入口 (10/1, docs/MEETING-MINUTES-PLAN.md §3)。
//!
//! 纪要在 tool-bridge 里生成 (meeting_minutes.py, JSON-RPC `meeting/summarize`),
//! 跟 recmode_rpc 一样走专门的命令, 不进小鲶的工具列表。这里注入三样东西:
//!   - auth_token: 员工自己的登录令牌 (oauth 静默刷新), 纪要用员工身份调大模型
//!   - gateway_url: Companion 配置里的网关地址。**显式传**, 不靠 tool-bridge 进程的
//!     环境变量 —— tool-bridge 启动时原来不带这个变量, 客户机上会连到 localhost
//!   - catfish_home: 会议目录的根

use std::collections::BTreeMap;
use std::time::Duration;

use serde_json::{json, Value};

use crate::services::meeting_store::{self as store, MeetingStatus};
use crate::services::{endpoints, oauth, tool_bridge_rpc};

/// 一小时会议分段总结要调几次大模型, 每次几十秒; 给足。
const SUMMARIZE_TIMEOUT: Duration = Duration::from_secs(15 * 60);

fn root() -> Result<std::path::PathBuf, String> {
    Ok(store::meetings_root(&store::home()?))
}

/// 说话人改名: {"0": "张三", "1": "李四"}。空名字 = 恢复成"说话人N"。
#[tauri::command]
pub fn meeting_set_speakers(id: String, names: BTreeMap<String, String>) -> Result<(), String> {
    let dir = store::meeting_dir(&root()?, &id)?;
    let clean: BTreeMap<String, String> = names
        .into_iter()
        .filter(|(k, v)| k.parse::<u32>().is_ok() && !v.trim().is_empty())
        .map(|(k, v)| (k, v.trim().chars().take(20).collect()))
        .collect();
    let body = serde_json::to_string_pretty(&clean).map_err(|e| e.to_string())?;
    std::fs::write(dir.join("speakers.json"), body).map_err(|e| format!("保存说话人名字失败: {e}"))
}

#[tauri::command]
pub fn meeting_speakers(id: String) -> Result<BTreeMap<String, String>, String> {
    let p = store::meeting_dir(&root()?, &id)?.join("speakers.json");
    match std::fs::read_to_string(&p) {
        Ok(t) => serde_json::from_str(&t).map_err(|e| format!("speakers.json 格式不对: {e}")),
        Err(_) => Ok(BTreeMap::new()),
    }
}

#[tauri::command]
pub async fn meeting_minutes_generate(id: String) -> Result<Value, String> {
    let home = store::home()?;
    let meta = store::load(&store::meetings_root(&home), &id)?;
    if meta.status != MeetingStatus::Transcribed {
        return Err("先完成转写再生成纪要".into());
    }
    let token = oauth::ensure_fresh_access_token()
        .await
        .ok_or("没登录 (拿不到访问令牌), 登录后再生成纪要")?;
    let params = json!({
        "meeting_id": id,
        "catfish_home": home.join(".catfish").display().to_string(),
        "auth_token": token,
        "gateway_url": endpoints::endpoints().gateway_base(),
    });
    log::info!("[meeting] {id} 生成纪要 → {}", endpoints::endpoints().gateway_base());
    tool_bridge_rpc::call_with_timeout("meeting/summarize", params, SUMMARIZE_TIMEOUT).await
}

/// {json: minutes.json, markdown: minutes.md}; 还没生成返回 null。
#[tauri::command]
pub fn meeting_minutes(id: String) -> Result<Value, String> {
    let dir = store::meeting_dir(&root()?, &id)?;
    let Ok(text) = std::fs::read_to_string(dir.join("minutes.json")) else { return Ok(Value::Null) };
    let doc: Value = serde_json::from_str(&text).map_err(|e| format!("minutes.json 格式不对: {e}"))?;
    let md = std::fs::read_to_string(dir.join("minutes.md")).unwrap_or_default();
    Ok(json!({ "json": doc, "markdown": md, "path": dir.join("minutes.md").display().to_string() }))
}
