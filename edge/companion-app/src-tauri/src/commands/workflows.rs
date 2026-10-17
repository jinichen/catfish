//! 工作流 skill (10/17, docs/WORKFLOW-SKILL-PLAN.md): 技能页的编辑 / 试跑 / 运行记录, 以及全局的「对外步骤确认」。
//!
//! 数据和规则都在 tool-bridge (skill_workflow.py / workflow_tool.py), 这里只转 RPC。
//! 只放行 `pim/workflow_*` 这一组。确认对外步骤 (`confirm`) 和「不用每次确认」开关 (`set_auto_confirm`)
//! 只有界面走这里能做 —— 模型的工具里没有这两个动作。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::tool_bridge_rpc;

/// 跑工作流 / 确认后接着跑可能要几分钟 (逐步调 skill、小鲶判断)。
const RPC_TIMEOUT: Duration = Duration::from_secs(600);

const ALLOWED: &[&str] = &[
    "catalog", "list", "get", "update", "draft_get", "draft_save", "draft_delete", "trial", "save", "run", "runs",
    "run_get", "pending", "confirm", "resume", "set_auto_confirm",
];

fn method_for(action: &str) -> Result<String, String> {
    if ALLOWED.contains(&action) {
        Ok(format!("pim/workflow_{action}"))
    } else {
        Err(format!("不支持的工作流动作: {action}"))
    }
}

#[tauri::command]
pub async fn workflow_call(action: String, params: Option<Value>) -> Result<Value, String> {
    let method = method_for(&action)?;
    tool_bridge_rpc::call_with_timeout(&method, params.unwrap_or_else(|| json!({})), RPC_TIMEOUT).await
}

#[cfg(test)]
mod tests {
    use super::method_for;

    #[test]
    fn 只放行工作流这一组() {
        assert_eq!(method_for("confirm").unwrap(), "pim/workflow_confirm");
        assert!(method_for("form_send").is_err());
        assert!(method_for("../tools/dispatch").is_err());
    }
}
