//! 一起填表 (10/17, docs/FORM-COLLECT-PLAN.md): 「协同」页的发起 / 填写 / 进度。
//!
//! 数据和规则都在 tool-bridge (forms.py / forms_excel.py / forms_tool.py), 这里只转 RPC。
//! 只放行 `pim/form_*` 这一组, 别的方法一律拒 —— 前端不能借这个口子调任意 tool-bridge 方法。
//! 选 Excel 模板复用 `sales_pick_xlsx`。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::tool_bridge_rpc;

/// 发 / 催 / 结束要逐个投邮筒, 收件人多时比单次 RPC 久一些。
const RPC_TIMEOUT: Duration = Duration::from_secs(60);

const ALLOWED: &[&str] = &[
    "list", "get", "sync", "from_excel", "create", "update", "send", "remind", "close", "export", "draft",
    "submit", "decline", "directory",
];

fn method_for(action: &str) -> Result<String, String> {
    if ALLOWED.contains(&action) {
        Ok(format!("pim/form_{action}"))
    } else {
        Err(format!("不支持的填表动作: {action}"))
    }
}

#[tauri::command]
pub async fn form_call(action: String, params: Option<Value>) -> Result<Value, String> {
    let method = method_for(&action)?;
    tool_bridge_rpc::call_with_timeout(&method, params.unwrap_or_else(|| json!({})), RPC_TIMEOUT).await
}

#[cfg(test)]
mod tests {
    use super::method_for;

    #[test]
    fn 只放行填表这一组() {
        assert_eq!(method_for("submit").unwrap(), "pim/form_submit");
        assert!(method_for("sales_publish").is_err());
        assert!(method_for("../tools/dispatch").is_err());
    }
}
