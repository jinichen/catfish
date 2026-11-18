//! 小鲶学习 (11/18, docs/LEARNING-PLAN.md) —— 前端入口, 转给 tool-bridge `learn/*` (learning.py)。
//!
//! 跟让小鲶改纪要一样注入员工的登录令牌 / 网关地址 / ~/.catfish。tool-bridge 那边先存员工的输入、立即返回,
//! 模型在后台跑, 界面轮询 `learn_get`, 所以这里的超时不用给长。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::meeting_store as store;
use crate::services::{endpoints, oauth, tool_bridge_rpc};

const TIMEOUT: Duration = Duration::from_secs(30);

async fn call(method: &str, mut params: Value, need_token: bool) -> Result<Value, String> {
    let home = store::home()?;
    let token = if need_token {
        oauth::ensure_fresh_access_token()
            .await
            .ok_or("没登录 (拿不到访问令牌), 登录后再学")?
    } else {
        String::new()
    };
    if let Some(obj) = params.as_object_mut() {
        obj.insert("catfish_home".into(), json!(home.join(".catfish").display().to_string()));
        obj.insert("auth_token".into(), json!(token));
        obj.insert("gateway_url".into(), json!(endpoints::endpoints().gateway_base()));
    }
    tool_bridge_rpc::call_with_timeout(method, params, TIMEOUT)
        .await
        .map_err(|e| e.split_once("]: ").map(|(_, m)| m.to_string()).unwrap_or(e))
}

#[tauri::command]
pub async fn learn_list() -> Result<Value, String> {
    call("learn/list", json!({}), false).await
}

#[tauri::command]
pub async fn learn_get(id: String) -> Result<Value, String> {
    call("learn/get", json!({ "id": id }), false).await
}

#[tauri::command]
pub async fn learn_create(goal: String, mode: String) -> Result<Value, String> {
    call("learn/create", json!({ "goal": goal, "mode": mode }), true).await
}

#[tauri::command]
pub async fn learn_answer(id: String, answer: Value) -> Result<Value, String> {
    let mut params = json!({ "id": id });
    if let (Some(obj), Some(ans)) = (params.as_object_mut(), answer.as_object()) {
        for k in ["qid", "picked", "text", "dont_know", "confidence", "thinking"] {
            if let Some(v) = ans.get(k) {
                obj.insert(k.into(), v.clone());
            }
        }
    }
    call("learn/answer", params, true).await
}

#[tauri::command]
pub async fn learn_say(id: String, text: String, start: Option<bool>) -> Result<Value, String> {
    call("learn/say", json!({ "id": id, "text": text, "start": start.unwrap_or(false) }), true).await
}

#[tauri::command]
pub async fn learn_retry(id: String) -> Result<Value, String> {
    call("learn/retry", json!({ "id": id }), true).await
}

#[tauri::command]
pub async fn learn_delete(id: String) -> Result<Value, String> {
    call("learn/delete", json!({ "id": id }), false).await
}
