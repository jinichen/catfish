//! 分享治理 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §2.3): 审批人「装到我这里试用」—— 只转 tool-bridge 的 `pim/trial_*`。
//!
//! 验签、写 trial.json、回传试用结果、刷新时自动卸掉不在待审的, 都在 tool-bridge (skill_trial.py)。这里只做两件
//! tool-bridge 做不了的事, 跟正式安装 / 卸载走同一条路:
//!   · 专家包试用装好 → 落 hermes profile (`expert_local_apply`, 跟 expert_pack_install 一样)
//!   · 专家包试用被自动卸掉 → 把它的 profile 也删了 (`expert_local_remove` 的 profile 那一半; 定义已经被 tool-bridge 删了)

use std::time::Duration;

use serde_json::{json, Value};

use super::experts_local::{expert_local_apply, expert_profile_remove};
use crate::services::tool_bridge_rpc;

/// 试用安装要下载 + dry-run; 同步要逐个问中央状态
const RPC_TIMEOUT: Duration = Duration::from_secs(180);

const ALLOWED: &[&str] = &[
    "skill_pending", "skill_install", "skill_list", "skill_uninstall", "skill_result", "skill_sync",
    "expert_pending", "expert_install", "expert_result", "expert_sync",
    "runs",
];

#[tauri::command]
pub async fn share_trial_call(action: String, params: Option<Value>) -> Result<Value, String> {
    if !ALLOWED.contains(&action.as_str()) {
        return Err(format!("不支持的试用动作: {action}"));
    }
    let params = params.unwrap_or_else(|| json!({}));
    let r = tool_bridge_rpc::call_with_timeout(&format!("pim/trial_{action}"), params, RPC_TIMEOUT).await?;
    match action.as_str() {
        "expert_install" if r.get("ok").and_then(Value::as_bool) == Some(true) => {
            let id = r.get("id").and_then(Value::as_str).ok_or("安装结果缺 id")?.to_string();
            if let Err(e) = expert_local_apply(id.clone()).await {
                return Ok(json!({ "ok": false, "installed": true, "id": id,
                                   "error": format!("试用包装好了, 但落到 profile 失败: {e} (在专家列表里点「卸掉试用」后重试)") }));
            }
            Ok(r)
        }
        "expert_sync" => {
            // 定义 + 包已经被 tool-bridge 卸了, 这里补删 profile; 删不掉只记日志, 不挡界面
            if let Some(removed) = r.get("removed").and_then(Value::as_array) {
                for item in removed {
                    if let Some(id) = item.get("id").and_then(Value::as_str) {
                        if let Err(e) = expert_profile_remove(id.to_string()).await {
                            log::warn!("[trial] 卸试用专家 {id} 的 profile 失败: {e}");
                        }
                    }
                }
            }
            Ok(r)
        }
        _ => Ok(r),
    }
}
