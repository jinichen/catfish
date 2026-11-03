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
}
