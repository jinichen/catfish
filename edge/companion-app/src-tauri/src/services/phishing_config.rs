//! P3.3.65 (6/13 鸿波): 钓鱼规则可配置 — yaml + 默认值.
//!
//! # 为啥
//!
//! P3.3.58 的 35 条规则 + 9 个关键词数组 + 3 个函数体 let 数组 + 5 个阈值
//! 全硬编码在 phishing_scan.rs 里. 这种"产品级 deterministic 规则集"对单
//! 员工本机用足够, 但**电信集团政企事业部场景**下:
//!   - 集团信安要按自家标准动规则
//!   - 各省可加省级特有规则
//!   - 政企客户域名 (chinatelecom.cn / ctyun.cn) 要白名单
//!   - 集团合规要能下发新规则不等 Companion 新版
//!
//! # 配置优先级 (高 → 低)
//!
//! 1. `~/.catfish/companion.yaml` 的 `phishing:` 段
//! 2. 代码默认值 (本文件 DEFAULT_* 静态值, 跟 P3.3.58 原 const 一致)
//!
//! 改 yaml 后**重启 Companion** 生效 (跟 email_config 同 pattern, OnceLock 单例).
//!
//! # yaml 例子 (companion.yaml)
//!
//! ```yaml
//! phishing:
//!   # 规则启停 (rule_id 前缀匹配, e.g. "PHISH-049" 关 PHISH-049-*)
//!   enabled_rules: []           # 空 = 全启
//!   disabled_rules:             # 优先级高于 enabled
//!     - PHISH-049               # generic_greeting 误报多默认关
//!
//!   # 关键词 (留空 = 用 catfish 默认)
//!   keywords:
//!     urgent_cn: []
//!     urgent_en: []
//!     personal_info: []
//!     crypto_transfer: []
//!     role_titles: []
//!     generic_greetings: []
//!
//!   # 域名列表 (org_whitelist 政企场景关键 — 这些域名不当钓鱼判)
//!   domain_lists:
//!     short_link: []
//!     suspicious_tld: []
//!     org_whitelist:
//!       - chinatelecom.cn
//!       - ctyun.cn
//!
//!   # 附件扩展名
//!   attachment_lists:
//!     exec_ext: []
//!     macro_ext: []
//!     container_ext: []
//!     common_doc_ext: []
//!     safe_doc_ext: []
//!
//!   # 阈值
//!   thresholds:
//!     max_url_len: 200
//!     max_domains_per_email: 5
//!     urgent_kw_hits_for_high: 2
//!     max_recipients: 50
//!     min_body_chars_with_link: 30
//!
//!   # 模式串 (urgent subject 升 High / http 登录页判定)
//!   patterns:
//!     urgent_subject: ["紧急", "urgent"]
//!     login_keywords: ["login", "verify", "pay", "signin"]
//!     exec_mime_substrings: ["msdownload", "x-executable", "x-msdos-program"]
//! ```

use std::sync::OnceLock;
use serde::{Deserialize, Serialize};

// ─── 默认值 (P3.3.58 原 const 搬过来, single source of truth) ────────

const DEFAULT_URGENT_CN: &[&str] = &[
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

const DEFAULT_URGENT_EN: &[&str] = &[
    "urgent action required", "verify your account", "reset your password",
    "account suspended", "unusual activity", "click here to verify",
    "your payment is overdue", "claim your prize",
];

const DEFAULT_PERSONAL_INFO: &[&str] = &[
    "身份证号", "银行卡号", "信用卡号", "手机验证码", "短信验证码",
    "登录密码", "支付密码", "动态口令",
    "ssn", "social security", "credit card number",
];

const DEFAULT_CRYPTO_TRANSFER: &[&str] = &[
    "比特币", "BTC", "USDT", "以太坊", "ETH", "数字货币钱包",
    "境外汇款", "私人转账", "公对私转账", "电汇",
];

const DEFAULT_ROLE_TITLES: &[&str] = &[
    "信安部", "信安中心", "信息安全", "客服中心", "技术支持",
    "财务部", "人事部", "行政部", "总裁办", "董事会",
];

const DEFAULT_GENERIC_GREETINGS: &[&str] = &[
    "Dear Customer", "Dear User", "Dear Valued Customer",
    "尊敬的用户", "尊敬的客户", "亲爱的用户",
];

const DEFAULT_SHORT_LINK: &[&str] = &[
    "bit.ly", "tinyurl.com", "t.co", "t.cn", "suo.im",
    "dwz.cn", "url.cn", "goo.gl", "ow.ly", "is.gd",
];

const DEFAULT_SUSPICIOUS_TLD: &[&str] = &[".tk", ".ml", ".ga", ".cf", ".top"];

const DEFAULT_EXEC_EXT: &[&str] = &[
    ".exe", ".com", ".pif", ".scr", ".bat", ".cmd",
    ".vbs", ".js", ".wsh", ".hta", ".ps1", ".jar",
    ".lnk", ".url",
];

const DEFAULT_MACRO_EXT: &[&str] = &[".docm", ".xlsm", ".pptm", ".dotm", ".xlam"];

const DEFAULT_CONTAINER_EXT: &[&str] = &[".iso", ".img", ".vhd", ".vhdx"];

const DEFAULT_COMMON_DOC_EXT: &[&str] = &[".pdf", ".docx", ".xlsx", ".jpg", ".png"];

const DEFAULT_SAFE_DOC_EXT: &[&str] = &[".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".jpg", ".png"];

const DEFAULT_URGENT_SUBJECT: &[&str] = &["紧急", "urgent"];

const DEFAULT_LOGIN_KEYWORDS: &[&str] = &["login", "verify", "pay", "signin"];

const DEFAULT_EXEC_MIME_SUBSTR: &[&str] = &["msdownload", "x-executable", "x-msdos-program"];

const DEFAULT_MAX_URL_LEN: usize = 200;
const DEFAULT_MAX_DOMAINS: usize = 5;
const DEFAULT_URGENT_KW_HITS_HIGH: usize = 2;
const DEFAULT_MAX_RECIPIENTS: usize = 50;
const DEFAULT_MIN_BODY_CHARS: usize = 30;

// ─── 公开 config struct (运行时类型) ────────────────────────────

#[derive(Debug, Clone)]
pub struct PhishingConfig {
    pub enabled_rules: Vec<String>,
    pub disabled_rules: Vec<String>,
    pub keywords: Keywords,
    pub domain_lists: DomainLists,
    pub attachment_lists: AttachmentLists,
    pub thresholds: Thresholds,
    pub patterns: Patterns,
}

#[derive(Debug, Clone)]
pub struct Keywords {
    pub urgent_cn: Vec<String>,
    pub urgent_en: Vec<String>,
    pub personal_info: Vec<String>,
    pub crypto_transfer: Vec<String>,
    pub role_titles: Vec<String>,
    pub generic_greetings: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct DomainLists {
    pub short_link: Vec<String>,
    pub suspicious_tld: Vec<String>,
    pub org_whitelist: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct AttachmentLists {
    pub exec_ext: Vec<String>,
    pub macro_ext: Vec<String>,
    pub container_ext: Vec<String>,
    pub common_doc_ext: Vec<String>,
    pub safe_doc_ext: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct Thresholds {
    pub max_url_len: usize,
    pub max_domains_per_email: usize,
    pub urgent_kw_hits_for_high: usize,
    pub max_recipients: usize,
    pub min_body_chars_with_link: usize,
}

#[derive(Debug, Clone)]
pub struct Patterns {
    pub urgent_subject: Vec<String>,
    pub login_keywords: Vec<String>,
    pub exec_mime_substrings: Vec<String>,
}

impl PhishingConfig {
    /// 判某条规则是否启用. rule_id 是 "PHISH-NNN-suffix" 形式, yaml 里写
    /// "PHISH-NNN" 前缀匹配即可 (不用全名), 这样 yaml 友好.
    pub fn is_rule_enabled(&self, rule_id: &str) -> bool {
        // disabled 优先 (前缀匹配)
        if self.disabled_rules.iter().any(|r| rule_id.starts_with(r.as_str())) {
            return false;
        }
        // enabled 空 = 全启
        if self.enabled_rules.is_empty() {
            return true;
        }
        self.enabled_rules.iter().any(|r| rule_id.starts_with(r.as_str()))
    }

    /// 判 domain 是否在组织白名单. domain 含尾匹配 (e.g. "mail.chinatelecom.cn"
    /// 匹配 whitelist "chinatelecom.cn").
    pub fn is_whitelisted_domain(&self, domain: &str) -> bool {
        let d = domain.trim_start_matches('.');
        self.domain_lists
            .org_whitelist
            .iter()
            .any(|w| {
                let w = w.trim_start_matches('.');
                d == w || d.ends_with(&format!(".{w}"))
            })
    }
}

impl Default for PhishingConfig {
    fn default() -> Self {
        Self::from_defaults()
    }
}

impl PhishingConfig {
    /// 拿全默认值的 PhishingConfig (yaml 不存在时用).
    pub fn from_defaults() -> Self {
        Self {
            enabled_rules: Vec::new(),
            disabled_rules: Vec::new(),
            keywords: Keywords {
                urgent_cn: to_strings(DEFAULT_URGENT_CN),
                urgent_en: to_strings(DEFAULT_URGENT_EN),
                personal_info: to_strings(DEFAULT_PERSONAL_INFO),
                crypto_transfer: to_strings(DEFAULT_CRYPTO_TRANSFER),
                role_titles: to_strings(DEFAULT_ROLE_TITLES),
                generic_greetings: to_strings(DEFAULT_GENERIC_GREETINGS),
            },
            domain_lists: DomainLists {
                short_link: to_strings(DEFAULT_SHORT_LINK),
                suspicious_tld: to_strings(DEFAULT_SUSPICIOUS_TLD),
                org_whitelist: Vec::new(), // 默认空, 集团下发
            },
            attachment_lists: AttachmentLists {
                exec_ext: to_strings(DEFAULT_EXEC_EXT),
                macro_ext: to_strings(DEFAULT_MACRO_EXT),
                container_ext: to_strings(DEFAULT_CONTAINER_EXT),
                common_doc_ext: to_strings(DEFAULT_COMMON_DOC_EXT),
                safe_doc_ext: to_strings(DEFAULT_SAFE_DOC_EXT),
            },
            thresholds: Thresholds {
                max_url_len: DEFAULT_MAX_URL_LEN,
                max_domains_per_email: DEFAULT_MAX_DOMAINS,
                urgent_kw_hits_for_high: DEFAULT_URGENT_KW_HITS_HIGH,
                max_recipients: DEFAULT_MAX_RECIPIENTS,
                min_body_chars_with_link: DEFAULT_MIN_BODY_CHARS,
            },
            patterns: Patterns {
                urgent_subject: to_strings(DEFAULT_URGENT_SUBJECT),
                login_keywords: to_strings(DEFAULT_LOGIN_KEYWORDS),
                exec_mime_substrings: to_strings(DEFAULT_EXEC_MIME_SUBSTR),
            },
        }
    }
}

fn to_strings(arr: &[&str]) -> Vec<String> {
    arr.iter().map(|s| s.to_string()).collect()
}

// ─── yaml 反序列化 (全 Option, 缺字段走默认) ──────────────────────

#[derive(Debug, Deserialize)]
struct YamlFile {
    phishing: Option<PhishingYaml>,
}

#[derive(Debug, Deserialize)]
struct PhishingYaml {
    enabled_rules: Option<Vec<String>>,
    disabled_rules: Option<Vec<String>>,
    keywords: Option<KeywordsYaml>,
    domain_lists: Option<DomainListsYaml>,
    attachment_lists: Option<AttachmentListsYaml>,
    thresholds: Option<ThresholdsYaml>,
    patterns: Option<PatternsYaml>,
}

#[derive(Debug, Deserialize)]
struct KeywordsYaml {
    urgent_cn: Option<Vec<String>>,
    urgent_en: Option<Vec<String>>,
    personal_info: Option<Vec<String>>,
    crypto_transfer: Option<Vec<String>>,
    role_titles: Option<Vec<String>>,
    generic_greetings: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct DomainListsYaml {
    short_link: Option<Vec<String>>,
    suspicious_tld: Option<Vec<String>>,
    org_whitelist: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct AttachmentListsYaml {
    exec_ext: Option<Vec<String>>,
    macro_ext: Option<Vec<String>>,
    container_ext: Option<Vec<String>>,
    common_doc_ext: Option<Vec<String>>,
    safe_doc_ext: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct ThresholdsYaml {
    max_url_len: Option<usize>,
    max_domains_per_email: Option<usize>,
    urgent_kw_hits_for_high: Option<usize>,
    max_recipients: Option<usize>,
    min_body_chars_with_link: Option<usize>,
}

#[derive(Debug, Deserialize)]
struct PatternsYaml {
    urgent_subject: Option<Vec<String>>,
    login_keywords: Option<Vec<String>>,
    exec_mime_substrings: Option<Vec<String>>,
}

fn yaml_path() -> Option<std::path::PathBuf> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(std::path::PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

fn read_yaml() -> Option<PhishingYaml> {
    let path = yaml_path()?;
    if !path.exists() {
        return None;
    }
    let content = std::fs::read_to_string(&path).ok()?;
    let parsed: YamlFile = serde_yaml::from_str(&content).ok()?;
    parsed.phishing
}

fn build() -> PhishingConfig {
    let default = PhishingConfig::from_defaults();
    let Some(yaml) = read_yaml() else { return default; };

    // 每一项: yaml 有 → 用 yaml; 没有 → 用 default. 完全保留 default 兼容.
    PhishingConfig {
        enabled_rules: yaml.enabled_rules.unwrap_or(default.enabled_rules),
        disabled_rules: yaml.disabled_rules.unwrap_or(default.disabled_rules),
        keywords: yaml.keywords.map(|y| Keywords {
            urgent_cn: y.urgent_cn.filter(|v| !v.is_empty()).unwrap_or(default.keywords.urgent_cn.clone()),
            urgent_en: y.urgent_en.filter(|v| !v.is_empty()).unwrap_or(default.keywords.urgent_en.clone()),
            personal_info: y.personal_info.filter(|v| !v.is_empty()).unwrap_or(default.keywords.personal_info.clone()),
            crypto_transfer: y.crypto_transfer.filter(|v| !v.is_empty()).unwrap_or(default.keywords.crypto_transfer.clone()),
            role_titles: y.role_titles.filter(|v| !v.is_empty()).unwrap_or(default.keywords.role_titles.clone()),
            generic_greetings: y.generic_greetings.filter(|v| !v.is_empty()).unwrap_or(default.keywords.generic_greetings.clone()),
        }).unwrap_or(default.keywords),
        domain_lists: yaml.domain_lists.map(|y| DomainLists {
            short_link: y.short_link.filter(|v| !v.is_empty()).unwrap_or(default.domain_lists.short_link.clone()),
            suspicious_tld: y.suspicious_tld.filter(|v| !v.is_empty()).unwrap_or(default.domain_lists.suspicious_tld.clone()),
            org_whitelist: y.org_whitelist.unwrap_or(default.domain_lists.org_whitelist.clone()),  // org_whitelist 允许空
        }).unwrap_or(default.domain_lists),
        attachment_lists: yaml.attachment_lists.map(|y| AttachmentLists {
            exec_ext: y.exec_ext.filter(|v| !v.is_empty()).unwrap_or(default.attachment_lists.exec_ext.clone()),
            macro_ext: y.macro_ext.filter(|v| !v.is_empty()).unwrap_or(default.attachment_lists.macro_ext.clone()),
            container_ext: y.container_ext.filter(|v| !v.is_empty()).unwrap_or(default.attachment_lists.container_ext.clone()),
            common_doc_ext: y.common_doc_ext.filter(|v| !v.is_empty()).unwrap_or(default.attachment_lists.common_doc_ext.clone()),
            safe_doc_ext: y.safe_doc_ext.filter(|v| !v.is_empty()).unwrap_or(default.attachment_lists.safe_doc_ext.clone()),
        }).unwrap_or(default.attachment_lists),
        thresholds: yaml.thresholds.map(|y| Thresholds {
            max_url_len: y.max_url_len.unwrap_or(default.thresholds.max_url_len),
            max_domains_per_email: y.max_domains_per_email.unwrap_or(default.thresholds.max_domains_per_email),
            urgent_kw_hits_for_high: y.urgent_kw_hits_for_high.unwrap_or(default.thresholds.urgent_kw_hits_for_high),
            max_recipients: y.max_recipients.unwrap_or(default.thresholds.max_recipients),
            min_body_chars_with_link: y.min_body_chars_with_link.unwrap_or(default.thresholds.min_body_chars_with_link),
        }).unwrap_or(default.thresholds),
        patterns: yaml.patterns.map(|y| Patterns {
            urgent_subject: y.urgent_subject.filter(|v| !v.is_empty()).unwrap_or(default.patterns.urgent_subject.clone()),
            login_keywords: y.login_keywords.filter(|v| !v.is_empty()).unwrap_or(default.patterns.login_keywords.clone()),
            exec_mime_substrings: y.exec_mime_substrings.filter(|v| !v.is_empty()).unwrap_or(default.patterns.exec_mime_substrings.clone()),
        }).unwrap_or(default.patterns),
    }
}

static PHISHING_CONFIG: OnceLock<PhishingConfig> = OnceLock::new();

/// 进程级单例. 第一次访问读 yaml + 兜默认, 之后 immutable. 改配置要重启
/// Companion (跟 email_config 同 pattern).
pub fn phishing_config() -> &'static PhishingConfig {
    PHISHING_CONFIG.get_or_init(build)
}

// ─── Tauri command (Dashboard 显当前 effective 配置) ─────────────

#[derive(Debug, Clone, Serialize)]
pub struct PhishingConfigPublic {
    pub enabled_rules: Vec<String>,
    pub disabled_rules: Vec<String>,
    pub org_whitelist: Vec<String>,
    pub keyword_count: KeywordCount,
    pub thresholds: ThresholdsPublic,
    pub yaml_path: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct KeywordCount {
    pub urgent_cn: usize,
    pub urgent_en: usize,
    pub personal_info: usize,
    pub crypto_transfer: usize,
    pub role_titles: usize,
    pub generic_greetings: usize,
    pub short_link_domains: usize,
    pub suspicious_tlds: usize,
    pub exec_ext: usize,
    pub macro_ext: usize,
    pub container_ext: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct ThresholdsPublic {
    pub max_url_len: usize,
    pub max_domains_per_email: usize,
    pub urgent_kw_hits_for_high: usize,
    pub max_recipients: usize,
    pub min_body_chars_with_link: usize,
}

#[tauri::command]
pub fn phishing_config_get() -> PhishingConfigPublic {
    let cfg = phishing_config();
    let path = yaml_path()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    PhishingConfigPublic {
        enabled_rules: cfg.enabled_rules.clone(),
        disabled_rules: cfg.disabled_rules.clone(),
        org_whitelist: cfg.domain_lists.org_whitelist.clone(),
        keyword_count: KeywordCount {
            urgent_cn: cfg.keywords.urgent_cn.len(),
            urgent_en: cfg.keywords.urgent_en.len(),
            personal_info: cfg.keywords.personal_info.len(),
            crypto_transfer: cfg.keywords.crypto_transfer.len(),
            role_titles: cfg.keywords.role_titles.len(),
            generic_greetings: cfg.keywords.generic_greetings.len(),
            short_link_domains: cfg.domain_lists.short_link.len(),
            suspicious_tlds: cfg.domain_lists.suspicious_tld.len(),
            exec_ext: cfg.attachment_lists.exec_ext.len(),
            macro_ext: cfg.attachment_lists.macro_ext.len(),
            container_ext: cfg.attachment_lists.container_ext.len(),
        },
        thresholds: ThresholdsPublic {
            max_url_len: cfg.thresholds.max_url_len,
            max_domains_per_email: cfg.thresholds.max_domains_per_email,
            urgent_kw_hits_for_high: cfg.thresholds.urgent_kw_hits_for_high,
            max_recipients: cfg.thresholds.max_recipients,
            min_body_chars_with_link: cfg.thresholds.min_body_chars_with_link,
        },
        yaml_path: path,
    }
}

// ─── 单测 ───────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_non_empty() {
        let cfg = PhishingConfig::from_defaults();
        assert!(!cfg.keywords.urgent_cn.is_empty());
        assert!(!cfg.keywords.urgent_en.is_empty());
        assert!(!cfg.keywords.personal_info.is_empty());
        assert!(!cfg.keywords.crypto_transfer.is_empty());
        assert!(!cfg.keywords.role_titles.is_empty());
        assert!(!cfg.keywords.generic_greetings.is_empty());
        assert!(!cfg.domain_lists.short_link.is_empty());
        assert!(!cfg.domain_lists.suspicious_tld.is_empty());
        assert!(cfg.domain_lists.org_whitelist.is_empty()); // 默认空
        assert!(!cfg.attachment_lists.exec_ext.is_empty());
        assert_eq!(cfg.thresholds.max_url_len, 200);
        assert_eq!(cfg.thresholds.max_domains_per_email, 5);
        assert_eq!(cfg.thresholds.max_recipients, 50);
        assert_eq!(cfg.thresholds.min_body_chars_with_link, 30);
    }

    #[test]
    fn rule_enabled_default_all_on() {
        let cfg = PhishingConfig::from_defaults();
        assert!(cfg.is_rule_enabled("PHISH-001-display-name-mismatch"));
        assert!(cfg.is_rule_enabled("PHISH-049-generic-greeting"));
        assert!(cfg.is_rule_enabled("PHISH-XXX-future"));
    }

    #[test]
    fn rule_disabled_prefix_match() {
        let mut cfg = PhishingConfig::from_defaults();
        cfg.disabled_rules = vec!["PHISH-049".into()];
        assert!(!cfg.is_rule_enabled("PHISH-049-generic-greeting"));
        assert!(cfg.is_rule_enabled("PHISH-001-display-name-mismatch")); // 其它仍启
    }

    #[test]
    fn rule_enabled_allowlist() {
        let mut cfg = PhishingConfig::from_defaults();
        cfg.enabled_rules = vec!["PHISH-001".into(), "PHISH-002".into()];
        assert!(cfg.is_rule_enabled("PHISH-001-display-name-mismatch"));
        assert!(cfg.is_rule_enabled("PHISH-002-similar-domain"));
        assert!(!cfg.is_rule_enabled("PHISH-049-generic-greeting")); // 不在 allowlist
    }

    #[test]
    fn rule_disabled_wins_over_enabled() {
        let mut cfg = PhishingConfig::from_defaults();
        cfg.enabled_rules = vec!["PHISH-049".into()];
        cfg.disabled_rules = vec!["PHISH-049".into()];
        assert!(!cfg.is_rule_enabled("PHISH-049-generic-greeting"));
    }

    #[test]
    fn whitelist_exact_match() {
        let mut cfg = PhishingConfig::from_defaults();
        cfg.domain_lists.org_whitelist = vec!["chinatelecom.cn".into()];
        assert!(cfg.is_whitelisted_domain("chinatelecom.cn"));
    }

    #[test]
    fn whitelist_subdomain_match() {
        let mut cfg = PhishingConfig::from_defaults();
        cfg.domain_lists.org_whitelist = vec!["chinatelecom.cn".into()];
        assert!(cfg.is_whitelisted_domain("mail.chinatelecom.cn"));
        assert!(cfg.is_whitelisted_domain("smtp.mail.chinatelecom.cn"));
    }

    #[test]
    fn whitelist_no_partial_match() {
        let mut cfg = PhishingConfig::from_defaults();
        cfg.domain_lists.org_whitelist = vec!["chinatelecom.cn".into()];
        // 关键防穿越: chinatelecom.cn.attacker.com 不能算白名单
        assert!(!cfg.is_whitelisted_domain("chinatelecom.cn.attacker.com"));
        // 也不算: notchinatelecom.cn
        assert!(!cfg.is_whitelisted_domain("notchinatelecom.cn"));
    }

    #[test]
    fn whitelist_empty_means_nothing_whitelisted() {
        let cfg = PhishingConfig::from_defaults();
        assert!(!cfg.is_whitelisted_domain("chinatelecom.cn"));
        assert!(!cfg.is_whitelisted_domain("anything.com"));
    }

    #[test]
    fn whitelist_leading_dot_normalized() {
        let mut cfg = PhishingConfig::from_defaults();
        cfg.domain_lists.org_whitelist = vec![".chinatelecom.cn".into()];
        assert!(cfg.is_whitelisted_domain("chinatelecom.cn"));
        assert!(cfg.is_whitelisted_domain("mail.chinatelecom.cn"));
    }

    #[test]
    fn yaml_parse_full_phishing_section() {
        let yaml = r#"
phishing:
  enabled_rules: []
  disabled_rules:
    - PHISH-049
  keywords:
    urgent_cn:
      - 紧急自定义
  domain_lists:
    org_whitelist:
      - chinatelecom.cn
      - ctyun.cn
  thresholds:
    max_url_len: 500
"#;
        let parsed: YamlFile = serde_yaml::from_str(yaml).unwrap();
        let p = parsed.phishing.unwrap();
        assert_eq!(p.disabled_rules.as_ref().unwrap(), &vec!["PHISH-049".to_string()]);
        assert_eq!(
            p.keywords.as_ref().unwrap().urgent_cn.as_ref().unwrap(),
            &vec!["紧急自定义".to_string()]
        );
        assert_eq!(
            p.domain_lists.as_ref().unwrap().org_whitelist.as_ref().unwrap().len(),
            2
        );
        assert_eq!(p.thresholds.as_ref().unwrap().max_url_len, Some(500));
    }
}
