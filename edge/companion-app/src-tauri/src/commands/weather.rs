//! P3.3.8 (6/10 鸿波): 早安天气.
//!
//! 数据源 wttr.in (无需 API key, JSON 返). 国内访问慢时 fall back 占位.
//! - 默认按 IP 自动定位 (wttr.in 服务端解析访问 IP) — 但 IP 走 Clash 出口可能不准
//! - 用户可在 ~/.catfish/weather_config.json 配 home_city (覆盖 IP 自动)
//! - temp_cities[] 临时城市 (出差用), 多城市并排显
//!
//! Cache 文件 ~/.catfish/weather_cache.json. 6 小时 stale 触发重拉.
//! 一天 4 次 (00:00 / 06:00 / 12:00 / 18:00 自然落点).
//!
//! 跟 BL-CENTRAL-EDGE: 员工本机数据, 天气直连 wttr.in 不出端 (中央 0 知道员工位置).

use std::path::PathBuf;
use std::time::Duration;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

const CACHE_TTL_HOURS: i64 = 6;       // 6h stale → 1 天 4 次
const HTTP_TIMEOUT: Duration = Duration::from_secs(8);

// ── 数据结构 ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WeatherConfig {
    /// 常驻城市. 空字符串 = 走 wttr.in IP 自动. 用户填了就用填的.
    pub home_city: String,
    /// 临时城市列表 (出差).
    pub temp_cities: Vec<String>,
}

impl Default for WeatherConfig {
    fn default() -> Self {
        Self {
            home_city: String::new(),  // 走 IP 自动
            temp_cities: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WeatherEntry {
    pub city: String,
    pub temp_c: String,           // 摄氏度
    pub desc: String,             // "晴" / "多云" / "小雨"
    pub humidity: String,         // %
    pub wind: String,             // 风速 km/h
    pub icon: String,             // emoji ☀️ ⛅ 🌧 等
    pub raw_area: String,         // wttr.in nearest_area, 给 debug
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WeatherCache {
    pub fetched_at: String,       // ISO-8601
    pub entries: Vec<WeatherEntry>,
    pub stale: bool,              // 客户端判断, server 不写
    pub error: Option<String>,    // 拉失败时 fallback
}

// ── 路径 helpers ─────────────────────────────────────────────────────

fn catfish_dir() -> Result<PathBuf, String> {
    let home = std::env::var("HOME").map_err(|e| format!("HOME 未设: {e}"))?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir)
}

fn config_path() -> Result<PathBuf, String> {
    Ok(catfish_dir()?.join("weather_config.json"))
}

fn cache_path() -> Result<PathBuf, String> {
    Ok(catfish_dir()?.join("weather_cache.json"))
}

// ── wttr.in fetch ────────────────────────────────────────────────────

fn parse_wttr_json(value: &serde_json::Value, requested_city: &str) -> Option<WeatherEntry> {
    let current = value.get("current_condition")?.as_array()?.first()?;
    let temp_c = current.get("temp_C")?.as_str()?.to_string();
    let humidity = current.get("humidity")?.as_str()?.to_string();
    let wind = current.get("windspeedKmph")?.as_str()?.to_string();
    let desc = current
        .get("lang_zh")
        .and_then(|v| v.as_array())
        .and_then(|arr| arr.first())
        .and_then(|item| item.get("value"))
        .and_then(|v| v.as_str())
        .map(String::from)
        .or_else(|| {
            current
                .get("weatherDesc")
                .and_then(|v| v.as_array())
                .and_then(|arr| arr.first())
                .and_then(|item| item.get("value"))
                .and_then(|v| v.as_str())
                .map(String::from)
        })
        .unwrap_or_else(|| "未知".to_string());

    // nearest_area for debug
    let area = value
        .get("nearest_area")
        .and_then(|v| v.as_array())
        .and_then(|arr| arr.first())
        .and_then(|item| item.get("areaName"))
        .and_then(|v| v.as_array())
        .and_then(|arr| arr.first())
        .and_then(|item| item.get("value"))
        .and_then(|v| v.as_str())
        .map(String::from)
        .unwrap_or_default();

    // emoji 映射 (wttr.in 自己有 weatherCode 字段, 但 hand map 简单)
    let icon = match desc.as_str() {
        s if s.contains("晴") || s.contains("Sunny") || s.contains("Clear") => "☀️",
        s if s.contains("多云") || s.contains("Cloudy") || s.contains("Overcast") => "☁️",
        s if s.contains("阴") => "☁️",
        s if s.contains("雨") || s.contains("Rain") || s.contains("Shower") => "🌧",
        s if s.contains("雪") || s.contains("Snow") => "🌨",
        s if s.contains("雾") || s.contains("Mist") || s.contains("Fog") => "🌫",
        s if s.contains("雷") || s.contains("Thunder") => "⛈",
        _ => "🌤",
    };

    Some(WeatherEntry {
        city: if requested_city.is_empty() {
            area.clone()
        } else {
            requested_city.to_string()
        },
        temp_c,
        desc,
        humidity,
        wind,
        icon: icon.to_string(),
        raw_area: area,
    })
}

async fn fetch_one_city(city: &str) -> Result<WeatherEntry, String> {
    // 空 city → wttr.in 按 IP 自动定位
    let url = if city.is_empty() {
        "https://wttr.in/?format=j1".to_string()
    } else {
        format!("https://wttr.in/{}?format=j1", urlencoding::encode(city))
    };

    let client = reqwest::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .build()
        .map_err(|e| format!("build http client 失败: {e}"))?;

    let resp = client
        .get(&url)
        .header("User-Agent", "catfish-companion/0.16")
        .send()
        .await
        .map_err(|e| format!("拉 {url} 失败: {e}"))?;

    if !resp.status().is_success() {
        return Err(format!("wttr.in HTTP {}", resp.status()));
    }

    let body = resp
        .text()
        .await
        .map_err(|e| format!("读 body 失败: {e}"))?;

    let value: serde_json::Value = serde_json::from_str(&body)
        .map_err(|e| format!("parse JSON 失败 (前 100 字: {}): {e}", &body.chars().take(100).collect::<String>()))?;

    parse_wttr_json(&value, city).ok_or_else(|| "wttr.in 返结构不识别".to_string())
}

// ── Tauri commands ───────────────────────────────────────────────────

#[tauri::command]
pub async fn weather_config_get() -> Result<WeatherConfig, String> {
    let path = config_path()?;
    if !path.exists() {
        return Ok(WeatherConfig::default());
    }
    let content = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 {path:?} 失败: {e}"))?;
    serde_json::from_str(&content).map_err(|e| format!("parse weather_config 失败: {e}"))
}

#[tauri::command(rename_all = "camelCase")]
pub async fn weather_config_set(config: WeatherConfig) -> Result<(), String> {
    let path = config_path()?;
    let line = serde_json::to_string_pretty(&config)
        .map_err(|e| format!("serialize 失败: {e}"))?;
    std::fs::write(&path, line).map_err(|e| format!("写 {path:?} 失败: {e}"))?;
    Ok(())
}

/// 拉天气 — 默认走 cache (6h stale), force=true 强制重拉.
#[tauri::command(rename_all = "camelCase")]
pub async fn weather_get(force: Option<bool>) -> Result<WeatherCache, String> {
    let force = force.unwrap_or(false);
    let path = cache_path()?;

    // 1. 看 cache
    if !force && path.exists() {
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Ok(mut cache) = serde_json::from_str::<WeatherCache>(&content) {
                if let Ok(fetched) = cache.fetched_at.parse::<DateTime<Utc>>() {
                    let age_hours = (Utc::now() - fetched).num_hours();
                    cache.stale = age_hours >= CACHE_TTL_HOURS;
                    if !cache.stale {
                        return Ok(cache);
                    }
                }
            }
        }
    }

    // 2. 重拉
    let config = weather_config_get().await.unwrap_or_default();
    let mut cities: Vec<String> = Vec::new();
    cities.push(config.home_city.clone()); // 空 = IP 自动
    for c in &config.temp_cities {
        if !c.trim().is_empty() {
            cities.push(c.trim().to_string());
        }
    }

    let mut entries = Vec::new();
    let mut error: Option<String> = None;
    for c in &cities {
        match fetch_one_city(c).await {
            Ok(entry) => entries.push(entry),
            Err(e) => {
                if error.is_none() {
                    error = Some(format!("{}: {}", if c.is_empty() { "本地" } else { c }, e));
                }
            }
        }
    }

    let cache = WeatherCache {
        fetched_at: Utc::now().to_rfc3339(),
        entries,
        stale: false,
        error,
    };

    // 3. 写 cache (即使 entries 空, 也写, 防失败死循环重试)
    if let Ok(line) = serde_json::to_string_pretty(&cache) {
        let _ = std::fs::write(&path, line);
    }

    Ok(cache)
}
