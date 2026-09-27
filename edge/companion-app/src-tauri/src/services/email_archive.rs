//! IMAP 后台归档 —— 每 10 分钟跑一轮 `catfish-email archive` (9/27)。
//!
//! # 为什么要有它
//!
//! 归档 (整封原文落到 ~/.catfish/mail_archive) 和保留策略 (归档后多久从服务器删)
//! 原来只挂在「收信」按钮上: Python 那边 sync_all 的注释写着"收信和后台轮询都会
//! 调", 实际后台轮询 (邮件页每几分钟、email_scheduler 每 10 分钟) 只刷列表。员工
//! 不点收信, 归档就一直不动; 保留策略的清理那一步更是从来没被调用过。
//!
//! # 为什么单独一个循环, 不塞进 email_scheduler
//!
//! email_scheduler 是"新未读 → 评级 → 通知", 它的节奏和失败处理都围着 LLM 转,
//! 而且 poll_secs=0 时整个不起。归档跟那些无关: 只要配了 IMAP 就该跑。
//!
//! 只调 `archive` 不调 `check`: check 还会让 Apple Mail 去服务器收信, 后台定时的
//! 动作不该在员工不知情时惊动别的客户端。
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use tokio::time::{self, Instant, MissedTickBehavior};

use crate::commands::email::email_command;
use crate::commands::imap_credentials;
use crate::services::catfish_paths;

/// 每轮间隔。一轮每个文件夹最多取 20 封原文、清 10 封到期的 (见 imap_sync.py),
/// 10 分钟一轮: 积压能在一天内补上, 又不至于一直占着带宽。
const ROUND_EVERY: Duration = Duration::from_secs(10 * 60);
/// 启动后先等一会儿 —— 刚启动时 hermes、邮件列表、评级都在抢资源。
const FIRST_ROUND_AFTER: Duration = Duration::from_secs(2 * 60);

static STARTED: AtomicBool = AtomicBool::new(false);

/// 邮件组件就绪后调一次 (由 schedule_email_scheduler 带起)。重复调用无副作用。
pub fn schedule_email_archive() {
    if STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        let mut interval = time::interval_at(Instant::now() + FIRST_ROUND_AFTER, ROUND_EVERY);
        // 一轮拖得比周期长 (大附件、慢网) 时, 漏掉的 tick 直接丢, 不连着补打
        interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            interval.tick().await;
            // 只看配置文件, 不碰凭据库 —— 没配 IMAP 的机器每 10 分钟不该去读钥匙串
            if !imap_credentials::is_configured() {
                continue;
            }
            match run_round().await {
                Ok(summary) => log::info!("email_archive: 一轮完成 {summary}"),
                Err(e) => log::warn!("email_archive: 这一轮没跑成, 下一轮再试: {e}"),
            }
        }
    });
}

async fn run_round() -> Result<String, String> {
    let bin =
        catfish_paths::catfish_email_bin().ok_or_else(|| "catfish-email CLI 没装".to_string())?;
    let output =
        tokio::task::spawn_blocking(move || email_command(&bin).args(["archive"]).output())
            .await
            .map_err(|e| format!("spawn_blocking 失败: {e}"))?
            .map_err(|e| format!("catfish-email 调用失败: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("退出码 {:?}", output.status.code())
        } else {
            stderr
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}
