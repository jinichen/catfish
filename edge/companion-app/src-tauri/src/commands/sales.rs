//! 销售跟进 (10/12, docs/SALES-FOLLOWUP-PLAN.md): 早安「客户跟进」+ 设置「销售模板」。
//!
//! 数据和规则都在 tool-bridge (customers.py / customer_radar.py), 这里只转 RPC:
//! - 雷达 / 模板走 `pim/*` (不进模型工具清单)
//! - 卡片上的「已兑现」走 `tools/dispatch` 调 catfish_customer action=done, 写回客户页
//! - 阶段 3 (§11): 「发布给主管」—— 结构化摘要发到本部门 wiki-hub (`pim/sales_publish*`), 员工点了才发
//! - 阶段 2 (§10): 设置里导入 Excel 价格表 / 公司报价单模板、填报价方信息 (`pim/sales_pricebook_*` / `pim/sales_quote_*`)

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

/// 原生文件选择器, 只列 .xlsx (价格表 / 报价单模板)。取消返回 None。
#[tauri::command]
pub fn sales_pick_xlsx(title: String) -> Option<String> {
    rfd::FileDialog::new()
        .set_title(&title)
        .add_filter("Excel", &["xlsx", "xlsm"])
        .pick_file()
        .map(|p| p.to_string_lossy().into_owned())
}

#[tauri::command]
pub async fn sales_pricebook_import(path: String) -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_pricebook_import", json!({ "path": path }), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_pricebook_get() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_pricebook_get", json!({}), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_quote_settings_get() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_quote_settings_get", json!({}), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_quote_settings_set(settings: Value) -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_quote_settings_set", json!({ "settings": settings }), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_quote_template_import(path: String) -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_quote_template_import", json!({ "path": path }), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_quote_template_clear() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_quote_template_clear", json!({}), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_publish_preview() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_publish_preview", json!({}), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_publish() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_publish", json!({}), RPC_TIMEOUT).await
}

#[tauri::command]
pub async fn sales_publish_withdraw() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/sales_publish_withdraw", json!({}), RPC_TIMEOUT).await
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
