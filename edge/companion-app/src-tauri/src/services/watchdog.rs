//! Background watchdog: 监控关键子进程死活, 死了自动 respawn.
//!
//! # 为啥需要 (踩过坑 2026-04-28 鸿波 demo)
//!
//! tool-bridge 内部的 `skill_watcher` / `config_watcher` 检测到员工
//! 文件变化时主动 `os._exit(0)`, 假设 "Companion autostart 会 respawn".
//!
//! 但 `services::autostart::schedule_autostart` **只在 app 启动时跑一次**
//! (Tauri setup hook 里), 中途子进程死了不会自动重启 → 员工卡死, 模型
//! 后续工具调用全部 Connection refused, 但模型不知道是 tool-bridge 死了,
//! 反而幻觉 "保存失败" 让员工重做.
//!
//! 实际场景:
//! ```text
//! 12:51:50  skill_watcher: SKILL.md 新增 +1
//! 12:52:16  skill_watcher: 退出 tool-bridge, Companion autostart 会 respawn
//!           [autostart 没监听这个事件, 不 respawn]
//!           [tool-bridge 进程死掉, socket 残留]
//! 12:52:17~ 模型调 write_file → Connection refused → 模型幻觉 "保存失败"
//! ```
//!
//! # 设计
//!
//! 一个 background tokio task, 每 `TICK_INTERVAL_SECS` 秒:
//!   1. 检查 gateway / tool-bridge 的 PID 文件是不是还活
//!      (用 `read_pid_file_alive_strict`, cmdline 检测防 PID 复用)
//!   2. 死了 → 调 `autostart::ensure_*_running` respawn
//!   3. 记录连续失败次数, 连续 `MAX_CONSECUTIVE_FAILURES` 次失败 → 进 backoff
//!      `BACKOFF_AFTER_FAILURES_SECS` 秒, 防疯重启 (配置坏了的场景)
//!
//! # 不监控的服务
//!
//! - **chrome**: 员工有意启动的 (要看到工作流), 不该 always-on. 只在
//!   员工/模型主动调 chrome_launch 时才起. 死了不自动 respawn.
//! - **local-search**: watcher, 死了重启没问题, 但优先级低. Phase 2 加.
//!
//! # 跟 autostart 的关系
//!
//! - `schedule_autostart()`: app 启动时跑一次, 拉起 gateway + tool-bridge
//! - `schedule_watchdog()`: app 启动时跑一次, 之后每 5s 监控
//! 两者并存: autostart 负责"冷启动", watchdog 负责"运行期 self-heal".

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tokio::time;

use crate::services::{autostart, catfish_paths, process};

const TICK_INTERVAL_SECS: u64 = 5;
const MAX_CONSECUTIVE_FAILURES: u32 = 5;
const BACKOFF_AFTER_FAILURES_SECS: u64 = 180;

/// 每个被监控服务的健康度状态.
///
/// 用 atomic + mutex 让 Send + Sync 给 tokio task 用.
#[derive(Default)]
struct ServiceHealth {
    /// 连续 spawn 失败次数. respawn 成功就清零.
    consecutive_failures: AtomicU32,
    /// 进 backoff 后, 这个时间点之前不再 respawn.
    backoff_until: Mutex<Option<Instant>>,
}

impl ServiceHealth {
    /// 记录一次 spawn 失败. 达到阈值进 backoff. 返回是否进 backoff.
    fn record_failure(&self, service_name: &str) -> bool {
        let prev = self.consecutive_failures.fetch_add(1, Ordering::SeqCst);
        let now = prev + 1;
        if now >= MAX_CONSECUTIVE_FAILURES {
            let mut guard = self.backoff_until.lock().unwrap();
            *guard = Some(Instant::now() + Duration::from_secs(BACKOFF_AFTER_FAILURES_SECS));
            log::warn!(
                "watchdog: {} 连续 {} 次 spawn 失败 → 进 backoff {}s. 配置/路径可能有问题, 先暂停自动重启.",
                service_name, MAX_CONSECUTIVE_FAILURES, BACKOFF_AFTER_FAILURES_SECS
            );
            true
        } else {
            log::warn!(
                "watchdog: {} 第 {}/{} 次 respawn 失败",
                service_name, now, MAX_CONSECUTIVE_FAILURES
            );
            false
        }
    }

    /// respawn 成功, 清掉所有失败计数 + backoff.
    fn record_success(&self, service_name: &str) {
        let prev = self.consecutive_failures.swap(0, Ordering::SeqCst);
        let mut guard = self.backoff_until.lock().unwrap();
        *guard = None;
        if prev > 0 {
            log::info!(
                "watchdog: {} respawn 成功 (之前累计失败 {} 次, 已清零)",
                service_name, prev
            );
        }
    }

    /// 是否在 backoff 期间. backoff 到期会自动解除 (下次检查放行).
    fn is_in_backoff(&self) -> bool {
        let guard = self.backoff_until.lock().unwrap();
        match *guard {
            Some(until) if Instant::now() < until => true,
            _ => false,
        }
    }
}

/// 检查 PID 文件对应的进程是否真的活着 (cmdline 防复用).
fn is_alive(pid_file_getter: impl Fn() -> Option<std::path::PathBuf>, cmdline: &str) -> bool {
    let Some(pid_file) = pid_file_getter() else {
        return false;
    };
    process::read_pid_file_alive_strict(&pid_file, cmdline).is_some()
}

/// app 启动时调一次, 起背景 watchdog task.
///
/// 跟 [`autostart::schedule_autostart`] 互补: autostart 负责冷启动拉起,
/// watchdog 负责运行期监控 + self-heal.
pub fn schedule_watchdog() {
    tauri::async_runtime::spawn(async move {
        let gateway_health = ServiceHealth::default();
        let tool_bridge_health = ServiceHealth::default();
        let mut interval = time::interval(Duration::from_secs(TICK_INTERVAL_SECS));
        // 第一 tick 立即返回, autostart 应该已经处理过了, 跳过
        interval.tick().await;
        log::info!(
            "watchdog: 启动, tick={}s, max_failures={}, backoff={}s",
            TICK_INTERVAL_SECS, MAX_CONSECUTIVE_FAILURES, BACKOFF_AFTER_FAILURES_SECS
        );
        loop {
            interval.tick().await;

            // gateway
            if !gateway_health.is_in_backoff()
                && !is_alive(catfish_paths::gateway_pid_file, "catfish_gateway")
            {
                log::info!("watchdog: gateway dead, respawning");
                autostart::ensure_gateway_running().await;
                if is_alive(catfish_paths::gateway_pid_file, "catfish_gateway") {
                    gateway_health.record_success("gateway");
                } else {
                    gateway_health.record_failure("gateway");
                }
            }

            // tool-bridge
            if !tool_bridge_health.is_in_backoff()
                && !is_alive(catfish_paths::tool_bridge_pid_file, "catfish_tool_bridge")
            {
                log::info!("watchdog: tool-bridge dead, respawning");
                autostart::ensure_tool_bridge_running().await;
                // tool-bridge 启动慢 (~3-5s import hermes), 给一点时间再检查
                time::sleep(Duration::from_secs(2)).await;
                if is_alive(catfish_paths::tool_bridge_pid_file, "catfish_tool_bridge") {
                    tool_bridge_health.record_success("tool-bridge");
                } else {
                    tool_bridge_health.record_failure("tool-bridge");
                }
            }
        }
    });
}
