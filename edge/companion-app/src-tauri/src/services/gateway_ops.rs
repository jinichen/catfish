//! 11/3: Companion 发出的 hermes 网关启停 (`gateway start / stop / restart`) 一次只做一个。
//!
//! # 病
//!
//! 11/3 鸿波 Windows 机: Companion 自己发起的一次 `gateway restart` 还在走 (Windows 上是先停、
//! 等旧网关彻底退、等端口空、再起、再等新网关就绪, 最长一两分钟), 这期间又有人 `gateway start`
//! 起了一个网关 —— restart 的「等旧网关退干净」把这个新的当成没退的旧网关强杀了。
//! Companion 里会发这几条命令的地方有七八处 (切模型兜底、插件更新、专家上下线、打开 Companion
//! 时补拉、换 hermes 目录前后 ...), 互相之间原来不排队, 撞上就是同样的事。
//!
//! # 做法
//!
//! 所有这些地方都经 [`serialized`] 跑: 前一个没做完 (含 restart 等新网关就绪那段), 后一个排队等。
//! 排到时网关已经被前一个拉起来了, `gateway start` 自己会说「已在跑」什么都不做。
//! 员工在终端里手敲的命令管不到 —— 那不经过 Companion。

use std::sync::{Mutex, TryLockError};

static GATEWAY_LIFECYCLE: Mutex<()> = Mutex::new(());

/// 是不是会启停网关的 hermes 命令 (`gateway start|stop|restart`)。
pub(crate) fn is_gateway_lifecycle(args: &[&str]) -> bool {
    matches!(args, ["gateway", "start" | "stop" | "restart", ..])
}

/// 排队跑 `f`: 前一个网关启停没做完就等它。`what` 只用来写日志。
pub(crate) fn serialized<T>(what: &str, f: impl FnOnce() -> T) -> T {
    let _turn = match GATEWAY_LIFECYCLE.try_lock() {
        Ok(guard) => guard,
        Err(TryLockError::WouldBlock) => {
            log::info!("[hermes-gateway] {what}: 前一个网关启停还没做完, 排队等它");
            GATEWAY_LIFECYCLE.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
        }
        // 前一个在里面 panic 了: 锁本身没坏, 接着用
        Err(TryLockError::Poisoned(poisoned)) => poisoned.into_inner(),
    };
    f()
}

// ── hermes v0.23: 有对话在跑就拒绝停 / 重启 (11/06) ─────────────────────────────
//
// v0.23 的 `gateway stop|restart` 先数进行中的对话 / 定时任务 / 后台子任务, 有就拒绝 (exit 1, 打
// 「Refusing to restart the gateway: N live session(s) would be interrupted.」), 要 `--yes` 才硬停
// (H23 hermes_cli/gateway_lifecycle_guards.py)。v0.22 没有这道闸, 也不认 `--yes`。
//
// Companion 的做法: 改了配置要重启的 (插件更新、专家上下线、改专家设定) 被拒就**不回滚、不 kickstart 硬杀**,
// 后台等对话收尾 (hermes_idle) 再试, 直到重启成功; 网关本来就坏了的兜底重启和换安装目录前停网关
// 可以打断, 带 `--yes` (见 [`interrupt_flag`])。

/// 装的 hermes 有没有「有对话就拒绝停 / 重启」那道闸 (v0.23+)。
pub(crate) fn guards_live_sessions(hermes_home: &std::path::Path) -> bool {
    hermes_home.join("hermes-agent").join("hermes_cli").join("gateway_lifecycle_guards.py").is_file()
}

/// 要打断进行中的对话时追加的参数 (v0.22 不认 `--yes`, 不加)。
pub(crate) fn interrupt_flag(hermes_home: &std::path::Path) -> Option<&'static str> {
    guards_live_sessions(hermes_home).then_some("--yes")
}

/// 命令输出是不是「有对话在跑, 拒绝停 / 重启」。
pub(crate) fn is_busy_refusal(text: &str) -> bool {
    text.contains("Refusing to restart the gateway") || text.contains("Refusing to stop the gateway")
}

/// 被拒后最多再试几次 (每次先等对话收尾, 等满 hermes_idle::MAX_WAIT 还忙就隔一分钟再看)。
const BUSY_RETRIES: u32 = 8;

/// 后台线程: 等对话收尾再跑 `attempt`, 还被拒就过一分钟再来, 直到成功 / 别的错 / 试满。
pub(crate) fn retry_when_idle(
    what: &'static str,
    attempt: impl Fn() -> Result<(), String> + Send + 'static,
) {
    let spawned = std::thread::Builder::new().name("hermes-gateway-retry".into()).spawn(move || {
        for _ in 0..BUSY_RETRIES {
            crate::services::hermes_idle::wait_until_idle(crate::services::hermes_idle::MAX_WAIT, what);
            // attempt 自己经过 serialized (run_hermes / run_hermes_cli 都是), 这里不能再套一层 —— 锁不可重入
            match attempt() {
                Ok(()) => {
                    log::info!("[hermes-gateway] {what}: 对话收尾后完成");
                    return;
                }
                Err(e) if is_busy_refusal(&e) => std::thread::sleep(std::time::Duration::from_secs(60)),
                Err(e) => {
                    log::warn!("[hermes-gateway] {what} 失败: {e}");
                    return;
                }
            }
        }
        log::warn!("[hermes-gateway] {what}: 一直有对话在进行, 试了 {BUSY_RETRIES} 次还没做成, 下次打开 Companion 再做");
    });
    if let Err(e) = spawned {
        log::warn!("[hermes-gateway] 起不了等空闲再{what}的线程 ({e})");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::time::Duration;

    #[test]
    fn 只认启停三条命令() {
        assert!(is_gateway_lifecycle(&["gateway", "restart"]));
        assert!(is_gateway_lifecycle(&["gateway", "start"]));
        assert!(is_gateway_lifecycle(&["gateway", "stop", "--all"]));
        assert!(!is_gateway_lifecycle(&["gateway", "status"]));
        assert!(!is_gateway_lifecycle(&["profile", "list"]));
        assert!(!is_gateway_lifecycle(&[]));
    }

    #[test]
    fn 两个线程同时启停_不会重叠() {
        let inside = Arc::new(AtomicUsize::new(0));
        let max_seen = Arc::new(AtomicUsize::new(0));
        let workers: Vec<_> = (0..4)
            .map(|_| {
                let (inside, max_seen) = (inside.clone(), max_seen.clone());
                std::thread::spawn(move || {
                    serialized("测试", || {
                        let now = inside.fetch_add(1, Ordering::SeqCst) + 1;
                        max_seen.fetch_max(now, Ordering::SeqCst);
                        std::thread::sleep(Duration::from_millis(30));
                        inside.fetch_sub(1, Ordering::SeqCst);
                    })
                })
            })
            .collect();
        for w in workers {
            w.join().unwrap();
        }
        assert_eq!(max_seen.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn 认得出_v023_的拒绝() {
        assert!(is_busy_refusal("Refusing to restart the gateway: 2 live session(s) would be interrupted."));
        assert!(is_busy_refusal("✗ Refusing to stop the gateway: 1 live session(s) would be interrupted."));
        assert!(!is_busy_refusal("Gateway restarted"));
        let t = tempfile::tempdir().unwrap();
        assert_eq!(interrupt_flag(t.path()), None, "v0.22: 不认 --yes, 不加");
        let guards = t.path().join("hermes-agent").join("hermes_cli");
        std::fs::create_dir_all(&guards).unwrap();
        std::fs::write(guards.join("gateway_lifecycle_guards.py"), "").unwrap();
        assert_eq!(interrupt_flag(t.path()), Some("--yes"));
    }
}
