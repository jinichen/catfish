//! 邮件定时扫描器 — BL-COMPANION-EMAIL-DIGEST-STEP2/3 (5/18).
//!
//! # 设计
//!
//! Tauri background tokio task, 每 N 分钟跑一次 `catfish-email list --unread --json`,
//! 跟上次结果的 message id 集合 diff.
//!
//! ## step2 (已 ship): 任何新未读 → macOS 系统通知
//!
//! ## step3 (本轮): LLM 评级 → 只"急"通知, "中/低"静默
//!
//! 新未读 → 喂给 gateway 快速 model 评 急/中/低 → 只"急"发系统通知 (Glass 声音 +
//! 桌宠提示框, 等 step4 BL-E13 hook), "中/低" 静默进 log 不打扰员工. 防通知疲劳.
//!
//! ## 不做的事
//!
//! - 不缓存邮件元数据到 Tauri state 给前端用 — 卡片仍然自己 shell out 拉.
//! - 桌宠主动闲聊 hook 留 step4 (跟 BL-E13 集成).
//! - 评级失败 → fallback 当"中" (不通知) — 不 fail 闭路径上不重要的事
//!
//! ## 配置
//!
//! - env `CATFISH_EMAIL_POLL_SECS`: 轮询间隔秒数, 默认 600 (10 min). 设 0 关.
//! - env `CATFISH_EMAIL_RATE_MODEL`: 评级用 model, 默认 catfish-public-deepseek-flash.
//! - env `CATFISH_EMAIL_RATE`: 1=开 (默认) / 0=关 (回 step2 任何新邮件都通知).
//!
//! ## 红线
//!
//! - 通知 / 评级里**只送主题 + 发件人**, 不送正文 (隐私 + token 省).
//! - 失败静默 — Mail.app 没开 / 没权限 / CLI 没装 / 评级 LLM 挂, log debug 不 spam 通知.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio::time;

use crate::services::{email_config, endpoints, oauth};

/// app handle 句柄, 给 background task 用来 emit Tauri 事件给前端.
/// schedule_email_scheduler() 启动时存进来.
static APP_HANDLE: OnceLock<AppHandle> = OnceLock::new();

/// urgency 评级缓存 — scheduler 评完一封, 写这里给 Tauri command email_urgency_map() 读.
/// id -> "急" | "中" | "低" 字符串 (跟 SerializableUrgency 同套). 已读 → tick 时自然
/// 从 baseline 移除 → 下次评级也不会再被加进来. 缓存上限 200 防内存涨, 老的先丢.
static URGENCY_CACHE: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();

fn urgency_cache() -> &'static Mutex<HashMap<String, String>> {
    URGENCY_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

#[derive(Deserialize, Clone, Debug)]
struct EmailItem {
    id: String,
    subject: String,
    sender: String,
}

/// LLM 评级结果. fallback 'medium' 时不通知, 不阻塞.
#[derive(Debug, Clone, PartialEq)]
enum Urgency {
    Urgent,   // 急: 系统通知 + (step4 桌宠主动闲聊)
    Medium,   // 中: 静默, 卡片显但不打扰
    Low,      // 低: 静默 (newsletter / 自动通知类)
}

impl Urgency {
    fn from_label(s: &str) -> Self {
        let s = s.trim().to_lowercase();
        if s.contains("急") || s.contains("urgent") || s.contains("high") {
            Self::Urgent
        } else if s.contains("低") || s.contains("low") || s.contains("noise") {
            Self::Low
        } else {
            Self::Medium
        }
    }

    fn is_urgent(&self) -> bool {
        matches!(self, Self::Urgent)
    }

    fn as_label(&self) -> &'static str {
        match self {
            Self::Urgent => "急",
            Self::Medium => "中",
            Self::Low => "低",
        }
    }
}

/// Tauri command 用 — 前端 EmailTab 读评级 badge.
/// 返 id → "急" | "中" | "低" map, 卡 cache 上限 200, 重启 Companion 清空.
#[tauri::command]
pub fn email_urgency_map() -> HashMap<String, String> {
    urgency_cache().lock()
        .map(|c| c.clone())
        .unwrap_or_default()
}

/// 5/18 BL-EMAIL-URGENCY-BADGE: 前端主动 batch 评级一批邮件 (按 id 给 subject/sender).
///
/// 老路径只评 scheduler diff 出的新邮件, 启动时已有的 99 封历史邮件永远无评级,
/// EmailTab badge 空白. 这命令让前端打开 tab 时主动调一次, 把所有可见的未评 id
/// 一次性评完. 已 cache 的 id 跳过 (省 token), 只评新 id.
///
/// Args:
///     items: 跟 fetch_unread 同 shape (id/subject/sender/account/date/is_read),
///            前端从 list_fetch 返的数据里挑没 cache 的传过来
///
/// Returns: 评完后的完整 cache map (含老的 + 新的). 调用方一次拿全, 不用再调 urgency_map.
///
/// 性能: LLM 调 1 次评 batch (跟 scheduler rate_emails 同函数 call_rate_llm).
/// 前端应该自己 batch 上限 (e.g. 30 一批), 不要一次塞 99 个 prompt 太长.
#[tauri::command]
pub async fn email_classify_now(
    items: Vec<EmailItemInput>,
) -> Result<HashMap<String, String>, String> {
    // 过滤已 cache 的 (省 LLM 调用). EmailItem 只要 id/subject/sender 三字段
    // (rate_emails 内部只用这三个), input 的 account/date/is_read 仅作前端
    // 自描述方便调用方传 list_fetch 的整条 dict, 这里丢掉.
    let to_rate: Vec<EmailItem> = {
        let cache = urgency_cache().lock().map_err(|e| e.to_string())?;
        items
            .iter()
            .filter(|it| !cache.contains_key(&it.id))
            .map(|it| EmailItem {
                id: it.id.clone(),
                subject: it.subject.clone(),
                sender: it.sender.clone(),
            })
            .collect()
    };

    if to_rate.is_empty() {
        // 全已 cache, 直接返
        return Ok(urgency_cache().lock().map(|c| c.clone()).unwrap_or_default());
    }

    log::info!(
        "email_classify_now: 评级 {} 封 (跳过 {} 已 cache)",
        to_rate.len(),
        items.len() - to_rate.len(),
    );

    let rated = rate_emails(&to_rate).await;
    if let Ok(mut cache) = urgency_cache().lock() {
        for (it, u) in to_rate.iter().zip(rated.iter()) {
            cache.insert(it.id.clone(), u.as_label().to_string());
        }
        if cache.len() > 200 {
            let keys: Vec<_> = cache.keys().take(100).cloned().collect();
            for k in keys {
                cache.remove(&k);
            }
        }
    }

    Ok(urgency_cache().lock().map(|c| c.clone()).unwrap_or_default())
}

/// 前端传给 email_classify_now 用的 input shape (比 EmailItem 多 Option, 兼容 list_fetch
/// JSON 字段缺失场景). 多余字段 (account/date/is_read) 仅作 future-proof 接收, 不读.
#[derive(serde::Deserialize)]
#[allow(dead_code)]
pub struct EmailItemInput {
    pub id: String,
    pub subject: String,
    pub sender: String,
    pub account: Option<String>,
    pub date: Option<String>,
    pub is_read: Option<bool>,
}

/// app 启动时调一次. poll_secs=0 (yaml 或 env) 则不起.
///
/// `app` 参数: 给 background task 存进 OnceLock, 用来 emit `catfish:email-urgent`
/// 事件 → 前端 App.tsx 监听 → 调起桌宠主动闲聊 (BL-E13 路径).
pub fn schedule_email_scheduler(app: AppHandle) {
    let _ = APP_HANDLE.set(app);
    let cfg = email_config::email_config();
    let poll_secs = cfg.poll_secs;

    if poll_secs == 0 {
        log::info!("email_scheduler: poll_secs=0 (yaml/env 关掉), 不起调度");
        return;
    }

    log::info!(
        "email_scheduler: 启动, 每 {}s 扫一次未读邮件 (评级 {}, model {})",
        poll_secs,
        if cfg.rate_enabled { "开" } else { "关 - 任何新邮件都通知" },
        cfg.rate_model,
    );

    tauri::async_runtime::spawn(async move {
        let mut interval = time::interval(Duration::from_secs(poll_secs));
        // 第一 tick 立即返, 用来建 baseline
        interval.tick().await;
        let mut seen: HashSet<String> = HashSet::new();
        let mut is_baseline = true;

        loop {
            match fetch_unread().await {
                Ok(items) => {
                    let current_ids: HashSet<String> =
                        items.iter().map(|i| i.id.clone()).collect();

                    if is_baseline {
                        // 第一次: 不发通知, 把当前所有未读 ID 记为 baseline.
                        seen = current_ids;
                        is_baseline = false;
                        log::info!(
                            "email_scheduler: baseline 建立 ({} 封未读)",
                            seen.len()
                        );
                    } else {
                        // 后续 tick: diff 出新 ID
                        let new_items: Vec<EmailItem> = items
                            .iter()
                            .filter(|i| !seen.contains(&i.id))
                            .cloned()
                            .collect();

                        if !new_items.is_empty() {
                            log::info!(
                                "email_scheduler: 检测到 {} 封新邮件",
                                new_items.len()
                            );
                            // step3: 评级 → 只"急"通知
                            let rated = rate_emails(&new_items).await;

                            // step2 (BL-COMPANION-EMAIL-TAB-STEP2): 写 urgency 缓存
                            // 给前端 EmailTab badge 用. 老 id 已读 → baseline 自然
                            // remove, 这里不主动清; LRU 上限 200 防内存涨.
                            if let Ok(mut cache) = urgency_cache().lock() {
                                for (it, u) in new_items.iter().zip(rated.iter()) {
                                    cache.insert(it.id.clone(), u.as_label().to_string());
                                }
                                // 简单 LRU: 超 200 时随便丢一半 (BTreeMap 不行用 HashMap)
                                if cache.len() > 200 {
                                    let keys: Vec<_> = cache.keys().take(100).cloned().collect();
                                    for k in keys { cache.remove(&k); }
                                }
                            }

                            let urgent: Vec<&EmailItem> = rated
                                .iter()
                                .zip(new_items.iter())
                                .filter_map(|(u, it)| if u.is_urgent() { Some(it) } else { None })
                                .collect();
                            log::info!(
                                "email_scheduler: 评级 {} 急 / {} 中 / {} 低",
                                rated.iter().filter(|u| u.is_urgent()).count(),
                                rated.iter().filter(|u| **u == Urgency::Medium).count(),
                                rated.iter().filter(|u| **u == Urgency::Low).count(),
                            );
                            if !urgent.is_empty() {
                                send_notification(&urgent);
                                emit_urgent_event(&urgent);
                            }
                        }
                        // 更新 baseline (含已读消除 + 新增)
                        seen = current_ids;
                    }
                }
                Err(e) => {
                    log::debug!("email_scheduler: 拉未读失败 (不通知): {e}");
                }
            }
            interval.tick().await;
        }
    });
}

/// shell out catfish-email list --unread --json. 失败返 Err.
async fn fetch_unread() -> Result<Vec<EmailItem>, String> {
    let bin = find_catfish_email()
        .ok_or_else(|| "catfish-email CLI 没装".to_string())?;

    // tokio spawn_blocking 让阻塞 subprocess 不卡 runtime
    let output = tokio::task::spawn_blocking(move || {
        Command::new(&bin)
            .args(["list", "--unread", "--json", "--limit", "20"])
            .output()
    })
    .await
    .map_err(|e| format!("spawn_blocking 失败: {e}"))?
    .map_err(|e| format!("catfish-email 调用失败: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(format!(
            "catfish-email 退出非 0: {}",
            if stderr.is_empty() { "no stderr".into() } else { stderr }
        ));
    }

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    if stdout.trim().is_empty() {
        return Ok(Vec::new());
    }

    serde_json::from_str::<Vec<EmailItem>>(&stdout)
        .map_err(|e| format!("JSON 解析失败: {e}"))
}

/// 评级新邮件. 调 gateway 快速 model. 失败 fallback 全标 Medium (不通知 + 不阻塞).
///
/// CATFISH_EMAIL_RATE=0 → 一律 Urgent (回 step2 行为, 任何新邮件都通知).
async fn rate_emails(items: &[EmailItem]) -> Vec<Urgency> {
    let cfg = email_config::email_config();
    if !cfg.rate_enabled {
        // 评级关掉 → 一律 Urgent (回 step2 行为, 任何新邮件都通知)
        return vec![Urgency::Urgent; items.len()];
    }
    if items.is_empty() {
        return Vec::new();
    }

    match call_rate_llm(items).await {
        Ok(urgencies) if urgencies.len() == items.len() => urgencies,
        Ok(urgencies) => {
            log::warn!(
                "email_scheduler: 评级返 {} 条 != 期望 {}, fallback Medium",
                urgencies.len(), items.len()
            );
            vec![Urgency::Medium; items.len()]
        }
        Err(e) => {
            log::debug!("email_scheduler: 评级失败 (fallback Medium): {e}");
            vec![Urgency::Medium; items.len()]
        }
    }
}

#[derive(Serialize)]
struct ChatMessage {
    role: &'static str,
    content: String,
}

#[derive(Serialize)]
struct ChatRequest {
    model: String,
    messages: Vec<ChatMessage>,
    temperature: f32,
    max_tokens: u32,
    stream: bool,
}

#[derive(Deserialize)]
struct ChatChoice {
    message: ChatChoiceMsg,
}

#[derive(Deserialize)]
struct ChatChoiceMsg {
    content: Option<String>,
}

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<ChatChoice>,
}

async fn call_rate_llm(items: &[EmailItem]) -> Result<Vec<Urgency>, String> {
    let token = oauth::current_access_token()
        .ok_or_else(|| "没拿到 access_token (员工没登录)".to_string())?;
    let gateway = endpoints::endpoints().gateway_base();
    let model = email_config::email_config().rate_model.clone();

    let list = items
        .iter()
        .enumerate()
        .map(|(i, it)| {
            format!(
                "{}. 主题: {} | 发件人: {}",
                i + 1,
                truncate(&it.subject, 60),
                truncate(&it.sender, 40),
            )
        })
        .collect::<Vec<_>>()
        .join("\n");

    let system = "你是邮件分类助手. 按重要程度评级邮件: 急 / 中 / 低. \
                  急 = 老板 / 客户 / 直接老板 / 含 deadline 关键词 / 紧急任务; \
                  低 = newsletter / 促销 / 自动通知 / GitHub PR review 之类的常规事项; \
                  其它一律 中. \
                  返回 JSON 数组, 每个元素只一个字: '急' / '中' / '低'. 不要其它任何解释.";
    let user = format!(
        "{list}\n\n按上面顺序, 返回 {} 个评级的 JSON 数组, 例如 [\"急\",\"中\",\"低\"]. 只返 JSON, 不要任何解释.",
        items.len()
    );

    let req = ChatRequest {
        model: model.clone(),
        messages: vec![
            ChatMessage { role: "system", content: system.to_string() },
            ChatMessage { role: "user", content: user },
        ],
        temperature: 0.0,
        max_tokens: 64,
        stream: false,
    };

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| format!("reqwest build 失败: {e}"))?;

    let resp = client
        .post(format!("{gateway}/v1/chat/completions"))
        .header("Authorization", format!("Bearer {token}"))
        .header("X-Catfish-Source", "companion-email-scheduler")
        .header("X-Catfish-Skip-Identity", "true")  // 不需要 SOUL inject, 服务式调用
        .json(&req)
        .send()
        .await
        .map_err(|e| format!("gateway 调用失败: {e}"))?;

    if !resp.status().is_success() {
        return Err(format!("gateway 返 {}", resp.status()));
    }

    let body: ChatResponse = resp
        .json()
        .await
        .map_err(|e| format!("response 解析失败: {e}"))?;
    let content = body
        .choices
        .first()
        .and_then(|c| c.message.content.clone())
        .unwrap_or_default();

    parse_urgencies(&content)
}

/// 从 LLM 输出文本解析 JSON 数组. 容错 — markdown code fence / 前后多余文字都试着扒出来.
fn parse_urgencies(s: &str) -> Result<Vec<Urgency>, String> {
    let trimmed = s.trim();
    // 找第一个 [ 到最后一个 ]
    let start = trimmed.find('[').ok_or_else(|| format!("没 JSON array: {trimmed:?}"))?;
    let end = trimmed.rfind(']').ok_or_else(|| format!("没 ] 闭合: {trimmed:?}"))?;
    if end <= start {
        return Err(format!("] 在 [ 前: {trimmed:?}"));
    }
    let array_str = &trimmed[start..=end];
    let labels: Vec<String> = serde_json::from_str(array_str)
        .map_err(|e| format!("JSON 解析失败 ({e}): {array_str:?}"))?;
    Ok(labels.iter().map(|l| Urgency::from_label(l)).collect())
}

/// 跟 commands/email.rs find_catfish_email 同模式, 这里 inline 避循环 import.
fn find_catfish_email() -> Option<PathBuf> {
    if let Ok(home) = std::env::var("HOME") {
        let candidate = PathBuf::from(home).join(".local/bin/catfish-email");
        if candidate.exists() {
            return Some(candidate);
        }
    }
    if let Ok(out) = Command::new("which").arg("catfish-email").output() {
        if out.status.success() {
            let path_str = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !path_str.is_empty() {
                return Some(PathBuf::from(path_str));
            }
        }
    }
    None
}

/// emit `catfish:email-urgent` 给前端 — 前端 App.tsx 接, 调桌宠主动闲聊.
/// 同时填一份简洁 starter 字符串, 前端不用再造句.
fn emit_urgent_event(urgent: &[&EmailItem]) {
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
}

/// 通过 osascript display notification 发 macOS 系统通知.
/// 1 封 → 显主题; 多封 → 显数量 + 第一封.
fn send_notification(new_items: &[&EmailItem]) {
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

fn truncate(s: &str, max_chars: usize) -> String {
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
    fn urgency_from_label_chinese() {
        assert_eq!(Urgency::from_label("急"), Urgency::Urgent);
        assert_eq!(Urgency::from_label("中"), Urgency::Medium);
        assert_eq!(Urgency::from_label("低"), Urgency::Low);
    }

    #[test]
    fn urgency_from_label_english() {
        assert_eq!(Urgency::from_label("urgent"), Urgency::Urgent);
        assert_eq!(Urgency::from_label("low"), Urgency::Low);
        assert_eq!(Urgency::from_label("medium"), Urgency::Medium);
    }

    #[test]
    fn urgency_from_label_fallback_to_medium() {
        // 模型乱返不挂, 当 Medium
        assert_eq!(Urgency::from_label("garbage"), Urgency::Medium);
        assert_eq!(Urgency::from_label(""), Urgency::Medium);
    }

    #[test]
    fn parse_urgencies_clean_json() {
        let r = parse_urgencies(r#"["急","中","低"]"#).unwrap();
        assert_eq!(r, vec![Urgency::Urgent, Urgency::Medium, Urgency::Low]);
    }

    #[test]
    fn parse_urgencies_with_markdown_fence() {
        let r = parse_urgencies("```json\n[\"急\",\"低\"]\n```").unwrap();
        assert_eq!(r, vec![Urgency::Urgent, Urgency::Low]);
    }

    #[test]
    fn parse_urgencies_with_leading_text() {
        let r = parse_urgencies("根据评级:\n[\"急\",\"中\"]\n谢谢").unwrap();
        assert_eq!(r, vec![Urgency::Urgent, Urgency::Medium]);
    }

    #[test]
    fn parse_urgencies_no_array_errors() {
        assert!(parse_urgencies("急 中 低").is_err());
        assert!(parse_urgencies("").is_err());
    }

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
