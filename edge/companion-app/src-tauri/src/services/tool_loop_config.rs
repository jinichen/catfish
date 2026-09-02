//! 为 Companion 使用的 Hermes 开启每轮工具循环硬停止。
//!
//! Hermes 自带完整检测器和阈值，但 `hard_stop_enabled` 默认关闭，只提示模型。
//! 9/2 实盘里提示没有阻止重复调用，最终 65 组工具结果才被中央网关 409 截停。
//! Companion 只负责打开上游开关，阈值继续继承 Hermes 当前版本默认值，避免两套
//! 数字长期漂移。已有 `tool_loop_guardrails` 配置则完全尊重，不覆盖员工选择。

use anyhow::{anyhow, Context, Result};
use serde_yaml::Value as YamlValue;
use std::fs;
use std::path::{Path, PathBuf};

const BLOCK: &str = "  tool_loop_guardrails:\n    hard_stop_enabled: true\n";

fn yaml_path() -> Result<PathBuf> {
    if let Ok(home) = std::env::var("HERMES_HOME") {
        return Ok(PathBuf::from(home).join("config.yaml"));
    }
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| anyhow!("找不到 HOME 环境变量"))?;
    Ok(PathBuf::from(home).join(".hermes").join("config.yaml"))
}

fn agent_has_guardrails(value: &YamlValue) -> Result<bool> {
    let Some(root) = value.as_mapping() else {
        return Err(anyhow!("config.yaml 顶层不是 mapping"));
    };
    let Some(agent) = root.get(YamlValue::from("agent")) else {
        return Ok(false);
    };
    let Some(agent) = agent.as_mapping() else {
        return Err(anyhow!("config.yaml 的 agent 不是 mapping"));
    };
    Ok(agent.contains_key(YamlValue::from("tool_loop_guardrails")))
}

fn insert_under_agent(raw: &str) -> Result<String> {
    let lines: Vec<&str> = raw.split_inclusive('\n').collect();
    let Some(start) = lines.iter().position(|line| {
        let trimmed = line.trim_end_matches(['\r', '\n']);
        trimmed == "agent:" || trimmed.starts_with("agent: #")
    }) else {
        let mut out = raw.to_string();
        if !out.is_empty() && !out.ends_with('\n') {
            out.push('\n');
        }
        out.push_str("\nagent:\n");
        out.push_str(BLOCK);
        return Ok(out);
    };

    let mut end = lines.len();
    for (index, line) in lines.iter().enumerate().skip(start + 1) {
        let text = line.trim_end_matches(['\r', '\n']);
        if text.is_empty() || text.trim_start().starts_with('#') {
            continue;
        }
        if !text.starts_with(' ') && !text.starts_with('\t') {
            end = index;
            break;
        }
    }

    let mut out = String::with_capacity(raw.len() + BLOCK.len());
    for line in &lines[..end] {
        out.push_str(line);
    }
    if !out.ends_with('\n') {
        out.push('\n');
    }
    out.push_str(BLOCK);
    for line in &lines[end..] {
        out.push_str(line);
    }
    Ok(out)
}

/// 缺配置时开启硬停止；保留原文件注释和其他字段。
pub fn ensure_at(path: &Path) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    let raw = fs::read_to_string(path)
        .with_context(|| format!("读 {} 失败", path.display()))?;
    let parsed: YamlValue = serde_yaml::from_str(&raw)
        .with_context(|| format!("解析 {} 失败", path.display()))?;
    if agent_has_guardrails(&parsed)? {
        return Ok(false);
    }

    let out = insert_under_agent(&raw)?;
    // 写前再解析一次，防文本插入产生不可用配置。
    let _: YamlValue = serde_yaml::from_str(&out).context("补入工具循环配置后 YAML 无效")?;
    let tmp = path.with_extension("yaml.catfish-loop-tmp");
    fs::write(&tmp, out).with_context(|| format!("写 {} 失败", tmp.display()))?;
    fs::rename(&tmp, path).with_context(|| format!("替换 {} 失败", path.display()))?;
    Ok(true)
}

pub fn ensure() -> Result<bool> {
    ensure_at(&yaml_path()?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn write(dir: &TempDir, body: &str) -> PathBuf {
        let path = dir.path().join("config.yaml");
        fs::write(&path, body).unwrap();
        path
    }

    #[test]
    fn appends_agent_section_when_missing() {
        let dir = TempDir::new().unwrap();
        let path = write(&dir, "# keep me\nmodel:\n  name: private\n");
        assert!(ensure_at(&path).unwrap());
        let out = fs::read_to_string(path).unwrap();
        assert!(out.starts_with("# keep me\nmodel:\n  name: private\n"));
        assert!(out.contains("agent:\n  tool_loop_guardrails:\n    hard_stop_enabled: true"));
    }

    #[test]
    fn inserts_into_existing_agent_mapping() {
        let dir = TempDir::new().unwrap();
        let path = write(
            &dir,
            "agent:\n  stall_guards: true\nmodel:\n  name: private\n",
        );
        assert!(ensure_at(&path).unwrap());
        let out = fs::read_to_string(path).unwrap();
        assert!(out.contains(
            "agent:\n  stall_guards: true\n  tool_loop_guardrails:\n    hard_stop_enabled: true\nmodel:"
        ));
    }

    #[test]
    fn respects_existing_user_configuration() {
        let dir = TempDir::new().unwrap();
        let original = "agent:\n  tool_loop_guardrails:\n    hard_stop_enabled: false\n";
        let path = write(&dir, original);
        assert!(!ensure_at(&path).unwrap());
        assert_eq!(fs::read_to_string(path).unwrap(), original);
    }

    #[test]
    fn missing_file_is_noop() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("missing.yaml");
        assert!(!ensure_at(&path).unwrap());
        assert!(!path.exists());
    }

    #[test]
    fn rejects_non_mapping_agent_without_touching_file() {
        let dir = TempDir::new().unwrap();
        let original = "agent: disabled\n";
        let path = write(&dir, original);
        assert!(ensure_at(&path).is_err());
        assert_eq!(fs::read_to_string(path).unwrap(), original);
    }
}
