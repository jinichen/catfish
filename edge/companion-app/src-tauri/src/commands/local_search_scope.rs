//! P3.5.126 (6/26 鸿波 catch "local_search 目录设置 UI 找不到"):
//! local_search 索引目录管理.
//!
//! 写到 ~/.catfish/search-scope.yaml (跟 catfish-local-search/src/catfish_search/config.py
//! CONFIG_FILE 路径对齐) —— python 端 load_config 唯一读的地方.
//!
//! BL-STYLE-FP-USE-INDEX (7/27): 这里是**唯一**的索引目录入口了.
//! 原来还有 style_fingerprint_dirs.rs 写 companion.yaml 的 style_fingerprint.scan_dirs,
//! 两套割裂让鸿波的文书风格抽了两个多月 0 文档, 已整个删除. Onboarding 第 4 步
//! 和 StyleFingerprintCard 现在都走这套命令.
//!
//! 命令:
//!   - local_search_scope_get → 读 yaml 返当前 include/exclude
//!   - local_search_scope_add → 校验路径 (存在 + 是 dir) + 追加, 不重复
//!   - local_search_scope_remove → 删一项
//!
//! yaml 写回策略:
//!   - 不动其它段 (exclude / file_types / max_file_size_mb), 只改 include
//!   - 读 → 改 → 全文 dump 写回. serde_yaml 会丢 comments — 第一版能接受.
//!     search-scope.yaml 有默认头注释, 鸿波改完后注释丢, 但 UI 加目录不靠注释.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_yaml::Value as YamlValue;

fn search_scope_yaml_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("search-scope.yaml"))
}

fn read_root() -> Result<YamlValue, String> {
    let path = search_scope_yaml_path()?;
    if !path.exists() {
        return Ok(YamlValue::Mapping(Default::default()));
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 search-scope.yaml 失败: {e}"))?;
    if text.trim().is_empty() {
        return Ok(YamlValue::Mapping(Default::default()));
    }
    serde_yaml::from_str(&text).map_err(|e| format!("解析 yaml 失败: {e}"))
}

fn write_root(root: &YamlValue) -> Result<(), String> {
    let path = search_scope_yaml_path()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建 ~/.catfish 失败: {e}"))?;
    }
    let yaml_text = serde_yaml::to_string(root)
        .map_err(|e| format!("序列化 yaml 失败: {e}"))?;
    std::fs::write(&path, yaml_text)
        .map_err(|e| format!("写 search-scope.yaml 失败: {e}"))
}

fn expand_home(s: &str) -> Result<PathBuf, String> {
    let s = s.trim();
    if s.is_empty() {
        return Err("路径不能空".into());
    }
    if let Some(rest) = s.strip_prefix("~/") {
        let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
        Ok(PathBuf::from(home).join(rest))
    } else if s == "~" {
        let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
        Ok(PathBuf::from(home))
    } else {
        Ok(PathBuf::from(s))
    }
}

fn get_string_list(root: &YamlValue, key: &str) -> Vec<String> {
    let list = match root.get(key).and_then(|v| v.as_sequence()) {
        Some(s) => s,
        None => return Vec::new(),
    };
    list.iter()
        .filter_map(|v| v.as_str().map(|s| s.to_string()))
        .collect()
}

fn set_string_list(root: &mut YamlValue, key: &str, values: Vec<String>) {
    let map = match root {
        YamlValue::Mapping(m) => m,
        _ => {
            *root = YamlValue::Mapping(Default::default());
            if let YamlValue::Mapping(m) = root {
                m
            } else {
                unreachable!()
            }
        }
    };
    let k = YamlValue::String(key.to_string());
    let seq: Vec<YamlValue> = values.into_iter().map(YamlValue::String).collect();
    map.insert(k, YamlValue::Sequence(seq));
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScopeResult {
    /// include 列表 — 员工想索引的目录 (yaml include 段)
    pub include: Vec<String>,
    /// exclude 列表 — 排除模式 (yaml exclude 段, 只读不让 UI 改)
    pub exclude: Vec<String>,
    /// yaml 文件路径 (员工想 vim 改 file_types / max_file_size_mb 自己去)
    pub yaml_path: String,
}

#[tauri::command]
pub async fn local_search_scope_get() -> Result<ScopeResult, String> {
    let root = read_root()?;
    Ok(ScopeResult {
        include: get_string_list(&root, "include"),
        exclude: get_string_list(&root, "exclude"),
        yaml_path: search_scope_yaml_path()?.to_string_lossy().into_owned(),
    })
}

#[tauri::command]
pub async fn local_search_scope_add(path: String) -> Result<ScopeResult, String> {
    let raw = path.trim().to_string();
    if raw.is_empty() {
        return Err("路径不能空".into());
    }
    if raw.len() > 500 {
        return Err("路径太长 (>500)".into());
    }
    // 校验: 展开 ~ 后必须存在 + 是 dir
    let expanded = expand_home(&raw)?;
    if !expanded.exists() {
        return Err(format!("路径不存在: {}", expanded.display()));
    }
    if !expanded.is_dir() {
        return Err(format!("不是目录: {}", expanded.display()));
    }
    let mut root = read_root()?;
    let mut dirs = get_string_list(&root, "include");
    // 去重 — 同 raw 或 同 expanded path 都算重复
    let expanded_str = expanded.to_string_lossy().to_string();
    if dirs.iter().any(|d| {
        d == &raw
            || expand_home(d)
                .map(|p| p.to_string_lossy() == expanded_str)
                .unwrap_or(false)
    }) {
        // 已存在不报错, 静默返回当前列表
        return local_search_scope_get().await;
    }
    dirs.push(raw);
    set_string_list(&mut root, "include", dirs);
    write_root(&root)?;
    local_search_scope_get().await
}

#[tauri::command]
pub async fn local_search_scope_remove(path: String) -> Result<ScopeResult, String> {
    let target = path.trim().to_string();
    if target.is_empty() {
        return Err("路径不能空".into());
    }
    let mut root = read_root()?;
    let dirs = get_string_list(&root, "include");
    let new_dirs: Vec<String> = dirs.into_iter().filter(|d| d != &target).collect();
    set_string_list(&mut root, "include", new_dirs);
    write_root(&root)?;
    local_search_scope_get().await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::test_env::ENV_LOCK;

    #[test]
    fn expand_home_handles_tilde() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        std::env::set_var("HOME", "/tmp/fakehome");
        assert_eq!(
            expand_home("~/foo/bar").unwrap(),
            PathBuf::from("/tmp/fakehome/foo/bar")
        );
        assert_eq!(expand_home("~").unwrap(), PathBuf::from("/tmp/fakehome"));
        assert_eq!(
            expand_home("/abs/path").unwrap(),
            PathBuf::from("/abs/path")
        );
        assert!(expand_home("").is_err());
        assert!(expand_home("   ").is_err());
    }

    #[test]
    fn get_set_include_roundtrip() {
        let mut root: YamlValue =
            serde_yaml::from_str("exclude:\n  - \"**/node_modules\"\n").unwrap();
        assert_eq!(get_string_list(&root, "include"), Vec::<String>::new());
        set_string_list(&mut root, "include", vec!["~/foo".to_string(), "~/bar".to_string()]);
        assert_eq!(
            get_string_list(&root, "include"),
            vec!["~/foo".to_string(), "~/bar".to_string()]
        );
        // exclude 段没丢
        assert!(root.get("exclude").is_some());
    }

    #[test]
    fn set_on_empty_yaml() {
        let mut root = YamlValue::Mapping(Default::default());
        set_string_list(&mut root, "include", vec!["~/x".to_string()]);
        assert_eq!(get_string_list(&root, "include"), vec!["~/x".to_string()]);
    }
}
