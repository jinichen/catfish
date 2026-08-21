//! `email_scheduler` 的单测。
//!
//! 8/15 拆出来: 加了两条不变量测试后 email_scheduler.rs 到 801 行, 越过
//! CLAUDE.md §1 的 800 行红线。产品代码和测试里, 先搬测试 —— 它是自足的
//! (只靠 super::* 和两个兄弟模块), 搬走不改变任何生产行为。
//!
//! ⚠ `每个_interval_都设了_missed_tick_behavior` 里的
//! `include_str!("email_scheduler.rs")` 走的是**本文件所在目录**的相对路径,
//! 同目录所以搬过来照样读得到那份产品代码。

use super::*;
// P3.5.143 (6/30 鸿波): dedup_* 4 个测试共享 static push_history OnceLock,
// cargo test 默认并行触发 race. #[serial(push_history)] 强制串行.
use serial_test::serial;
// ⚠ 这里的 super 指 email_scheduler (靠 #[path] 挂进去的), 不是 services ——
// 引兄弟模块仍要走 crate::services::。
use crate::services::email_state::{now_epoch_secs, push_history};
use crate::services::email_types::EmailItem;

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
/// ⚠ 只数**产品代码**, 必须先剥掉两样东西, 否则判据比真事宽:
///
///   1. `#[cfg(test)]` 之后的部分 —— 本测试自己就写着
///      `"time::interval("` 和 `"set_missed_tick_behavior"` 两个字符串
///      字面量, 加上 panic 文案里还各有一份。第一版没剥, 实测数成
///      **5 个 interval / 3 处 behavior**, 而真实代码里是 1:1, 于是无辜地红。
///   2. 注释行 —— 上面那段解释 Burst 的注释里就有 `MissedTickBehavior`。
///
/// 一条用来防"判据写错"的测试, 第一版自己就栽在判据上。留着这段是提醒:
/// 凡是拿源码当输入的检查, 先问一句"它会不会把自己算进去"。
#[test]
fn 每个_interval_都设了_missed_tick_behavior() {
    let full = include_str!("email_scheduler.rs");
    let prod = full.split("#[cfg(test)]").next().unwrap_or(full);
    let code: String = prod
        .lines()
        .filter(|l| !l.trim_start().starts_with("//"))
        .collect::<Vec<_>>()
        .join("\n");

    let n_interval = code.matches("time::interval(").count();
    let n_behavior = code.matches("set_missed_tick_behavior").count();
    assert!(
        n_interval > 0,
        "产品代码里没找到 time::interval( —— 判据过期了, 去看看 scheduler 改成什么了"
    );
    assert!(
        n_behavior >= n_interval,
        "产品代码里有 {} 个 time::interval( 但只有 {} 处 set_missed_tick_behavior \
         —— 漏掉的那个会用 tokio 默认的 Burst, 慢的时候连着补打",
        n_interval,
        n_behavior,
    );
}

fn mk(id: &str) -> EmailItem {
    EmailItem {
        id: id.to_string(),
        subject: "test".to_string(),
        sender: "x@y.com".to_string(),
        snippet: String::new(),
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
