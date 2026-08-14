//! 邮件调度的领域类型: EmailItem 与 Urgency。
//!
//! 2026-08-15 从 email_scheduler.rs 切出来 (1196 行超限)。纯搬迁, 逻辑一行未改。
//!
//! 单独一层是为了理顺方向: call_rate_llm / parse_urgencies 的返回类型就是
//! Urgency。如果 Urgency 留在 email_scheduler.rs, email_llm.rs 就得反过来
//! import 前门文件。现在是 types ← state ← llm/notify ← email_scheduler。

use serde::Deserialize;

#[derive(Deserialize, Clone, Debug)]
pub(crate) struct EmailItem {
    pub(crate) id: String,
    pub(crate) subject: String,
    pub(crate) sender: String,
}

/// LLM 评级结果. fallback 'medium' 时不通知, 不阻塞.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Urgency {
    Urgent,   // 急: 系统通知 + (step4 桌宠主动闲聊)
    Medium,   // 中: 静默, 卡片显但不打扰
    Low,      // 低: 静默 (newsletter / 自动通知类)
}

impl Urgency {
    pub(crate) fn from_label(s: &str) -> Self {
        let s = s.trim().to_lowercase();
        if s.contains("急") || s.contains("urgent") || s.contains("high") {
            Self::Urgent
        } else if s.contains("低") || s.contains("low") || s.contains("noise") {
            Self::Low
        } else {
            Self::Medium
        }
    }

    pub(crate) fn is_urgent(&self) -> bool {
        matches!(self, Self::Urgent)
    }

    pub(crate) fn as_label(&self) -> &'static str {
        match self {
            Self::Urgent => "急",
            Self::Medium => "中",
            Self::Low => "低",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Urgency;

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
}
