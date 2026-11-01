//! 手机能让电脑做的事 —— 白名单 (docs/MOBILE-CLIENT-DESIGN.md §5)。
//!
//!   chat      发消息, 流式回复 (跟 Companion 自己对话同一条路: Hermes /v1/chat/completions)
//!   stop      停掉正在回的那条 (target = 那条的 rid)
//!   approve   批准 / 拒绝 —— **只能单次** (once / deny), 「始终允许」只能在电脑上点
//!   sessions  最近的对话列表 (只读)
//!   history   一段对话的记录 (只读)
//!   briefing  早安简报 (只读, 读 Companion 已算好的那份, 不另调模型)
//!   ping
//!
//! 别的一律回「手机上不能做这个」: 读写文件、改配置、装技能 / MCP、看外发记录、换机迁移等。
//!
//! 每条回给手机的内容在「数据外发记录」里记一笔: 目的地「手机 (经中央中转, 已加密)」, 只记大小不存内容。

use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

/// 回给手机的出口 (由 mod.rs 接上加密和中转站)
pub type Outbox = Arc<dyn Fn(Value) -> usize + Send + Sync>;

pub const EGRESS_PURPOSE: &str = "手机 (经中央中转, 已加密)";
const SOURCE_QUERY: &str = "?catfish_source=companion-chat";
const HISTORY_LIMIT: usize = 60;
const SESSIONS_LIMIT: usize = 20;
/// 流式回复攒多久 / 多少字发一次 (每帧都要加密、过中转站, 不逐字发)
const FLUSH_EVERY: Duration = Duration::from_millis(150);
const FLUSH_CHARS: usize = 400;

/// (request_id, execution_generation): 批准回执要原样带上 (hermes v0.22.1 严格校验)
type ApprovalKey = (Option<String>, Option<i64>);

/// 等手机批准的那几步: run_id → ApprovalKey
fn approvals() -> &'static Mutex<HashMap<String, ApprovalKey>> {
    static M: OnceLock<Mutex<HashMap<String, ApprovalKey>>> = OnceLock::new();
    M.get_or_init(Default::default)
}

/// 正在跑的对话 (手机, rid) → 任务, 「停止」用
fn running() -> &'static Mutex<HashMap<(String, String), tokio::task::AbortHandle>> {
    static M: OnceLock<Mutex<HashMap<(String, String), tokio::task::AbortHandle>>> = OnceLock::new();
    M.get_or_init(Default::default)
}

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

/// 只认这两个 —— 手机更容易被别人拿到, 「本次会话都允许 / 始终允许」只能在电脑上点
pub fn phone_choice_ok(choice: &str) -> bool {
    matches!(choice, "once" | "deny")
}

pub fn record_egress(bytes: usize) {
    if bytes == 0 {
        return;
    }
    let host = crate::services::endpoints::endpoints().gateway_host.clone();
    let req = crate::commands::transparent_log::RecordReq {
        method: "WS".into(),
        url: "relay://phone".into(),
        request_body: None,
        status: Some(200),
        response_bytes: Some(0),
        response_summary: None,
        error: None,
        category: Some("content".into()),
        origin: Some("companion-bg".into()),
        dest_kind: Some("central".into()),
        dest_host: Some(host),
        purpose: Some(EGRESS_PURPOSE.into()),
        request_bytes: Some(bytes as u64),
    };
    std::thread::spawn(move || {
        if let Err(e) = crate::commands::transparent_log::record_blocking(req) {
            log::debug!("[mobile] 外发记录失败: {e}");
        }
    });
}

pub async fn dispatch(phone: String, m: Value, out: Outbox) {
    let op = s(&m, "op");
    let rid = s(&m, "rid");
    let reply = |v: Value| out(v);
    match op.as_str() {
        "ping" => {
            reply(json!({"op": "pong", "rid": rid}));
        }
        "chat" => {
            let text = s(&m, "text");
            if text.trim().is_empty() {
                reply(json!({"op": "error", "rid": rid, "message": "消息是空的"}));
                return;
            }
            let sid = m.get("sid").and_then(Value::as_str).map(String::from);
            let key = (phone.clone(), rid.clone());
            let key2 = key.clone();
            let out2 = out.clone();
            let rid2 = rid.clone();
            let task = tokio::spawn(async move {
                let sent = chat(&rid2, sid, &text, &out2).await;
                record_egress(sent);
                running().lock().unwrap().remove(&key2);
            });
            running().lock().unwrap().insert(key, task.abort_handle());
        }
        "stop" => {
            // target = 要停的那条对话的 rid (这条 stop 请求自己另有 rid)
            let target = s(&m, "target");
            if let Some(h) = running().lock().unwrap().remove(&(phone, target.clone())) {
                h.abort();
                reply(json!({"op": "done", "rid": target, "stopped": true}));
            }
        }
        "approve" => {
            let choice = s(&m, "choice");
            let run_id = s(&m, "runId");
            let n = match approve(&run_id, &choice).await {
                Ok(()) => reply(json!({"op": "result", "rid": rid, "data": {"ok": true}})),
                Err(e) => reply(json!({"op": "error", "rid": rid, "message": e})),
            };
            record_egress(n);
        }
        "sessions" => {
            let n = match recent_sessions().await {
                Ok(list) => reply(json!({"op": "result", "rid": rid, "data": list})),
                Err(e) => reply(json!({"op": "error", "rid": rid, "message": e})),
            };
            record_egress(n);
        }
        "history" => {
            let n = match history(&s(&m, "sid")).await {
                Ok(list) => reply(json!({"op": "result", "rid": rid, "data": list})),
                Err(e) => reply(json!({"op": "error", "rid": rid, "message": e})),
            };
            record_egress(n);
        }
        "briefing" => {
            let n = match briefing().await {
                Ok(b) => reply(json!({"op": "result", "rid": rid, "data": b})),
                Err(e) => reply(json!({"op": "error", "rid": rid, "message": e})),
            };
            record_egress(n);
        }
        _ => {
            reply(json!({"op": "error", "rid": rid, "message": "手机上不能做这个, 请在电脑上操作"}));
        }
    }
}

fn hermes() -> Result<(String, String), String> {
    let cfg = crate::services::hermes_api_config::hermes_api_config();
    let key = cfg.key.as_ref().ok_or("电脑上的小鲶还没准备好 (本机服务没有密钥), 请在电脑上打开 Companion 看看")?;
    Ok((cfg.url.trim_end_matches('/').to_string(), format!("Bearer {key}")))
}

fn http() -> reqwest::Client {
    // 本机 127.0.0.1, 不走系统代理
    reqwest::Client::builder().no_proxy().build().unwrap_or_default()
}

/// 跟 Companion 对话同样的消息格式: 历史 (只取有字的 user / assistant) + 这次的话
fn wire_history(detail: &crate::commands::sessions::SessionDetail) -> Vec<Value> {
    detail
        .messages
        .iter()
        .filter(|m| (m.role == "user" || m.role == "assistant") && !m.content.trim().is_empty() && m.display_kind.is_none())
        .map(|m| json!({"role": m.role, "content": m.content}))
        .collect()
}

async fn chat(rid: &str, sid: Option<String>, text: &str, out: &Outbox) -> usize {
    let mut sent = 0usize;
    match chat_inner(rid, sid, text, out, &mut sent).await {
        Ok(sid) => sent += out(json!({"op": "done", "rid": rid, "sid": sid})),
        Err(e) => sent += out(json!({"op": "error", "rid": rid, "message": e})),
    }
    sent
}

async fn chat_inner(rid: &str, sid: Option<String>, text: &str, out: &Outbox, sent: &mut usize) -> Result<String, String> {
    use crate::commands::session_write::{session_create, session_message_append, MessageAppendInput, SessionCreateInput};

    let (base, auth) = hermes()?;
    let model = crate::commands::picker_state::picker_state_get()
        .await
        .ok()
        .flatten()
        .map(|p| p.chat_model)
        .filter(|m| !m.is_empty())
        .unwrap_or_else(|| "hermes-agent".into());

    let (sid, mut messages) = match sid.filter(|s| !s.is_empty()) {
        Some(sid) => {
            let detail = crate::commands::sessions::sessions_get(sid.clone()).await?;
            let msgs = wire_history(&detail);
            (sid, msgs)
        }
        None => {
            let created = session_create(SessionCreateInput { model: model.clone(), title: None, system_prompt: None }).await?;
            (created.id, vec![])
        }
    };
    messages.push(json!({"role": "user", "content": text}));
    let append = |role: &str, content: String| MessageAppendInput {
        session_id: sid.clone(),
        role: role.into(),
        content,
        tool_calls: None,
        tool_call_id: None,
        tool_name: None,
        token_count: None,
        finish_reason: None,
    };
    if let Err(e) = session_message_append(append("user", text.to_string())).await {
        log::warn!("[mobile] 写对话记录失败: {e}");
    }

    let mut req = http()
        .post(format!("{base}/v1/chat/completions{SOURCE_QUERY}"))
        .header("Authorization", auth)
        .header("Content-Type", "application/json")
        .header("X-Hermes-Session-Id", &sid)
        .json(&json!({"model": model, "messages": messages, "stream": true}));
    if let Some(email) = crate::services::oauth::try_load_session().map(|s| s.email).filter(|e| !e.is_empty()) {
        req = req.header("X-Catfish-User", email);
    }
    let mut resp = req.send().await.map_err(|e| format!("连不上电脑上的小鲶: {e}"))?;
    if !resp.status().is_success() {
        let code = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        return Err(friendly_http_error(code, &body));
    }

    let mut buf = String::new();
    let mut pending = String::new();
    let mut full = String::new();
    let mut last_flush = Instant::now();
    let mut thinking = false;
    'outer: while let Some(chunk) = resp.chunk().await.map_err(|e| format!("回复中断了: {e}"))? {
        buf.push_str(&String::from_utf8_lossy(&chunk));
        while let Some(i) = buf.find("\n\n") {
            let block = buf[..i].to_string();
            buf.drain(..i + 2);
            let (event, data) = parse_sse(&block);
            let Some(data) = data else { continue };
            match event.as_deref() {
                Some("approval.request") => {
                    if let Ok(ev) = serde_json::from_str::<Value>(&data) {
                        let run_id = s(&ev, "run_id");
                        if !run_id.is_empty() {
                            let req_id = ev.get("request_id").and_then(Value::as_str).map(String::from);
                            let gen = ev.get("execution_generation").and_then(Value::as_i64);
                            approvals().lock().unwrap().insert(run_id.clone(), (req_id, gen));
                            *sent += out(json!({
                                "op": "approval", "rid": rid, "runId": run_id,
                                "command": s(&ev, "command"), "description": s(&ev, "description"),
                            }));
                        }
                    }
                }
                // 11/01: 小鲶在用工具时手机上要看得到 (不然批准之后几分钟没动静, 像卡住了)
                Some("hermes.tool.progress") => {
                    if let Ok(ev) = serde_json::from_str::<Value>(&data) {
                        if let Some(t) = tool_progress_text(&ev) {
                            *sent += out(json!({"op": "status", "rid": rid, "text": t}));
                        }
                    }
                }
                Some("hermes.status") => {
                    if let Ok(ev) = serde_json::from_str::<Value>(&data) {
                        let t = s(&ev, "text");
                        if !t.trim().is_empty() {
                            *sent += out(json!({"op": "status", "rid": rid, "text": t}));
                        }
                    }
                }
                Some(_) => {}
                None => {
                    if data == "[DONE]" {
                        break 'outer;
                    }
                    let Ok(v) = serde_json::from_str::<Value>(&data) else { continue };
                    if let Some(msg) = stream_error(&v) {
                        return Err(msg);
                    }
                    let delta = &v["choices"][0]["delta"];
                    if let Some(c) = delta.get("content").and_then(Value::as_str) {
                        pending.push_str(c);
                        full.push_str(c);
                    } else if delta.get("reasoning_content").and_then(Value::as_str).is_some() && !thinking {
                        thinking = true;
                        *sent += out(json!({"op": "status", "rid": rid, "text": "🤔 正在思考…"}));
                    }
                }
            }
            if !pending.is_empty() && (pending.chars().count() >= FLUSH_CHARS || last_flush.elapsed() >= FLUSH_EVERY) {
                *sent += out(json!({"op": "delta", "rid": rid, "text": std::mem::take(&mut pending)}));
                last_flush = Instant::now();
            }
        }
    }
    if !pending.is_empty() {
        *sent += out(json!({"op": "delta", "rid": rid, "text": pending}));
    }
    if !full.trim().is_empty() {
        if let Err(e) = session_message_append(append("assistant", full)).await {
            log::warn!("[mobile] 写对话记录失败: {e}");
        }
    }
    Ok(sid)
}

/// 一段 SSE: (event 名, 最后一行 data) —— 跟 chat.ts 同样的读法
pub fn parse_sse(block: &str) -> (Option<String>, Option<String>) {
    let mut event = None;
    let mut data = None;
    for line in block.lines() {
        if let Some(e) = line.strip_prefix("event:") {
            event = Some(e.trim().to_string());
        } else if let Some(d) = line.strip_prefix("data:") {
            data = Some(d.trim().to_string());
        }
    }
    (event, data)
}

/// 工具进度 → 一行字。running 显示「emoji 在做什么」(截 60 字), completed 显示「接着想…」。
/// tool = catfish-lifecycle 是插件桥过来的压缩上下文等提示, 用它的 label / preview。
pub fn tool_progress_text(ev: &Value) -> Option<String> {
    let tool = s(ev, "tool");
    let label = ev.get("label").or(ev.get("preview")).and_then(Value::as_str).unwrap_or("").trim().to_string();
    if tool == "catfish-lifecycle" {
        return (!label.is_empty()).then_some(label);
    }
    match s(ev, "status").as_str() {
        "running" => {
            let what = if label.is_empty() { tool } else { label };
            let short: String = what.chars().take(60).collect();
            let emoji = s(ev, "emoji");
            Some(if emoji.is_empty() { format!("正在用工具: {short}") } else { format!("{emoji} {short}") })
        }
        "completed" => Some("接着想…".into()),
        _ => None,
    }
}

/// 两种报错形状都认: {"error": "文案"} / {"error": {"message": "文案"}} (同 chat.ts chatStreamError)
fn stream_error(v: &Value) -> Option<String> {
    match v.get("error")? {
        Value::String(s) => Some(s.clone()),
        Value::Object(o) => Some(o.get("message").and_then(Value::as_str).unwrap_or("小鲶回复出错了").to_string()),
        _ => None,
    }
}

fn friendly_http_error(code: u16, body: &str) -> String {
    if code == 429 {
        if let Ok(v) = serde_json::from_str::<Value>(body) {
            if let Some(m) = v["detail"]["message"].as_str().or(v["detail"].as_str()) {
                return m.to_string();
            }
        }
        return "今天的用量满了, 请在电脑上换个模型再试".into();
    }
    format!("电脑上的小鲶回复失败 (HTTP {code})")
}

async fn approve(run_id: &str, choice: &str) -> Result<(), String> {
    if !phone_choice_ok(choice) {
        return Err("手机上只能「允许这一次」或「拒绝」; 「始终允许」请在电脑上点".into());
    }
    if run_id.is_empty() {
        return Err("缺少要批准的那一步".into());
    }
    let (req_id, gen) = approvals().lock().unwrap().remove(run_id).unwrap_or((None, None));
    let body = match (req_id, gen) {
        (Some(r), Some(g)) => json!({"choice": choice, "request_id": r, "execution_generation": g}),
        (Some(r), None) => json!({"choice": choice, "request_id": r}),
        _ => json!({"choice": choice}),
    };
    let (base, auth) = hermes()?;
    let resp = http()
        .post(format!("{base}/v1/runs/{}/approval", urlencoding(run_id)))
        .header("Authorization", auth)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("连不上电脑上的小鲶: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("批准没送到 (HTTP {}), 可能已经超时", resp.status().as_u16()));
    }
    Ok(())
}

fn urlencoding(s: &str) -> String {
    url::form_urlencoded::byte_serialize(s.as_bytes()).collect()
}

async fn recent_sessions() -> Result<Value, String> {
    let list = crate::commands::sessions::sessions_list().await?;
    let items: Vec<Value> = list
        .into_iter()
        .take(SESSIONS_LIMIT)
        .map(|m| {
            let title = m.title.clone().filter(|t| !t.trim().is_empty())
                .or(m.first_user_message.clone())
                .unwrap_or_else(|| "(没有标题)".into());
            json!({"id": m.id, "title": title, "startedAt": m.started_at, "messageCount": m.message_count})
        })
        .collect();
    Ok(Value::Array(items))
}

async fn history(sid: &str) -> Result<Value, String> {
    if sid.is_empty() {
        return Err("缺少对话".into());
    }
    let detail = crate::commands::sessions::sessions_get(sid.to_string()).await?;
    let msgs = wire_history(&detail);
    let skip = msgs.len().saturating_sub(HISTORY_LIMIT);
    Ok(Value::Array(msgs.into_iter().skip(skip).collect()))
}

/// 早安简报: 只给标题 / 紧急程度 / 一句话理由, 和「已默认处理」的条数 —— 手机上看个大概, 细节在电脑上
pub fn briefing_view(cache: Option<&crate::commands::advisor_cache::AdvisorCache>) -> Value {
    let Some(c) = cache else {
        return json!({"computedAt": null, "mainTasks": [], "handled": []});
    };
    let tasks: Vec<Value> = c.result["mainTasks"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|t| json!({"title": t["title"], "urgency": t["urgency"], "reason": t["reason"]}))
                .collect()
        })
        .unwrap_or_default();
    let handled: Vec<Value> = c.result["handledSilently"]
        .as_array()
        .map(|a| a.iter().map(|h| json!({"category": h["category"], "count": h["count"]})).collect())
        .unwrap_or_default();
    json!({"computedAt": c.computed_at, "mainTasks": tasks, "handled": handled})
}

async fn briefing() -> Result<Value, String> {
    let cache = crate::commands::advisor_cache::advisor_cache_get().await?;
    Ok(briefing_view(cache.as_ref()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 手机只能单次批准或拒绝() {
        assert!(phone_choice_ok("once"));
        assert!(phone_choice_ok("deny"));
        assert!(!phone_choice_ok("always"));
        assert!(!phone_choice_ok("session"));
        assert!(!phone_choice_ok(""));
    }

    #[tokio::test]
    async fn 始终允许在手机上被拒() {
        let err = approve("run-1", "always").await.unwrap_err();
        assert!(err.contains("电脑上点"));
    }

    #[tokio::test]
    async fn 不在白名单的请求回错() {
        let got: Arc<Mutex<Vec<Value>>> = Arc::default();
        let g = got.clone();
        let out: Outbox = Arc::new(move |v| {
            g.lock().unwrap().push(v);
            0
        });
        dispatch("p".into(), json!({"op": "read_file", "rid": "1", "path": "/etc/passwd"}), out).await;
        let v = got.lock().unwrap();
        assert_eq!(v[0]["op"], "error");
        assert_eq!(v[0]["rid"], "1");
    }

    #[test]
    fn 读sse() {
        assert_eq!(
            parse_sse("event: approval.request\ndata: {\"a\":1}"),
            (Some("approval.request".into()), Some("{\"a\":1}".into()))
        );
        assert_eq!(parse_sse("data: [DONE]"), (None, Some("[DONE]".into())));
    }

    #[test]
    fn 工具进度转成一行字() {
        let run = json!({"tool": "web_search", "emoji": "🔍", "label": "搜索 英伟达 股价", "status": "running"});
        assert_eq!(tool_progress_text(&run).unwrap(), "🔍 搜索 英伟达 股价");
        assert_eq!(tool_progress_text(&json!({"tool": "web_search", "status": "completed"})).unwrap(), "接着想…");
        let life = json!({"tool": "catfish-lifecycle", "label": "压缩上下文中", "status": "running"});
        assert_eq!(tool_progress_text(&life).unwrap(), "压缩上下文中");
        assert!(tool_progress_text(&json!({"tool": "x", "status": "approval_pending"})).is_none());
    }

    #[test]
    fn 报错两种形状都认() {
        assert_eq!(stream_error(&json!({"error": "额度满了"})).unwrap(), "额度满了");
        assert_eq!(stream_error(&json!({"error": {"message": "超时"}})).unwrap(), "超时");
        assert!(stream_error(&json!({"choices": []})).is_none());
    }

    #[test]
    fn 早安简报只给大概() {
        let c = crate::commands::advisor_cache::AdvisorCache {
            computed_at: "2026-11-01T08:00:00+08:00".into(),
            result: json!({
                "mainTasks": [{"title": "回复王总", "urgency": "high", "reason": "今天截止",
                               "options": [{"x": 1}], "contextRefs": ["邮件原文…"]}],
                "handledSilently": [{"type": "email_archive", "count": 3, "category": "通知类邮件"}],
            }),
            ..serde_json::from_value(json!({"computedAt": "", "result": {}})).unwrap()
        };
        let v = briefing_view(Some(&c));
        assert_eq!(v["mainTasks"][0], json!({"title": "回复王总", "urgency": "high", "reason": "今天截止"}));
        assert_eq!(v["handled"][0], json!({"category": "通知类邮件", "count": 3}));
        assert!(briefing_view(None)["mainTasks"].as_array().unwrap().is_empty());
    }
}
