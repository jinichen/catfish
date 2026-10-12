//! 销售跟进 (10/12, docs/SALES-FOLLOWUP-PLAN.md): 早安「客户跟进」+ 设置「销售模板」。
//!
//! 数据和规则都在 tool-bridge (customers.py / customer_radar.py), 这里只转 RPC:
//! - 雷达 / 模板走 `pim/*` (不进模型工具清单)
//! - 卡片上的「已兑现」走 `tools/dispatch` 调 catfish_customer action=done, 写回客户页

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::tool_bridge_rpc;

const RPC_TIMEOUT: Duration = Duration::from_secs(30);

#[tauri::command]
pub async fn sales_customer_radar() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/customer_radar", json!({}), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_template_get() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_template_get", json!({}), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_template_set(template: Value) -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_template_set", json!({ "template": template }), RPC_TIMEOUT).await
}

/// 把我方承诺标成已兑现 (客户名 + 承诺原文片段)。
#[tauri::command]
pub async fn sales_promise_done(customer: String, text: String) -> Result<Value, String> {
    let args = json!({ "action": "done", "customer": customer, "text": text });
    let v = tool_bridge_rpc::call_with_timeout("tools/dispatch", json!({ "name": "catfish_customer", "args": args }), RPC_TIMEOUT)
        .await?;
    unwrap_dispatch(v)
}

/// tools/dispatch 外层 {ok, result}, 内层是工具自己的 {ok, error?}; 任一层失败都转成 Err。
fn unwrap_dispatch(v: Value) -> Result<Value, String> {
    if v.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(v.get("error").and_then(Value::as_str).unwrap_or("tool-bridge 调用失败").to_string());
    }
    let r = v.get("result").cloned().unwrap_or(Value::Null);
    if r.get("ok").and_then(Value::as_bool) == Some(false) {
        return Err(r.get("error").and_then(Value::as_str).unwrap_or("失败").to_string());
    }
    Ok(r)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dispatch_errors_surface_from_either_layer() {
        assert_eq!(unwrap_dispatch(json!({"ok": false, "error": "bridge down"})).unwrap_err(), "bridge down");
        assert_eq!(unwrap_dispatch(json!({"ok": true, "result": {"ok": false, "error": "没找到"}})).unwrap_err(), "没找到");
        assert_eq!(unwrap_dispatch(json!({"ok": true, "result": {"ok": true, "done": "x"}})).unwrap()["done"], "x");
    }
}
