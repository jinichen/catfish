//! BL-PHISHING-SCAN (P3.3.58, 6/12 鸿波 "钓鱼识别"): 邮件钓鱼识别双层架构.
//!
//! # 设计 (跟 advisor_scans.py 同 pattern, Rust 端实现)
//! =====================================================
//! 1. **deterministic 规则层** — 35 条预置规则 (5 类), 不依赖 LLM
//! 2. **LLM 复审层** — 鸿波 6/12 拍板: 所有邮件都调 LLM 复审 (不只触发规则的),
//!    用 batch 模式 (一次评 N 封邮件, 跟 email_scheduler 同款) 省 token
//!
//! # 集成 (段 2 由 email_scheduler 调用, 本文件不直接集成)
//! ============================================================
//! - email_scheduler 拉新未读 → 调 scan_messages_batch
//! - 高 severity → macOS 通知 + 红条 UI
//! - 留档 ~/.catfish/audit/phishing_scan.jsonl 走 audit_chain (P3.3.51)
//!
//! # 红线
//! ======
//! - 数据零出端: scan 100% 本机, LLM 走 catfish gateway 内网模型
//! - 员工主权: 员工可关闭 (env CATFISH_PHISHING_SCAN=0)
//! - 失败静默: 规则挂 / LLM 挂都不阻塞邮件流, 静默 fallback "未识别"
//!
//! # 鸿波 6/12 拍板
//! ================
//! - 规则数: 50+ (本文件起步 35, V2 扩到 50)
//! - LLM 复审: 所有邮件都调 (含未触发规则的)
//! - audit: 只记触发规则的 (含 LLM 误报, 不记安全邮件)
//! - UI 文案: "审慎打开 / 咨询信安部门" 保守

use serde::{Deserialize, Serialize};

use super::super::commands::audit_chain::chain_append_impl;
use super::phishing_rules::{scan_attachments, scan_content, scan_links, scan_sender, scan_urgent_keywords};

// ─── 数据类型 ────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    High,
    Medium,
    Low,
    #[default]
    None,
}

impl Severity {
    pub fn rank(&self) -> u8 {
        match self {
            Severity::High => 3,
            Severity::Medium => 2,
            Severity::Low => 1,
            Severity::None => 0,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Category {
    SenderSpoofing,
    UrgentKeywords,
    SuspiciousLink,
    SuspiciousAttachment,
    ContentAnomaly,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PhishingFlag {
    pub rule_id: String,
    pub severity: Severity,
    pub category: Category,
    pub reason: String,
    pub matched_text: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct PhishingScanResult {
    pub message_id: String,
    pub scanned_at: String,
    pub flags: Vec<PhishingFlag>,
    pub highest_severity: Severity,
    pub llm_verdict: Option<String>,    // "phishing" / "suspicious" / "marketing" (P3.5.197) / "safe" / None
    pub llm_reason: Option<String>,
}

/// 输入: 单封邮件元数据.
#[derive(Debug, Clone)]
pub struct MessageData<'a> {
    pub id: &'a str,
    pub subject: &'a str,
    pub sender: &'a str,            // 格式 "显示名 <addr@domain>" 或纯 addr
    pub recipients: &'a [String],
    pub body_text: &'a str,         // list 场景 snippet 即可
    pub body_html: &'a str,
    pub attachments: &'a [AttachmentInfo],
    pub our_domains: &'a [String],  // 企业自有域名白名单 (如 ["chinatelecom.cn", "ffcs.cn"])
}

#[derive(Debug, Clone)]
pub struct AttachmentInfo {
    pub filename: String,
    pub content_type: String,
}

// ─── 规则数据 ────────────────────────────────────────────────
//
// P3.3.65 (6/13 鸿波): 规则全搬到 phishing_config.rs, 这里只 import.
// yaml 没设 → 用 PhishingConfig::from_defaults() 即原 P3.3.58 默认值,
// 单测 / 单员工本机行为不变. 集团下发 yaml 覆盖.

use super::phishing_config::phishing_config;

// ─── 核心 scan ──────────────────────────────────────────────

/// 跑全规则集. 不走 LLM (由 caller 异步调 phishing_llm::batch_llm_review).
///
/// P3.3.65 (6/13 鸿波): 内部读 phishing_config(), yaml 没设 → 用默认值,
/// 行为跟 P3.3.58 一致. 集团下发 yaml 后, 启停 / 关键词 / 阈值 / 白名单可配.
pub fn scan_rules(msg: &MessageData) -> PhishingScanResult {
    let cfg = phishing_config();
    let mut flags = Vec::new();
    scan_sender(msg, cfg, &mut flags);
    scan_urgent_keywords(msg, cfg, &mut flags);
    scan_links(msg, cfg, &mut flags);
    scan_attachments(msg, cfg, &mut flags);
    scan_content(msg, cfg, &mut flags);

    let highest_severity = flags
        .iter()
        .map(|f| f.severity)
        .max_by_key(|s| s.rank())
        .unwrap_or(Severity::None);

    PhishingScanResult {
        message_id: msg.id.to_string(),
        scanned_at: chrono::Utc::now().to_rfc3339(),
        flags,
        highest_severity,
        llm_verdict: None,
        llm_reason: None,
    }
}

/// list 阶段轻量 scan — 只看 subject + sender (catfish-email list 不拉 body).
/// 段 2A 由 email_scheduler 跑. 段 2C (TBD) detail 阶段拿到 body 后再调 scan_rules
/// 做完整扫.
pub fn scan_rules_light(
    id: &str,
    subject: &str,
    sender: &str,
    our_domains: &[String],
) -> PhishingScanResult {
    let recv: Vec<String> = Vec::new();
    let attachments: Vec<AttachmentInfo> = Vec::new();
    let msg = MessageData {
        id, subject, sender,
        recipients: &recv,
        body_text: "",
        body_html: "",
        attachments: &attachments,
        our_domains,
    };
    scan_rules(&msg)
}

/// audit 留档 — 鸿波 6/12 拍板"只记触发规则的".
pub async fn persist_audit(result: &PhishingScanResult, subject: &str, sender: &str) {
    if result.flags.is_empty() && result.llm_verdict.is_none() {
        // 安全邮件不记
        return;
    }
    // BL-WIN-HOME (7/17): Windows 用 USERPROFILE
    let path = match crate::util::paths::home_env().or_else(|_| std::env::var("USERPROFILE")) {
        Ok(h) => format!("{h}/.catfish/audit/phishing_scan.jsonl"),
        Err(_) => return,
    };
    let payload = serde_json::json!({
        "event_type": "phishing_scan",
        "message_id": result.message_id,
        "subject": subject,
        "sender": sender,
        "flags": result.flags,
        "highest_severity": result.highest_severity,
        "llm_verdict": result.llm_verdict,
        "llm_reason": result.llm_reason,
    });
    if let Err(e) = chain_append_impl(path, payload).await {
        log::warn!("[phishing_scan] audit chain append 失败 (不阻塞): {e}");
    }
}

// ─── 单测 ────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn our_domains() -> Vec<String> {
        vec!["chinatelecom.cn".into(), "ffcs.cn".into()]
    }

    /// P3.5.132 收尾 (6/29 鸿波 catch warning): msg 真返加 sender/body, caller
    /// 真不再真重复传 string literal 给 make. dead param 清掉.
    fn msg(
        subject: &str,
        sender: &str,
        body: &str,
        recipients: &[&str],
        attachments: Vec<AttachmentInfo>,
    ) -> (
        String,
        String,
        String,
        Vec<String>,
        Vec<AttachmentInfo>,
        Vec<String>,
    ) {
        let recv: Vec<String> = recipients.iter().map(|s| s.to_string()).collect();
        (
            subject.to_string(),
            sender.to_string(),
            body.to_string(),
            recv,
            attachments,
            our_domains(),
        )
    }

    fn make<'a>(
        s: &'a str, sub: &'a str, send: &'a str, body: &'a str,
        recv: &'a [String], att: &'a [AttachmentInfo], dom: &'a [String],
    ) -> MessageData<'a> {
        MessageData {
            id: s, subject: sub, sender: send,
            recipients: recv, body_text: body, body_html: "", attachments: att,
            our_domains: dom,
        }
    }

    #[test]
    fn safe_email_no_flags() {
        let (sub, send, body, recv, att, dom) = msg(
            "项目周报",
            "张三 <zhangsan@chinatelecom.cn>",
            "本周项目进展...",
            &["lisi@chinatelecom.cn"],
            vec![],
        );
        let m = make("id1", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.is_empty(), "{:?}", r.flags);
        assert_eq!(r.highest_severity, Severity::None);
    }

    #[test]
    fn sender_display_name_mismatch_detected() {
        let (sub, send, body, recv, att, dom) = msg(
            "信安通告",
            "信安部 <attacker@gmail.com>",
            "请点此验证",
            &["target@chinatelecom.cn"],
            vec![],
        );
        let m = make("id2", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-001-display-name-mismatch"));
        assert_eq!(r.highest_severity, Severity::High);
    }

    #[test]
    fn similar_domain_homograph() {
        let (sub, send, body, recv, att, dom) = msg(
            "账户验证",
            "system <admin@ch1natelecom.cn>",
            "请验证账户",
            &["target@chinatelecom.cn"],
            vec![],
        );
        let m = make("id3", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-002-similar-domain"));
    }

    #[test]
    fn urgent_keywords_detected() {
        let (sub, send, body, recv, att, dom) = msg(
            "紧急: 账户冻结",
            "noreply@example.com",
            "您的账户异常, 请立即验证密码, 24小时内重置密码",
            &["target@chinatelecom.cn"],
            vec![],
        );
        let m = make("id4", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-011-urgent-keywords"));
        // 命中 4+ 个紧迫词 + subject 含"紧急" → 应该是 high
        let f = r.flags.iter().find(|f| f.rule_id == "PHISH-011-urgent-keywords").unwrap();
        assert_eq!(f.severity, Severity::High);
    }

    #[test]
    fn ordinary_subsidy_notice_is_not_phishing() {
        let (sub, send, body, recv, att, dom) = msg(
            "关于一级建造师证书补贴相关要求的通知",
            "ffyanzb@chinatelecom.cn",
            "请各位同事按通知办理证书补贴，具体要求见附件说明。",
            &[],
            vec![],
        );
        let m = make("id-business", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(!r.flags.iter().any(|f| f.rule_id == "PHISH-011-urgent-keywords"));
        assert_eq!(r.highest_severity, Severity::None, "业务通知不应被紧迫词规则抬高: {:?}", r.flags);
    }

    #[test]
    fn one_urgent_keyword_without_context_is_not_suspicious() {
        let (sub, send, body, recv, att, dom) = msg(
            "项目进度提醒",
            "colleague@chinatelecom.cn",
            "请尽快处理本周项目台账。",
            &[],
            vec![],
        );
        let m = make("id-single-urgent", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(!r.flags.iter().any(|f| f.rule_id == "PHISH-011-urgent-keywords"));
        assert_eq!(r.highest_severity, Severity::None);
    }

    #[test]
    fn personal_info_request_high() {
        let (sub, send, body, recv, att, dom) = msg(
            "账户问题",
            "support@bank.com",
            "请回信告知您的身份证号和短信验证码以恢复账户",
            &["target@chinatelecom.cn"],
            vec![],
        );
        let m = make("id5", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-013-personal-info-request"));
    }

    #[test]
    fn shortlink_detected() {
        let (sub, send, body, recv, att, dom) = msg(
            "新政策",
            "info@example.com",
            "请查看 https://t.cn/abc123 了解新政策",
            &[],
            vec![],
        );
        let m = make("id6", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-021-shortlink"));
    }

    #[test]
    fn ip_url_detected() {
        let (sub, send, body, recv, att, dom) = msg(
            "登录",
            "info@example.com",
            "登录 http://1.2.3.4/login.html",
            &[],
            vec![],
        );
        let m = make("id7", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-022-ip-url"));
    }

    #[test]
    fn exec_attachment_detected() {
        let (sub, send, body, recv, _, dom) = msg("发票", "vendor@chinatelecom.cn", "请查收附件", &[], vec![]);
        let att = vec![AttachmentInfo {
            filename: "invoice.exe".into(),
            content_type: "application/x-msdownload".into(),
        }];
        let m = make("id8", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-031-exec-attachment"));
    }

    #[test]
    fn macro_doc_detected() {
        let (sub, send, body, recv, _, dom) = msg("报表", "hr@chinatelecom.cn", "见附件", &[], vec![]);
        let att = vec![AttachmentInfo {
            filename: "report.docm".into(),
            content_type: "application/vnd.ms-word.document.macroEnabled.12".into(),
        }];
        let m = make("id9", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-033-macro-doc"));
    }

    #[test]
    fn double_extension_detected() {
        let (sub, send, body, recv, _, dom) = msg("发票", "x@y.com", "see file", &[], vec![]);
        let att = vec![AttachmentInfo {
            filename: "invoice.pdf.exe".into(),
            content_type: "application/octet-stream".into(),
        }];
        let m = make("id10", &sub, &send, &body, &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-035-double-extension"));
    }

    /// P3.3.59 (6/12): "Aria @ HeyGen" 不是邮箱, 不应触发 PHISH-008.
    /// 老版 display.contains('@') 单 check 撞这种 calendar invite display name.
    #[test]
    fn display_with_at_but_not_email_no_false_positive() {
        let recv = vec!["target@chinatelecom.cn".to_string()];
        let attachments: Vec<AttachmentInfo> = vec![];
        let dom = our_domains();
        let m = make(
            "id_calendar",
            "Join us for HeyGen Hangouts",
            "Aria @ HeyGen <invitations@google.com>",  // display 含 @ 但不是邮箱
            "calendar invite body",
            &recv,
            &attachments,
            &dom,
        );
        let r = scan_rules(&m);
        // 不应触发 PHISH-008 (false positive guard)
        assert!(
            !r.flags.iter().any(|f| f.rule_id == "PHISH-008-display-no-domain"),
            "calendar invite 显示名含 @ 但不是邮箱, 不应触发 PHISH-008: {:?}",
            r.flags
        );
    }

    /// PHISH-008 真触发 — display 看起来真像邮箱地址.
    #[test]
    fn display_is_real_email_triggers() {
        let recv = vec!["target@chinatelecom.cn".to_string()];
        let attachments: Vec<AttachmentInfo> = vec![];
        let dom = our_domains();
        let m = make(
            "id_spoof",
            "账户验证",
            "boss@company.com <attacker@phish.cn>",  // display 是真邮箱, 跟 from 不同
            "verify your account",
            &recv,
            &attachments,
            &dom,
        );
        let r = scan_rules(&m);
        assert!(
            r.flags.iter().any(|f| f.rule_id == "PHISH-008-display-no-domain"),
            "{:?}",
            r.flags
        );
    }

    #[test]
    fn highest_severity_picks_max() {
        let (sub, send, body, recv, _, dom) = msg(
            "紧急: 账户冻结请验证",
            "信安部 <attacker@gmail.com>",
            "请立即验证密码, 重置密码, 账户冻结",
            &[],
            vec![],
        );
        let attachments = vec![AttachmentInfo {
            filename: "verify.exe".into(),
            content_type: "application/x-msdownload".into(),
        }];
        let m = make("id11", &sub, &send, &body, &recv, &attachments, &dom);
        let r = scan_rules(&m);
        assert_eq!(r.highest_severity, Severity::High);
        assert!(r.flags.len() >= 3, "多个 high 应触发: {:?}", r.flags);
    }
}
