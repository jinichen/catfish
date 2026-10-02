//! Windows 上的提醒 / 日历 = Catfish 自己 (10/2 鸿波拍板)。
//!
//! 9/23 这里走的是 Outlook COM (system_outlook.rs)。但新版 Outlook 没有 COM ——
//! 邮件那边 9/26 已经定了"Windows 不再依赖客户端"(只用 IMAP), 提醒 / 日历跟着同一个
//! 原则: 存进本机任务库, 到点 tool-bridge 弹 Windows 通知, 日历事件另生成 .ics 给系统
//! 日历程序。实现只有一份, 在 tool-bridge 的 local_pim.py; 这里四个命令转发过去
//! (跟小鲶调工具落到同一个地方)。
//!
//! 早安页"今日 / 本周日程"读 Catfish 自己记的事件走 `pim/calendar_events` (不进模型
//! 工具清单)。装了经典版 Outlook 的机器, calendar.rs 还会把 Outlook 里的会议并进来 ——
//! 那是读员工已有的日程, 有就加、没有就跳过, 不依赖它。

#![cfg(windows)]

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::tool_bridge_rpc;

const RPC_TIMEOUT: Duration = Duration::from_secs(30);

fn str_field(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or_default().to_string()
}

fn str_list(v: &Value, key: &str) -> Vec<String> {
    v.get(key)
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

/// 调一个 tool-bridge 原生工具, 拿它的 result; 外层 / 内层任一 ok=false 都转成 Err。
async fn dispatch(tool: &str, args: Value) -> Result<Value, String> {
    let v = tool_bridge_rpc::call_with_timeout("tools/dispatch", json!({ "name": tool, "args": args }), RPC_TIMEOUT)
        .await?;
    if v.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(v.get("error").and_then(Value::as_str).unwrap_or("tool-bridge 调用失败").to_string());
    }
    let r = v.get("result").cloned().unwrap_or(Value::Null);
    if r.get("ok").and_then(Value::as_bool) == Some(false) {
        return Err(r.get("error").and_then(Value::as_str).unwrap_or("失败").to_string());
    }
    Ok(r)
}

pub async fn create_reminder(
    title: &str,
    body: Option<&str>,
    due_iso: Option<&str>,
    list_name: Option<&str>,
    priority: Option<u8>,
) -> Result<String, String> {
    let r = dispatch(
        "catfish_create_reminder",
        json!({ "title": title, "body": body, "due_date_iso": due_iso, "list_name": list_name, "priority": priority }),
    )
    .await?;
    Ok(str_field(&r, "reminder_name"))
}

pub async fn list_reminder_lists() -> Result<Vec<String>, String> {
    dispatch("catfish_list_reminder_lists", json!({})).await.map(|r| str_list(&r, "list_names"))
}

#[allow(clippy::too_many_arguments)]
pub async fn create_calendar_event(
    title: &str,
    start_iso: &str,
    end_iso: Option<&str>,
    location: Option<&str>,
    description: Option<&str>,
    calendar_name: Option<&str>,
    alarm_minutes_before: &[u32],
) -> Result<String, String> {
    let r = dispatch(
        "catfish_create_calendar_event",
        json!({
            "title": title, "start_iso": start_iso, "end_iso": end_iso, "location": location,
            "description": description, "calendar_name": calendar_name,
            "alarm_minutes_before": alarm_minutes_before,
        }),
    )
    .await?;
    Ok(str_field(&r, "event_summary"))
}

pub async fn list_calendars() -> Result<Vec<String>, String> {
    dispatch("catfish_list_calendars", json!({})).await.map(|r| str_list(&r, "calendar_names"))
}

/// Catfish 自己记的日历事件, 形状跟 mac EventKit / JXA 一样的 JSON 数组。
/// `range`: "today" / "natural-week"。
pub async fn calendar_events(range: &str) -> Result<Vec<Value>, String> {
    let v = tool_bridge_rpc::call_with_timeout("pim/calendar_events", json!({ "range": range }), RPC_TIMEOUT).await?;
    Ok(v.as_array().cloned().unwrap_or_default())
}
