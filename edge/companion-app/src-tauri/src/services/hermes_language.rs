//! 10/13 鸿波「要彻底的话把 hermes 切成中文」: 给 hermes 设界面语言 `display.language: zh`。
//!
//! # 为什么
//!
//! hermes 发给员工的固定提示有两种: 写死在代码里的英文 (审批提示、排队 / 重启 / 出错提示
//! —— catfish-xcatfish-user 插件 P28 / P51 逐句译), 和走它自带翻译表 `locales/*.yaml` 的
//! 454 处 (/status /reset /stop 的回复、审批回执、过期提示 ...)。后者只认 `display.language`,
//! 不设就是英文, 微信里会冒出来。
//!
//! 影响范围 (agent/i18n.py 文件头写明): **只有 hermes 自己显示给人看的固定提示**; 模型看到的
//! 内容、工具输出、日志都不变, 不影响模型行为。是整个 hermes 进程的开关 —— 微信、Companion
//! 里透出的 hermes 提示、命令行 `hermes ...` 一起变中文 (Companion 代码不依赖这些英文原文,
//! 10/13 已 grep 核过)。hermes 按进程缓存这个值, 下次 hermes 重启生效。
//!
//! # 行为
//!
//! 跟 hermes_compression.rs 同款: 启动时 `ensure_default()` —— `display.language` **已有**
//! → 不动 (员工 / 运维设过的语言尊重); 没有 → 只补这一个 key, 其余原样保留, atomic 写回;
//! config.yaml 不存在 (hermes 没装) → 不凭空造文件。

use anyhow::{Context, Result};
use serde_yaml::Value as YamlValue;
use std::fs;
use std::path::{Path, PathBuf};

/// hermes 界面语言 (agent/i18n.py 支持 zh / zh-hant / en ...)。
pub const DEFAULT_LANGUAGE: &str = "zh";

fn yaml_path() -> Result<PathBuf> {
    if let Ok(home) = std::env::var("HERMES_HOME") {
        return Ok(PathBuf::from(home).join("config.yaml"));
    }
    let home = crate::util::paths::home_env().context("找不到 HOME / USERPROFILE 环境变量")?;
    Ok(crate::services::catfish_paths::hermes_home_for(&PathBuf::from(home)).join("config.yaml"))
}

fn read_root(path: &Path) -> YamlValue {
    fs::read_to_string(path)
        .ok()
        .and_then(|raw| serde_yaml::from_str::<YamlValue>(&raw).ok())
        .unwrap_or_else(|| YamlValue::Mapping(Default::default()))
}

/// 已配置的 display.language (None = 没配)。测试用。
#[cfg(test)]
pub fn current_language_at(path: &Path) -> Option<String> {
    read_root(path)
        .get("display")?
        .get("language")?
        .as_str()
        .map(str::to_string)
}

/// 没配 display.language → 写 "zh"; 已配 → 不动。返 Ok(true) = 这次写了。
pub fn ensure_default_at(path: &Path) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    let mut root = read_root(path);
    // 看 key 在不在, 不看值 —— 设成 en 也是有人的决定, 不覆盖
    if root.get("display").and_then(|d| d.get("language")).is_some() {
        return Ok(false);
    }
    let map = match &mut root {
        YamlValue::Mapping(m) => m,
        _ => anyhow::bail!("config.yaml 顶层不是 mapping"),
    };
    let section = map
        .entry(YamlValue::from("display"))
        .or_insert_with(|| YamlValue::Mapping(Default::default()));
    if !matches!(section, YamlValue::Mapping(_)) {
        // 段存在但不是 mapping (写坏了) —— 覆盖成 mapping, 别让整个文件卡住
        *section = YamlValue::Mapping(Default::default());
    }
    if let YamlValue::Mapping(m) = section {
        m.insert(YamlValue::from("language"), YamlValue::from(DEFAULT_LANGUAGE));
    }
    let out = serde_yaml::to_string(&root).context("序列化 config.yaml 失败")?;
    let tmp = path.with_extension("yaml.lang.tmp");
    fs::write(&tmp, out).with_context(|| format!("写 {}", tmp.display()))?;
    fs::rename(&tmp, path).with_context(|| format!("rename → {}", path.display()))?;
    Ok(true)
}

/// 生产入口 (lib.rs setup 用)。
pub fn ensure_default() -> Result<bool> {
    ensure_default_at(&yaml_path()?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn cfg(tmp: &TempDir, body: &str) -> PathBuf {
        let p = tmp.path().join("config.yaml");
        fs::write(&p, body).unwrap();
        p
    }

    #[test]
    fn missing_file_is_left_alone() {
        let tmp = TempDir::new().unwrap();
        let p = tmp.path().join("config.yaml");
        assert!(!ensure_default_at(&p).unwrap());
        assert!(!p.exists());
    }

    #[test]
    fn adds_language_keeping_other_display_keys_and_sections() {
        let tmp = TempDir::new().unwrap();
        // 跟本机真实 config 同形: display 段已有 show_reasoning
        let p = cfg(&tmp, "display:\n  show_reasoning: false\nmemory:\n  provider: catfish-memory\n");
        assert!(ensure_default_at(&p).unwrap());
        assert_eq!(current_language_at(&p).as_deref(), Some("zh"));
        let root = read_root(&p);
        assert_eq!(root["display"]["show_reasoning"].as_bool(), Some(false));
        assert_eq!(root["memory"]["provider"].as_str(), Some("catfish-memory"));
        // 第二次是 no-op
        assert!(!ensure_default_at(&p).unwrap());
    }

    #[test]
    fn adds_display_section_when_absent() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(&tmp, "model:\n  default: catfish-auto\n");
        assert!(ensure_default_at(&p).unwrap());
        assert_eq!(current_language_at(&p).as_deref(), Some("zh"));
    }

    #[test]
    fn operator_language_is_respected() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(&tmp, "display:\n  language: en\n");
        assert!(!ensure_default_at(&p).unwrap());
        assert_eq!(current_language_at(&p).as_deref(), Some("en"));
    }

    #[test]
    fn broken_display_section_is_repaired() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(&tmp, "display: oops\n");
        assert!(ensure_default_at(&p).unwrap());
        assert_eq!(current_language_at(&p).as_deref(), Some("zh"));
    }
}
