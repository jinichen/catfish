//! 派任务给多位同事 (11/4, docs/COLLEAGUE-TASK-PLAN.md): 「协同」页的派出 / 接下 / 交回 / 进度。
//!
//! 数据和规则都在 tool-bridge (colleague_tasks.py), 这里只转 RPC。
//! 只放行 `pim/ctask_*` 这一组, 别的方法一律拒 —— 前端不能借这个口子调任意 tool-bridge 方法。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::tool_bridge_rpc;

/// 派出 / 催 / 取消要逐个投邮筒 (最多 20 人), 比单次 RPC 久一些。
const RPC_TIMEOUT: Duration = Duration::from_secs(90);

const ALLOWED: &[&str] = &[
    "list", "get", "sync", "create", "add", "send", "remind", "cancel", "accept", "draft", "submit", "decline",
    "update", "period_submit", "quit", "redo",
    "pipeline_create", "pipeline_get", "pipeline_release", "pipeline_cancel",
];

fn method_for(action: &str) -> Result<String, String> {
    if ALLOWED.contains(&action) {
        Ok(format!("pim/ctask_{action}"))
    } else {
        Err(format!("不支持的任务动作: {action}"))
    }
}

#[tauri::command]
pub async fn colleague_task_call(action: String, params: Option<Value>) -> Result<Value, String> {
    let method = method_for(&action)?;
    tool_bridge_rpc::call_with_timeout(&method, params.unwrap_or_else(|| json!({})), RPC_TIMEOUT).await
}

#[cfg(test)]
mod tests {
    use super::method_for;

    #[test]
    fn 只放行任务这一组() {
        assert_eq!(method_for("submit").unwrap(), "pim/ctask_submit");
        assert!(method_for("form_submit").is_err());
        assert_eq!(method_for("pipeline_release").unwrap(), "pim/ctask_pipeline_release");
        assert!(method_for("../tools/dispatch").is_err());
    }
}
