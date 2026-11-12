//! 知识库里的「人 → 邮箱」(11/12, tool-bridge wiki_people.py): 会议纪要发邮件时按说话人名字填收件人,
//! 收件人 / 抄送框打字给候选。只读本机知识库, 不出本机。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::tool_bridge_rpc;

/// `names` 给了就顺带按名字查 (matches: {名字: [邮箱..]})。
#[tauri::command]
pub async fn wiki_people_emails(names: Option<Vec<String>>) -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("wiki/people_emails", json!({ "names": names.unwrap_or_default() }), Duration::from_secs(20))
        .await
}
