//! BL-E11 命名权 (五一 sprint 5/3 晚)
//!
//! 员工自定义鲶鱼叫啥 + 选什么人设. 写到 ~/.catfish/companion.yaml 的 `agent` 段.
//!
//! 加载优先级 (跟 oauth.rs 一致):
//!   1. ~/.catfish/companion.yaml 的 agent 段
//!   2. 都没 → 默认 (name="小鲶", personality="gentle")
//!
//! 给 chat 请求带 X-Catfish-Agent-Name / -Personality header 走, gateway 拼 preamble
//! 在 SOUL.md 前面 (见 central/llm-gateway/src/catfish_gateway/identity_inject.py).
//!
//! 持久化: 改 yaml 用 serde_yaml::to_string + atomic 写 (先写 .tmp 再 rename).
//! 不破坏 yaml 里其他段 (oidc / ...): 用 serde_yaml::Value 全量读 → patch agent 段 → 写回.

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use serde_yaml::Value as YamlValue;
use std::fs;
use std::path::{Path, PathBuf};

/// 3 档预设, 跟 gateway identity_inject._PERSONALITY_PRESETS 保持一致.
pub const PERSONALITIES: &[&str] = &["gentle", "direct", "roast"];
pub const DEFAULT_NAME: &str = "小鲶";
pub const DEFAULT_PERSONALITY: &str = "gentle";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentPrefs {
    pub name: String,
    pub personality: String,
}

impl Default for AgentPrefs {
    fn default() -> Self {
        Self {
            name: DEFAULT_NAME.to_string(),
            personality: DEFAULT_PERSONALITY.to_string(),
        }
    }
}

/// yaml schema (只关心 agent 段, 其他段透传保留)
#[derive(Debug, Deserialize)]
struct CompanionYamlAgent {
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    personality: Option<String>,
}

fn yaml_path() -> Result<PathBuf> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| anyhow!("找不到 HOME 环境变量"))?;
    Ok(PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

/// 读 agent 段, 没有就返默认. (内部版, 测试直接传 path 不动 env, parallel-safe.)
pub fn load_at(path: &Path) -> Result<AgentPrefs> {
    if !path.exists() {
        return Ok(AgentPrefs::default());
    }
    let raw = fs::read_to_string(path)
        .with_context(|| format!("读 {} 失败", path.display()))?;
    let v: YamlValue = serde_yaml::from_str(&raw)
        .with_context(|| format!("解析 {} 失败 (yaml 语法错)", path.display()))?;
    let agent_node = v.get("agent");
    if agent_node.is_none() {
        return Ok(AgentPrefs::default());
    }
    let agent: CompanionYamlAgent = serde_yaml::from_value(agent_node.unwrap().clone())
        .context("agent 段格式不对")?;

    let name = agent
        .name
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_NAME.to_string());
    let personality = agent
        .personality
        .map(|s| s.trim().to_lowercase())
        .filter(|s| PERSONALITIES.contains(&s.as_str()))
        .unwrap_or_else(|| DEFAULT_PERSONALITY.to_string());
    Ok(AgentPrefs { name, personality })
}

/// 公开版: 用 env 解析 yaml_path, 生产代码用这个.
pub fn load() -> Result<AgentPrefs> {
    load_at(&yaml_path()?)
}

/// 保存 agent 段, 不破坏 yaml 其他段.
///
/// 流程:
///   1. 读现有 yaml 全量 (没文件则空 mapping)
///   2. patch agent 段 (替换 / 新增)
///   3. 序列化写到 .tmp
///   4. rename .tmp → 真路径 (atomic, 防写一半挂)
pub fn save_at(path: &Path, prefs: &AgentPrefs) -> Result<()> {
    // 校验
    let name = prefs.name.trim().to_string();
    if name.is_empty() {
        return Err(anyhow!("name 不能为空 (默认 '小鲶' 也行, 但不能完全空)"));
    }
    if name.chars().count() > 32 {
        return Err(anyhow!("name 太长 (最多 32 个字符)"));
    }
    let personality = prefs.personality.trim().to_lowercase();
    if !PERSONALITIES.contains(&personality.as_str()) {
        return Err(anyhow!(
            "personality '{}' 不在白名单 {:?}",
            personality, PERSONALITIES
        ));
    }

    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("创建目录 {} 失败", parent.display()))?;
    }

    // 读现有 yaml, 没文件就空 mapping
    let mut root: YamlValue = if path.exists() {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("读 {} 失败", path.display()))?;
        serde_yaml::from_str(&raw).unwrap_or_else(|_| YamlValue::Mapping(Default::default()))
    } else {
        YamlValue::Mapping(Default::default())
    };

    // patch agent 段
    let mut agent_map = serde_yaml::Mapping::new();
    agent_map.insert(YamlValue::from("name"), YamlValue::from(name.clone()));
    agent_map.insert(
        YamlValue::from("personality"),
        YamlValue::from(personality.clone()),
    );
    if let YamlValue::Mapping(ref mut m) = root {
        m.insert(YamlValue::from("agent"), YamlValue::Mapping(agent_map));
    } else {
        let mut m = serde_yaml::Mapping::new();
        m.insert(YamlValue::from("agent"), YamlValue::Mapping(agent_map));
        root = YamlValue::Mapping(m);
    }

    let out = serde_yaml::to_string(&root).context("序列化 yaml 失败")?;
    let tmp = path.with_extension("yaml.tmp");
    fs::write(&tmp, out).with_context(|| format!("写临时文件 {} 失败", tmp.display()))?;
    fs::rename(&tmp, path)
        .with_context(|| format!("rename {} → {} 失败", tmp.display(), path.display()))?;
    Ok(())
}

/// 公开版: 用 env 解析路径. 生产代码用这个.
pub fn save(prefs: &AgentPrefs) -> Result<()> {
    save_at(&yaml_path()?, prefs)
}

#[cfg(test)]
mod tests {
    // 5/8 BL-CR fix v2: 全部测试改用 _at(path) 直接传 TempDir 路径,
    // 不动 env (HOME 是 process-global, parallel test stomp 会随机失败).
    use super::*;
    use tempfile::TempDir;

    fn yaml_in(tmp: &TempDir) -> PathBuf {
        tmp.path().join("companion.yaml")
    }

    #[test]
    fn load_returns_default_when_yaml_missing() {
        let tmp = TempDir::new().unwrap();
        let p = load_at(&yaml_in(&tmp)).unwrap();
        assert_eq!(p.name, DEFAULT_NAME);
        assert_eq!(p.personality, DEFAULT_PERSONALITY);
    }

    #[test]
    fn load_returns_default_when_yaml_has_no_agent_section() {
        let tmp = TempDir::new().unwrap();
        let path = yaml_in(&tmp);
        fs::write(&path, "oidc:\n  issuer: http://localhost:8998\n").unwrap();
        let p = load_at(&path).unwrap();
        assert_eq!(p.name, DEFAULT_NAME);
        assert_eq!(p.personality, DEFAULT_PERSONALITY);
    }

    #[test]
    fn save_then_load_roundtrip() {
        let tmp = TempDir::new().unwrap();
        let path = yaml_in(&tmp);
        let prefs = AgentPrefs {
            name: "老李".into(),
            personality: "direct".into(),
        };
        save_at(&path, &prefs).unwrap();
        let loaded = load_at(&path).unwrap();
        assert_eq!(loaded, prefs);
    }

    #[test]
    fn save_preserves_other_yaml_sections() {
        let tmp = TempDir::new().unwrap();
        let path = yaml_in(&tmp);
        fs::write(
            &path,
            "oidc:\n  issuer: http://localhost:8998\n  client_id: catfish-companion\n",
        )
        .unwrap();
        save_at(
            &path,
            &AgentPrefs {
                name: "小赵".into(),
                personality: "roast".into(),
            },
        )
        .unwrap();
        let raw = fs::read_to_string(&path).unwrap();
        assert!(raw.contains("oidc"));
        assert!(raw.contains("issuer"));
        assert!(raw.contains("agent"));
        assert!(raw.contains("小赵"));
        assert!(raw.contains("roast"));
    }

    #[test]
    fn save_rejects_empty_name() {
        let tmp = TempDir::new().unwrap();
        let r = save_at(
            &yaml_in(&tmp),
            &AgentPrefs {
                name: "  ".into(),
                personality: "gentle".into(),
            },
        );
        assert!(r.is_err());
    }

    #[test]
    fn save_rejects_too_long_name() {
        let tmp = TempDir::new().unwrap();
        let r = save_at(
            &yaml_in(&tmp),
            &AgentPrefs {
                name: "x".repeat(50),
                personality: "gentle".into(),
            },
        );
        assert!(r.is_err());
    }

    #[test]
    fn save_rejects_unknown_personality() {
        let tmp = TempDir::new().unwrap();
        let r = save_at(
            &yaml_in(&tmp),
            &AgentPrefs {
                name: "老李".into(),
                personality: "tsundere".into(),
            },
        );
        assert!(r.is_err());
    }

    #[test]
    fn load_unknown_personality_in_yaml_fallbacks_to_default() {
        let tmp = TempDir::new().unwrap();
        let path = yaml_in(&tmp);
        fs::write(&path, "agent:\n  name: 老李\n  personality: weirdmood\n").unwrap();
        let p = load_at(&path).unwrap();
        assert_eq!(p.name, "老李");
        assert_eq!(p.personality, DEFAULT_PERSONALITY); // weird → fallback
    }

    #[test]
    fn load_empty_name_in_yaml_fallbacks_to_default() {
        let tmp = TempDir::new().unwrap();
        let path = yaml_in(&tmp);
        fs::write(&path, "agent:\n  name: \"  \"\n  personality: gentle\n").unwrap();
        let p = load_at(&path).unwrap();
        assert_eq!(p.name, DEFAULT_NAME);
    }
}
