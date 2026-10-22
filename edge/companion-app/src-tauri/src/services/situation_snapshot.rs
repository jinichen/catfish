//! 「此刻」快照: 给 hermes 里的 catfish-memory 插件读 (10/22 P1)。
//!
//! # 为什么
//!
//! 主聊天原来只知道今天几号星期几。早安页知道今天的会、急邮件、到期待办, 主聊天
//! 不知道 —— 员工在工作台问「今天先做什么」「下午那个会要准备什么」, 模型得自己
//! 想起来去调三四个工具。日历走 EventKit (只有 Companion 能调), 未读邮件的主题 /
//! 发件人只在 Companion 的邮件扫描器手里, 插件 (Python, 跑在 hermes 里) 拿不到。
//!
//! 所以 Companion 把这两样已经在拿的东西顺手落盘, 插件每轮 prefetch 读:
//!
//!   ~/.catfish/situation/unread_email.json   邮件扫描器每轮拉完未读写 (只主题 + 发件人 + 急/中/低)
//!   ~/.catfish/situation/calendar_today.json 每 15 分钟取一次今天的日程
//!
//! 待办插件直接读 task_library.db, 不经过这里。
//!
//! # 边界
//!
//! 只写本机 (`~/.catfish`), 跟 email_urgency.json 同级; 不带正文。插件那边过期就不用
//! (日历看日期, 邮件超过 1 小时不用), 所以 Companion 没开的时候不会拿旧数据当「此刻」。

use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;
use tokio::time;

use super::email_types::EmailItem;

const DIR: &str = "situation";
const CALENDAR_EVERY: Duration = Duration::from_secs(15 * 60);
/// 未读太多时只留前面这些 (急的排前)
const MAX_UNREAD: usize = 40;

fn situation_dir() -> Option<PathBuf> {
    let home = crate::util::paths::home_env().ok()?;
    Some(PathBuf::from(home).join(".catfish").join(DIR))
}

fn write_atomic(name: &str, value: &Value) {
    let Some(dir) = situation_dir() else { return };
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let path = dir.join(name);
    let tmp = path.with_extension("json.tmp");
    let Ok(text) = serde_json::to_string(value) else { return };
    if std::fs::write(&tmp, text).is_ok() {
        let _ = std::fs::rename(&tmp, &path);
    }
}

fn now_iso() -> String {
    chrono::Local::now().to_rfc3339()
}

fn urgency_rank(u: &str) -> u8 {
    match u {
        "急" => 0,
        "中" => 1,
        "低" => 2,
        _ => 1,
    }
}

/// 未读列表 → JSON (纯函数, 好测)。急的排前, 截到 MAX_UNREAD, 计数用全量。
pub(crate) fn unread_json(items: &[EmailItem], urgency: &HashMap<String, String>, at: &str) -> Value {
    let mut rows: Vec<(&EmailItem, &str)> = items
        .iter()
        .map(|it| (it, urgency.get(&it.id).map(String::as_str).unwrap_or("")))
        .collect();
    rows.sort_by_key(|(_, u)| urgency_rank(u));
    let count = |label: &str| rows.iter().filter(|(_, u)| *u == label).count();
    json!({
        "updated_at": at,
        "total": items.len(),
        "urgent": count("急"),
        "medium": count("中"),
        "low": count("低"),
        "items": rows.iter().take(MAX_UNREAD).map(|(it, u)| json!({
            "subject": it.subject.chars().take(80).collect::<String>(),
            "sender": it.sender.chars().take(60).collect::<String>(),
            "urgency": u,
        })).collect::<Vec<_>>(),
    })
}

/// 邮件扫描器每轮拉到未读后调。失败静默 —— 快照缺了插件就不显示这一块。
pub(crate) fn write_unread(items: &[EmailItem]) {
    let urgency = super::email_state::urgency_cache()
        .lock()
        .map(|c| c.clone())
        .unwrap_or_default();
    write_atomic("unread_email.json", &unread_json(items, &urgency, &now_iso()));
}

/// 今天的日程 → JSON。`raw` 是 calendar_today_fetch 原样返回的事件数组。
pub(crate) fn calendar_json(raw: &str, at: &str, date: &str) -> Option<Value> {
    let events: Value = serde_json::from_str(raw).ok()?;
    let arr = events.as_array()?;
    Some(json!({
        "updated_at": at,
        "date": date,
        "events": arr.iter().take(30).map(|e| json!({
            "summary": e.get("summary").and_then(Value::as_str).unwrap_or("").chars().take(80).collect::<String>(),
            "start": e.get("start").and_then(Value::as_str).unwrap_or(""),
            "end": e.get("end").and_then(Value::as_str).unwrap_or(""),
            "all_day": e.get("all_day").and_then(Value::as_bool).unwrap_or(false),
            "location": e.get("location").and_then(Value::as_str).unwrap_or("").chars().take(40).collect::<String>(),
        })).collect::<Vec<_>>(),
    }))
}

/// 启动后每 15 分钟取一次今天的日程写盘 (calendar_today_fetch 自带 5 分钟缓存, 跟早安页共用)。
pub fn schedule_calendar_snapshot() {
    tauri::async_runtime::spawn(async move {
        let mut interval = time::interval(CALENDAR_EVERY);
        // 取日程失败 / 慢时不连着补打 (tokio 默认 Burst)
        interval.set_missed_tick_behavior(time::MissedTickBehavior::Delay);
        loop {
            interval.tick().await;
            match crate::commands::calendar::calendar_today_fetch(Some(false)).await {
                Ok(raw) => {
                    let now = chrono::Local::now();
                    if let Some(v) = calendar_json(&raw, &now.to_rfc3339(), &now.format("%Y-%m-%d").to_string()) {
                        write_atomic("calendar_today.json", &v);
                    }
                }
                Err(e) => log::debug!("[situation] 取今天日程失败 (不写快照): {e}"),
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mail(id: &str, subject: &str) -> EmailItem {
        EmailItem { id: id.into(), subject: subject.into(), sender: "张三 <z@x.cn>".into(), snippet: "正文不该出现".into() }
    }

    #[test]
    fn 急的排前_只带主题发件人_不带正文() {
        let items = vec![mail("a", "周报"), mail("b", "今天下班前回复投标函"), mail("c", "订阅")];
        let urg: HashMap<String, String> =
            [("a", "中"), ("b", "急"), ("c", "低")].iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let v = unread_json(&items, &urg, "t");
        assert_eq!(v["total"], 3);
        assert_eq!(v["urgent"], 1);
        assert_eq!(v["items"][0]["subject"], "今天下班前回复投标函");
        assert!(!v.to_string().contains("正文不该出现"));
    }

    #[test]
    fn 日历原样数组转快照_坏json不写() {
        let raw = r#"[{"calendar":"Work","summary":"资质评审会","start":"2026-10-22T14:00:00+08:00","end":"2026-10-22T15:00:00+08:00","all_day":false,"location":"3楼"}]"#;
        let v = calendar_json(raw, "t", "2026-10-22").unwrap();
        assert_eq!(v["events"][0]["summary"], "资质评审会");
        assert_eq!(v["date"], "2026-10-22");
        assert!(calendar_json("not json", "t", "d").is_none());
    }
}
