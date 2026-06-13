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

// ─── 规则数据 (35 条起步, V2 扩到 50+) ────────────────────────

/// 紧迫感关键词 (中英) — 触发 medium
const URGENT_KEYWORDS_CN: &[&str] = &[
    "24小时内", "48小时内", "立即处理", "立刻处理", "尽快处理",
    "账户冻结", "账户异常", "账户锁定", "立即冻结",
    "验证身份", "验证账户", "验证密码", "验证您的",
    "重置密码", "恢复账户", "重新登录",
    "付款逾期", "欠费", "立即缴费", "停服通知",
    "可疑活动", "异常登录", "异地登录",
    "中奖", "抽奖", "奖金领取",
    "退税", "补贴", "政府补助",
    "包裹异常", "海关扣押", "签收失败",
    "法院传票", "法律警告", "起诉",
];

const URGENT_KEYWORDS_EN: &[&str] = &[
    "urgent action required", "verify your account", "reset your password",
    "account suspended", "unusual activity", "click here to verify",
    "your payment is overdue", "claim your prize",
];

/// 个人信息请求 — 触发 high (问敏感信息的几乎都是钓鱼)
const PERSONAL_INFO_KEYWORDS: &[&str] = &[
    "身份证号", "银行卡号", "信用卡号", "手机验证码", "短信验证码",
    "登录密码", "支付密码", "动态口令",
    "ssn", "social security", "credit card number",
];

/// 加密货币 / 汇款 (常见诈骗主题)
const CRYPTO_TRANSFER_KEYWORDS: &[&str] = &[
    "比特币", "BTC", "USDT", "以太坊", "ETH", "数字货币钱包",
    "境外汇款", "私人转账", "公对私转账", "电汇",
];

/// 短链域名
const SHORT_LINK_DOMAINS: &[&str] = &[
    "bit.ly", "tinyurl.com", "t.co", "t.cn", "suo.im",
    "dwz.cn", "url.cn", "goo.gl", "ow.ly", "is.gd",
];

/// 可疑顶级域 (常用于钓鱼活动)
const SUSPICIOUS_TLDS: &[&str] = &[".tk", ".ml", ".ga", ".cf", ".top"];

/// 可执行附件扩展名 (即拒)
const EXEC_EXTENSIONS: &[&str] = &[
    ".exe", ".com", ".pif", ".scr", ".bat", ".cmd",
    ".vbs", ".js", ".wsh", ".hta", ".ps1", ".jar",
    ".lnk", ".url",
];

/// macro 文档扩展 (高风险)
const MACRO_EXTENSIONS: &[&str] = &[".docm", ".xlsm", ".pptm", ".dotm", ".xlam"];

/// 镜像 / 容器格式 (绕过 Mark of the Web)
const CONTAINER_EXTENSIONS: &[&str] = &[".iso", ".img", ".vhd", ".vhdx"];

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

fn scan_sender(msg: &MessageData, flags: &mut Vec<PhishingFlag>) {
    let (display, addr, domain) = parse_sender(msg.sender);

    // PHISH-001: 显示名含"安全/客服/技术/财务/行政"等组织角色, 但邮箱域不在白名单
    let role_keywords = [
        "信安部", "信安中心", "信息安全", "客服中心", "技术支持",
        "财务部", "人事部", "行政部", "总裁办", "董事会",
    ];
    let display_has_role = role_keywords.iter().any(|kw| display.contains(kw));
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

    // PHISH-002: 域名同形异义 (chinatelecom vs ch1natelecom)
    if let Some(ours) = looks_like_homograph(&domain, msg.our_domains) {
        flags.push(PhishingFlag {
            rule_id: "PHISH-002-similar-domain".into(),
            severity: Severity::High,
            category: Category::SenderSpoofing,
            reason: format!("发件人域 '{}' 跟企业域 '{}' 同形异义 (字符替换/连字符)", domain, ours),
            matched_text: Some(domain.clone()),
        });
    }

    // PHISH-003: 子域名仿冒 (secure-chinatelecom.attacker.com)
    for ours in msg.our_domains {
        // 检查 our_domain 出现在子域名位置但不是真正的 root
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

    // PHISH-006: 可疑 TLD (.tk .ml .ga .cf)
    for sus in SUSPICIOUS_TLDS {
        if domain.ends_with(sus) {
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

    // PHISH-008: 显示名**真像邮箱地址** (含 @ + 含 .) 但跟 from 地址不同.
    // P3.3.59 fix: 老版只 check '@' 撞 false positive (e.g. "Aria @ HeyGen" 不是邮箱
    // 但含 @). 现在再 check 域名部分含点 + 至少 4 字符像域名形状, 才算真"显示名是
    // 邮箱地址".
    if display.contains('@') && !addr.is_empty() && display != addr {
        // display 切 @ 看后半像不像域名 (含 dot + 2+ 字符)
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

fn scan_urgent_keywords(msg: &MessageData, flags: &mut Vec<PhishingFlag>) {
    let haystack = format!("{} {}", msg.subject, msg.body_text).to_lowercase();
    let subject_lower = msg.subject.to_lowercase();

    let mut hits = 0;
    let mut matched: Option<String> = None;
    for kw in URGENT_KEYWORDS_CN.iter().chain(URGENT_KEYWORDS_EN.iter()) {
        if haystack.contains(&kw.to_lowercase()) {
            hits += 1;
            if matched.is_none() {
                matched = Some((*kw).to_string());
            }
        }
    }
    if hits > 0 {
        let sev = if hits >= 2 || subject_lower.contains("紧急") || subject_lower.contains("urgent") {
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

    // PHISH-013: 含个人信息请求 (身份证 / 银行卡 / 验证码)
    let mut info_hits = 0;
    let mut info_match: Option<String> = None;
    for kw in PERSONAL_INFO_KEYWORDS {
        if haystack.contains(&kw.to_lowercase()) {
            info_hits += 1;
            if info_match.is_none() {
                info_match = Some((*kw).to_string());
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

    // PHISH-044: 加密货币 / 私人汇款
    for kw in CRYPTO_TRANSFER_KEYWORDS {
        if haystack.contains(&kw.to_lowercase()) {
            flags.push(PhishingFlag {
                rule_id: "PHISH-044-crypto-or-wire".into(),
                severity: Severity::High,
                category: Category::ContentAnomaly,
                reason: format!("邮件含加密货币 / 私人汇款关键词 '{}'", kw),
                matched_text: Some((*kw).to_string()),
            });
            break;
        }
    }
}

fn scan_links(msg: &MessageData, flags: &mut Vec<PhishingFlag>) {
    let urls = extract_urls(msg.body_text, msg.body_html);
    if urls.is_empty() {
        return;
    }

    let mut domains = std::collections::HashSet::new();

    for url in &urls {
        let host = url_host(url);
        domains.insert(host.clone());

        // PHISH-021: 短链
        for short in SHORT_LINK_DOMAINS {
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

        // PHISH-022: 裸 IP URL
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

        // PHISH-024: 可疑 TLD 链接
        for sus in SUSPICIOUS_TLDS {
            if host.ends_with(sus) {
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

        // PHISH-026: URL 异常长 (>200 字)
        if url.len() > 200 {
            flags.push(PhishingFlag {
                rule_id: "PHISH-026-long-url".into(),
                severity: Severity::Medium,
                category: Category::SuspiciousLink,
                reason: format!("URL 异常长 ({} 字符), 通常多层 redirect 或编码隐藏", url.len()),
                matched_text: Some(format!("{}...", &url[..60.min(url.len())])),
            });
        }

        // PHISH-030: http (非 https) 含 login/verify/pay
        if url.starts_with("http://")
            && (url.contains("login") || url.contains("verify") || url.contains("pay") || url.contains("signin"))
        {
            flags.push(PhishingFlag {
                rule_id: "PHISH-030-http-login-page".into(),
                severity: Severity::Medium,
                category: Category::SuspiciousLink,
                reason: "登录/支付/验证页未走 https".into(),
                matched_text: Some(url.clone()),
            });
        }
    }

    // PHISH-029: 单封邮件含 5+ 不同域名 (撒网)
    if domains.len() >= 5 {
        flags.push(PhishingFlag {
            rule_id: "PHISH-029-multiple-domains".into(),
            severity: Severity::Medium,
            category: Category::SuspiciousLink,
            reason: format!("邮件含 {} 个不同域名链接 (撒网式)", domains.len()),
            matched_text: None,
        });
    }
}

fn scan_attachments(msg: &MessageData, flags: &mut Vec<PhishingFlag>) {
    for att in msg.attachments {
        let ext = ext_of(&att.filename);

        // PHISH-035: 双扩展名 — 在 PHISH-031 之前 check (双扩展 catfish 钓鱼信号更强)
        let parts: Vec<&str> = att.filename.split('.').collect();
        if parts.len() >= 3 {
            let last_two = format!(".{}.{}", parts[parts.len() - 2], parts[parts.len() - 1]);
            let common_doc_exts = [".pdf", ".docx", ".xlsx", ".jpg", ".png"];
            let last_dot_ext = format!(".{}", parts[parts.len() - 1]);
            if common_doc_exts.iter().any(|d| last_two.starts_with(d))
                && EXEC_EXTENSIONS.contains(&last_dot_ext.as_str())
            {
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

        // PHISH-031: 可执行附件
        if EXEC_EXTENSIONS.contains(&ext.as_str()) {
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
        if MACRO_EXTENSIONS.contains(&ext.as_str()) {
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
        if CONTAINER_EXTENSIONS.contains(&ext.as_str()) {
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
        let mime_says_exec = att.content_type.contains("msdownload")
            || att.content_type.contains("x-executable")
            || att.content_type.contains("x-msdos-program");
        let ext_says_doc = matches!(
            ext.as_str(),
            ".pdf" | ".docx" | ".xlsx" | ".pptx" | ".txt" | ".jpg" | ".png"
        );
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

fn scan_content(msg: &MessageData, flags: &mut Vec<PhishingFlag>) {
    // PHISH-046: 收件人 > 50 人 (撒网)
    if msg.recipients.len() > 50 {
        flags.push(PhishingFlag {
            rule_id: "PHISH-046-mass-recipients".into(),
            severity: Severity::Low,
            category: Category::ContentAnomaly,
            reason: format!("收件人 {} 人 (>50, 撒网式钓鱼常见)", msg.recipients.len()),
            matched_text: None,
        });
    }

    // PHISH-048: 邮件正文 < 30 字但含链接 (诱饵)
    let body_compact = msg.body_text.trim();
    if body_compact.chars().count() < 30 && (body_compact.contains("http://") || body_compact.contains("https://")) {
        flags.push(PhishingFlag {
            rule_id: "PHISH-048-short-body-link".into(),
            severity: Severity::Medium,
            category: Category::ContentAnomaly,
            reason: "邮件正文极短但含链接 (典型诱饵邮件)".into(),
            matched_text: None,
        });
    }

    // PHISH-049: 通用打招呼 (Dear Customer, 尊敬的用户) — 没有员工姓名
    let generic_greetings = [
        "Dear Customer", "Dear User", "Dear Valued Customer",
        "尊敬的用户", "尊敬的客户", "亲爱的用户",
    ];
    for greet in &generic_greetings {
        if msg.body_text.contains(greet) {
            flags.push(PhishingFlag {
                rule_id: "PHISH-049-generic-greeting".into(),
                severity: Severity::Low,
                category: Category::ContentAnomaly,
                reason: format!("通用打招呼 '{}' (合法邮件通常含真实姓名)", greet),
                matched_text: Some((*greet).to_string()),
            });
            break;
        }
    }
}

// ─── 核心 scan ──────────────────────────────────────────────

/// 跑全规则集. 不走 LLM (由 caller 异步调 batch_llm_review).
pub fn scan_rules(msg: &MessageData) -> PhishingScanResult {
    let mut flags = Vec::new();
    scan_sender(msg, &mut flags);
    scan_urgent_keywords(msg, &mut flags);
    scan_links(msg, &mut flags);
    scan_attachments(msg, &mut flags);
    scan_content(msg, &mut flags);

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
pub async fn batch_llm_review(
    items: &[LlmReviewInput],
    gateway: &str,
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
        .post(format!("{gateway}/v1/chat/completions"))
        .header("Authorization", format!("Bearer {token}"))
        .header("X-Catfish-Source", "companion-phishing-scan")
        .header("X-Catfish-Skip-Identity", "true")
        .json(&req)
        .send()
        .await
        .map_err(|e| format!("gateway 调用失败: {e}"))?;

    if !resp.status().is_success() {
        return Err(format!("gateway 返 {}", resp.status()));
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

    fn msg(
        subject: &str,
        sender: &str,
        body: &str,
        recipients: &[&str],
        attachments: Vec<AttachmentInfo>,
    ) -> (String, Vec<String>, Vec<AttachmentInfo>, Vec<String>) {
        let recv: Vec<String> = recipients.iter().map(|s| s.to_string()).collect();
        (
            subject.to_string(),
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
        let (sub, recv, att, dom) = msg(
            "项目周报",
            "张三 <zhangsan@chinatelecom.cn>",
            "本周项目进展...",
            &["lisi@chinatelecom.cn"],
            vec![],
        );
        let m = make("id1", &sub, "张三 <zhangsan@chinatelecom.cn>", "本周项目进展...", &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.is_empty(), "{:?}", r.flags);
        assert_eq!(r.highest_severity, Severity::None);
    }

    #[test]
    fn sender_display_name_mismatch_detected() {
        let (sub, recv, att, dom) = msg(
            "信安通告",
            "信安部 <attacker@gmail.com>",
            "请点此验证",
            &["target@chinatelecom.cn"],
            vec![],
        );
        let m = make("id2", &sub, "信安部 <attacker@gmail.com>", "请点此验证", &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-001-display-name-mismatch"));
        assert_eq!(r.highest_severity, Severity::High);
    }

    #[test]
    fn similar_domain_homograph() {
        let (sub, recv, att, dom) = msg(
            "账户验证",
            "system <admin@ch1natelecom.cn>",
            "请验证账户",
            &["target@chinatelecom.cn"],
            vec![],
        );
        let m = make("id3", &sub, "system <admin@ch1natelecom.cn>", "请验证账户", &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-002-similar-domain"));
    }

    #[test]
    fn urgent_keywords_detected() {
        let (sub, recv, att, dom) = msg(
            "紧急: 账户冻结",
            "noreply@example.com",
            "您的账户异常, 请立即验证密码, 24小时内重置密码",
            &["target@chinatelecom.cn"],
            vec![],
        );
        let m = make("id4", &sub, "noreply@example.com", "您的账户异常, 请立即验证密码, 24小时内重置密码", &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-011-urgent-keywords"));
        // 命中 4+ 个紧迫词 + subject 含"紧急" → 应该是 high
        let f = r.flags.iter().find(|f| f.rule_id == "PHISH-011-urgent-keywords").unwrap();
        assert_eq!(f.severity, Severity::High);
    }

    #[test]
    fn personal_info_request_high() {
        let (sub, recv, att, dom) = msg(
            "账户问题",
            "support@bank.com",
            "请回信告知您的身份证号和短信验证码以恢复账户",
            &["target@chinatelecom.cn"],
            vec![],
        );
        let m = make("id5", &sub, "support@bank.com", "请回信告知您的身份证号和短信验证码以恢复账户", &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-013-personal-info-request"));
    }

    #[test]
    fn shortlink_detected() {
        let (sub, recv, att, dom) = msg(
            "新政策",
            "info@example.com",
            "请查看 https://t.cn/abc123 了解新政策",
            &[],
            vec![],
        );
        let m = make("id6", &sub, "info@example.com", "请查看 https://t.cn/abc123 了解新政策", &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-021-shortlink"));
    }

    #[test]
    fn ip_url_detected() {
        let (sub, recv, att, dom) = msg(
            "登录",
            "info@example.com",
            "登录 http://1.2.3.4/login.html",
            &[],
            vec![],
        );
        let m = make("id7", &sub, "info@example.com", "登录 http://1.2.3.4/login.html", &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-022-ip-url"));
    }

    #[test]
    fn exec_attachment_detected() {
        let (sub, recv, _, dom) = msg("发票", "vendor@chinatelecom.cn", "请查收附件", &[], vec![]);
        let att = vec![AttachmentInfo {
            filename: "invoice.exe".into(),
            content_type: "application/x-msdownload".into(),
        }];
        let m = make("id8", &sub, "vendor@chinatelecom.cn", "请查收附件", &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-031-exec-attachment"));
    }

    #[test]
    fn macro_doc_detected() {
        let (sub, recv, _, dom) = msg("报表", "hr@chinatelecom.cn", "见附件", &[], vec![]);
        let att = vec![AttachmentInfo {
            filename: "report.docm".into(),
            content_type: "application/vnd.ms-word.document.macroEnabled.12".into(),
        }];
        let m = make("id9", &sub, "hr@chinatelecom.cn", "见附件", &recv, &att, &dom);
        let r = scan_rules(&m);
        assert!(r.flags.iter().any(|f| f.rule_id == "PHISH-033-macro-doc"));
    }

    #[test]
    fn double_extension_detected() {
        let (sub, recv, _, dom) = msg("发票", "x@y.com", "see file", &[], vec![]);
        let att = vec![AttachmentInfo {
            filename: "invoice.pdf.exe".into(),
            content_type: "application/octet-stream".into(),
        }];
        let m = make("id10", &sub, "x@y.com", "see file", &recv, &att, &dom);
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
        let (sub, recv, _, dom) = msg(
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
        let m = make("id11", &sub, "信安部 <attacker@gmail.com>",
            "请立即验证密码, 重置密码, 账户冻结", &recv, &attachments, &dom);
        let r = scan_rules(&m);
        assert_eq!(r.highest_severity, Severity::High);
        assert!(r.flags.len() >= 3, "多个 high 应触发: {:?}", r.flags);
    }
}
