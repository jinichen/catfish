//! BL-ADVISOR-CONFIG (5/22 Phase 7 + cold start v3): advisor 配置读 yaml.
//!
//! 读 ~/.catfish/companion.yaml 的 `advisor` section:
//!   advisor:
//!     refresh_times: ["08:30", "11:30", "14:00", "16:30"]
//!     cache_max_age_minutes: 90
//!
//! 缺 yaml / 缺 advisor section → 用默认值 (4 个时段 + 90 分钟 TTL).
//!
//! 鸿波 5/22 拍: 时段不硬编码, 让员工可改 (跟 useProactiveScheduler 注释一致
//! "后续加 UI, 现在硬编码" 同款修法).

use std::path::PathBuf;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AdvisorConfig {
    /// 时段触发时间 (HH:MM, 本机时区, 24h).
    pub refresh_times: Vec<String>,
    /// 缓存最长存活时间 (分钟). 时段触发挂了的兜底.
    pub cache_max_age_minutes: u32,
}

impl Default for AdvisorConfig {
    fn default() -> Self {
        Self {
            refresh_times: vec![
                "08:30".to_string(),
                "11:30".to_string(),
                "14:00".to_string(),
                "16:30".to_string(),
            ],
            // 5/22 鸿波点: TTL 必须 ≥ 工作时段最大间隔 (3h) + buffer.
            // 否则时段之间 cache 过期会触发意外 LLM 调用, 违背"时段触发" 初衷.
            // 240 min (4h) 覆盖 08:30→11:30 这种 3h 间隔 + 1h buffer.
            // 跨夜 (16:30→次日 08:30 = 16h) 是 by design — 早上时段会自动跑新的.
            cache_max_age_minutes: 240,
        }
    }
}

fn companion_yaml_path() -> Result<PathBuf, String> {
    let home = std::env::var("HOME").map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

/// 读 advisor 配置. 缺 yaml / 缺 section → 默认值.
#[tauri::command]
pub async fn advisor_config_get() -> Result<AdvisorConfig, String> {
    let path = companion_yaml_path()?;
    if !path.exists() {
        return Ok(AdvisorConfig::default());
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 companion.yaml 失败: {e}"))?;

    // yaml 解析 — 整文件读出来, 取 advisor 段
    let root: serde_yaml::Value = serde_yaml::from_str(&text)
        .map_err(|e| format!("解析 yaml 失败: {e}"))?;

    let advisor = match root.get("advisor") {
        Some(v) => v,
        None => return Ok(AdvisorConfig::default()),
    };

    let mut cfg = AdvisorConfig::default();

    // refresh_times: List<String>
    if let Some(times) = advisor.get("refresh_times").and_then(|v| v.as_sequence()) {
        let parsed: Vec<String> = times
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .filter(|s| is_valid_hhmm(s))
            .collect();
        if !parsed.is_empty() {
            cfg.refresh_times = parsed;
        }
    }

    // cache_max_age_minutes: u32
    if let Some(n) = advisor.get("cache_max_age_minutes").and_then(|v| v.as_u64()) {
        if n > 0 && n <= 24 * 60 {
            cfg.cache_max_age_minutes = n as u32;
        }
    }

    Ok(cfg)
}

fn is_valid_hhmm(s: &str) -> bool {
    let parts: Vec<&str> = s.split(':').collect();
    if parts.len() != 2 {
        return false;
    }
    // P3.5.145 (6/30 鸿波): 严格 HH/MM 两位 — test 期望 "8:30" 不接受.
    // len() 对 ASCII 数字 = 字符数, 中文 / 非数字 byte 数 > 2 也会被拒 (符合预期).
    if parts[0].len() != 2 || parts[1].len() != 2 {
        return false;
    }
    let h: u32 = match parts[0].parse() {
        Ok(n) => n,
        Err(_) => return false,
    };
    let m: u32 = match parts[1].parse() {
        Ok(n) => n,
        Err(_) => return false,
    };
    h < 24 && m < 60
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_4_times_plus_240min_ttl() {
        let d = AdvisorConfig::default();
        assert_eq!(d.refresh_times.len(), 4);
        assert!(d.refresh_times.contains(&"08:30".to_string()));
        // TTL 必须 ≥ 工作时段最大间隔 (3h = 180 min) + buffer
        assert!(d.cache_max_age_minutes >= 180, "TTL 不能 < 工作时段最大间隔");
        assert_eq!(d.cache_max_age_minutes, 240);
    }

    #[test]
    fn hhmm_validator() {
        assert!(is_valid_hhmm("08:30"));
        assert!(is_valid_hhmm("00:00"));
        assert!(is_valid_hhmm("23:59"));
        assert!(!is_valid_hhmm("24:00"));
        assert!(!is_valid_hhmm("12:60"));
        assert!(!is_valid_hhmm("8:30"));   // 严格 HH 两位
        assert!(!is_valid_hhmm("garbage"));
    }
}
