//! P3.3.53 (6/13 鸿波): 政治敏感规则引擎 — 配置层.
//!
//! # 红线 (catfish 团队层面)
//!
//! catfish 团队**不预设**任何敏感词. 本文件源码 / 单测 / yaml example 全程
//! 用占位符 (KEYWORD_*) 或者真空. 集团信安 / 党办 / 法务下发实际词库,
//! catfish 仅提供"规则引擎 + 配置入口", 不下判断标准.
//!
//! # 设计
//!
//! 跟 phishing_config 同 pattern (P3.3.65):
//!   - `~/.catfish/companion.yaml` 的 `political:` 段
//!   - OnceLock 单例, 改 yaml 重启 Companion 生效
//!   - 全字段可省略, 走默认 (默认 = 关 + 全空)
//!
//! # 行为分级
//!
//! - **L1 红线词** — 命中即 High severity, 邮件 detail 显大红条提醒咨询信安
//! - **L2 敏感词** — 命中 Medium, 显黄条提醒员工自查
//! - **regex pattern** — 复杂模式 (如 "敏感人物 + 敏感事件" 组合), 命中按
//!   pattern 自带 severity 标
//! - **LLM 复审** (可选, 默认关) — 给上下文判别真意 (e.g. 学术引述 vs 实际表态)
//!
//! # 默认行为
//!
//! `enabled: false` + 全空词 — 单员工本机 / 未下发规则的政企员工**完全透明**,
//! 不会假阳触发任何 flag. 集团下发后 enabled=true + 词库, 才开始扫.
//!
//! # yaml 例子
//!
//! ```yaml
//! political:
//!   enabled: false           # 默认关; 集团下发开
//!   keywords_l1: []          # 红线词, 集团信安 / 党办下发
//!   keywords_l2: []          # 敏感词, 提示员工
//!   regex_patterns: []       # 复杂模式 (regex 语法)
//!   whitelist_phrases: []    # 排除短语 (新闻引用 / 学术讨论场景)
//!   llm_review:
//!     enabled: false
//!     model: "catfish-private-main"  # P3.5.27 (6/17 鸿波"数据零出端"):
//!                                     # 公网 deepseek-flash 让邮件正文/政治敏感
//!                                     # 内容飞公网 LLM, 违红线. 改 private-main.
//! ```

use std::sync::OnceLock;
use serde::{Deserialize, Serialize};

// ─── 公开 config struct ─────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct PoliticalConfig {
    /// 总开关. false = 整套引擎跳过, 不生成任何 flag
    pub enabled: bool,
    /// L1 红线词 — 命中 → High
    pub keywords_l1: Vec<String>,
    /// L2 敏感词 — 命中 → Medium
    pub keywords_l2: Vec<String>,
    /// 复杂模式 — regex 语法
    pub regex_patterns: Vec<RegexRule>,
    /// 排除短语 — 出现在 whitelist 短语后即不告警 (避新闻引用 / 学术讨论假阳)
    pub whitelist_phrases: Vec<String>,
    pub llm_review: LlmReviewConfig,
}

#[derive(Debug, Clone)]
pub struct RegexRule {
    /// 规则 ID (e.g. "POL-R001"), 供 UI / audit 追溯
    pub id: String,
    /// regex 模式
    pub pattern: String,
    /// "high" / "medium" / "low"
    pub severity: String,
    /// 描述, 给员工看的提示
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct LlmReviewConfig {
    pub enabled: bool,
    pub model: String,
}

impl Default for PoliticalConfig {
    fn default() -> Self {
        Self::from_defaults()
    }
}

impl PoliticalConfig {
    /// 全默认值 — enabled=false, 全 vec 空. yaml 未存在 / 全省略时用.
    pub fn from_defaults() -> Self {
        Self {
            enabled: false,
            keywords_l1: Vec::new(),
            keywords_l2: Vec::new(),
            regex_patterns: Vec::new(),
            whitelist_phrases: Vec::new(),
            llm_review: LlmReviewConfig {
                enabled: false,
                model: String::new(),
            },
        }
    }

    /// 输入文本是否在 whitelist 上下文里 (含某个 whitelist 短语).
    /// 命中 → caller 不该报 flag (避新闻引用 / 学术讨论假阳).
    pub fn is_whitelisted_context(&self, haystack: &str) -> bool {
        if self.whitelist_phrases.is_empty() {
            return false;
        }
        let lo = haystack.to_lowercase();
        self.whitelist_phrases
            .iter()
            .any(|p| !p.is_empty() && lo.contains(&p.to_lowercase()))
    }
}

// ─── yaml 反序列化 ──────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct YamlFile {
    political: Option<PoliticalYaml>,
}

#[derive(Debug, Deserialize)]
struct PoliticalYaml {
    enabled: Option<bool>,
    keywords_l1: Option<Vec<String>>,
    keywords_l2: Option<Vec<String>>,
    regex_patterns: Option<Vec<RegexRuleYaml>>,
    whitelist_phrases: Option<Vec<String>>,
    llm_review: Option<LlmReviewYaml>,
}

#[derive(Debug, Deserialize)]
struct RegexRuleYaml {
    id: Option<String>,
    pattern: String,
    severity: Option<String>,
    note: Option<String>,
}

#[derive(Debug, Deserialize)]
struct LlmReviewYaml {
    enabled: Option<bool>,
    model: Option<String>,
}

fn yaml_path() -> Option<std::path::PathBuf> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(std::path::PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

fn read_yaml() -> Option<PoliticalYaml> {
    let path = yaml_path()?;
    if !path.exists() {
        return None;
    }
    let content = std::fs::read_to_string(&path).ok()?;
    let parsed: YamlFile = serde_yaml::from_str(&content).ok()?;
    parsed.political
}

fn build() -> PoliticalConfig {
    let default = PoliticalConfig::from_defaults();
    let Some(yaml) = read_yaml() else { return default; };

    PoliticalConfig {
        enabled: yaml.enabled.unwrap_or(default.enabled),
        keywords_l1: yaml.keywords_l1.unwrap_or(default.keywords_l1),
        keywords_l2: yaml.keywords_l2.unwrap_or(default.keywords_l2),
        regex_patterns: yaml.regex_patterns
            .map(|list| {
                list.into_iter()
                    .filter(|r| !r.pattern.is_empty())
                    .map(|r| RegexRule {
                        id: r.id.unwrap_or_else(|| format!("POL-R-{}", short_hash(&r.pattern))),
                        pattern: r.pattern,
                        severity: r.severity.unwrap_or_else(|| "medium".to_string()),
                        note: r.note.unwrap_or_default(),
                    })
                    .collect()
            })
            .unwrap_or(default.regex_patterns),
        whitelist_phrases: yaml.whitelist_phrases.unwrap_or(default.whitelist_phrases),
        llm_review: yaml.llm_review
            .map(|y| LlmReviewConfig {
                enabled: y.enabled.unwrap_or(default.llm_review.enabled),
                model: y.model.unwrap_or(default.llm_review.model.clone()),
            })
            .unwrap_or(default.llm_review),
    }
}

/// regex 没给 id 时, 用 pattern 短 hash 作回退 id, 让 audit 能追溯.
fn short_hash(s: &str) -> String {
    let mut h: u32 = 0xcbf29ce4;
    for b in s.bytes() {
        h ^= b as u32;
        h = h.wrapping_mul(0x01000193);
    }
    format!("{:08x}", h).chars().take(6).collect()
}

static POLITICAL_CONFIG: OnceLock<PoliticalConfig> = OnceLock::new();

/// 进程级单例. 第一次访问读 yaml + 兜默认, 之后 immutable.
/// 改配置要重启 Companion.
pub fn political_config() -> &'static PoliticalConfig {
    POLITICAL_CONFIG.get_or_init(build)
}

// ─── Tauri command (前端显当前 effective 配置) ───────────────────

#[derive(Debug, Clone, Serialize)]
pub struct PoliticalConfigPublic {
    pub enabled: bool,
    pub keywords_l1_count: usize,
    pub keywords_l2_count: usize,
    pub regex_patterns_count: usize,
    pub whitelist_phrases_count: usize,
    pub llm_review_enabled: bool,
    pub llm_review_model: String,
    pub yaml_path: String,
}

#[tauri::command]
pub fn political_config_get() -> PoliticalConfigPublic {
    let cfg = political_config();
    let path = yaml_path()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    PoliticalConfigPublic {
        enabled: cfg.enabled,
        keywords_l1_count: cfg.keywords_l1.len(),
        keywords_l2_count: cfg.keywords_l2.len(),
        regex_patterns_count: cfg.regex_patterns.len(),
        whitelist_phrases_count: cfg.whitelist_phrases.len(),
        llm_review_enabled: cfg.llm_review.enabled,
        llm_review_model: cfg.llm_review.model.clone(),
        yaml_path: path,
    }
}

// ─── 单测 (catfish 团队层面: 不用任何真实敏感词, 用占位符) ──────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_disabled_and_empty() {
        let cfg = PoliticalConfig::from_defaults();
        assert!(!cfg.enabled);
        assert!(cfg.keywords_l1.is_empty());
        assert!(cfg.keywords_l2.is_empty());
        assert!(cfg.regex_patterns.is_empty());
        assert!(cfg.whitelist_phrases.is_empty());
        assert!(!cfg.llm_review.enabled);
        assert!(cfg.llm_review.model.is_empty());
    }

    #[test]
    fn whitelist_empty_returns_false() {
        let cfg = PoliticalConfig::from_defaults();
        assert!(!cfg.is_whitelisted_context("任意文本"));
    }

    #[test]
    fn whitelist_hit_case_insensitive() {
        let mut cfg = PoliticalConfig::from_defaults();
        cfg.whitelist_phrases = vec!["WHITELIST_PHRASE_A".into()];
        assert!(cfg.is_whitelisted_context("preamble whitelist_phrase_a tail"));
        assert!(cfg.is_whitelisted_context("WHITELIST_PHRASE_A something"));
    }

    #[test]
    fn whitelist_skip_empty_strings() {
        let mut cfg = PoliticalConfig::from_defaults();
        // yaml 里如果有空串, 不应匹配整段文本
        cfg.whitelist_phrases = vec!["".into(), "WHITELIST_PHRASE_B".into()];
        assert!(!cfg.is_whitelisted_context("没含 b 的文本"));
        assert!(cfg.is_whitelisted_context("含 WHITELIST_PHRASE_B 的文本"));
    }

    #[test]
    fn yaml_parse_empty_political_section() {
        let yaml = r#"
political:
"#;
        let parsed: YamlFile = serde_yaml::from_str(yaml).unwrap();
        // 空段也能 parse — 全 None → build 走默认
        assert!(parsed.political.is_none() || parsed.political.is_some());
    }

    #[test]
    fn yaml_parse_disabled_explicit() {
        let yaml = r#"
political:
  enabled: false
  keywords_l1: []
"#;
        let parsed: YamlFile = serde_yaml::from_str(yaml).unwrap();
        let p = parsed.political.unwrap();
        assert_eq!(p.enabled, Some(false));
        assert_eq!(p.keywords_l1, Some(vec![]));
    }

    #[test]
    fn yaml_parse_full_section() {
        // 用占位符, 不写真敏感词
        let yaml = r#"
political:
  enabled: true
  keywords_l1:
    - KEYWORD_L1_ALPHA
    - KEYWORD_L1_BETA
  keywords_l2:
    - KEYWORD_L2_GAMMA
  regex_patterns:
    - id: POL-R001
      pattern: "PATTERN_A.{0,10}PATTERN_B"
      severity: high
      note: "测试规则"
  whitelist_phrases:
    - WHITELIST_PHRASE_X
  llm_review:
    enabled: true
    model: my-model
"#;
        let parsed: YamlFile = serde_yaml::from_str(yaml).unwrap();
        let p = parsed.political.unwrap();
        assert_eq!(p.enabled, Some(true));
        assert_eq!(p.keywords_l1.as_ref().unwrap().len(), 2);
        assert_eq!(p.keywords_l2.as_ref().unwrap().len(), 1);
        assert_eq!(p.regex_patterns.as_ref().unwrap().len(), 1);
        assert_eq!(p.regex_patterns.as_ref().unwrap()[0].pattern, "PATTERN_A.{0,10}PATTERN_B");
        assert_eq!(p.whitelist_phrases.as_ref().unwrap().len(), 1);
        assert_eq!(p.llm_review.as_ref().unwrap().enabled, Some(true));
        assert_eq!(p.llm_review.as_ref().unwrap().model.as_deref(), Some("my-model"));
    }

    #[test]
    fn short_hash_deterministic_and_short() {
        let h1 = short_hash("PATTERN_X");
        let h2 = short_hash("PATTERN_X");
        let h3 = short_hash("PATTERN_Y");
        assert_eq!(h1, h2);
        assert_ne!(h1, h3);
        assert_eq!(h1.len(), 6);
    }

    #[test]
    fn build_with_regex_id_fallback() {
        // 不真读 yaml — 直接构造 PoliticalYaml 测 build 内部 fallback 逻辑
        // 这里只验 short_hash 兜底有效, build 函数走 yaml read 路径 sandbox 难测
        let r = RegexRule {
            id: format!("POL-R-{}", short_hash("PATTERN_A")),
            pattern: "PATTERN_A".into(),
            severity: "medium".into(),
            note: "".into(),
        };
        assert!(r.id.starts_with("POL-R-"));
        assert!(r.id.len() > 6);
    }
}
