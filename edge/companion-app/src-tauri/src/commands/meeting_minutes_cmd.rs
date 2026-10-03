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
use crate::services::meeting_templates::{self as templates, Template};
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

/// `template_id`: None / "default" = 原来那份固定格式; 否则用员工存的模版 (10/3)。
#[tauri::command]
pub async fn meeting_minutes_generate(id: String, template_id: Option<String>) -> Result<Value, String> {
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
    let mut params = params;
    if let Some(tid) = template_id.filter(|t| t != templates::DEFAULT_ID) {
        // 模版正文在这里读好随 RPC 带过去, tool-bridge 那边不用知道文件在哪
        let t = templates::load(&home, &tid)?;
        params["template"] = if t.is_file() {
            // Word / Excel: 给原文件路径, tool-bridge 生成时重新认空、填好另存到会议目录
            json!({ "id": t.id, "name": t.name, "kind": t.kind,
                    "path": templates::file_path(&home, &t).display().to_string() })
        } else {
            json!({ "id": t.id, "name": t.name, "kind": "markdown", "body": t.body })
        };
    }
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

// ── 纪要模版 (10/3, services/meeting_templates.rs) ──

#[tauri::command]
pub fn meeting_templates_list() -> Result<Vec<Template>, String> {
    Ok(templates::list(&store::home()?))
}

/// id 不传 = 新建。
#[tauri::command]
pub fn meeting_template_save(id: Option<String>, name: String, body: String) -> Result<Template, String> {
    templates::save(&store::home()?, id.as_deref(), &name, &body)
}

#[tauri::command]
pub fn meeting_template_delete(id: String) -> Result<(), String> {
    templates::delete(&store::home()?, &id)
}

/// 上传单位固定的 Word / Excel 纪要模版 (10/3)。id 给了 = 替换那个模版的文件。
/// 先让 tool-bridge 认一遍要填的空, 认不出来就不收 (返回带"怎么加占位符"的说明)。
#[tauri::command]
pub async fn meeting_template_upload(
    id: Option<String>,
    name: String,
    filename: String,
    data_b64: String,
) -> Result<Template, String> {
    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data_b64.trim())
        .map_err(|e| format!("文件读取失败: {e}"))?;
    let home = store::home()?;
    let (tid, kind, staged) = templates::stage_file(&home, id.as_deref(), &filename, &bytes)?;
    let inspected = tool_bridge_rpc::call_with_timeout(
        "meeting/template_inspect",
        json!({ "path": staged.display().to_string() }),
        Duration::from_secs(60),
    )
    .await;
    let slots = match inspected {
        Ok(v) => v.get("slots").cloned().unwrap_or(Value::Null),
        Err(e) => {
            templates::discard_file(&staged);
            // "tool-bridge 错误 [-32602]: 没在模版里找到…" → 只留后半句给员工看
            return Err(e.split_once("]: ").map(|(_, m)| m.to_string()).unwrap_or(e));
        }
    };
    templates::commit_file(&home, &tid, &kind, &staged, &name, &filename, slots)
}

/// 生成好的 Word / Excel 纪要 (minutes.json 里的 output_file) 用系统默认程序打开。
#[tauri::command]
pub async fn meeting_minutes_open_file(id: String, reveal: bool) -> Result<(), String> {
    let dir = store::meeting_dir(&root()?, &id)?;
    let doc: Value = serde_json::from_str(
        &std::fs::read_to_string(dir.join("minutes.json")).map_err(|_| "还没生成纪要".to_string())?,
    )
    .map_err(|e| format!("minutes.json 格式不对: {e}"))?;
    let file = doc.get("output_file").and_then(Value::as_str).ok_or("这份纪要没有 Word / Excel 文件")?;
    // 只开会议目录里的文件 (minutes.json 是本机写的, 也不给它指到别处的机会)
    let path = std::path::PathBuf::from(file);
    if path.parent() != Some(dir.as_path()) || !path.is_file() {
        return Err("纪要文件不见了, 重新生成一次".into());
    }
    let p = path.display().to_string();
    if reveal {
        super::file::reveal_in_finder(p).await
    } else {
        super::file::open_file(p).await
    }
}
