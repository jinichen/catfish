//! 急件的对外提醒: emit 给前端的事件 + macOS 系统通知。
//!
//! 2026-08-15 从 email_scheduler.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! truncate / extract_sender_name 也在这里 —— 它俩主要为这里的文案服务
//! (6 处), email_llm.rs 也用 truncate (2 处), 见那个文件头里的说明。

#[cfg(target_os = "macos")]
use std::process::Command;

use serde::Serialize;
use tauri::Emitter;

use super::email_state::APP_HANDLE;
use super::email_types::EmailItem;

/// emit `catfish:email-urgent` 给前端 — 前端 App.tsx 接, 调主动闲聊 (BL-E13).
/// 同时填一份简洁 starter 字符串, 前端不用再造句.
pub(crate) fn emit_urgent_event(urgent: &[&EmailItem]) {
    let Some(app) = APP_HANDLE.get() else {
        log::debug!("email_scheduler: APP_HANDLE 未初始化, 不 emit");
        return;
    };

    let starter = if urgent.len() == 1 {
        let item = urgent[0];
        format!(
            "{} 那封紧的来了 — {} 帮你看?",
            extract_sender_name(&item.sender),
            truncate(&item.subject, 30),
        )
    } else {
        let first = urgent[0];
        format!(
            "你有 {} 封紧的邮件 (最新: {} - {}), 帮你过一遍?",
            urgent.len(),
            extract_sender_name(&first.sender),
            truncate(&first.subject, 25),
        )
    };

    let payload = UrgentEventPayload {
        count: urgent.len(),
        starter,
        ids: urgent.iter().map(|i| i.id.clone()).collect(),
        // BL-EMAIL-URGENT-LLM-PUSH (5/20): 多带 metadata 让前端调 LLM 写 starter
        // 不用回查 (没这个字段前端要再调 email_urgency_map + email list).
        items: urgent
            .iter()
            .map(|i| UrgentItemMeta {
                subject: i.subject.clone(),
                sender: i.sender.clone(),
            })
            .collect(),
    };

    if let Err(e) = app.emit("catfish:email-urgent", &payload) {
        log::warn!("email_scheduler: emit catfish:email-urgent 失败: {e}");
    } else {
        log::info!("email_scheduler: emit catfish:email-urgent (count={})", payload.count);
    }
}

#[derive(Serialize, Clone)]
struct UrgentEventPayload {
    count: usize,
    starter: String,
    ids: Vec<String>,
    items: Vec<UrgentItemMeta>,
}

#[derive(Serialize, Clone)]
struct UrgentItemMeta {
    subject: String,
    sender: String,
}

/// 通过 osascript display notification 发 macOS 系统通知.
/// 1 封 → 显主题; 多封 → 显数量 + 第一封.
pub(crate) fn send_notification(new_items: &[&EmailItem]) {
    let (title, body) = if new_items.len() == 1 {
        let item = &new_items[0];
        (
            format!("📧 {}", truncate(&extract_sender_name(&item.sender), 30)),
            truncate(&item.subject, 80),
        )
    } else {
        let first = &new_items[0];
        (
            format!("📧 {} 封新邮件", new_items.len()),
            format!(
                "最新: {} — {}",
                truncate(&extract_sender_name(&first.sender), 20),
                truncate(&first.subject, 50)
            ),
        )
    };

    #[cfg(target_os = "macos")]
    {
        let safe_title = title.replace('"', "\\\"");
        let safe_body = body.replace('"', "\\\"");
        let script = format!(
            "display notification \"{}\" with title \"{}\" sound name \"Glass\"",
            safe_body, safe_title,
        );
        let _ = Command::new("osascript").args(["-e", &script]).status();
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = (title, body);
        log::info!("email_scheduler: 非 macOS 不通知 (TODO Linux/Win)");
    }
}

/// "张三 <zhang@x.com>" → "张三". 没显示名 → 邮箱 @ 前部分.
fn extract_sender_name(sender: &str) -> String {
    if sender.is_empty() {
        return "(未知)".to_string();
    }
    // 找 '<' 前的内容
    if let Some(idx) = sender.find('<') {
        let name = sender[..idx].trim().trim_matches('"');
        if !name.is_empty() {
            return name.to_string();
        }
    }
    // 没显示名 → 邮箱 @ 前
    if let Some(idx) = sender.find('@') {
        return sender[..idx].to_string();
    }
    sender.to_string()
}

pub(crate) fn truncate(s: &str, max_chars: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() <= max_chars {
        s.to_string()
    } else {
        let head: String = chars[..max_chars].iter().collect();
        format!("{head}…")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_sender_name_with_display() {
        assert_eq!(extract_sender_name("张三 <zhang@x.com>"), "张三");
        assert_eq!(extract_sender_name("\"Acme Corp\" <noreply@acme.com>"), "Acme Corp");
    }

    #[test]
    fn extract_sender_name_plain_email() {
        assert_eq!(extract_sender_name("bob@example.com"), "bob");
    }

    #[test]
    fn truncate_short_unchanged() {
        assert_eq!(truncate("abc", 10), "abc");
    }

    #[test]
    fn truncate_long_with_ellipsis() {
        assert_eq!(truncate("abcdefghij", 5), "abcde…");
    }

    #[test]
    fn truncate_chinese_counts_chars() {
        assert_eq!(truncate("一二三四五六", 3), "一二三…");
    }
}
