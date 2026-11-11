//! 11/12 文书风格 = 员工自己指定的范文 + 小鲶总结的写法特点 (tool-bridge style_samples.py)。
//!
//! 老的「自动学」(style_fingerprint.py) 取的是搜索索引里最近 500 篇, 实盘 395 篇是小鲶自己的产出 ——
//! 学的不是员工。现在范文由员工在「你的文书风格」页挑, 文件走群聊同一个暂存口 (colleague_room_call stage) 传给 tool-bridge。
//! 总结写法特点要调大模型 (会把范文发出去, 页面上写明), 跟群聊的小鲶一样在这里注入员工自己的令牌。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::{endpoints, oauth, tool_bridge_rpc};

const RPC_TIMEOUT: Duration = Duration::from_secs(90);
const SUMMARIZE_TIMEOUT: Duration = Duration::from_secs(180);
const ALLOWED: &[&str] = &["state", "add", "remove", "set_traits", "clear"];

fn method_for(action: &str) -> Result<String, String> {
    if ALLOWED.contains(&action) {
        Ok(format!("style/{action}"))
    } else {
        Err(format!("不支持的文书风格动作: {action}"))
    }
}

#[tauri::command]
pub async fn style_samples_call(action: String, params: Option<Value>) -> Result<Value, String> {
    let method = method_for(&action)?;
    tool_bridge_rpc::call_with_timeout(&method, params.unwrap_or_else(|| json!({})), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn style_samples_summarize() -> Result<Value, String> {
    let token = oauth::ensure_fresh_access_token()
        .await
        .ok_or("没登录 (拿不到访问令牌), 登录后再让小鲶总结")?;
    let params = json!({ "auth_token": token, "gateway_url": endpoints::endpoints().gateway_base() });
    tool_bridge_rpc::call_with_timeout("style/summarize", params, SUMMARIZE_TIMEOUT).await
}

#[cfg(test)]
mod tests {
    use super::method_for;

    #[test]
    fn 只放行范文这一组_总结不走这个口() {
        assert_eq!(method_for("add").unwrap(), "style/add");
        assert!(method_for("summarize").is_err());
        assert!(method_for("../tools/dispatch").is_err());
    }
}
