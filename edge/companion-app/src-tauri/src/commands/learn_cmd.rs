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

// ── 第 2 步 (11/18): 练习 / 复习卡 / 复习 ──────────────────────────

#[tauri::command]
pub async fn learn_practice(id: String, next: Option<bool>) -> Result<Value, String> {
    let method = if next.unwrap_or(false) { "learn/next" } else { "learn/practice" };
    call(method, json!({ "id": id }), true).await
}

#[tauri::command]
pub async fn learn_attempt(id: String, text: String, thinking: Option<String>) -> Result<Value, String> {
    call("learn/attempt", json!({ "id": id, "text": text, "thinking": thinking.unwrap_or_default() }), true).await
}

/// action: hint (要提示) / give_up (不做了) / teach (回去接着学) / cards (做成复习卡) / ingest (沉淀要点)
#[tauri::command]
pub async fn learn_action(id: String, action: String) -> Result<Value, String> {
    let method = match action.as_str() {
        "hint" => "learn/hint",
        "give_up" => "learn/give_up",
        "teach" => "learn/teach",
        "cards" => "learn/cards",
        "ingest" => "learn/ingest",
        _ => return Err(format!("不认识的操作: {action}")),
    };
    call(method, json!({ "id": id }), action != "give_up").await
}

#[tauri::command]
pub async fn learn_review_next(n: Option<u32>) -> Result<Value, String> {
    call("learn/review_next", json!({ "n": n.unwrap_or(20) }), false).await
}

#[tauri::command]
pub async fn learn_review_answer(qid: i64, user_answer: Option<String>, rating: Option<String>) -> Result<Value, String> {
    call(
        "learn/review_answer",
        json!({ "qid": qid, "user_answer": user_answer.unwrap_or_default(), "rating": rating.unwrap_or_default() }),
        false,
    )
    .await
}

/// 会议纪要「记住这几条」→ 复习卡 (tool-bridge meeting/minutes_cards, 调一次大模型, 同步等结果)。
#[tauri::command]
pub async fn meeting_minutes_cards(id: String) -> Result<Value, String> {
    let home = store::home()?;
    let token = oauth::ensure_fresh_access_token()
        .await
        .ok_or("没登录 (拿不到访问令牌), 登录后再做复习卡")?;
    let params = json!({
        "meeting_id": id,
        "catfish_home": home.join(".catfish").display().to_string(),
        "auth_token": token,
        "gateway_url": endpoints::endpoints().gateway_base(),
    });
    tool_bridge_rpc::call_with_timeout("meeting/minutes_cards", params, CARDS_TIMEOUT)
        .await
        .map_err(|e| e.split_once("]: ").map(|(_, m)| m.to_string()).unwrap_or(e))
}

/// 做卡最多重交 3 次, 每次几十秒
const CARDS_TIMEOUT: Duration = Duration::from_secs(600);

// ── 第 3 步 (11/18): 我学过的要点 (要点 + 推理链) ──────────────────────

/// action: view / import ({stage_id, name}: 导入一份资料, 后台提炼) / delete ({source})
#[tauri::command]
pub async fn learn_graph(action: String, params: Option<Value>) -> Result<Value, String> {
    let method = match action.as_str() {
        "view" => "learn/graph_view",
        "import" => "learn/graph_import",
        "delete" => "learn/graph_delete",
        _ => return Err(format!("不认识的操作: {action}")),
    };
    call(method, params.unwrap_or_else(|| json!({})), action == "import").await
}

// ── 第 4 步 (11/18): 答辩陪练 ──────────────────────────────────

/// action: start (开始 / 接着) / answer ({text, secs}) / skip / next
#[tauri::command]
pub async fn learn_drill(id: String, action: String, text: Option<String>, secs: Option<f64>) -> Result<Value, String> {
    let method = match action.as_str() {
        "start" => "learn/drill",
        "answer" => "learn/drill_answer",
        "skip" => "learn/drill_skip",
        "next" => "learn/drill_next",
        _ => return Err(format!("不认识的操作: {action}")),
    };
    call(method, json!({ "id": id, "text": text.unwrap_or_default(), "secs": secs }), action == "start" || action == "answer").await
}

// ── 第 5 步 (11/18): 外语 ─────────────────────────────────────

/// action: start ({activity, scene}) / say ({text, secs}) / shadow ({idx, heard, secs}) / end
#[tauri::command]
pub async fn learn_lang(id: String, action: String, params: Option<Value>) -> Result<Value, String> {
    let method = match action.as_str() {
        "start" => "learn/lang_start",
        "say" => "learn/lang_say",
        "shadow" => "learn/lang_shadow",
        "end" => "learn/lang_end",
        _ => return Err(format!("不认识的操作: {action}")),
    };
    let mut p = params.unwrap_or_else(|| json!({}));
    if let Some(obj) = p.as_object_mut() {
        obj.insert("id".into(), json!(id));
    }
    call(method, p, action != "shadow").await
}

/// 新建外语学习: 跟 learn_create 一样, 多一个目标语言 (en / ja / ko …)
#[tauri::command]
pub async fn learn_create_lang(goal: String, lang: String) -> Result<Value, String> {
    call("learn/create", json!({ "goal": goal, "mode": "language", "lang": lang }), true).await
}
