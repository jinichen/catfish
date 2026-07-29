//! 测试专用: 全进程唯一的环境变量锁 (P3.5.80 · 7/28).
//!
//! ── 在防什么 ──────────────────────────────────────────────────────
//!
//! 好几个模块的单测要把 HOME / CATFISH_HOME 指到临时目录, 而 `std::env` 是
//! **进程全局**的, cargo test 又默认多线程并发跑. 于是 A 模块刚把 HOME 设成
//! tmpA, B 模块就设成了 tmpB —— A 的测试跑到一半去 tmpB 里找自己的 db,
//! 报出来是 "state.db 不存在" / "attempt to write a readonly database" /
//! "disk I/O error" 这类看着像磁盘坏了的错.
//!
//! 这些模块**都意识到了**这个问题, 各自的注释里写着
//!     "HOME env 是进程级共享, 多 test 并发会互相覆盖 —— 用一个 mutex 串行化"
//! 但每个模块**各建了一把 `static ENV_LOCK`**:
//!     attachments.rs / session_write.rs / drafts.rs / mcp_oauth.rs / tasks_history.rs
//! 五把锁互不相干, 只能序列化各自模块内部, 跨模块照旧对撞.
//!
//! 症状是**随机**的: 取决于线程调度, 单独跑某个模块永远绿, 全量跑偶尔红.
//! 这种测试比没有还糟 —— 下次真有 bug 时, 红了也会被当成"又是那个老毛病".
//!
//! ── 用法 ──────────────────────────────────────────────────────────
//!
//! 凡是要改 env 的测试, 开头拿一次这个锁, 并把 guard 持有到测试结束:
//!
//! ```text
//! fn setup_test_env() -> (TempDir, std::sync::MutexGuard<'static, ()>) {
//!     let guard = crate::util::test_env::env_lock();
//!     let tmp = TempDir::new().unwrap();
//!     std::env::set_var("HOME", tmp.path());
//!     (tmp, guard)
//! }
//! ```
//!
//! guard 一定要返回给调用方持有 —— 只在 setup 里拿完就 drop 等于没锁.
//!
//! 注: 只读 env 的测试不需要拿锁. 它们看到的 HOME 可能是别的测试的临时目录,
//! 但只要断言不依赖具体路径前缀就没影响.

use std::sync::{Mutex, MutexGuard};

/// 全进程唯一的 env 锁.
///
/// 中毒 (某个测试 panic 时持有锁) 直接 `into_inner()` 继续 —— 锁只是用来
/// 排队的, 里面没有需要保护的不变量; 因为一个测试炸了就让后面全部 panic
/// 反而掩盖真正的失败原因.
pub fn env_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::new(());
    LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lock_is_reentrant_across_sequential_calls() {
        // 顺序拿两次不应该死锁 (第一次的 guard 已 drop).
        {
            let _g = env_lock();
        }
        let _g = env_lock();
    }

    #[test]
    fn lock_actually_serializes() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        use std::sync::Arc;

        // 同时进临界区的线程数必须始终 ≤ 1. 若 5 个模块各用各的锁,
        // 这个断言就会挂 —— 正是线上那 4 条测试失败的原因.
        let inside = Arc::new(AtomicUsize::new(0));
        let max_seen = Arc::new(AtomicUsize::new(0));
        let mut hs = Vec::new();
        for _ in 0..8 {
            let inside = Arc::clone(&inside);
            let max_seen = Arc::clone(&max_seen);
            hs.push(std::thread::spawn(move || {
                let _g = env_lock();
                let n = inside.fetch_add(1, Ordering::SeqCst) + 1;
                max_seen.fetch_max(n, Ordering::SeqCst);
                std::thread::sleep(std::time::Duration::from_millis(2));
                inside.fetch_sub(1, Ordering::SeqCst);
            }));
        }
        for h in hs {
            h.join().unwrap();
        }
        assert_eq!(
            max_seen.load(Ordering::SeqCst),
            1,
            "同时有多个线程进了临界区 —— 锁没起作用"
        );
    }
}
