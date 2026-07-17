//! BL-STYLE-FP-DIR-PICKER (5/22 鸿波): style_fingerprint scan_dirs UI 管理.
//!
//! 5/22 鸿波: 默认扫 ~/Documents/work + ~/.catfish/output, 大部分员工文档不在那
//! → fingerprint 0 数据. 老办法让员工自己改 ~/.catfish/companion.yaml 加段太硬核,
//! 50 个员工都得学 yaml. 改成 UI 文本输入 + 列表 + 删除按钮, 后端这层只管:
//!
//!   - style_fingerprint_scan_dirs_get → 读 yaml 返当前 scan_dirs
//!   - style_fingerprint_scan_dirs_add → 校验路径 (存在 + 是 dir) + 追加, 不重复
//!   - style_fingerprint_scan_dirs_remove → 删一项
//!
//! yaml 写回策略 (5/22 鸿波):
//!   - **不动**其它段 (advisor / 等), 只改 style_fingerprint.scan_dirs
//!   - 读 → 改 → 全文 dump 写回. serde_yaml 会丢 comments — 第一版能接受,
//!     第二版用 yaml_edit 保 comment. companion.yaml 短, comment 不多.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_yaml::Value as YamlValue;

fn companion_yaml_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("companion.yaml"))
}

fn read_root() -> Result<YamlValue, String> {
    let path = companion_yaml_path()?;
    if !path.exists() {
        return Ok(YamlValue::Mapping(Default::default()));
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 companion.yaml 失败: {e}"))?;
    if text.trim().is_empty() {
        return Ok(YamlValue::Mapping(Default::default()));
    }
    serde_yaml::from_str(&text).map_err(|e| format!("解析 yaml 失败: {e}"))
}

fn write_root(root: &YamlValue) -> Result<(), String> {
    let path = companion_yaml_path()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建 ~/.catfish 失败: {e}"))?;
    }
    let yaml_text = serde_yaml::to_string(root)
        .map_err(|e| format!("序列化 yaml 失败: {e}"))?;
    std::fs::write(&path, yaml_text)
        .map_err(|e| format!("写 companion.yaml 失败: {e}"))
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

fn get_scan_dirs(root: &YamlValue) -> Vec<String> {
    let style = match root.get("style_fingerprint") {
        Some(v) => v,
        None => return Vec::new(),
    };
    let dirs = match style.get("scan_dirs").and_then(|v| v.as_sequence()) {
        Some(s) => s,
        None => return Vec::new(),
    };
    dirs.iter()
        .filter_map(|v| v.as_str().map(|s| s.to_string()))
        .collect()
}

fn set_scan_dirs(root: &mut YamlValue, dirs: Vec<String>) {
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
    let style_key = YamlValue::String("style_fingerprint".to_string());
    let style = map
        .entry(style_key)
        .or_insert_with(|| YamlValue::Mapping(Default::default()));
    let style_map = match style {
        YamlValue::Mapping(m) => m,
        _ => {
            *style = YamlValue::Mapping(Default::default());
            if let YamlValue::Mapping(m) = style {
                m
            } else {
                unreachable!()
            }
        }
    };
    let scan_key = YamlValue::String("scan_dirs".to_string());
    let seq: Vec<YamlValue> = dirs.into_iter().map(YamlValue::String).collect();
    style_map.insert(scan_key, YamlValue::Sequence(seq));
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanDirsResult {
    /// 用户配置的 scan_dirs (yaml 里 style_fingerprint.scan_dirs)
    pub user_dirs: Vec<String>,
    /// 默认目录 (DEFAULT_SOURCE_DIRS in style_fingerprint.py) — 写死不可改
    pub default_dirs: Vec<String>,
    /// yaml 文件路径
    pub yaml_path: String,
}

const DEFAULT_DIRS_DISPLAY: &[&str] = &["~/Documents/work", "~/.catfish/output"];

#[tauri::command]
pub async fn style_fingerprint_scan_dirs_get() -> Result<ScanDirsResult, String> {
    let root = read_root()?;
    Ok(ScanDirsResult {
        user_dirs: get_scan_dirs(&root),
        default_dirs: DEFAULT_DIRS_DISPLAY.iter().map(|s| s.to_string()).collect(),
        yaml_path: companion_yaml_path()?.to_string_lossy().into_owned(),
    })
}

#[tauri::command]
pub async fn style_fingerprint_scan_dirs_add(path: String) -> Result<ScanDirsResult, String> {
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
    let mut dirs = get_scan_dirs(&root);
    // 去重 — 同 raw 或 同 expanded path 都算重复
    let expanded_str = expanded.to_string_lossy().to_string();
    if dirs.iter().any(|d| {
        d == &raw
            || expand_home(d)
                .map(|p| p.to_string_lossy() == expanded_str)
                .unwrap_or(false)
    }) {
        // 已存在不报错, 静默返回当前列表
        return style_fingerprint_scan_dirs_get().await;
    }
    dirs.push(raw);
    set_scan_dirs(&mut root, dirs);
    write_root(&root)?;
    style_fingerprint_scan_dirs_get().await
}

#[tauri::command]
pub async fn style_fingerprint_scan_dirs_remove(
    path: String,
) -> Result<ScanDirsResult, String> {
    let target = path.trim().to_string();
    if target.is_empty() {
        return Err("路径不能空".into());
    }
    let mut root = read_root()?;
    let dirs = get_scan_dirs(&root);
    let new_dirs: Vec<String> = dirs.into_iter().filter(|d| d != &target).collect();
    set_scan_dirs(&mut root, new_dirs);
    write_root(&root)?;
    style_fingerprint_scan_dirs_get().await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expand_home_handles_tilde() {
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
    fn get_set_scan_dirs_roundtrip() {
        let mut root: YamlValue =
            serde_yaml::from_str("advisor:\n  refresh_times: [\"08:30\"]\n").unwrap();
        assert_eq!(get_scan_dirs(&root), Vec::<String>::new());
        set_scan_dirs(&mut root, vec!["~/foo".to_string(), "~/bar".to_string()]);
        assert_eq!(
            get_scan_dirs(&root),
            vec!["~/foo".to_string(), "~/bar".to_string()]
        );
        // advisor 段没丢
        assert!(root.get("advisor").is_some());
    }

    #[test]
    fn set_on_empty_yaml() {
        let mut root = YamlValue::Mapping(Default::default());
        set_scan_dirs(&mut root, vec!["~/x".to_string()]);
        assert_eq!(get_scan_dirs(&root), vec!["~/x".to_string()]);
    }
}
