//! BL-HERMES-CTX-CAP (10/6 鸿波「中间有三次任务停下来了」): 给 hermes 的上下文
//! 压缩设一个**绝对 token 上限**。
//!
//! # 为什么
//!
//! 10/5 重点软件企业那条会话跑到 320K–400K tokens 上下文 (120 多次工具调用,
//! 单个工具结果最大 64K 字, 每步 5–9K 字 reasoning)。hermes 的压缩阈值是
//! `compression.threshold` = 窗口的 50% (小于 512K 的窗口抬到 75%), 而
//! catfish-public-qwen-flash 解析出来的窗口是 1M 量级, 所以到 400K 都没压过
//! 一次。结果是模型 (qwen-flash) 工具调用退化: 一晚上 80 条 "Unrepairable
//! tool_call arguments" / "No code provided", 三次回合以一句打算 + 无工具调用
//! 结束 (agent.log: text_response(finish_reason=stop) response_len=28/32/39)。
//!
//! `compression.threshold_tokens` 是 hermes 自带的旋钮 (config_defaults.py:844):
//! 设了就取 "比例阈值" 和 "这个数" 的较小者, 且 apply 时 clamp 到模型窗口 ——
//! 所以对 40K 窗口的内网模型无副作用, 对 1M 窗口的公网模型把压缩点从 500K
//! 拉到 120K。
//!
//! # 行为
//!
//! 跟 curator_config.rs 同款: 启动时 `ensure_default()` —— config.yaml 的
//! `compression` 段**已有 threshold_tokens** → 不动 (尊重员工/运维调过的值);
//! 没有 → 只补这一个 key, 其余 key 原样保留, atomic 写回。
//!
//! 只有 `_at` 系列接受 Path, 测试直接传 tempdir, 不动 env。

use anyhow::{Context, Result};
use serde_yaml::Value as YamlValue;
use std::fs;
use std::path::{Path, PathBuf};

/// 压缩触发的绝对 token 上限 (hermes 会再 clamp 到模型窗口)。
pub const DEFAULT_THRESHOLD_TOKENS: u64 = 120_000;

/// 10/19 (鸿波「才一句对话就激活压缩, 而且时间都非常长」): 提前修剪的触发线。
///
/// 那次是「股票日报」在对话里做了 47 次搜索 + 61 次读网页, 上下文 12 分钟三次撞到 120K 压缩线,
/// 每次让模型写摘要 3-6 分钟。`compression.proactive_prune_tokens` 是 hermes 自带的旋钮 (默认关):
/// 到这个数就先把**最近 20 条之外、超过 8000 字**的旧工具结果换成一行说明 (原文留在会话记录里, 可以
/// session_search 找回), 不调模型、几乎不花时间。比到 120K 再让模型写摘要丢得少 (只丢旧的大块原文,
/// 员工的话 / 推理 / 最近 20 条不动), 也不会被转述错。设 80K (比 hermes 例子里的 48K 保守) —— 准确性优先。
pub const DEFAULT_PROACTIVE_PRUNE_TOKENS: u64 = 80_000;

/// 启动时补的 compression 默认值: (key, 值)。已有的 key 一律不动。
const DEFAULTS: &[(&str, u64)] = &[
    ("threshold_tokens", DEFAULT_THRESHOLD_TOKENS),
    ("proactive_prune_tokens", DEFAULT_PROACTIVE_PRUNE_TOKENS),
];

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

/// 已配置的 threshold_tokens (None = 没配 / 文件不存在 / 段不存在)。测试用。
#[cfg(test)]
pub fn current_threshold_tokens_at(path: &Path) -> Option<u64> {
    read_root(path)
        .get("compression")?
        .get("threshold_tokens")?
        .as_u64()
}

/// DEFAULTS 里没配的 key → 写入默认值; 已配的 → 不动。返 Ok(true) = 这次写了。
pub fn ensure_default_at(path: &Path) -> Result<bool> {
    let mut root = read_root(path);
    let exists = path.exists();
    // 看 key 在不在, 不看值合不合法 —— 运维写了个奇怪的值也是他的决定, 不覆盖
    let missing: Vec<(&str, u64)> = DEFAULTS
        .iter()
        .copied()
        .filter(|(k, _)| root.get("compression").and_then(|c| c.get(*k)).is_none())
        .collect();
    if missing.is_empty() {
        return Ok(false);
    }
    // hermes 没装 (config.yaml 不存在) → 不凭空造文件, 等装好再说
    if !exists {
        return Ok(false);
    }
    let map = match &mut root {
        YamlValue::Mapping(m) => m,
        _ => anyhow::bail!("config.yaml 顶层不是 mapping"),
    };
    let key = YamlValue::from("compression");
    let section = map
        .entry(key)
        .or_insert_with(|| YamlValue::Mapping(Default::default()));
    let section_map = match section {
        YamlValue::Mapping(m) => m,
        other => {
            // 段存在但不是 mapping (写坏了) —— 覆盖成 mapping, 别让整个文件卡住
            *other = YamlValue::Mapping(Default::default());
            match other {
                YamlValue::Mapping(m) => m,
                _ => unreachable!(),
            }
        }
    };
    for (k, v) in &missing {
        section_map.insert(YamlValue::from(*k), YamlValue::from(*v));
    }
    let out = serde_yaml::to_string(&root).context("序列化 config.yaml 失败")?;
    let tmp = path.with_extension("yaml.tmp");
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
    fn 已有压缩线的也补上提前修剪_值都不覆盖() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(&tmp, "compression:\n  threshold_tokens: 90000\n");
        assert!(ensure_default_at(&p).unwrap());
        let root = read_root(&p);
        assert_eq!(root["compression"]["threshold_tokens"].as_u64(), Some(90000), "员工 / 运维调过的不动");
        assert_eq!(root["compression"]["proactive_prune_tokens"].as_u64(), Some(DEFAULT_PROACTIVE_PRUNE_TOKENS));
        assert!(!ensure_default_at(&p).unwrap(), "第二次 no-op");
        let p2 = cfg(&tmp, "compression:\n  threshold_tokens: 120000\n  proactive_prune_tokens: 0\n");
        assert!(!ensure_default_at(&p2).unwrap(), "员工明确关掉 (0) 也不动");
    }

    #[test]
    fn adds_threshold_without_touching_other_sections() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(&tmp, "model:\n  default: catfish-auto\ncurator:\n  enabled: true\n");
        assert!(ensure_default_at(&p).unwrap());
        assert_eq!(current_threshold_tokens_at(&p), Some(DEFAULT_THRESHOLD_TOKENS));
        let raw = fs::read_to_string(&p).unwrap();
        assert!(raw.contains("catfish-auto"));
        assert!(raw.contains("curator"));
        // 第二次是 no-op
        assert!(!ensure_default_at(&p).unwrap());
    }

    #[test]
    fn existing_section_keeps_its_other_keys() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(&tmp, "compression:\n  enabled: true\n  threshold: 0.6\n");
        assert!(ensure_default_at(&p).unwrap());
        let root = read_root(&p);
        let c = root.get("compression").unwrap();
        assert_eq!(c.get("threshold").and_then(|v| v.as_f64()), Some(0.6));
        assert_eq!(c.get("enabled").and_then(|v| v.as_bool()), Some(true));
        assert_eq!(c.get("threshold_tokens").and_then(|v| v.as_u64()), Some(DEFAULT_THRESHOLD_TOKENS));
    }

    #[test]
    fn operator_value_is_respected() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(&tmp, "compression:\n  threshold_tokens: 50000\n");
        // 10/19 起会补上缺的 proactive_prune_tokens (所以这次有写), 但运维设的 50000 原样保留
        assert!(ensure_default_at(&p).unwrap());
        assert_eq!(current_threshold_tokens_at(&p), Some(50_000));
    }
}
