//! 上游把错误当正文返回时的识别 + 冷却 (8/8).
//!
//! # 病
//!
//! 8/8 百炼 token-plan 一周配额耗尽 (429 insufficient_quota, 6 天后才恢复)。
//! hermes 的 agent loop 重试三次之后, 把自己的错误信息当成"助手的回答"返回:
//!
//! ```text
//!     HTTP 200
//!     choices[0].message.content = "API call failed after 3 retries:
//!                                   An error occurred during streaming"
//! ```
//!
//! email_scheduler::call_rate_llm 拿到的就是这个。它 status 是 200, 于是
//! 一路走到 parse_urgencies, 报「没 JSON array」, 而那条错误是
//! `log::debug!` —— **生产日志级别是 INFO, 它永远不会出现**。然后静默
//! fallback 成 Medium。
//!
//! 后果: 每 30 秒重来一次, 每次经 hermes 还要 ×3 次重试, 一烧就是 6 天,
//! 而日志上一个字都看不到。前台侧同款问题见 lib/upstreamErrorGuard.ts
//! (那边是把错误当草稿填进正文框, 更显眼)。
//!
//! # 为什么网关的检测盖不到
//!
//! 网关有同款 (app.py:2081 BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT, 6/1),
//! 但那只盖"经过网关"的调用。这段错误文本是 **hermes 自己**产生的,
//! Companion → hermes 这一跳网关不在路上。
//!
//! # 冷却为什么是"分钟"而不是"到配额恢复"
//!
//! content 是通用文案 ("An error occurred during streaming"), **它并没有说
//! 配额**。真配额耗尽 (6 天) 和上游临时抽风 (几秒) 在这一层分不出来。
//! 所以不赌: 命中就退避十分钟, 到点自己再试一次。配额没恢复就再冷却十分钟,
//! 恢复了下一次就正常 —— 自愈, 不需要人干预, 也不会把一次网络抖动变成
//! 六天不评级。
//!
//! 十分钟 = 跳过 20 个 tick。够把"每 30 秒 ×3 次"的浪费砍掉 95%,
//! 又不至于让员工感觉评级"坏了"。

use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

/// 命中就冷却这么久。见文件头"为什么是分钟"。
const COOLDOWN: Duration = Duration::from_secs(600);

/// 超过这个长度就不当错误看 —— 真错误都很短, 长文本更可能是 LLM 正常复读
/// 这些词 (员工真问"API call failed 是什么意思"的时候)。
/// 跟前台 lib/upstreamErrorGuard.ts 的 MAX_ERROR_LEN 保持同值。
const MAX_ERROR_LEN: usize = 500;

/// 上游 (hermes / LiteLLM / 私有 LLM 包装层) 把错误当 content 返时的特征词。
/// 跟网关 app.py:2086 和前台 upstreamErrorGuard.ts 同源 —— 三处要一起改。
const MARKERS: &[&str] = &[
    "API call failed",
    "retries exhausted",
    "during streaming",
];

fn cooldown_until() -> &'static Mutex<Option<Instant>> {
    static CELL: OnceLock<Mutex<Option<Instant>>> = OnceLock::new();
    CELL.get_or_init(|| Mutex::new(None))
}

/// content 是不是"上游错误伪装成的正文"。
///
/// 两个条件同时成立才算: 命中特征词 **且** 长度在上限内。只看词会误伤。
pub fn is_upstream_error_as_content(content: &str) -> bool {
    if content.len() >= MAX_ERROR_LEN {
        return false;
    }
    let lower = content.to_lowercase();
    MARKERS.iter().any(|m| lower.contains(&m.to_lowercase()))
        // "after N retries" 这类带数字的单独认一下, 不写正则避免多拉一个依赖
        || (lower.contains("after ") && lower.contains(" retries"))
}

/// 记一次命中, 开始冷却。返回冷却时长, 给调用方打日志用。
pub fn mark_upstream_error(where_: &str, content: &str) -> Duration {
    if let Ok(mut g) = cooldown_until().lock() {
        *g = Some(Instant::now() + COOLDOWN);
    }
    log::warn!(
        "[{where_}] 上游把错误当正文返了 (HTTP 200 + 错误文本), 不是模型的输出。\
         常见真因: 上游配额耗尽 / 服务挂 / 重试用尽。\
         接下来 {} 秒跳过 LLM 调用, 到点自动再试。原文: {}",
        COOLDOWN.as_secs(),
        content.chars().take(200).collect::<String>(),
    );
    COOLDOWN
}

/// 还在冷却里吗。是的话返剩余时长, 调用方应该跳过这次 LLM 调用。
pub fn cooling_down() -> Option<Duration> {
    let g = cooldown_until().lock().ok()?;
    let until = (*g)?;
    let now = Instant::now();
    if until > now {
        Some(until - now)
    } else {
        None
    }
}

/// 只给测试用 —— 清掉冷却状态。
#[cfg(test)]
pub fn reset_for_test() {
    if let Ok(mut g) = cooldown_until().lock() {
        *g = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 认得出实际撞到的那句() {
        assert!(is_upstream_error_as_content(
            "API call failed after 3 retries: An error occurred during streaming"
        ));
    }

    #[test]
    fn 正常的评级输出不误伤() {
        assert!(!is_upstream_error_as_content(r#"["急","中","低"]"#));
        assert!(!is_upstream_error_as_content("急"));
        assert!(!is_upstream_error_as_content(""));
    }

    #[test]
    fn 长文本不当错误看() {
        // 员工真问"API call failed 是什么意思", LLM 的正常长回答不该被判成错误
        let long = format!("API call failed 的意思是{}", "详细解释".repeat(200));
        assert!(long.len() >= MAX_ERROR_LEN);
        assert!(!is_upstream_error_as_content(&long));
    }

    #[test]
    fn 冷却会到期() {
        reset_for_test();
        assert!(cooling_down().is_none(), "初始不该在冷却里");
        mark_upstream_error("test", "API call failed after 3 retries");
        let left = cooling_down().expect("标记后应该在冷却里");
        assert!(left <= COOLDOWN && left > Duration::from_secs(0));
        reset_for_test();
        assert!(cooling_down().is_none());
    }
}
