//! 11/09 知识页「分享到部门」对话框的预检 (tool-bridge wiki_share.py)。
//!
//! 只读: 这一页发出去长什么样 (来源换成人话 / 原件链接)、每条来源能不能把原件一起分享、
//! 每条关系同事那边能不能看到、每一项的内容扫描结果。真发布还是走 tools/dispatch catfish_wiki_publish
//! (界面上点的确认才算数), 这里不开别的口子。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::tool_bridge_rpc;

/// 要拉一次部门知识库列表 + 扫本机几十个条目, 给宽一点
const PLAN_TIMEOUT: Duration = Duration::from_secs(30);

#[tauri::command]
pub async fn wiki_share_plan(rel_path: String, attach_sources: Option<Vec<String>>) -> Result<Value, String> {
    if !rel_path.starts_with("wiki/") || rel_path.contains("..") {
        return Err(format!("只能分享知识库里的页: {rel_path}"));
    }
    tool_bridge_rpc::call_with_timeout(
        "wiki/share_plan",
        json!({ "wiki_rel_path": rel_path, "attach_sources": attach_sources.unwrap_or_default() }),
        PLAN_TIMEOUT,
    )
    .await
}
