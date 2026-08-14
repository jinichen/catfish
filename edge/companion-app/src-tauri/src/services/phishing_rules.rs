//! 钓鱼识别的规则层 —— 5 个工具函数 + 5 个 scan_*。
//!
//! 2026-08-15 从 phishing_scan.rs 切出来 (1143 行超限)。纯搬迁, 逻辑一行未改,
//! 只把 5 个 scan_* 改成 pub(crate) 供同目录的编排层调用。
//!
//! 这里全是**纯函数**: 输入 MessageData + PhishingConfig, 输出往 flags 里追加。
//! 不碰网络、不碰磁盘、不读全局状态 —— 编排 (scan_rules)、LLM 复审、audit 落盘
//! 分别在 phishing_scan.rs / phishing_llm.rs。
//!
//! 红线 (跟 phishing_scan.rs 同): 数据零出端, 这一层 100% 本机。

use super::phishing_config::PhishingConfig;
use super::phishing_scan::{Category, MessageData, PhishingFlag, Severity};

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

pub(crate) fn scan_sender(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
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
    if cfg.is_rule_enabled("PHISH-008")
        && display.contains('@')
        && !addr.is_empty()
        && display != addr
    {
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

pub(crate) fn scan_urgent_keywords(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
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

pub(crate) fn scan_links(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
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

pub(crate) fn scan_attachments(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
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

pub(crate) fn scan_content(msg: &MessageData, cfg: &PhishingConfig, flags: &mut Vec<PhishingFlag>) {
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

#[cfg(test)]
mod tests {
    use super::{extract_urls, parse_sender};

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
}
