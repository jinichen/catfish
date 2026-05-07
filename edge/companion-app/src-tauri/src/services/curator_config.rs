//! BL-CR Curator 集成 步骤 1 + 步骤 3 (5/7).
//!
//! Curator 是 hermes 0.12 加的"自动整理工", 默认 30 天 stale / 90 天 archive,
//! idle 2h 触发, 太激进. 我们 patch 成 60 / 180 / 4h, 给央企季度脚本宽容.
//!
//! 配置文件: ~/.hermes/config.yaml 的 `curator` 段
//!
//! 加载优先级:
//!   1. 文件存在 → 读 + 校验, 缺字段补默认
//!   2. 文件不存在 / 没 curator 段 → 返我们的保守默认 (ensure_default 会把它写进去)
//!
//! 跟 agent_prefs.rs 同模式: 用 serde_yaml::Value 全量读, patch 一段, 写回.
//! **不破坏 yaml 其他段** (未来 hermes 可能加更多段).

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use serde_yaml::Value as YamlValue;
use std::fs;
use std::path::PathBuf;

/// 鲶鱼推荐的保守默认 (跟 hermes 默认对比注释见 RUNBOOK § 6).
pub const DEFAULT_ENABLED: bool = true;
pub const DEFAULT_INTERVAL_HOURS: u32 = 168; // 1 周
pub const DEFAULT_MIN_IDLE_HOURS: u32 = 4; // hermes 默认 2 太激进
pub const DEFAULT_STALE_AFTER_DAYS: u32 = 60; // hermes 默认 30, 季度脚本会被冤
pub const DEFAULT_ARCHIVE_AFTER_DAYS: u32 = 180; // hermes 默认 90, 半年保险

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CuratorConfig {
    pub enabled: bool,
    pub interval_hours: u32,
    pub min_idle_hours: u32,
    pub stale_after_days: u32,
    pub archive_after_days: u32,
}

impl Default for CuratorConfig {
    fn default() -> Self {
        Self {
            enabled: DEFAULT_ENABLED,
            interval_hours: DEFAULT_INTERVAL_HOURS,
            min_idle_hours: DEFAULT_MIN_IDLE_HOURS,
            stale_after_days: DEFAULT_STALE_AFTER_DAYS,
            archive_after_days: DEFAULT_ARCHIVE_AFTER_DAYS,
        }
    }
}

/// 解析层 (yaml 字段都 optional, 缺一个就用默认补).
#[derive(Debug, Deserialize)]
struct CuratorYaml {
    #[serde(default)]
    enabled: Option<bool>,
    #[serde(default)]
    interval_hours: Option<u32>,
    #[serde(default)]
    min_idle_hours: Option<u32>,
    #[serde(default)]
    stale_after_days: Option<u32>,
    #[serde(default)]
    archive_after_days: Option<u32>,
}

fn yaml_path() -> Result<PathBuf> {
    // 5/7 BL-CR: 测试用 HERMES_HOME env 重定向到 tmp, 不污染真 ~/.hermes
    if let Ok(home) = std::env::var("HERMES_HOME") {
        return Ok(PathBuf::from(home).join("config.yaml"));
    }
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| anyhow!("找不到 HOME 环境变量"))?;
    Ok(PathBuf::from(home).join(".hermes").join("config.yaml"))
}

/// 读 curator 段, 缺字段补默认.
pub fn load() -> Result<CuratorConfig> {
    let path = yaml_path()?;
    if !path.exists() {
        return Ok(CuratorConfig::default());
    }
    let raw = fs::read_to_string(&path)
        .with_context(|| format!("读 {} 失败", path.display()))?;
    let v: YamlValue = serde_yaml::from_str(&raw)
        .with_context(|| format!("解析 {} 失败 (yaml 语法错)", path.display()))?;
    let curator_node = v.get("curator");
    if curator_node.is_none() {
        return Ok(CuratorConfig::default());
    }
    let parsed: CuratorYaml = serde_yaml::from_value(curator_node.unwrap().clone())
        .context("curator 段格式不对")?;

    Ok(CuratorConfig {
        enabled: parsed.enabled.unwrap_or(DEFAULT_ENABLED),
        interval_hours: parsed.interval_hours.unwrap_or(DEFAULT_INTERVAL_HOURS),
        min_idle_hours: parsed.min_idle_hours.unwrap_or(DEFAULT_MIN_IDLE_HOURS),
        stale_after_days: parsed.stale_after_days.unwrap_or(DEFAULT_STALE_AFTER_DAYS),
        archive_after_days: parsed
            .archive_after_days
            .unwrap_or(DEFAULT_ARCHIVE_AFTER_DAYS),
    })
}

/// 保存 curator 段, 不破坏 yaml 其他段 (atomic 写).
pub fn save(cfg: &CuratorConfig) -> Result<()> {
    // 校验: 关系约束
    if cfg.archive_after_days <= cfg.stale_after_days {
        return Err(anyhow!(
            "archive_after_days ({}) 必须 > stale_after_days ({}); \
             否则 skill 会跳过 stale 直接 archive",
            cfg.archive_after_days,
            cfg.stale_after_days,
        ));
    }
    if cfg.interval_hours == 0 || cfg.min_idle_hours == 0 {
        return Err(anyhow!(
            "interval_hours 和 min_idle_hours 都必须 > 0 (设 0 等于禁用 Curator, 请用 enabled=false)"
        ));
    }

    let path = yaml_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("创建目录 {} 失败", parent.display()))?;
    }

    let mut root: YamlValue = if path.exists() {
        let raw = fs::read_to_string(&path)
            .with_context(|| format!("读 {} 失败", path.display()))?;
        serde_yaml::from_str(&raw).unwrap_or_else(|_| YamlValue::Mapping(Default::default()))
    } else {
        YamlValue::Mapping(Default::default())
    };

    let mut curator_map = serde_yaml::Mapping::new();
    curator_map.insert(YamlValue::from("enabled"), YamlValue::from(cfg.enabled));
    curator_map.insert(
        YamlValue::from("interval_hours"),
        YamlValue::from(cfg.interval_hours),
    );
    curator_map.insert(
        YamlValue::from("min_idle_hours"),
        YamlValue::from(cfg.min_idle_hours),
    );
    curator_map.insert(
        YamlValue::from("stale_after_days"),
        YamlValue::from(cfg.stale_after_days),
    );
    curator_map.insert(
        YamlValue::from("archive_after_days"),
        YamlValue::from(cfg.archive_after_days),
    );
    if let YamlValue::Mapping(ref mut m) = root {
        m.insert(YamlValue::from("curator"), YamlValue::Mapping(curator_map));
    } else {
        let mut m = serde_yaml::Mapping::new();
        m.insert(YamlValue::from("curator"), YamlValue::Mapping(curator_map));
        root = YamlValue::Mapping(m);
    }

    let out = serde_yaml::to_string(&root).context("序列化 yaml 失败")?;
    let tmp = path.with_extension("yaml.tmp");
    fs::write(&tmp, out).with_context(|| format!("写临时文件 {} 失败", tmp.display()))?;
    fs::rename(&tmp, &path)
        .with_context(|| format!("rename {} → {} 失败", tmp.display(), path.display()))?;
    Ok(())
}

/// 如果 ~/.hermes/config.yaml 没 curator 段, 写入鲶鱼保守默认.
///
/// 用法:
///   - install.sh 装鲶鱼时调一次 (客户首次部署)
///   - app 启动后调一次 (5/7 现状: 鸿波 mac 已经升级 0.12 但没有保守 config, 一启动就刷上)
///
/// 已有 curator 段 → 不动 (尊重员工已 tune 过的值, 哪怕值跟我们默认不一样).
///
/// 返 Ok(true) = 真写了, Ok(false) = 已存在不动.
pub fn ensure_default() -> Result<bool> {
    let path = yaml_path()?;
    if path.exists() {
        let raw = fs::read_to_string(&path)?;
        let v: YamlValue = serde_yaml::from_str(&raw).unwrap_or(YamlValue::Mapping(Default::default()));
        if v.get("curator").is_some() {
            return Ok(false);
        }
    }
    save(&CuratorConfig::default())?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn with_temp_hermes<F: FnOnce()>(f: F) {
        let tmp = TempDir::new().unwrap();
        let prev = std::env::var("HERMES_HOME").ok();
        std::env::set_var("HERMES_HOME", tmp.path());
        f();
        if let Some(p) = prev {
            std::env::set_var("HERMES_HOME", p);
        } else {
            std::env::remove_var("HERMES_HOME");
        }
    }

    #[test]
    fn load_returns_conservative_default_when_yaml_missing() {
        with_temp_hermes(|| {
            let cfg = load().unwrap();
            assert_eq!(cfg.enabled, true);
            assert_eq!(cfg.stale_after_days, 60);
            assert_eq!(cfg.archive_after_days, 180);
            assert_eq!(cfg.min_idle_hours, 4);
            assert_eq!(cfg.interval_hours, 168);
        });
    }

    #[test]
    fn load_returns_default_when_yaml_no_curator_section() {
        with_temp_hermes(|| {
            let path = yaml_path().unwrap();
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, "other_section:\n  foo: bar\n").unwrap();
            let cfg = load().unwrap();
            assert_eq!(cfg, CuratorConfig::default());
        });
    }

    #[test]
    fn load_reads_existing_curator_section() {
        with_temp_hermes(|| {
            let path = yaml_path().unwrap();
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(
                &path,
                "curator:\n  enabled: false\n  stale_after_days: 30\n  archive_after_days: 90\n",
            )
            .unwrap();
            let cfg = load().unwrap();
            assert_eq!(cfg.enabled, false);
            assert_eq!(cfg.stale_after_days, 30);
            assert_eq!(cfg.archive_after_days, 90);
            // 缺字段补默认
            assert_eq!(cfg.interval_hours, 168);
        });
    }

    #[test]
    fn save_writes_and_round_trips() {
        with_temp_hermes(|| {
            let cfg = CuratorConfig {
                enabled: false,
                interval_hours: 24,
                min_idle_hours: 8,
                stale_after_days: 90,
                archive_after_days: 365,
            };
            save(&cfg).unwrap();
            let loaded = load().unwrap();
            assert_eq!(cfg, loaded);
        });
    }

    #[test]
    fn save_preserves_other_yaml_sections() {
        with_temp_hermes(|| {
            let path = yaml_path().unwrap();
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(
                &path,
                "models:\n  default: gpt-4\nproviders:\n  openai:\n    base_url: https://x\n",
            )
            .unwrap();
            save(&CuratorConfig::default()).unwrap();
            let raw = fs::read_to_string(&path).unwrap();
            assert!(raw.contains("models:"));
            assert!(raw.contains("default: gpt-4"));
            assert!(raw.contains("providers:"));
            assert!(raw.contains("curator:"));
            assert!(raw.contains("stale_after_days: 60"));
        });
    }

    #[test]
    fn save_rejects_bad_archive_smaller_than_stale() {
        with_temp_hermes(|| {
            let cfg = CuratorConfig {
                enabled: true,
                interval_hours: 168,
                min_idle_hours: 4,
                stale_after_days: 100,
                archive_after_days: 50,
            };
            let err = save(&cfg).unwrap_err();
            assert!(err.to_string().contains("archive"));
        });
    }

    #[test]
    fn save_rejects_zero_intervals() {
        with_temp_hermes(|| {
            let mut cfg = CuratorConfig::default();
            cfg.interval_hours = 0;
            assert!(save(&cfg).is_err());
            cfg.interval_hours = 168;
            cfg.min_idle_hours = 0;
            assert!(save(&cfg).is_err());
        });
    }

    #[test]
    fn ensure_default_writes_when_missing() {
        with_temp_hermes(|| {
            assert_eq!(ensure_default().unwrap(), true);
            // 第二次调 → 已存在不动
            assert_eq!(ensure_default().unwrap(), false);
            let cfg = load().unwrap();
            assert_eq!(cfg, CuratorConfig::default());
        });
    }

    #[test]
    fn ensure_default_does_not_overwrite_user_tuned() {
        with_temp_hermes(|| {
            let path = yaml_path().unwrap();
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            // 员工自己 tune 过 (stale=14)
            fs::write(&path, "curator:\n  enabled: true\n  stale_after_days: 14\n").unwrap();
            assert_eq!(ensure_default().unwrap(), false);
            let cfg = load().unwrap();
            assert_eq!(cfg.stale_after_days, 14); // 没被覆盖成 60
        });
    }
}
