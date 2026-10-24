//! wiki slug 工具 —— 校验 / 归一化 / 碰撞检测 / 文件名生成。
//!
//! 2026-08-15 从 wiki_write.rs 切出来 (906 行超限)。纯搬迁, 逻辑一行未改,
//! 只把跨文件用到的几个改成 pub(crate)。
//!
//! 这组的核心是 `normalize_slug` + `find_normalized_collision`: 同一个东西被
//! 写成 `FFCS数字鲶鱼` / `FFCS 数字鲶鱼` / `ffcs_数字鲶鱼` 时不能变成 3 个条目。

use std::fs;
use std::path::{Path, PathBuf};

fn home_dir() -> Result<PathBuf, String> {
    crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME".to_string())
}

pub(crate) fn catfish_home() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish"))
}

/// slug 校验 — unicode word char + `-` + `_`, 不允许 path-unsafe.
/// Rust 真 char::is_alphanumeric 接受 unicode (CJK / accented).
pub(crate) fn validate_slug(slug: &str) -> Result<(), String> {
    if slug.is_empty() {
        return Err("slug 不能空".to_string());
    }
    if slug.len() > 100 {
        return Err(format!("slug 太长 (>100 chars): {slug}"));
    }
    for c in slug.chars() {
        if !c.is_alphanumeric() && c != '-' && c != '_' {
            return Err(format!("slug 含非法字符 '{c}': {slug}"));
        }
    }
    if slug.starts_with('-') || slug.starts_with('.') {
        return Err(format!("slug 不能 - / . 开头: {slug}"));
    }
    Ok(())
}

/// P3.3.3 (6/9 鸿波) — slug normalize, 防 `FFCS数字鲶鱼` / `FFCS 数字鲶鱼` /
/// `FFCS-数字鲶鱼` / `ffcs_数字鲶鱼` 被当成 4 个不同 entity.
///
/// 规则: 去所有 whitespace / `-` / `_` / `.`, 再 lowercase.
/// 比对用, 不参与文件名生成 (文件名仍走 slugify).
fn normalize_slug(s: &str) -> String {
    s.chars()
        .filter(|c| {
            !c.is_whitespace()
                && *c != '-'
                && *c != '_'
                && *c != '.'
                && *c != '\u{3000}' // 全角空格
        })
        .collect::<String>()
        .to_lowercase()
}

/// 扫现有 entities/ / concepts/ 找 normalize-等价的 slug, 命中返已有 rel_path.
pub(crate) fn find_normalized_collision(
    home: &Path,
    sub_dir: &str,
    new_slug: &str,
) -> Option<String> {
    let norm_new = normalize_slug(new_slug);
    if norm_new.is_empty() {
        return None;
    }
    let dir = home.join(sub_dir);
    if !dir.is_dir() {
        return None;
    }
    let entries = fs::read_dir(&dir).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("md") {
            continue;
        }
        let stem = match path.file_stem().and_then(|s| s.to_str()) {
            Some(s) => s,
            None => continue,
        };
        if normalize_slug(stem) == norm_new {
            return Some(format!("{sub_dir}/{stem}.md"));
        }
    }
    None
}

/// 10/24: 新建条目的文件名 —— 标题里 validate_slug 不收的字 (+ （ ） 、 & …) 一律换成 `-`, 连续的并一个。
/// 原来 slugify 只换路径危险的那十几个字, 「AI+政务产品包」「IDC（算力）专业化直营团队」生成的 slug 被
/// validate_slug 拦下, 员工在条目页点「新建这个条目」只看到「slug 含非法字符 '+'」。
/// 标题原样写进 frontmatter, 只有文件名换字; 资料入库 (raw/sources 文件名) 仍用 slugify, 不受影响。
pub(crate) fn entry_slug(title: &str, max_chars: usize) -> String {
    let mut out = String::new();
    for c in slugify(title, max_chars).chars() {
        let c = if c.is_alphanumeric() || c == '_' { c } else { '-' };
        if !(c == '-' && out.ends_with('-')) {
            out.push(c);
        }
    }
    let trimmed = out.trim_matches('-');
    if trimmed.is_empty() { "untitled".to_string() } else { trimmed.to_string() }
}

/// title slugify — 简单 replace 非 word char 为 `-` (跟 P1.2.2 wiki_save slugify 一致).
pub(crate) fn slugify(title: &str, max_chars: usize) -> String {
    let bad: &[char] = &[
        '/', '\\', ':', '*', '?', '"', '<', '>', '|', '\n', '\r', '\t', ' ', '\u{3000}', '.',
    ];
    let cleaned: String = title
        .chars()
        .take(max_chars)
        .map(|c| if bad.contains(&c) { '-' } else { c })
        .collect();
    let trimmed = cleaned.trim_matches('-').trim_matches('.');
    if trimmed.is_empty() {
        "untitled".to_string()
    } else {
        trimmed.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn entry_slug_带加号括号顿号的标题也能建条目() {
        for title in ["AI+政务产品包", "IDC（算力）专业化直营团队", "3+5+1专项团队", "福州、厦门、漳州公共智算中心", "A & B"] {
            let slug = entry_slug(title, 50);
            assert!(validate_slug(&slug).is_ok(), "{title} → {slug}");
        }
        assert_eq!(entry_slug("AI+政务产品包", 50), "AI-政务产品包");
        assert_eq!(entry_slug("IDC（算力）专业化直营团队", 50), "IDC-算力-专业化直营团队");
        assert_eq!(entry_slug("3+5+1专项团队", 50), "3-5-1专项团队");
        assert_eq!(entry_slug("+++", 50), "untitled");
    }
}
