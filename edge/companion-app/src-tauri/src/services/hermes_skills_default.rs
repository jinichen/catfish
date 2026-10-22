//! 员工机默认停用 hermes 自带的、跟办公无关的技能 (10/22)。
//!
//! # 为什么
//!
//! hermes 每次聊天都把全部技能的名字 + 说明拼进系统提示 (技能清单)。它自带的一批 —— 机器学习训练、
//! GitHub、写代码调试、作曲 / 动画 / ASCII 画、智能家居、社交媒体、iMessage / Find My ... —— 办公员工
//! 用不上, 却每次都占几千字 (10/22 量: 首个回复前模型要先读完整份请求, 请求越大等得越久)。
//! Apple 提醒事项 / himalaya 邮件跟鲶鱼自己的提醒 / 邮件工具重复, 还会让模型选错。
//!
//! # 怎么做
//!
//! 写进 config.yaml 的 `skills.disabled` (hermes 自己的开关: 不进技能清单, 文件不动, 随时能开)。
//! **每台机器只停一次**: 停过的名字记在 hermes 目录的标记文件里; 之后员工 / IT 在
//! 「设置 → 技能 → 技能库 → 高级」里点「启用」打开的, 下次启动不会再被关掉。名单以后加了新名字,
//! 只停新加的那几个。config.yaml 不存在 (hermes 还没装) → 什么都不做, 也不记标记, 装好后下次启动再停。

use anyhow::{Context, Result};
use serde_yaml::{Mapping, Value as YamlValue};
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

/// 默认停用的 hermes 自带技能 (按 SKILL.md 的 name)。员工机上没有的名字无害。
pub const DEFAULT_OFF: &[&str] = &[
    // 机器学习 / 模型训练部署
    "huggingface-hub", "audiocraft-audio-generation", "segment-anything-model", "serving-llms-vllm",
    "llama-cpp", "evaluating-llms-harness", "weights-and-biases",
    // GitHub / 写代码 / 调试
    "github", "github-auth", "github-repo-management", "github-pr-workflow", "github-issue-to-pr",
    "github-code-review", "github-issues", "test-driven-development", "dogfood", "systematic-debugging",
    "python-debugpy", "codebase-inspection", "spike", "requesting-code-review", "simplify-code",
    "node-inspect-debugger", "inspecting-hermes-desktop-dom", "hermes-tool-backend-diagnosis",
    "hermes-desktop-plugins", "jupyter-live-kernel", "sdlc-review",
    // 别的 AI 编程代理
    "claude-code", "codex", "opencode", "merge-reconciler",
    // 作曲 / 动画 / 创意编程
    "comfyui", "p5js", "songwriting-and-ai-music", "ascii-video", "touchdesigner-mcp", "ascii-art",
    "manim-video", "youtube-content", "heartmula", "songsee", "gif-search",
    // 个人生活 / 社交
    "imessage", "findmy", "openhue", "xurl", "obsidian", "polymarket", "arxiv", "research-paper-writing",
    // 跟鲶鱼自己的工具重复 (提醒 / 邮件), 留着会让模型选错
    "apple-reminders", "himalaya",
];

pub const MARKER: &str = ".catfish-skills-default-off.json";

fn hermes_home() -> Result<PathBuf> {
    if let Ok(home) = std::env::var("HERMES_HOME") {
        return Ok(PathBuf::from(home));
    }
    let home = crate::util::paths::home_env().context("找不到 HOME / USERPROFILE 环境变量")?;
    Ok(crate::services::catfish_paths::hermes_home_for(&PathBuf::from(home)))
}

fn read_yaml(path: &Path) -> Result<YamlValue> {
    let raw = fs::read_to_string(path).with_context(|| format!("读 {}", path.display()))?;
    if raw.trim().is_empty() {
        return Ok(YamlValue::Mapping(Mapping::new()));
    }
    // 解析失败不覆盖 —— 那是 hermes 自己也读不了的文件, 写回去只会丢东西
    serde_yaml::from_str(&raw).with_context(|| format!("{} 不是合法 YAML", path.display()))
}

fn write_yaml(path: &Path, root: &YamlValue) -> Result<()> {
    let out = serde_yaml::to_string(root).context("序列化 config.yaml 失败")?;
    let tmp = path.with_extension("yaml.skills.tmp");
    fs::write(&tmp, out).with_context(|| format!("写 {}", tmp.display()))?;
    fs::rename(&tmp, path).with_context(|| format!("rename → {}", path.display()))
}

fn disabled_of(root: &YamlValue) -> BTreeSet<String> {
    root.get("skills")
        .and_then(|s| s.get("disabled"))
        .and_then(|d| d.as_sequence())
        .map(|seq| seq.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

fn set_disabled(root: &mut YamlValue, names: &BTreeSet<String>) -> Result<()> {
    let map = root.as_mapping_mut().context("config.yaml 顶层不是 mapping")?;
    let skills = map
        .entry(YamlValue::from("skills"))
        .or_insert_with(|| YamlValue::Mapping(Mapping::new()));
    if !skills.is_mapping() {
        *skills = YamlValue::Mapping(Mapping::new());
    }
    let seq = names.iter().map(|n| YamlValue::from(n.as_str())).collect();
    skills.as_mapping_mut().expect("刚确保是 mapping").insert(YamlValue::from("disabled"), YamlValue::Sequence(seq));
    Ok(())
}

/// 停用名单里还没停过的。返回这次新停的名字。
pub fn apply_once_at(config: &Path, marker: &Path) -> Result<Vec<String>> {
    if !config.exists() {
        return Ok(Vec::new());
    }
    let applied: BTreeSet<String> = fs::read_to_string(marker)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();
    let todo: Vec<String> = DEFAULT_OFF.iter().filter(|n| !applied.contains(**n)).map(|n| n.to_string()).collect();
    if todo.is_empty() {
        return Ok(Vec::new());
    }
    let mut root = read_yaml(config)?;
    let mut disabled = disabled_of(&root);
    let added: Vec<String> = todo.iter().filter(|n| disabled.insert((*n).clone())).cloned().collect();
    if !added.is_empty() {
        set_disabled(&mut root, &disabled)?;
        write_yaml(config, &root)?;
    }
    let all: BTreeSet<String> = applied.into_iter().chain(todo).collect();
    fs::write(marker, serde_json::to_string(&all)?).with_context(|| format!("写 {}", marker.display()))?;
    Ok(added)
}

/// 生产入口 (启动 / 装机后调)。
pub fn apply_once() -> Result<Vec<String>> {
    let home = hermes_home()?;
    apply_once_at(&home.join("config.yaml"), &home.join(MARKER))
}

pub fn list_disabled_at(config: &Path) -> Result<Vec<String>> {
    if !config.exists() {
        return Ok(Vec::new());
    }
    Ok(disabled_of(&read_yaml(config)?).into_iter().collect())
}

/// 启用 (从 skills.disabled 去掉) 或停用 (加进去)。返回改没改。
pub fn set_enabled_at(config: &Path, name: &str, enabled: bool) -> Result<bool> {
    let mut root = read_yaml(config)?;
    let mut disabled = disabled_of(&root);
    let changed = if enabled { disabled.remove(name) } else { disabled.insert(name.to_string()) };
    if changed {
        set_disabled(&mut root, &disabled)?;
        write_yaml(config, &root)?;
    }
    Ok(changed)
}

#[tauri::command]
pub fn hermes_skills_disabled() -> Result<Vec<String>, String> {
    hermes_home().and_then(|h| list_disabled_at(&h.join("config.yaml"))).map_err(|e| format!("{e:#}"))
}

/// `names` 为空 = 全部启用。新对话生效 (hermes 建系统提示时读这个开关)。
#[tauri::command]
pub fn hermes_skills_set_enabled(names: Vec<String>, enabled: bool) -> Result<usize, String> {
    let run = || -> Result<usize> {
        let config = hermes_home()?.join("config.yaml");
        let targets = if names.is_empty() { list_disabled_at(&config)? } else { names };
        let mut n = 0;
        for name in &targets {
            n += usize::from(set_enabled_at(&config, name, enabled)?);
        }
        Ok(n)
    };
    run().map_err(|e| format!("{e:#}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn setup(body: &str) -> (TempDir, PathBuf, PathBuf) {
        let tmp = TempDir::new().unwrap();
        let cfg = tmp.path().join("config.yaml");
        fs::write(&cfg, body).unwrap();
        let marker = tmp.path().join(MARKER);
        (tmp, cfg, marker)
    }

    #[test]
    fn 第一次停用_保留原有配置和已停的() {
        let (_t, cfg, marker) = setup("model:\n  provider: custom\nskills:\n  external_dirs:\n  - /x\n  disabled:\n  - mine\n");
        let added = apply_once_at(&cfg, &marker).unwrap();
        assert_eq!(added.len(), DEFAULT_OFF.len());
        let d = list_disabled_at(&cfg).unwrap();
        assert!(d.contains(&"mine".to_string()) && d.contains(&"apple-reminders".to_string()));
        let root = read_yaml(&cfg).unwrap();
        assert_eq!(root["model"]["provider"], YamlValue::from("custom"));
        assert_eq!(root["skills"]["external_dirs"][0], YamlValue::from("/x"));
    }

    #[test]
    fn 员工启用过的_下次启动不再关() {
        let (_t, cfg, marker) = setup("skills: {}\n");
        apply_once_at(&cfg, &marker).unwrap();
        assert!(set_enabled_at(&cfg, "arxiv", true).unwrap());
        assert!(apply_once_at(&cfg, &marker).unwrap().is_empty(), "第二次启动不该再动");
        assert!(!list_disabled_at(&cfg).unwrap().contains(&"arxiv".to_string()));
    }

    #[test]
    fn 名单新加的名字只停新加的() {
        let (_t, cfg, marker) = setup("skills: {}\n");
        let old: Vec<&str> = DEFAULT_OFF.iter().copied().filter(|n| *n != "himalaya").collect();
        fs::write(&marker, serde_json::to_string(&old).unwrap()).unwrap();
        assert_eq!(apply_once_at(&cfg, &marker).unwrap(), vec!["himalaya".to_string()]);
    }

    #[test]
    fn hermes_没装_什么都不做也不记标记() {
        let tmp = TempDir::new().unwrap();
        let cfg = tmp.path().join("config.yaml");
        let marker = tmp.path().join(MARKER);
        assert!(apply_once_at(&cfg, &marker).unwrap().is_empty());
        assert!(!cfg.exists() && !marker.exists());
    }

    #[test]
    fn 坏_yaml_不覆盖() {
        let (_t, cfg, marker) = setup("skills: [unclosed\n");
        assert!(apply_once_at(&cfg, &marker).is_err());
        assert_eq!(fs::read_to_string(&cfg).unwrap(), "skills: [unclosed\n");
    }

    #[test]
    fn 停用再启用() {
        let (_t, cfg, _m) = setup("skills: {}\n");
        assert!(set_enabled_at(&cfg, "obsidian", false).unwrap());
        assert!(!set_enabled_at(&cfg, "obsidian", false).unwrap());
        assert!(set_enabled_at(&cfg, "obsidian", true).unwrap());
        assert!(list_disabled_at(&cfg).unwrap().is_empty());
    }
}
