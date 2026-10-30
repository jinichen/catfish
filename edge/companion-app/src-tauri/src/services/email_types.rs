//! 邮件调度的领域类型: EmailItem 与 Urgency。
//!
//! 2026-08-15 从 email_scheduler.rs 切出来 (1196 行超限)。纯搬迁, 逻辑一行未改。
//!
//! 单独一层是为了理顺方向: call_rate_llm / parse_urgencies 的返回类型就是
//! Urgency。如果 Urgency 留在 email_scheduler.rs, email_llm.rs 就得反过来
//! import 前门文件。现在是 types ← state ← llm/notify ← email_scheduler。

use serde::Deserialize;

#[derive(Deserialize, Clone, Debug, Default)]
pub(crate) struct EmailItem {
    pub(crate) id: String,
    pub(crate) subject: String,
    pub(crate) sender: String,
    /// 8/21 分诊升级: 正文摘要 (list 场景 catfish-email 的 body_text 就是 snippet)。
    /// 之前分诊只喂 主题60字+发件人40字, **连正文都不看** —— 于是永远提不出
    /// "9月10日前报汇总表"这种截止日。rename 直接白拿 CLI JSON 里现成的字段;
    /// default 兜底老调用方手工构造的 EmailItem (不给就空串, 行为同从前)。
    #[serde(default, rename = "body_text")]
    pub(crate) snippet: String,
    /// 10/31: 这封收在哪个账号 + 收件人 / 抄送 —— 分诊判「直接发给我 / 抄送我 / 群发」用。
    /// 原来模型只看主题 + 发件人 + 摘要, 群发给近百人的通报和点名找我办的事长得一样,
    /// 「办」标得过多。CLI JSON 里本来就有 (base.py Message), 老调用方不给就空。
    #[serde(default)]
    pub(crate) account: String,
    #[serde(default)]
    pub(crate) recipients: Vec<String>,
    #[serde(default)]
    pub(crate) cc: Vec<String>,
}

impl EmailItem {
    /// 分诊用的「收件方式」, 如 `直接发给我, 共 3 人` / `抄送我, 共 96 人`。
    /// 不知道收件人 (老数据 / 客户端不给) 返回空串, 调用方就不写这一栏。
    pub(crate) fn delivery_hint(&self) -> String {
        let total = self.recipients.len() + self.cc.len();
        if total == 0 {
            return String::new();
        }
        let me = self.account.trim().to_lowercase();
        let hit = |list: &[String]| !me.is_empty() && list.iter().any(|a| a.to_lowercase().contains(&me));
        let how = if hit(&self.recipients) {
            "直接发给我"
        } else if hit(&self.cc) {
            "抄送我"
        } else {
            "我不在收件人里 (邮件组 / 密送)"
        };
        format!("{how}, 共 {total} 人")
    }
}

/// LLM 评级结果. fallback 'medium' 时不通知, 不阻塞.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Urgency {
    Urgent,   // 急: 系统通知 + 前端主动闲聊 (BL-E13)
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

#[cfg(test)]
mod tests_delivery_hint {
    use super::EmailItem;

    fn item(account: &str, to: &[&str], cc: &[&str]) -> EmailItem {
        EmailItem {
            account: account.into(),
            recipients: to.iter().map(|s| s.to_string()).collect(),
            cc: cc.iter().map(|s| s.to_string()).collect(),
            ..Default::default()
        }
    }

    #[test]
    fn 直接_抄送_群发_不知道() {
        assert_eq!(item("me@x.cn", &["Me <ME@x.cn>", "b@x.cn"], &[]).delivery_hint(), "直接发给我, 共 2 人");
        assert_eq!(item("me@x.cn", &["a@x.cn"], &["me@x.cn", "c@x.cn"]).delivery_hint(), "抄送我, 共 3 人");
        assert_eq!(item("me@x.cn", &["list@x.cn"], &[]).delivery_hint(), "我不在收件人里 (邮件组 / 密送), 共 1 人");
        assert_eq!(item("me@x.cn", &[], &[]).delivery_hint(), "");
    }
}
