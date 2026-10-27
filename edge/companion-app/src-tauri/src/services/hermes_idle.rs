//! 10/27: Companion 要重启 hermes 网关 (升级后换解释器 / 插件更新 / 补依赖) 之前, 先等手头的回答写完。
//!
//! Windows 上 `hermes gateway restart` 是**先停再起** (hermes_cli/gateway_windows.py `restart()`),
//! 停的时候只给进行中的 /v1 回答留 `cron_drain_timeout` 30 秒, 过了直接打断。mac 走 SIGUSR1,
//! hermes 自己会等对话收尾 (最多 30 分钟), 不需要这里。
//!
//! 现场 (10/27 鸿波 Windows 机): 08:59 和 11:45 两次「回答说到一半停了」, 都是 Companion 刚启动、
//! 员工刚发出消息, 启动流程里重写开机自启脚本 → 重启网关 → `Gateway drain timed out after 30.1s
//! with ... 1 api_server run(s); interrupting remaining work`。
//!
//! 「忙」的判据, 任一成立就等:
//!   · Companion 自己有流式请求在收 (http_proxy 的 ACTIVE_STREAMS) —— 员工正在等的那条回答
//!   · hermes 的 gateway_state.json 说有进行中的工作 (`active_agents`, 含微信 / 定时任务) 或
//!     api_server 有进行中的 run —— 只信 2 分钟内写过的 (hermes 每 30 秒心跳一次; 进程死了文件
//!     还留着, 不能让一份旧文件把重启拖 30 分钟)
//! 连续两次 (间隔 5 秒) 都不忙才算空下来; 最多等 30 分钟 (跟 hermes 自己的 restart_after_turn_timeout
//! 一样), 到点照样重启 —— 卡死的回合不能让新版本永远不生效。

// mac 上 hermes 自己等对话收尾, 这里只有 Windows / Linux 的重启路径在用
#![cfg_attr(target_os = "macos", allow(dead_code))]

use std::path::Path;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use serde_json::Value;

const POLL: Duration = Duration::from_secs(5);
pub const MAX_WAIT: Duration = Duration::from_secs(30 * 60);
/// gateway_state.json 超过这么久没更新就不信它说的「忙」
const STATE_FRESH_SECS: i64 = 120;

/// hermes 网关状态文件说它在忙吗。`now` 由调用方给, 测试好写。
pub(crate) fn state_says_busy(state: &Value, now: DateTime<Utc>) -> Option<String> {
    let fresh = |v: Option<&Value>| {
        v.and_then(Value::as_str)
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
            .map(|t| (now - t.with_timezone(&Utc)).num_seconds() <= STATE_FRESH_SECS)
            .unwrap_or(false)
    };
    let api = state.pointer("/platforms/api_server");
    let api_fresh = fresh(api.and_then(|a| a.get("updated_at")))
        || fresh(state.pointer("/platforms/api_server/metrics/last_heartbeat"));
    if !(api_fresh || fresh(state.get("updated_at"))) {
        return None;
    }
    let agents = state.get("active_agents").and_then(Value::as_u64).unwrap_or(0);
    if agents > 0 {
        return Some(format!("hermes 有 {agents} 个进行中的任务"));
    }
    let runs = api
        .and_then(|a| a.pointer("/metrics/active_runs"))
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if api_fresh && runs > 0 {
        return Some(format!("hermes 有 {runs} 个回答在写"));
    }
    None
}

fn state_busy_at(home: &Path) -> Option<String> {
    let text = std::fs::read_to_string(home.join("gateway_state.json")).ok()?;
    let state: Value = serde_json::from_str(text.trim_start_matches('\u{feff}')).ok()?;
    state_says_busy(&state, Utc::now())
}

/// 现在忙不忙; 忙就说为什么 (写日志用)。
pub fn busy_reason() -> Option<String> {
    let streams = crate::commands::http_proxy::active_stream_count();
    if streams > 0 {
        return Some(format!("Companion 有 {streams} 条回答在收"));
    }
    crate::services::catfish_paths::hermes_home().and_then(|h| state_busy_at(&h))
}

/// 阻塞到 hermes 空下来 (连续两次不忙) 或等满 `max`。返回是否真空下来了。只在后台线程里调。
pub fn wait_until_idle(max: Duration, what: &str) -> bool {
    let start = Instant::now();
    let mut idle_streak = 0;
    let mut logged = false;
    loop {
        match busy_reason() {
            None => {
                idle_streak += 1;
                if idle_streak >= 2 {
                    if logged {
                        log::info!("[hermes-idle] 空下来了, 等了 {}s, 现在{what}", start.elapsed().as_secs());
                    }
                    return true;
                }
            }
            Some(reason) => {
                idle_streak = 0;
                if !logged {
                    log::info!("[hermes-idle] {reason}, 先不{what}, 等它写完 (最多 {} 分钟)", max.as_secs() / 60);
                    logged = true;
                }
            }
        }
        if start.elapsed() >= max {
            log::warn!("[hermes-idle] 等了 {} 分钟还在忙, 照样{what}", max.as_secs() / 60);
            return false;
        }
        std::thread::sleep(POLL);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn now() -> DateTime<Utc> {
        DateTime::parse_from_rfc3339("2026-10-27T03:44:00+00:00").unwrap().with_timezone(&Utc)
    }

    #[test]
    fn 新鲜的状态文件说有任务就算忙() {
        let s = json!({"updated_at": "2026-10-27T03:43:30+00:00", "active_agents": 1});
        assert!(state_says_busy(&s, now()).is_some());
    }

    #[test]
    fn api_server_有回答在写也算忙() {
        let s = json!({"active_agents": 0, "updated_at": "2026-10-27T01:00:00+00:00",
            "platforms": {"api_server": {"updated_at": "2026-10-27T03:43:50+00:00",
                "metrics": {"active_runs": 1}}}});
        assert!(state_says_busy(&s, now()).is_some());
    }

    #[test]
    fn 旧文件不信_进程死了别拖三十分钟() {
        let s = json!({"updated_at": "2026-10-27T03:30:00+00:00", "active_agents": 2,
            "platforms": {"api_server": {"updated_at": "2026-10-27T03:30:00+00:00",
                "metrics": {"active_runs": 1}}}});
        assert!(state_says_busy(&s, now()).is_none());
    }

    #[test]
    fn 都是零就不忙() {
        let s = json!({"updated_at": "2026-10-27T03:43:59+00:00", "active_agents": 0,
            "platforms": {"api_server": {"updated_at": "2026-10-27T03:43:59+00:00",
                "metrics": {"active_runs": 0}}}});
        assert!(state_says_busy(&s, now()).is_none());
    }

    #[test]
    fn 读文件_带_bom_也认() {
        let dir = tempfile::tempdir().unwrap();
        let fresh = Utc::now().to_rfc3339();
        std::fs::write(
            dir.path().join("gateway_state.json"),
            format!("\u{feff}{{\"updated_at\": \"{fresh}\", \"active_agents\": 1}}"),
        )
        .unwrap();
        assert!(state_busy_at(dir.path()).is_some());
        assert!(state_busy_at(&dir.path().join("nope")).is_none());
    }
}
