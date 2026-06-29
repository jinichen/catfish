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

// ─── 数据类型 ────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    High,
    Medium,
    Low,
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
    pub llm_verdict: Option<String>,    // "phishing" / "suspicious" / "safe" / None
    pub llm_reason: Option<String>,
}

impl Default for Severity {
    fn default() -> Self {
        Severity::None
    }
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

use super::phishing_config::{phishing_config, PhishingConfig};

// ─── 工具函数 ────────────────────────────────────────────────

/// 从 "显示名 <addr@domain>" 抽 (display_name, email_addr, domain).
fn parse_sender(sender: &str) -> (String, String, String) {
    let s = sender.trim();
    if let (Some(lt), Some(gt)) = (s.rfind('<'), s.rfind('>')) {
        if lt < gt {
            let display = s[..lt].trim().trim_matches('"').to_string();
            let addr = s[lt + 1..gt].trim().to_string();
            let domain = addr.split('@').nth(1).unwrap_or("").to_lowercase();
            return (display, addr, domain);
        }
    }
    // 纯地址
    let domain = s.split('@').nth(1).unwrap_or("").to_lowercase();
    (String::new(), s.to_string(), domain)
}

/// 从 body_html + body_text 抽 URL 列表 (粗略).
fn extract_urls(body_text: &str, body_html: &str) -> Vec<String> {
    let mut urls = Vec::new();
    let combined = format!("{} {}", body_text, body_html);
    let mut chars = combined.chars().peekable();
    let mut buf = String::new();
    while let Some(c) = chars.next() {
        if c == 'h' || c == 'H' {
            let mut peek_buf: String = c.to_string();
            for _ in 0..7 {
                if let Some(&next) = chars.peek() {
                    peek_buf.push(next);
                    chars.next();
                } else {
                    break;
                }
            }
            if peek_buf.to_lowercase().starts_with("http") {
                buf.clear();
                buf.push_str(&peek_buf);
                while let Some(&next) = chars.peek() {
                    if next.is_whitespace() || matches!(next, '"' | '\'' | '<' | '>' | ')' | ']') {
                        break;
                    }
                    buf.push(next);
                    chars.next();
                }
                urls.push(buf.clone());
            }
        }
    }
    urls
}

/// URL → host (lowercased).
fn url_host(url: &str) -> String {
    let s = url
        .trim_start_matches("https://")
        .trim_start_matches("http://");
    s.split(['/', '?', '#', ':'])
        .next()
        .unwrap_or("")
        .to_lowercase()
}

/// 域名同形异义检测 — 把字符规约到 canonical form, 看是否撞 our_domain.
/// 规约: 0/o → o, 1/l/i → l, 3/e → e, 删 hyphen.
/// 这样 "ch1natelecom" 跟 "chinatelecom" 都 → "chlnatelecom", 撞.
fn looks_like_homograph(domain: &str, our_domains: &[String]) -> Option<String> {
    let normalize = |s: &str| -> String {
        s.chars()
            .filter_map(|c| match c {
                '0' | 'o' => Some('o'),
                '1' | 'l' | 'i' => Some('l'),
                '3' | 'e' => Some('e'),
                '-' => None,  // 删 hyphen
                _ => Some(c),
            })
            .collect()
    };
    let normalized = normalize(domain);
    for ours in our_domains {
        let our_norm = normalize(ours);
        if normalized == our_norm && domain != ours {
            return Some(ours.clone());
        }
    }
    None
}

/// 扩展名 (含 dot, 小写).
fn ext_of(filename: &str) -> String {
    if let Some(pos) = filename.rfind('.') {
        filename[pos..].to_lowercase()
    } else {
        String::new()
    }
}

// ─── 5 类扫描函数 ────────────────────────────────────────────

fn scan_sender(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
    let (display, addr, domain) = parse_sender(msg.sender);

    // P3.3.65 政企白名单: domain 在 org_whitelist 整段 sender 扫跳过 — 政企内部
    // 域名 (chinatelecom.cn 等) 不可能假阳触发 PHISH-001/002/003/006/008.
    if !domain.is_empty() && cfg.is_whitelisted_domain(&domain) {
        return;
    }

    // PHISH-001: 显示名含"安全/客服/技术/财务/行政"等组织角色, 但邮箱域不在白名单
    if cfg.is_rule_enabled("PHISH-001") {
        let display_has_role = cfg.keywords.role_titles.iter().any(|kw| display.contains(kw.as_str()));
        let domain_in_white = msg.our_domains.iter().any(|d| domain.ends_with(d.as_str()));
        if display_has_role && !domain_in_white && !domain.is_empty() {
            flags.push(PhishingFlag {
                rule_id: "PHISH-001-display-name-mismatch".into(),
                severity: Severity::High,
                category: Category::SenderSpoofing,
                reason: format!(
                    "发件人显示名含组织角色 '{}', 但邮箱域 '{}' 不在企业白名单",
                    display, domain,
                ),
                matched_text: Some(msg.sender.to_string()),
            });
        }
    }

    // PHISH-002: 域名同形异义 (chinatelecom vs ch1natelecom)
    if cfg.is_rule_enabled("PHISH-002") {
        if let Some(ours) = looks_like_homograph(&domain, msg.our_domains) {
            flags.push(PhishingFlag {
                rule_id: "PHISH-002-similar-domain".into(),
                severity: Severity::High,
                category: Category::SenderSpoofing,
                reason: format!("发件人域 '{}' 跟企业域 '{}' 同形异义 (字符替换/连字符)", domain, ours),
                matched_text: Some(domain.clone()),
            });
        }
    }

    // PHISH-003: 子域名仿冒 (secure-chinatelecom.attacker.com)
    if cfg.is_rule_enabled("PHISH-003") {
        for ours in msg.our_domains {
            if domain.contains(ours.as_str()) && !domain.ends_with(ours.as_str()) {
                flags.push(PhishingFlag {
                    rule_id: "PHISH-003-subdomain-spoofing".into(),
                    severity: Severity::High,
                    category: Category::SenderSpoofing,
                    reason: format!(
                        "发件人域 '{}' 含企业名 '{}' 但实际根域不同 (子域仿冒)",
                        domain, ours,
                    ),
                    matched_text: Some(domain.clone()),
                });
                break;
            }
        }
    }

    // PHISH-006: 可疑 TLD (.tk .ml .ga .cf)
    if cfg.is_rule_enabled("PHISH-006") {
        for sus in &cfg.domain_lists.suspicious_tld {
            if domain.ends_with(sus.as_str()) {
                flags.push(PhishingFlag {
                    rule_id: "PHISH-006-suspicious-tld".into(),
                    severity: Severity::Medium,
                    category: Category::SenderSpoofing,
                    reason: format!("发件人域使用可疑顶级域 '{}'", sus),
                    matched_text: Some(domain.clone()),
                });
                break;
            }
        }
    }

    // PHISH-008: 显示名**真像邮箱地址** (含 @ + 含 .) 但跟 from 地址不同.
    // P3.3.59 fix: display 后半要含 . + ≥4 字符 + 无空格 才算真"显示名是邮箱".
    if cfg.is_rule_enabled("PHISH-008") {
        if display.contains('@') && !addr.is_empty() && display != addr {
            let display_looks_like_email = display
                .split('@')
                .nth(1)
                .map(|d| d.contains('.') && d.len() >= 4 && !d.contains(' '))
                .unwrap_or(false);
            if display_looks_like_email {
                flags.push(PhishingFlag {
                    rule_id: "PHISH-008-display-no-domain".into(),
                    severity: Severity::High,
                    category: Category::SenderSpoofing,
                    reason: format!("显示名 '{}' 是邮箱地址, 跟实际 from '{}' 不同 (隐藏真实发件人)", display, addr),
                    matched_text: Some(msg.sender.to_string()),
                });
            }
        }
    }
}

fn scan_urgent_keywords(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
    let haystack = format!("{} {}", msg.subject, msg.body_text).to_lowercase();
    let subject_lower = msg.subject.to_lowercase();

    // PHISH-011: 紧迫感关键词 (中英 合扫)
    if cfg.is_rule_enabled("PHISH-011") {
        let mut hits = 0;
        let mut matched: Option<String> = None;
        for kw in cfg.keywords.urgent_cn.iter().chain(cfg.keywords.urgent_en.iter()) {
            if haystack.contains(&kw.to_lowercase()) {
                hits += 1;
                if matched.is_none() {
                    matched = Some(kw.clone());
                }
            }
        }
        if hits > 0 {
            let subj_urgent = cfg.patterns.urgent_subject
                .iter()
                .any(|p| subject_lower.contains(&p.to_lowercase()));
            let sev = if hits >= cfg.thresholds.urgent_kw_hits_for_high || subj_urgent {
                Severity::High
            } else {
                Severity::Medium
            };
            flags.push(PhishingFlag {
                rule_id: "PHISH-011-urgent-keywords".into(),
                severity: sev,
                category: Category::UrgentKeywords,
                reason: format!("命中 {} 个紧迫感关键词 (e.g. '{}')", hits, matched.as_deref().unwrap_or("")),
                matched_text: matched,
            });
        }
    }

    // PHISH-013: 含个人信息请求 (身份证 / 银行卡 / 验证码)
    if cfg.is_rule_enabled("PHISH-013") {
        let mut info_hits = 0;
        let mut info_match: Option<String> = None;
        for kw in &cfg.keywords.personal_info {
            if haystack.contains(&kw.to_lowercase()) {
                info_hits += 1;
                if info_match.is_none() {
                    info_match = Some(kw.clone());
                }
            }
        }
        if info_hits > 0 {
            flags.push(PhishingFlag {
                rule_id: "PHISH-013-personal-info-request".into(),
                severity: Severity::High,
                category: Category::UrgentKeywords,
                reason: format!("邮件含个人敏感信息请求 (e.g. '{}')", info_match.as_deref().unwrap_or("")),
                matched_text: info_match,
            });
        }
    }

    // PHISH-044: 加密货币 / 私人汇款
    if cfg.is_rule_enabled("PHISH-044") {
        for kw in &cfg.keywords.crypto_transfer {
            if haystack.contains(&kw.to_lowercase()) {
                flags.push(PhishingFlag {
                    rule_id: "PHISH-044-crypto-or-wire".into(),
                    severity: Severity::High,
                    category: Category::ContentAnomaly,
                    reason: format!("邮件含加密货币 / 私人汇款关键词 '{}'", kw),
                    matched_text: Some(kw.clone()),
                });
                break;
            }
        }
    }
}

fn scan_links(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
    let urls = extract_urls(msg.body_text, msg.body_html);
    if urls.is_empty() {
        return;
    }

    let mut domains = std::collections::HashSet::new();

    for url in &urls {
        let host = url_host(url);
        // P3.3.65: org_whitelist 域名链接不触发任何 link 规则 (内部链接不应被钓鱼判)
        if cfg.is_whitelisted_domain(&host) {
            domains.insert(host.clone());
            continue;
        }
        domains.insert(host.clone());

        // PHISH-021: 短链
        if cfg.is_rule_enabled("PHISH-021") {
            for short in &cfg.domain_lists.short_link {
                if host == *short || host.ends_with(&format!(".{short}")) {
                    flags.push(PhishingFlag {
                        rule_id: "PHISH-021-shortlink".into(),
                        severity: Severity::High,
                        category: Category::SuspiciousLink,
                        reason: format!("链接使用短链域名 '{}' (隐藏真实地址)", host),
                        matched_text: Some(url.clone()),
                    });
                    break;
                }
            }
        }

        // PHISH-022: 裸 IP URL
        if cfg.is_rule_enabled("PHISH-022") {
            let parts: Vec<&str> = host.split('.').collect();
            if parts.len() == 4 && parts.iter().all(|p| p.parse::<u8>().is_ok()) {
                flags.push(PhishingFlag {
                    rule_id: "PHISH-022-ip-url".into(),
                    severity: Severity::High,
                    category: Category::SuspiciousLink,
                    reason: format!("链接直接使用 IP 地址 '{}' (非域名)", host),
                    matched_text: Some(url.clone()),
                });
            }
        }

        // PHISH-024: 可疑 TLD 链接
        if cfg.is_rule_enabled("PHISH-024") {
            for sus in &cfg.domain_lists.suspicious_tld {
                if host.ends_with(sus.as_str()) {
                    flags.push(PhishingFlag {
                        rule_id: "PHISH-024-suspicious-tld-link".into(),
                        severity: Severity::Medium,
                        category: Category::SuspiciousLink,
                        reason: format!("链接指向可疑顶级域 '{}'", sus),
                        matched_text: Some(url.clone()),
                    });
                    break;
                }
            }
        }

        // PHISH-026: URL 异常长
        if cfg.is_rule_enabled("PHISH-026") && url.len() > cfg.thresholds.max_url_len {
            flags.push(PhishingFlag {
                rule_id: "PHISH-026-long-url".into(),
                severity: Severity::Medium,
                category: Category::SuspiciousLink,
                reason: format!("URL 异常长 ({} 字符), 通常多层 redirect 或编码隐藏", url.len()),
                matched_text: Some(format!("{}...", &url[..60.min(url.len())])),
            });
        }

        // PHISH-030: http (非 https) 含 login/verify/pay/signin
        if cfg.is_rule_enabled("PHISH-030") && url.starts_with("http://") {
            let hits_login = cfg.patterns.login_keywords.iter().any(|kw| url.contains(kw.as_str()));
            if hits_login {
                flags.push(PhishingFlag {
                    rule_id: "PHISH-030-http-login-page".into(),
                    severity: Severity::Medium,
                    category: Category::SuspiciousLink,
                    reason: "登录/支付/验证页未走 https".into(),
                    matched_text: Some(url.clone()),
                });
            }
        }
    }

    // PHISH-029: 单封邮件含 N+ 不同域名 (撒网)
    if cfg.is_rule_enabled("PHISH-029") && domains.len() >= cfg.thresholds.max_domains_per_email {
        flags.push(PhishingFlag {
            rule_id: "PHISH-029-multiple-domains".into(),
            severity: Severity::Medium,
            category: Category::SuspiciousLink,
            reason: format!("邮件含 {} 个不同域名链接 (撒网式)", domains.len()),
            matched_text: None,
        });
    }
}

fn scan_attachments(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
    for att in msg.attachments {
        let ext = ext_of(&att.filename);

        // PHISH-035: 双扩展名 — 在 PHISH-031 之前 check (双扩展信号更强)
        if cfg.is_rule_enabled("PHISH-035") {
            let parts: Vec<&str> = att.filename.split('.').collect();
            if parts.len() >= 3 {
                let last_two = format!(".{}.{}", parts[parts.len() - 2], parts[parts.len() - 1]);
                let last_dot_ext = format!(".{}", parts[parts.len() - 1]);
                let hits_doc = cfg.attachment_lists.common_doc_ext
                    .iter()
                    .any(|d| last_two.starts_with(d.as_str()));
                let hits_exec = cfg.attachment_lists.exec_ext
                    .iter()
                    .any(|e| e.as_str() == last_dot_ext.as_str());
                if hits_doc && hits_exec {
                    flags.push(PhishingFlag {
                        rule_id: "PHISH-035-double-extension".into(),
                        severity: Severity::High,
                        category: Category::SuspiciousAttachment,
                        reason: format!("附件 '{}' 双扩展名 (伪装常见文档为可执行)", att.filename),
                        matched_text: Some(att.filename.clone()),
                    });
                    continue;  // 双扩展已经标了, 不重复 PHISH-031
                }
            }
        }

        // PHISH-031: 可执行附件
        if cfg.is_rule_enabled("PHISH-031")
            && cfg.attachment_lists.exec_ext.iter().any(|e| e.as_str() == ext.as_str())
        {
            flags.push(PhishingFlag {
                rule_id: "PHISH-031-exec-attachment".into(),
                severity: Severity::High,
                category: Category::SuspiciousAttachment,
                reason: format!("附件 '{}' 是可执行类型 ({})", att.filename, ext),
                matched_text: Some(att.filename.clone()),
            });
            continue;
        }

        // PHISH-033: macro 文档
        if cfg.is_rule_enabled("PHISH-033")
            && cfg.attachment_lists.macro_ext.iter().any(|e| e.as_str() == ext.as_str())
        {
            flags.push(PhishingFlag {
                rule_id: "PHISH-033-macro-doc".into(),
                severity: Severity::High,
                category: Category::SuspiciousAttachment,
                reason: format!("附件 '{}' 是含 macro 的 Office 文档 ({})", att.filename, ext),
                matched_text: Some(att.filename.clone()),
            });
            continue;
        }

        // PHISH-037: ISO / VHD 镜像 (绕过 MotW)
        if cfg.is_rule_enabled("PHISH-037")
            && cfg.attachment_lists.container_ext.iter().any(|e| e.as_str() == ext.as_str())
        {
            flags.push(PhishingFlag {
                rule_id: "PHISH-037-iso-img".into(),
                severity: Severity::High,
                category: Category::SuspiciousAttachment,
                reason: format!("附件 '{}' 是镜像格式 ({}, 绕过 Mark of the Web)", att.filename, ext),
                matched_text: Some(att.filename.clone()),
            });
            continue;
        }

        // PHISH-038: 文件名扩展 vs MIME 不匹配
        if cfg.is_rule_enabled("PHISH-038") {
            let mime_says_exec = cfg.patterns.exec_mime_substrings
                .iter()
                .any(|sub| att.content_type.contains(sub.as_str()));
            let ext_says_doc = cfg.attachment_lists.safe_doc_ext
                .iter()
                .any(|e| e.as_str() == ext.as_str());
            if mime_says_exec && ext_says_doc {
                flags.push(PhishingFlag {
                    rule_id: "PHISH-038-mime-mismatch".into(),
                    severity: Severity::High,
                    category: Category::SuspiciousAttachment,
                    reason: format!(
                        "附件 '{}' 扩展名是文档, 但 MIME '{}' 实际是可执行",
                        att.filename, att.content_type,
                    ),
                    matched_text: Some(att.filename.clone()),
                });
            }
        }
    }
}

fn scan_content(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
    // PHISH-046: 收件人 > N 人 (撒网)
    if cfg.is_rule_enabled("PHISH-046") && msg.recipients.len() > cfg.thresholds.max_recipients {
        flags.push(PhishingFlag {
            rule_id: "PHISH-046-mass-recipients".into(),
            severity: Severity::Low,
            category: Category::ContentAnomaly,
            reason: format!(
                "收件人 {} 人 (>{}, 撒网式钓鱼常见)",
                msg.recipients.len(),
                cfg.thresholds.max_recipients,
            ),
            matched_text: None,
        });
    }

    // PHISH-048: 邮件正文 < N 字但含链接 (诱饵)
    if cfg.is_rule_enabled("PHISH-048") {
        let body_compact = msg.body_text.trim();
        if body_compact.chars().count() < cfg.thresholds.min_body_chars_with_link
            && (body_compact.contains("http://") || body_compact.contains("https://"))
        {
            flags.push(PhishingFlag {
                rule_id: "PHISH-048-short-body-link".into(),
                severity: Severity::Medium,
                category: Category::ContentAnomaly,
                reason: "邮件正文极短但含链接 (典型诱饵邮件)".into(),
                matched_text: None,
            });
        }
    }

    // PHISH-049: 通用打招呼 (Dear Customer, 尊敬的用户) — 没有员工姓名
    if cfg.is_rule_enabled("PHISH-049") {
        for greet in &cfg.keywords.generic_greetings {
            if msg.body_text.contains(greet.as_str()) {
                flags.push(PhishingFlag {
                    rule_id: "PHISH-049-generic-greeting".into(),
                    severity: Severity::Low,
                    category: Category::ContentAnomaly,
                    reason: format!("通用打招呼 '{}' (合法邮件通常含真实姓名)", greet),
                    matched_text: Some(greet.clone()),
                });
                break;
            }
        }
    }
}

// ─── 核心 scan ──────────────────────────────────────────────

/// 跑全规则集. 不走 LLM (由 caller 异步调 batch_llm_review).
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

// ─── LLM 复审 batch ────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct LlmReviewInput {
    pub subject: String,
    pub sender: String,
    /// 规则触发结果一句话总结 (e.g. "PHISH-001 (sender_spoofing high), PHISH-021 (suspicious_link high)" 或 "无触发")
    pub rule_summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LlmPhishingVerdict {
    /// "phishing" / "suspicious" / "safe" / "unknown" (LLM 漏返时)
    pub verdict: String,
    pub reason: String,
}

#[derive(Serialize)]
struct ChatMessage { role: &'static str, content: String }

#[derive(Serialize)]
struct ChatRequest {
    model: String,
    messages: Vec<ChatMessage>,
    temperature: f32,
    max_tokens: u32,
    stream: bool,
}

#[derive(Deserialize)]
struct ChatChoice { message: ChatChoiceMsg }
#[derive(Deserialize)]
struct ChatChoiceMsg { content: Option<String> }
#[derive(Deserialize)]
struct ChatResponse { choices: Vec<ChatChoice> }

/// LLM batch 复审 — 跟 email_scheduler::call_rate_llm 同款 reqwest pattern.
/// 一次喂 N 封邮件 (轻量 metadata + 规则结果), 让 LLM 返 N 个 verdict+reason.
/// 鸿波 6/12 拍板"所有邮件都调", 这就是 batch all.
///
/// P3.5.140 (6/29 鸿波"Rust 和 TS 统一走 hermes"): 参数 `base_url` 接 hermes 8642
/// 或 gateway 8999 (caller 灰度选), `token` 接 hermes raw key 或 OAuth token (对应
/// base_url 选). callee 不区分, 一律 `Authorization: Bearer {token}`.
pub async fn batch_llm_review(
    items: &[LlmReviewInput],
    base_url: &str,
    token: &str,
    model: &str,
) -> Result<Vec<LlmPhishingVerdict>, String> {
    if items.is_empty() {
        return Ok(Vec::new());
    }

    let list = items
        .iter()
        .enumerate()
        .map(|(i, it)| {
            format!(
                "{}. 主题: {} | 发件人: {} | 规则: {}",
                i + 1,
                truncate(&it.subject, 60),
                truncate(&it.sender, 40),
                truncate(&it.rule_summary, 80),
            )
        })
        .collect::<Vec<_>>()
        .join("\n");

    let system = "你是邮件钓鱼识别助手. 看主题/发件人/规则触发结果, 评每封邮件: \
                  phishing (确定钓鱼) / suspicious (可疑待查) / safe (常规邮件). \
                  返 JSON 数组, 每元素 {\"verdict\":\"...\",\"reason\":\"一句话理由\"}. \
                  不要 markdown, 不要解释, 只返 JSON 数组.";
    let user = format!(
        "{list}\n\n按上面顺序返回 {} 个 verdict 的 JSON 数组. 只返 JSON.",
        items.len()
    );

    let req = ChatRequest {
        model: model.to_string(),
        messages: vec![
            ChatMessage { role: "system", content: system.to_string() },
            ChatMessage { role: "user", content: user },
        ],
        temperature: 0.0,
        max_tokens: 1024,
        stream: false,
    };

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .build()
        .map_err(|e| format!("reqwest build 失败: {e}"))?;

    let resp = client
        .post(format!("{base_url}/v1/chat/completions"))
        .header("Authorization", format!("Bearer {token}"))
        .header("X-Catfish-Source", "companion-phishing-scan")
        .header("X-Catfish-Skip-Identity", "true")
        .json(&req)
        .send()
        .await
        .map_err(|e| format!("LLM 调用失败 ({base_url}): {e}"))?;

    if !resp.status().is_success() {
        return Err(format!("LLM 返 {} (via {})", resp.status(), base_url));
    }

    let body: ChatResponse = resp
        .json()
        .await
        .map_err(|e| format!("response 解析失败: {e}"))?;
    let content = body
        .choices
        .first()
        .and_then(|c| c.message.content.clone())
        .unwrap_or_default();

    parse_verdicts(&content, items.len())
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let mut out: String = s.chars().take(max).collect();
        out.push('…');
        out
    }
}

fn parse_verdicts(s: &str, expected_n: usize) -> Result<Vec<LlmPhishingVerdict>, String> {
    let trimmed = s.trim();
    let start = trimmed.find('[').ok_or_else(|| format!("没 JSON array: {trimmed:?}"))?;
    let end = trimmed.rfind(']').ok_or_else(|| format!("没 ]: {trimmed:?}"))?;
    if end <= start {
        return Err("] 在 [ 前".into());
    }
    let arr = &trimmed[start..=end];
    let parsed: Vec<LlmPhishingVerdict> = serde_json::from_str(arr)
        .map_err(|e| format!("verdicts JSON 解析失败 ({e}): {arr:?}"))?;
    if parsed.len() != expected_n {
        return Err(format!("LLM 返 {} 个 verdict, 期望 {}", parsed.len(), expected_n));
    }
    Ok(parsed)
}

/// audit 留档 — 鸿波 6/12 拍板"只记触发规则的".
pub async fn persist_audit(result: &PhishingScanResult, subject: &str, sender: &str) {
    if result.flags.is_empty() && result.llm_verdict.is_none() {
        // 安全邮件不记
        return;
    }
    let path = match std::env::var("HOME") {
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

    #[test]
    fn extract_urls_basic() {
        let urls = extract_urls("see https://example.com/foo and http://1.2.3.4/bar", "");
        assert_eq!(urls.len(), 2);
        assert!(urls.iter().any(|u| u.contains("example.com")));
        assert!(urls.iter().any(|u| u.contains("1.2.3.4")));
    }

    #[test]
    fn parse_sender_works() {
        let (d, a, dom) = parse_sender("信安部 <admin@chinatelecom.cn>");
        assert_eq!(d, "信安部");
        assert_eq!(a, "admin@chinatelecom.cn");
        assert_eq!(dom, "chinatelecom.cn");

        let (d, a, dom) = parse_sender("plain@example.com");
        assert_eq!(d, "");
        assert_eq!(a, "plain@example.com");
        assert_eq!(dom, "example.com");
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
