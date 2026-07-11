//! P3.3.53 (6/13 鸿波): 政治敏感规则引擎 — 扫描层.
//!
//! # 集成
//!
//! 跟 phishing_scan 并列:
//!   - email_scheduler 在拉新邮件时同位置调 scan_political_for_new
//!   - 留档 ~/.catfish/audit/political_scan.jsonl 走 audit_chain (P3.3.51)
//!   - UI: ListItem + DetailPane 显 ⚠ 政治敏感 badge / 红条
//!
//! # 设计
//!
//! 三层 deterministic + 可选 LLM 复审:
//!   1. **L1 红线关键词** 命中 → High
//!   2. **L2 敏感关键词** 命中 → Medium
//!   3. **regex pattern** 命中 → 按 pattern 的 severity 标
//!   4. **whitelist 上下文** 命中 → 整封跳, 不报 flag
//!   5. (可选) **LLM 复审** — 给 catfish-gateway 模型, 判别上下文意图
//!
//! # 红线 (catfish 团队层面)
//!
//! catfish 不预设任何敏感词. 单测用 PLACEHOLDER. 真规则集团下发到 yaml.

use serde::{Deserialize, Serialize};

use super::political_config::{political_config, PoliticalConfig};
use super::super::commands::audit_chain::chain_append_impl;

// ─── 数据类型 ────────────────────────────────────────────────────

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

    /// 从字符串 parse, 容错 (yaml 里可能写 "high" "High" 等).
    pub fn from_str_lenient(s: &str) -> Self {
        match s.trim().to_lowercase().as_str() {
            "high" | "h" => Severity::High,
            "medium" | "med" | "m" => Severity::Medium,
            "low" | "l" => Severity::Low,
            _ => Severity::Medium, // 默认 medium 容错
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FlagLevel {
    /// L1 红线关键词
    L1Redline,
    /// L2 敏感关键词
    L2Sensitive,
    /// regex pattern
    Regex,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PoliticalFlag {
    pub rule_id: String,
    pub level: FlagLevel,
    pub severity: Severity,
    /// 命中的关键词 / pattern id (不含具体内容, 防审计日志反向泄露)
    pub matched_label: String,
    /// 上下文摘录 (命中前后 20 字), audit 用. UI 显完整正文时也可参考.
    pub excerpt: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct PoliticalScanResult {
    pub message_id: String,
    pub scanned_at: String,
    pub flags: Vec<PoliticalFlag>,
    pub highest_severity: Severity,
    pub llm_verdict: Option<String>,
    pub llm_reason: Option<String>,
    /// 配置当前是否开启 — 关时 flags 必空, UI 不挂 badge
    pub engine_enabled: bool,
}

/// 输入: 单封邮件元数据. scan 当前只看 subject + body_text — sender 由 caller
/// 直接传给 persist_audit (跟入 jsonl, 不用塞 MessageData).
#[derive(Debug, Clone)]
pub struct MessageData<'a> {
    pub id: &'a str,
    pub subject: &'a str,
    pub body_text: &'a str,
}

// ─── 扫描 ────────────────────────────────────────────────────────

/// 跑全规则集. yaml 不开 enabled / 无规则 → 返空结果 (engine_enabled=false).
pub fn scan_rules(msg: &MessageData) -> PoliticalScanResult {
    scan_rules_with(msg, political_config())
}

/// 测试用变体 — caller 注入 config.
pub fn scan_rules_with(msg: &MessageData, cfg: &PoliticalConfig) -> PoliticalScanResult {
    let mut result = PoliticalScanResult {
        message_id: msg.id.to_string(),
        scanned_at: chrono::Utc::now().to_rfc3339(),
        flags: Vec::new(),
        highest_severity: Severity::None,
        llm_verdict: None,
        llm_reason: None,
        engine_enabled: cfg.enabled,
    };

    if !cfg.enabled {
        return result;
    }

    // 合扫 subject + body — 大小写不敏感
    let haystack_raw = format!("{} {}", msg.subject, msg.body_text);
    let haystack_lower = haystack_raw.to_lowercase();

    // whitelist 上下文 命中 → 全跳
    if cfg.is_whitelisted_context(&haystack_raw) {
        return result;
    }

    // L1 红线关键词
    for kw in &cfg.keywords_l1 {
        if kw.is_empty() {
            continue;
        }
        let kw_lower = kw.to_lowercase();
        if haystack_lower.contains(&kw_lower) {
            let excerpt = extract_excerpt(&haystack_raw, &haystack_lower, &kw_lower, 20);
            result.flags.push(PoliticalFlag {
                rule_id: format!("POL-L1-{}", short_label(kw)),
                level: FlagLevel::L1Redline,
                severity: Severity::High,
                matched_label: short_label(kw),
                excerpt,
            });
        }
    }

    // L2 敏感关键词
    for kw in &cfg.keywords_l2 {
        if kw.is_empty() {
            continue;
        }
        let kw_lower = kw.to_lowercase();
        if haystack_lower.contains(&kw_lower) {
            let excerpt = extract_excerpt(&haystack_raw, &haystack_lower, &kw_lower, 20);
            result.flags.push(PoliticalFlag {
                rule_id: format!("POL-L2-{}", short_label(kw)),
                level: FlagLevel::L2Sensitive,
                severity: Severity::Medium,
                matched_label: short_label(kw),
                excerpt,
            });
        }
    }

    // regex pattern
    for rule in &cfg.regex_patterns {
        // 编译失败 → 静默跳, 不挂引擎. yaml 写错单条不应停服.
        let Ok(re) = regex::RegexBuilder::new(&rule.pattern)
            .case_insensitive(true)
            .build()
        else {
            log::warn!("[political_scan] regex 编译失败, 跳过: id={} pattern={}", rule.id, rule.pattern);
            continue;
        };
        if let Some(m) = re.find(&haystack_raw) {
            let sev = Severity::from_str_lenient(&rule.severity);
            let excerpt = Some(extract_around(&haystack_raw, m.start(), m.end(), 20));
            result.flags.push(PoliticalFlag {
                rule_id: rule.id.clone(),
                level: FlagLevel::Regex,
                severity: sev,
                matched_label: rule.note.clone(),
                excerpt,
            });
        }
    }

    // 聚合 highest
    result.highest_severity = result.flags
        .iter()
        .map(|f| f.severity)
        .max_by_key(|s| s.rank())
        .unwrap_or(Severity::None);

    result
}

/// 抽关键词命中位置前后 N 字符的上下文, 给 audit / UI 用.
/// 输入 haystack_raw 是原文, haystack_lower 是 lowercase 副本 (位置一致),
/// needle_lower 是已 lowercase 的关键词.
fn extract_excerpt(
    haystack_raw: &str,
    haystack_lower: &str,
    needle_lower: &str,
    around: usize,
) -> Option<String> {
    let start = haystack_lower.find(needle_lower)?;
    let end = start + needle_lower.len();
    Some(extract_around(haystack_raw, start, end, around))
}

/// 在 raw 文本里, 围绕 [start, end] byte range 抽 around 字符上下文.
/// 处理 UTF-8 边界, 防切坏中文字符.
fn extract_around(raw: &str, start: usize, end: usize, around: usize) -> String {
    // 找 around 个字符前 / 后的安全 char boundary
    let prefix_start = floor_char_boundary(raw, start.saturating_sub(around * 4));
    let suffix_end = ceil_char_boundary(raw, (end + around * 4).min(raw.len()));
    let s = &raw[prefix_start..suffix_end];
    // 用 char-level 限长 (around*2 + needle len)
    s.chars()
        .take(around * 2 + 80)
        .collect::<String>()
        .replace(['\n', '\r'], " ")
        .trim()
        .to_string()
}

/// 找 <= idx 的最近 char boundary (Rust 1.79 floor_char_boundary 还不稳, 自己实现).
fn floor_char_boundary(s: &str, mut idx: usize) -> usize {
    if idx >= s.len() {
        return s.len();
    }
    while idx > 0 && !s.is_char_boundary(idx) {
        idx -= 1;
    }
    idx
}

/// 找 >= idx 的最近 char boundary.
fn ceil_char_boundary(s: &str, mut idx: usize) -> usize {
    if idx >= s.len() {
        return s.len();
    }
    while idx < s.len() && !s.is_char_boundary(idx) {
        idx += 1;
    }
    idx
}

/// 关键词的"短 label" — 防 audit 日志原文返显, 取前 4 字符 + 长度.
/// e.g. "ABCDEFGH" → "ABCD_8", 中文 "甲乙丙丁戊己" → "甲乙丙丁_6"
fn short_label(kw: &str) -> String {
    let total: usize = kw.chars().count();
    let head: String = kw.chars().take(4).collect();
    format!("{head}_{total}")
}

// ─── audit 留档 ──────────────────────────────────────────────────

/// audit 留档 — 鸿波 6/13 拍板"只记触发规则的, 关时全跳".
pub async fn persist_audit(result: &PoliticalScanResult, subject: &str, sender: &str) {
    if !result.engine_enabled {
        return;
    }
    if result.flags.is_empty() && result.llm_verdict.is_none() {
        return;
    }
    let path = match std::env::var("HOME") {
        Ok(h) => format!("{h}/.catfish/audit/political_scan.jsonl"),
        Err(_) => return,
    };
    // 关键: subject / sender 留原文 (邮件元数据本来就是员工本机), flags 里
    // 只含 matched_label (short_label 已脱敏关键词原文) + excerpt 上下文.
    let payload = serde_json::json!({
        "event_type": "political_scan",
        "message_id": result.message_id,
        "subject": subject,
        "sender": sender,
        "flags": result.flags,
        "highest_severity": result.highest_severity,
        "llm_verdict": result.llm_verdict,
        "llm_reason": result.llm_reason,
    });
    if let Err(e) = chain_append_impl(path, payload).await {
        log::warn!("[political_scan] audit chain append 失败 (不阻塞): {e}");
    }
}

// ─── 单测 (catfish 团队层面: 用 KEYWORD_* PLACEHOLDER, 不写真敏感词) ──

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::political_config::{PoliticalConfig, RegexRule};

    fn cfg_disabled() -> PoliticalConfig {
        PoliticalConfig::from_defaults()
    }

    fn cfg_enabled_with(l1: Vec<&str>, l2: Vec<&str>) -> PoliticalConfig {
        let mut c = PoliticalConfig::from_defaults();
        c.enabled = true;
        c.keywords_l1 = l1.into_iter().map(String::from).collect();
        c.keywords_l2 = l2.into_iter().map(String::from).collect();
        c
    }

    fn msg<'a>(subject: &'a str, body: &'a str) -> MessageData<'a> {
        MessageData {
            id: "test-id",
            subject,
            body_text: body,
        }
    }

    #[test]
    fn disabled_engine_returns_empty() {
        let c = cfg_disabled();
        let m = msg("anything", "anything KEYWORD_L1_ALPHA");
        let r = scan_rules_with(&m, &c);
        assert!(!r.engine_enabled);
        assert!(r.flags.is_empty());
        assert_eq!(r.highest_severity, Severity::None);
    }

    #[test]
    fn enabled_no_match_no_flags() {
        let c = cfg_enabled_with(vec!["KEYWORD_L1_ALPHA"], vec![]);
        let m = msg("正常邮件", "今天周报内容 ...");
        let r = scan_rules_with(&m, &c);
        assert!(r.engine_enabled);
        assert!(r.flags.is_empty());
        assert_eq!(r.highest_severity, Severity::None);
    }

    #[test]
    fn l1_match_high_severity() {
        let c = cfg_enabled_with(vec!["KEYWORD_L1_ALPHA"], vec![]);
        let m = msg("主题", "正文含 KEYWORD_L1_ALPHA 中间");
        let r = scan_rules_with(&m, &c);
        assert_eq!(r.flags.len(), 1);
        assert!(matches!(r.flags[0].level, FlagLevel::L1Redline));
        assert_eq!(r.flags[0].severity, Severity::High);
        assert_eq!(r.highest_severity, Severity::High);
    }

    #[test]
    fn l2_match_medium_severity() {
        let c = cfg_enabled_with(vec![], vec!["KEYWORD_L2_BETA"]);
        let m = msg("主题", "正文 KEYWORD_L2_BETA 末尾");
        let r = scan_rules_with(&m, &c);
        assert_eq!(r.flags.len(), 1);
        assert!(matches!(r.flags[0].level, FlagLevel::L2Sensitive));
        assert_eq!(r.flags[0].severity, Severity::Medium);
    }

    #[test]
    fn l1_match_in_subject_also_works() {
        let c = cfg_enabled_with(vec!["KEYWORD_L1_GAMMA"], vec![]);
        let m = msg("KEYWORD_L1_GAMMA 主题", "正常正文");
        let r = scan_rules_with(&m, &c);
        assert_eq!(r.flags.len(), 1);
        assert_eq!(r.flags[0].severity, Severity::High);
    }

    #[test]
    fn case_insensitive_match() {
        let c = cfg_enabled_with(vec!["KEYWORD_l1_DELTA"], vec![]);
        let m = msg("主题", "正文 keyword_L1_delta 大小写混");
        let r = scan_rules_with(&m, &c);
        assert_eq!(r.flags.len(), 1);
    }

    #[test]
    fn multiple_matches_each_flagged() {
        let c = cfg_enabled_with(
            vec!["KEYWORD_L1_A", "KEYWORD_L1_B"],
            vec!["KEYWORD_L2_C"],
        );
        let m = msg("主题", "A KEYWORD_L1_A 中间 KEYWORD_L1_B 末 KEYWORD_L2_C 完");
        let r = scan_rules_with(&m, &c);
        assert_eq!(r.flags.len(), 3);
        // 应该是 High (取最高)
        assert_eq!(r.highest_severity, Severity::High);
    }

    #[test]
    fn highest_severity_picks_max() {
        let c = cfg_enabled_with(vec![], vec!["KEYWORD_L2_X"]);
        // 只命中 L2, highest = Medium
        let m = msg("主题", "KEYWORD_L2_X 在这里");
        let r = scan_rules_with(&m, &c);
        assert_eq!(r.highest_severity, Severity::Medium);
    }

    #[test]
    fn whitelist_context_skips_all() {
        let mut c = cfg_enabled_with(vec!["KEYWORD_L1_Y"], vec![]);
        c.whitelist_phrases = vec!["新闻报道".into()];
        // 关键词在新闻报道上下文里, 整段跳过, 不报 flag
        let m = msg("主题", "新闻报道 提到 KEYWORD_L1_Y");
        let r = scan_rules_with(&m, &c);
        assert!(r.engine_enabled);
        assert!(r.flags.is_empty());
    }

    #[test]
    fn empty_keyword_in_list_doesnt_match_everything() {
        // yaml 里如果有空字符串, 不应当作"任何文本都命中"
        let c = cfg_enabled_with(vec!["", "KEYWORD_L1_REAL"], vec![]);
        let m = msg("主题", "正常文本无敏感词");
        let r = scan_rules_with(&m, &c);
        assert!(r.flags.is_empty());
    }

    #[test]
    fn regex_match_with_custom_severity() {
        let mut c = PoliticalConfig::from_defaults();
        c.enabled = true;
        c.regex_patterns = vec![RegexRule {
            id: "POL-R001".into(),
            pattern: r"PATTERN_X.{0,10}PATTERN_Y".into(),
            severity: "high".into(),
            note: "测试 pattern X-Y 组合".into(),
        }];
        let m = msg("主题", "正文 PATTERN_X 中间 PATTERN_Y 末尾");
        let r = scan_rules_with(&m, &c);
        assert_eq!(r.flags.len(), 1);
        assert!(matches!(r.flags[0].level, FlagLevel::Regex));
        assert_eq!(r.flags[0].severity, Severity::High);
        assert_eq!(r.flags[0].rule_id, "POL-R001");
    }

    #[test]
    fn regex_bad_pattern_doesnt_panic() {
        let mut c = PoliticalConfig::from_defaults();
        c.enabled = true;
        c.regex_patterns = vec![RegexRule {
            id: "POL-RX-BAD".into(),
            pattern: r"[unclosed".into(),  // 非法 regex
            severity: "medium".into(),
            note: "".into(),
        }];
        let m = msg("主题", "无所谓内容");
        // 不应 panic, 仅跳过这条 regex
        let r = scan_rules_with(&m, &c);
        assert!(r.flags.is_empty());
    }

    #[test]
    fn short_label_chinese_and_ascii() {
        assert_eq!(short_label("ABCDEFGH"), "ABCD_8");
        let cn = short_label("甲乙丙丁戊己");
        assert!(cn.starts_with("甲乙丙丁"));
        assert!(cn.ends_with("_6"));
    }

    #[test]
    fn extract_excerpt_handles_utf8() {
        let raw = "前缀文本 KEYWORD_X 后缀中文内容";
        let lo = raw.to_lowercase();
        let ex = extract_excerpt(raw, &lo, "keyword_x", 5);
        assert!(ex.is_some());
        let s = ex.unwrap();
        assert!(s.contains("KEYWORD_X"));
    }

    #[test]
    fn severity_from_str_lenient() {
        assert_eq!(Severity::from_str_lenient("high"), Severity::High);
        assert_eq!(Severity::from_str_lenient(" High "), Severity::High);
        assert_eq!(Severity::from_str_lenient("medium"), Severity::Medium);
        assert_eq!(Severity::from_str_lenient("med"), Severity::Medium);
        assert_eq!(Severity::from_str_lenient("low"), Severity::Low);
        assert_eq!(Severity::from_str_lenient("garbage"), Severity::Medium); // 默认
    }
}

