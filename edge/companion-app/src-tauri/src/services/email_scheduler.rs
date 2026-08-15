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
//! - env `CATFISH_EMAIL_RATE_MODEL`: 评级用 model 显式 override (可选).
//!   8/9 鸿波: 评级 model **只来自 picker**. 原来还有 roles.yaml rate_fast /
//!   yaml rate_model 两级兜底, 已砍 —— 后台悄悄换模型来源, 界面上毫无痕迹,
//!   而 rate_fast 默认还可能是内网 model (P3.5.27 数据零出端红线), 公网
//!   override 走 .env / yaml 显式启用 (要承担飞公网 LLM 的合规风险).
//!   P3.5.139 (6/29 鸿波"都要去除硬编码"): 删 DEFAULT_RATE_MODEL 常量.
//! - env `CATFISH_EMAIL_RATE`: 1=开 (默认) / 0=关 (回 step2 任何新邮件都通知).
//!
//! ## 红线
//!
//! - 通知 / 评级里**只送主题 + 发件人**, 不送正文 (隐私 + token 省).
//! - 失败静默 — Mail.app 没开 / 没权限 / CLI 没装 / 评级 LLM 挂, log debug 不 spam 通知.

use std::collections::{HashMap, HashSet};
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use tauri::AppHandle;
use tokio::time;
use tokio::time::MissedTickBehavior;

use crate::services::{
    catfish_paths, email_config, hermes_api_config, picker_config, upstream_error_guard,
};
// P3.3.58 (6/12 鸿波): 段 2A 集成 phishing_scan
// 8/15: LLM 复审那层切到了 phishing_llm.rs (它在 phishing_scan.rs 里没有调用者,
// 唯一的调用者就是本文件), 所以这里分成两个 import。
use crate::services::phishing_llm::{self, LlmReviewInput};
use crate::services::phishing_scan::{self, PhishingScanResult, Severity};

use super::email_llm::call_rate_llm;
use super::email_notify::{emit_urgent_event, send_notification};
use super::email_state::{
    load_persisted_state, now_epoch_secs, persist_push_history, persist_urgency_cache,
    push_history, urgency_cache, APP_HANDLE, DEDUP_WINDOW_SECS,
};
use super::email_types::{EmailItem, Urgency};

// BL-COMPANION-BRIEFING-V2 sub-task 2 (5/20): 通知去重 + 评级持久化.
//
// 1. urgency_cache 现在不只 in-memory, 启动时从 ~/.catfish/email_urgency.json
//    加载, 评完一封后异步写回 disk. Companion 重启不重评 (省 LLM token).
// 2. 急邮件 push history 持久化到 ~/.catfish/email_push_history.json,
//    24h 内同 id 不重复 push macOS 通知. 防"同一急邮件每次 scheduler tick 都叫醒".

/// 过滤掉 24h 内已 push 过的 id, 返还能 push 的 items (顺手把现 push 的 id 记进 history).
/// 同时 persist push_history 到 disk.
fn dedup_for_push<'a>(urgent: &[&'a EmailItem]) -> Vec<&'a EmailItem> {
    let now = now_epoch_secs();
    let cutoff = now.saturating_sub(DEDUP_WINDOW_SECS);
    let mut hist = match push_history().lock() {
        Ok(g) => g,
        Err(_) => return urgent.to_vec(),  // lock 坏不该 silent skip notification
    };
    // GC 老 entry
    hist.retain(|_, ts| *ts >= cutoff);
    // 过滤未 push 过的
    let allowed: Vec<&EmailItem> = urgent
        .iter()
        .copied()
        .filter(|it| !hist.contains_key(&it.id))
        .collect();
    // 记录这次 push 的 id
    for it in &allowed {
        hist.insert(it.id.clone(), now);
    }
    drop(hist);  // 释放锁再写 disk
    persist_push_history();
    allowed
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
        // 8/15: 200 → URGENCY_CACHE_MAX (600)。见常量上那段注释 ——
        // 上限低于列表上限 500 是那个"永动机"的燃料之一。
        if cache.len() > URGENCY_CACHE_MAX {
            let keys: Vec<_> = cache
                .keys()
                .take(URGENCY_CACHE_MAX / 2)
                .cloned()
                .collect();
            for k in keys {
                cache.remove(&k);
            }
        }
    }
    // BL-COMPANION-BRIEFING-V2 sub-task 2 (5/20): 持久化, 重启不重评
    persist_urgency_cache();

    Ok(urgency_cache().lock().map(|c| c.clone()).unwrap_or_default())
}

/// P3.3.58 段 2B (6/12 鸿波): 前端打开邮件 tab 时主动 trigger 钓鱼扫描.
/// 跟 email_classify_now 同 pattern — 跳过已 scan 的 id 省 LLM call, 新 id 走
/// scan_phishing_for_new (light scan + LLM batch + store + audit).
///
/// Returns: PHISHING_STORE 完整 snapshot (id → result), 调用方一次拿全.
#[tauri::command]
pub async fn email_phishing_scan_now(
    items: Vec<EmailItemInput>,
) -> Result<HashMap<String, PhishingScanResult>, String> {
    // 跳过已 scan 的
    let to_scan: Vec<EmailItem> = {
        let store = phishing_store().lock().map_err(|e| e.to_string())?;
        items
            .iter()
            .filter(|it| !store.contains_key(&it.id))
            .map(|it| EmailItem {
                id: it.id.clone(),
                subject: it.subject.clone(),
                sender: it.sender.clone(),
            })
            .collect()
    };

    if !to_scan.is_empty() {
        log::info!(
            "email_phishing_scan_now: 钓鱼扫描 {} 封 (跳过 {} 已扫)",
            to_scan.len(),
            items.len() - to_scan.len(),
        );
        scan_phishing_for_new(&to_scan).await;
    }

    Ok(phishing_store().lock().map(|s| s.clone()).unwrap_or_default())
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

    // BL-COMPANION-BRIEFING-V2 sub-task 2 (5/20): load 持久化的 urgency cache +
    // push history. Companion 重启不重评 / 不再叫醒同一急邮件 24h.
    load_persisted_state();

    if poll_secs == 0 {
        log::info!("email_scheduler: poll_secs=0 (yaml/env 关掉), 不起调度");
        return;
    }

    // P3.5.139 (6/29 鸿波"都要去除硬编码"): rate_model 现在 Option, None 表示
    // "跟随 chain (picker > role > Err)". log 显示 Some 用具体值, None 用语义文案.
    let rate_model_display = cfg
        .rate_model
        .as_deref()
        .unwrap_or("跟随 picker (8/9 起唯一来源)");
    log::info!(
        "email_scheduler: 启动, 每 {}s 扫一次未读邮件 (评级 {}, model {})",
        poll_secs,
        if cfg.rate_enabled { "开" } else { "关 - 任何新邮件都通知" },
        rate_model_display,
    );

    tauri::async_runtime::spawn(async move {
        let mut interval = time::interval(Duration::from_secs(poll_secs));
        // 8/15: tokio 的 interval 默认是 MissedTickBehavior::Burst —— 循环体
        // 耗时超过周期时, 漏掉的 tick 会**一次性连着补打**。
        //
        // 这个循环体里有三个长 await: fetch_unread (子进程)、rate_emails
        // (LLM, 实测 5–10s)、scan_phishing_for_new (LLM)。上游一慢 (8/15 是
        // 配额耗尽 → fallback → 更慢), 单轮就轻松超过 30s, 于是补打的 tick
        // 让循环以"体耗"而不是"周期"的节奏空转 —— **越慢烧得越凶**, 自我放大。
        //
        // Skip: 漏掉的 tick 直接丢, 下一次对齐到未来的周期点。对"定期扫未读"
        // 这种幂等轮询, 漏掉一轮没有任何损失 —— 下一轮拿到的是同一份未读列表。
        interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
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

                            // P3.3.58 (6/12 鸿波): 钓鱼 light scan + LLM 复审, 失败静默
                            scan_phishing_for_new(&new_items).await;

                            // step2 (BL-COMPANION-EMAIL-TAB-STEP2): 写 urgency 缓存
                            // 给前端 EmailTab badge 用. 老 id 已读 → baseline 自然
                            // remove, 这里不主动清; LRU 上限 200 防内存涨.
                            if let Ok(mut cache) = urgency_cache().lock() {
                                for (it, u) in new_items.iter().zip(rated.iter()) {
                                    cache.insert(it.id.clone(), u.as_label().to_string());
                                }
                                // 简单 LRU: 超 200 时随便丢一半 (BTreeMap 不行用 HashMap)
                                if cache.len() > URGENCY_CACHE_MAX {
                                    let keys: Vec<_> = cache
                                        .keys().take(URGENCY_CACHE_MAX / 2).cloned().collect();
                                    for k in keys { cache.remove(&k); }
                                }
                            }
                            // BL-COMPANION-BRIEFING-V2 sub-task 2 (5/20): 评完一批
                            // 异步写 urgency_cache 到 disk. 失败静默, 不阻塞 scheduler.
                            persist_urgency_cache();

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
                                // BL-COMPANION-BRIEFING-V2 sub-task 2 (5/20): 24h dedup
                                // 同一急邮件不重复 push macOS 通知. 第一次 push 之后,
                                // 24h 内 scheduler tick 检测到这封仍 unread 不再叫醒.
                                // 仍 emit Tauri 事件给前端 — 前端桌宠主动闲聊 path
                                // (BL-E13 step4) 自己有 dedup, 这里不替它做主.
                                let to_push = dedup_for_push(&urgent);
                                if !to_push.is_empty() {
                                    send_notification(&to_push);
                                } else {
                                    log::info!(
                                        "email_scheduler: {} 急邮件 24h 内已 push 过, 跳过通知",
                                        urgent.len()
                                    );
                                }
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
    let bin = catfish_paths::catfish_email_bin()
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

// ─── P3.3.58 phishing scan 集成 (段 2A) ──────────────────────
//
// in-memory store, 重启丢 (段 2B/2C 再加 persist + UI). 给前端通过
// phishing_for_message 查.
static PHISHING_STORE: OnceLock<Mutex<HashMap<String, PhishingScanResult>>> = OnceLock::new();
fn phishing_store() -> &'static Mutex<HashMap<String, PhishingScanResult>> {
    PHISHING_STORE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// 前端查单封邮件的 phishing 结果. 没扫过 → None.
pub fn phishing_for_message(id: &str) -> Option<PhishingScanResult> {
    phishing_store().lock().ok().and_then(|m| m.get(id).cloned())
}

// ─── P3.3.53 political scan 集成 ──────────────────────────────
//
// 跟 phishing 同 in-memory store pattern, 但 list 阶段不扫 (主题信息少, 真敏感词
// 通常在 body 里). 仅 detail pane 打开时调 email_political_scan_now 拿 body
// 后扫. 默认 enabled=false 不会触发任何 scan / audit.
use super::political_scan::PoliticalScanResult;
static POLITICAL_STORE: OnceLock<Mutex<HashMap<String, PoliticalScanResult>>> = OnceLock::new();
fn political_store() -> &'static Mutex<HashMap<String, PoliticalScanResult>> {
    POLITICAL_STORE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// 前端查单封邮件的 political 结果. 没扫过 → None.
pub fn political_for_message(id: &str) -> Option<PoliticalScanResult> {
    political_store().lock().ok().and_then(|m| m.get(id).cloned())
}

/// 写入 POLITICAL_STORE. 超 500 时丢一半 (跟 phishing 同 LRU 策略).
pub fn political_store_insert(id: &str, result: &PoliticalScanResult) {
    if let Ok(mut store) = political_store().lock() {
        store.insert(id.to_string(), result.clone());
        if store.len() > 500 {
            let keys: Vec<_> = store.keys().take(250).cloned().collect();
            for k in keys {
                store.remove(&k);
            }
        }
    }
}

/// 从 EmailItem 列表抽 our_domains (员工账号自己的域名).
/// 简单从 EmailItem.account 抽 (account 是邮箱地址形式 abc@xxx.cn).
fn our_domains_from_items(items: &[EmailItem]) -> Vec<String> {
    let mut set = std::collections::HashSet::new();
    for it in items {
        // EmailItem 没 account 字段, 走 sender 抽不太准. 暂时用 hardcoded 央企域 +
        // sender domain 都加. 段 2B 改成读 ~/.hermes/auth.json 拿员工真正账号.
        if let Some(domain) = it.sender.rfind('@').map(|i| it.sender[i + 1..].trim_end_matches('>').to_lowercase()) {
            if !domain.is_empty() && domain != "<unknown>" {
                set.insert(domain);
            }
        }
    }
    // 央企常用域兜底
    set.insert("chinatelecom.cn".into());
    set.insert("ffcs.cn".into());
    set.into_iter().collect()
}

/// 段 2A: 对 new_items 跑 light scan + LLM batch 复审, 存 PHISHING_STORE +
/// audit chain 留档. 失败静默, 不阻塞 scheduler.
///
/// 鸿波 6/12 拍板:
///   - 所有邮件都调 LLM 复审 (含未触发规则的)
///   - audit 只记触发规则或 LLM 标 phishing/suspicious 的
async fn scan_phishing_for_new(new_items: &[EmailItem]) {
    if new_items.is_empty() { return; }

    let our_domains = our_domains_from_items(new_items);

    // 1. 规则 light scan
    let mut scans: Vec<PhishingScanResult> = new_items
        .iter()
        .map(|it| phishing_scan::scan_rules_light(&it.id, &it.subject, &it.sender, &our_domains))
        .collect();

    // 2. LLM batch 复审 (鸿波拍板"所有邮件都调")
    let llm_inputs: Vec<LlmReviewInput> = new_items
        .iter()
        .zip(scans.iter())
        .map(|(it, sr)| {
            let summary = if sr.flags.is_empty() {
                "无触发".to_string()
            } else {
                sr.flags.iter()
                    .map(|f| format!("{} ({:?})", f.rule_id, f.severity))
                    .collect::<Vec<_>>()
                    .join(", ")
            };
            LlmReviewInput {
                subject: it.subject.clone(),
                sender: it.sender.clone(),
                rule_summary: summary,
            }
        })
        .collect();

    // P3.5.140 (6/29 鸿波"数据流应该是 companion → hermes → gateway(8999), 不是双路径"):
    // 单路径: Companion → hermes 8642 → gateway 8999 → LLM. 没有 fallback.
    // hermes_api.key 没配 → 跳 LLM 复审 (仅规则结果存盘), 不再 fallback gateway OAuth.
    let hermes_cfg = hermes_api_config::hermes_api_config();
    let (base_url, token) = match hermes_cfg.key.as_deref() {
        Some(k) => (hermes_cfg.url.clone(), k.to_string()),
        None => {
            log::debug!(
                "[phishing] hermes_api.key 没配, 跳 LLM 复审 (~/.catfish/companion.yaml \
                 hermes_api.key 必填). 仅规则结果存盘."
            );
            store_and_audit(new_items, scans).await;
            return;
        }
    };
    // P3.5.29 Phase 4 (6/17 鸿波): 钓鱼复审跟评级共享 chain picker > role > yaml > Err.
    // 员工 chat picker 切 private, phishing scan 跟着走.
    // 数据零出端红线一致 + 客户改 roles.yaml 跟着走.
    //
    // P3.5.139 (6/29 鸿波"都要去除硬编码"): chain 最后一段从 unwrap_or_else
    // 兜底 hardcode 改成 Err. 没拿到 model 说明 picker 没选 + roles.yaml 没 load
    // + yaml 没 override — 这种情况评级本来就该挂, 别静默走 hardcode 字面值.
    //
    // P3.5.140 (6/29 鸿波"TS和Rust 后端 都用硬chain"): 硬 chain 保留, 跟 TS 端
    // DetailPane / Chat 同款. 没 model 不 silent 兜底, 不走 LLM.
    // 8/9 鸿波: **模型只能来自 picker**, 不许有第二个来源。
    //
    // 原来这里是 picker → roles.yaml(rate_fast) → email.rate_model 三级链。
    // 后两级的问题不是"可能指向死模型", 是**员工不知道自己在用哪个模型** ——
    // 后台悄悄换一个来源, 界面上毫无痕迹。而 rate_fast 默认还可能是内网模型
    // (见本文件顶部 P3.5.27 数据零出端红线那段), 公网/内网切换更不该静默发生。
    //
    // 同一条判断已经在 P46 (plugin 侧 model_authority.py) 对 hermes 的会话级
    // override 做过一次: picker 是唯一真源。
    //
    // 拿不到 picker 就**不跑** —— 不猜、不兜底。日志用 warn 不用 debug: 悄悄
    // 不跑跟跑错模型一样难查。
    let model = match picker_config::current_model() {
        Some(m) => m,
        None => {
            log::warn!(
                "[phishing] picker 未选模型 (~/.catfish/picker_model 空/不在), \
                 跳过 LLM 复审。开一次 Companion 的对话 tab 让 picker 落盘即可。"
            );
            store_and_audit(new_items, scans).await;
            return;
        }
    };

    match phishing_llm::batch_llm_review(&llm_inputs, &base_url, &token, &model).await {
        Ok(verdicts) if verdicts.len() == scans.len() => {
            for (s, v) in scans.iter_mut().zip(verdicts.iter()) {
                s.llm_verdict = Some(v.verdict.clone());
                s.llm_reason = Some(v.reason.clone());
                // P3.5.197 (7/7 鸿波军规审判): verdict → severity 分档映射.
                // phishing → High (红 banner "钓鱼嫌疑")
                // suspicious → Medium (橙 banner "可疑")
                // marketing → Low (灰 badge "营销", 不显 banner 避免警报麻木)
                // safe → None (无 banner 无 badge)
                //
                // 只在规则没触发时才用 LLM 判决覆盖 (规则优先, LLM 补漏).
                if s.highest_severity == Severity::None {
                    s.highest_severity = match v.verdict.as_str() {
                        "phishing" => Severity::High,
                        "suspicious" => Severity::Medium,
                        "marketing" => Severity::Low,
                        _ => Severity::None, // safe / 未知 verdict
                    };
                }
            }
            log::info!(
                "[phishing] LLM 复审 {} 封: {} phishing / {} suspicious / {} marketing",
                scans.len(),
                scans.iter().filter(|s| s.llm_verdict.as_deref() == Some("phishing")).count(),
                scans.iter().filter(|s| s.llm_verdict.as_deref() == Some("suspicious")).count(),
                scans.iter().filter(|s| s.llm_verdict.as_deref() == Some("marketing")).count(),
            );
        }
        Ok(verdicts) => {
            log::warn!("[phishing] LLM 返 {} verdict != {} 期望 (仅规则)", verdicts.len(), scans.len());
        }
        Err(e) => {
            log::debug!("[phishing] LLM 复审挂 (仅规则结果): {e}");
        }
    }

    store_and_audit(new_items, scans).await;
}

/// store + audit 提抽成单独函数防多入口重复.
async fn store_and_audit(items: &[EmailItem], scans: Vec<PhishingScanResult>) {
    // store
    if let Ok(mut store) = phishing_store().lock() {
        for s in &scans {
            store.insert(s.message_id.clone(), s.clone());
        }
        // 简单 LRU: 超 500 时丢一半
        if store.len() > 500 {
            let keys: Vec<_> = store.keys().take(250).cloned().collect();
            for k in keys { store.remove(&k); }
        }
    }
    // audit (鸿波拍板"只记触发规则的", persist_audit 内部已 filter)
    for (it, s) in items.iter().zip(scans.iter()) {
        phishing_scan::persist_audit(s, &it.subject, &it.sender).await;
    }
}

/// 评级新邮件. 调 gateway 快速 model. 失败 fallback 全标 Medium (不通知 + 不阻塞).
///
/// CATFISH_EMAIL_RATE=0 → 一律 Urgent (回 step2 行为, 任何新邮件都通知).
/// urgency_cache 上限。**必须 > EmailTab 的 MAX_EMAIL_LIST_LIMIT (500)**。
///
/// 8/15 之前是 200, 而列表一次能拉 500 (email.rs:164 `clamp(1, 500)`)。
/// 于是缓存永远装不下整个列表, 每次超限丢一半, `email_classify_now` 返回的
/// map 就比员工可见的邮件少 —— 前端把"不在 map 里"当成"没评过", 再评一遍,
/// 再撑爆, 再丢一半。**这是 8/15 那个评级永动机的燃料之一**
/// (另外两处: EmailTab 那两个 effect 依赖自己写的 state; store 的
/// setUrgencyMap 是整份覆盖不是 merge。三处一起修才堵得住)。
///
/// 改这个值之前先确认 MAX_EMAIL_LIST_LIMIT 没变大 —— 两者的大小关系是
/// 这里唯一要守的不变量, 见 tests 里的 `urgency_cache_上限必须大于列表上限`。
pub(crate) const URGENCY_CACHE_MAX: usize = 600;

async fn rate_emails(items: &[EmailItem]) -> Vec<Urgency> {
    let cfg = email_config::email_config();
    if !cfg.rate_enabled {
        // 评级关掉 → 一律 Urgent (回 step2 行为, 任何新邮件都通知)
        return vec![Urgency::Urgent; items.len()];
    }
    if items.is_empty() {
        return Vec::new();
    }

    // 8/8: 冷却期内直接跳 —— 上游刚把错误当正文返过, 再敲也是白敲
    // (而且每次经 hermes ×3)。到点自动再试, 见 upstream_error_guard.rs。
    if let Some(left) = upstream_error_guard::cooling_down() {
        log::info!(
            "email_scheduler: 上游冷却中 (还剩 {}s), 本轮跳过评级, 全标 Medium",
            left.as_secs()
        );
        return vec![Urgency::Medium; items.len()];
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


#[cfg(test)]
mod tests {
    use super::*;
    // P3.5.143 (6/30 鸿波): dedup_* 4 个测试共享 static push_history OnceLock,
    // cargo test 默认并行触发 race. #[serial(push_history)] 强制串行.
    use serial_test::serial;
    // ⚠ mod 内部的 super 指 email_scheduler 本身, 不是 services ——
    // 引兄弟模块必须走 crate::services::。
    use crate::services::email_state::{now_epoch_secs, push_history};
    use crate::services::email_types::EmailItem;

    // ── BL-COMPANION-BRIEFING-V2 sub-task 2 (5/20): 通知去重 ──

    // ── 8/15: 评级永动机的两条不变量 ──────────────────────────────

    /// 评级缓存必须装得下整个列表。
    ///
    /// 8/15 之前 URGENCY_CACHE_MAX=200 而列表上限 500: 缓存永远装不下,
    /// 每次超限丢一半, email_classify_now 返回的 map 就比员工可见的邮件少,
    /// 前端把缺的当"没评过"再评一遍 —— 永动机。实测 83 分钟 2470 万 token,
    /// 把百炼周配额从 07:54 重置烧到 09:17 见底。
    ///
    /// 这条红 = 又有人把缓存调小、或者把列表上限调大。
    #[test]
    fn urgency_cache_上限必须大于列表上限() {
        use crate::commands::email::EMAIL_LIST_MAX;
        assert!(
            URGENCY_CACHE_MAX > EMAIL_LIST_MAX as usize,
            "urgency 缓存 {} 装不下列表上限 {} —— 前端会把装不下的当作没评过反复重评",
            URGENCY_CACHE_MAX,
            EMAIL_LIST_MAX,
        );
    }

    /// 本文件里每个 `time::interval(` 都必须显式设 MissedTickBehavior。
    ///
    /// tokio 默认是 Burst: 循环体耗时超过周期时, 漏掉的 tick 会一次性连着补打。
    /// 而这个 scheduler 的循环体里有三个长 await (fetch_unread 子进程 +
    /// rate_emails LLM + scan_phishing_for_new LLM), 上游一慢单轮就超 30s,
    /// 于是循环以"体耗"而不是"周期"的节奏空转 —— 越慢烧得越凶。
    ///
    /// 用读源码的方式测, 是因为 Burst 与否没有可断言的运行时接口
    /// (Interval 不暴露当前 behavior), 而真跑一个 >30s 的 tokio 计时测试
    /// 又太慢。这条守的是"别再忘了写这一行"。
    #[test]
    fn 每个_interval_都设了_missed_tick_behavior() {
        let src = include_str!("email_scheduler.rs");
        let n_interval = src.matches("time::interval(").count();
        let n_behavior = src.matches("set_missed_tick_behavior").count();
        assert!(
            n_interval > 0,
            "没找到 time::interval( —— 测试的判据过期了, 去看看 scheduler 改成什么了"
        );
        assert!(
            n_behavior >= n_interval,
            "有 {} 个 time::interval( 但只有 {} 处 set_missed_tick_behavior —— \
             漏掉的那个会用 tokio 默认的 Burst, 慢的时候连着补打",
            n_interval,
            n_behavior,
        );
    }

    fn mk(id: &str) -> EmailItem {
        EmailItem {
            id: id.to_string(),
            subject: "test".to_string(),
            sender: "x@y.com".to_string(),
        }
    }

    #[test]
    #[serial(push_history)]
    fn dedup_first_push_all_allowed() {
        // 清空 history (其他测试可能污染 OnceLock)
        if let Ok(mut h) = push_history().lock() { h.clear(); }
        let a = mk("id1");
        let b = mk("id2");
        let urgent = vec![&a, &b];
        let allowed = dedup_for_push(&urgent);
        assert_eq!(allowed.len(), 2);
    }

    #[test]
    #[serial(push_history)]
    fn dedup_repeat_push_blocked() {
        // 清空起步
        if let Ok(mut h) = push_history().lock() { h.clear(); }
        let a = mk("dup-id-A");
        let urgent = vec![&a];
        let first = dedup_for_push(&urgent);
        assert_eq!(first.len(), 1);
        // 立即重 push 同 id → 0 allowed
        let second = dedup_for_push(&urgent);
        assert_eq!(second.len(), 0, "24h 内同 id 重复 push 应被拦");
    }

    #[test]
    #[serial(push_history)]
    fn dedup_old_entry_expired_after_24h() {
        // 清空 + 注入一个 25h 前的 entry
        if let Ok(mut h) = push_history().lock() {
            h.clear();
            let old_ts = now_epoch_secs().saturating_sub(25 * 3600);
            h.insert("old-id".to_string(), old_ts);
        }
        let a = mk("old-id");
        let urgent = vec![&a];
        // 25h 前 push 过, 现在应该重新允许 push
        let allowed = dedup_for_push(&urgent);
        assert_eq!(allowed.len(), 1, "24h 前的 entry 已过 dedup window, 应重新 push");
    }

    #[test]
    #[serial(push_history)]
    fn dedup_mixed_some_blocked_some_allowed() {
        if let Ok(mut h) = push_history().lock() {
            h.clear();
            // id-A 1h 前已 push, 应拦; id-B 没 push 过, 应通
            h.insert("id-A".to_string(), now_epoch_secs().saturating_sub(3600));
        }
        let a = mk("id-A");
        let b = mk("id-B");
        let urgent = vec![&a, &b];
        let allowed = dedup_for_push(&urgent);
        assert_eq!(allowed.len(), 1);
        assert_eq!(allowed[0].id, "id-B");
    }
}
