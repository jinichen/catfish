//! 同事群聊 (11/5, docs/GROUP-CHAT-PLAN.md): 「协同」页的建群 / 加入 / 发消息 / 群主管理。
//!
//! 数据和规则都在 tool-bridge (colleague_rooms.py), 这里只转 RPC。
//! 只放行 `pim/croom_*` 这一组, 别的方法一律拒 —— 前端不能借这个口子调任意 tool-bridge 方法。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::{endpoints, oauth, tool_bridge_rpc};

/// 一条群消息要给每个成员各投一封 (最多 50 人, 8 路并发), 比单次 RPC 久一些。
const RPC_TIMEOUT: Duration = Duration::from_secs(90);

const ALLOWED: &[&str] = &[
    "list", "get", "sync", "create", "join", "decline", "send", "read", "add", "remove", "update", "disband", "leave",
    "delete", "search", "settings_get", "settings_set", "transfer", "resend", "file_save",
];

fn method_for(action: &str) -> Result<String, String> {
    if ALLOWED.contains(&action) {
        Ok(format!("pim/croom_{action}"))
    } else {
        Err(format!("不支持的群聊动作: {action}"))
    }
}

#[tauri::command]
pub async fn colleague_room_call(action: String, params: Option<Value>) -> Result<Value, String> {
    let method = method_for(&action)?;
    tool_bridge_rpc::call_with_timeout(&method, params.unwrap_or_else(|| json!({})), RPC_TIMEOUT).await
}

/// 第三步: 小鲶总结 / 整理待办 / 起草回复 (要调大模型)。跟会议纪要一样, 这里注入员工自己的登录令牌和网关地址,
/// tool-bridge 用员工身份调模型; 只回草稿, 发不发员工在界面上点。
const ASSIST_TIMEOUT: Duration = Duration::from_secs(180);
const ASSIST_KINDS: &[&str] = &["summary", "todos", "reply"];

#[tauri::command]
pub async fn colleague_room_assist(
    room_id: String,
    kind: String,
    instruction: Option<String>,
    reply_to: Option<String>,
) -> Result<Value, String> {
    if !ASSIST_KINDS.contains(&kind.as_str()) {
        return Err(format!("小鲶不会这个: {kind}"));
    }
    let token = oauth::ensure_fresh_access_token()
        .await
        .ok_or("没登录 (拿不到访问令牌), 登录后再让小鲶帮忙")?;
    let params = json!({
        "room_id": room_id,
        "kind": kind,
        "instruction": instruction.unwrap_or_default(),
        "reply_to": reply_to.unwrap_or_default(),
        "auth_token": token,
        "gateway_url": endpoints::endpoints().gateway_base(),
    });
    tool_bridge_rpc::call_with_timeout("room/assist", params, ASSIST_TIMEOUT).await
}

#[cfg(test)]
mod tests {
    use super::method_for;

    #[test]
    fn 只放行群聊这一组() {
        assert_eq!(method_for("send").unwrap(), "pim/croom_send");
        assert_eq!(method_for("disband").unwrap(), "pim/croom_disband");
        assert!(method_for("ctask_send").is_err());
        assert!(method_for("../tools/dispatch").is_err());
    }
}
