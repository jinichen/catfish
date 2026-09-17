//! 9/17 (semantica 第 2 条): wiki frontmatter 冲突 —— 读 + 员工二选一。
//!
//! 写侧在蒸馏插件 (catfish_memory_wiki_provenance.record_conflicts): 第二次蒸馏把
//! 同一条目的类型或 typed relation 改成不同的值时, **保留旧值**, 新值记进
//!
//!     conflicts: [{field: "entity_type", current: "org", proposed: "department", seen: "journal:2026-09-17"},
//!                 {field: "rel:中电福富", current: "隶属", proposed: "协作", seen: "..."}]
//!
//! 这里只做两件事: 把这一行解析成结构给前端 (关系工作台列成"冲突"任务), 以及
//! 员工选了之后把文件改掉 —— 选 proposed 就把值换过去, 选 current 什么都不动;
//! 两种都把那条冲突从 conflicts 里删掉, 并打 authored_by: employee (这是人定的)。

use serde::{Deserialize, Serialize};
use std::fs;

use super::wiki_frontmatter::mark_authored_by_employee;
use super::wiki_slug::catfish_home;

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct WikiConflict {
    /// `entity_type` / `concept_type` / `rel:<对方名字>`
    pub field: String,
    pub current: String,
    pub proposed: String,
    /// 新值来自哪 (journal:日期 / raw/sources/...), 可能为空
    pub seen: String,
}

/// 从 frontmatter 文本里解析 `conflicts:` 行。没有 → 空。
pub(crate) fn parse_conflicts(fm: &str) -> Vec<WikiConflict> {
    let Some(line) = fm.lines().map(str::trim).find(|l| l.starts_with("conflicts:")) else {
        return Vec::new();
    };
    let inner = line["conflicts:".len()..].trim().trim_start_matches('[').trim_end_matches(']');
    split_top_level(inner)
        .into_iter()
        .filter_map(|item| parse_item(item.trim()))
        .collect()
}

fn parse_item(item: &str) -> Option<WikiConflict> {
    let body = item.strip_prefix('{')?.strip_suffix('}')?;
    let mut c = WikiConflict { field: String::new(), current: String::new(), proposed: String::new(), seen: String::new() };
    for (k, v) in quoted_pairs(body) {
        match k.as_str() {
            "field" => c.field = v,
            "current" => c.current = v,
            "proposed" => c.proposed = v,
            "seen" => c.seen = v,
            _ => {}
        }
    }
    (!c.field.is_empty()).then_some(c)
}

/// `key: "value", key2: "v2"` → pairs。值一律带双引号 (写侧 _quote 保证), 内部 `\"` 解回。
fn quoted_pairs(body: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut rest = body;
    while let Some(colon) = rest.find(':') {
        let key = rest[..colon].trim().trim_start_matches(',').trim().to_string();
        rest = rest[colon + 1..].trim_start();
        let Some(r) = rest.strip_prefix('"') else { break };
        let mut val = String::new();
        let mut chars = r.char_indices();
        let mut end = r.len();
        while let Some((i, ch)) = chars.next() {
            match ch {
                '\\' => {
                    if let Some((_, esc)) = chars.next() {
                        val.push(esc);
                    }
                }
                '"' => {
                    end = i + 1;
                    break;
                }
                _ => val.push(ch),
            }
        }
        out.push((key, val));
        rest = &r[end..];
    }
    out
}

/// 顶层 split —— `{...}` / `[...]` / 双引号 里的 `,` 不切。wiki_read.rs 也用它 (9/17 从那边搬来, 加了认引号)。
pub(crate) fn split_top_level(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut depth = 0i32;
    let mut in_str = false;
    let mut buf = String::new();
    for c in s.chars() {
        match c {
            '"' => {
                in_str = !in_str;
                buf.push(c);
            }
            '{' | '[' if !in_str => {
                depth += 1;
                buf.push(c);
            }
            '}' | ']' if !in_str => {
                depth -= 1;
                buf.push(c);
            }
            ',' if depth == 0 && !in_str => {
                if !buf.trim().is_empty() {
                    out.push(buf.clone());
                }
                buf.clear();
            }
            _ => buf.push(c),
        }
    }
    if !buf.trim().is_empty() {
        out.push(buf);
    }
    out
}

fn quote(v: &str) -> String {
    format!("\"{}\"", v.replace('\\', "\\\\").replace('"', "\\\""))
}

fn render(conflicts: &[WikiConflict]) -> String {
    let items: Vec<String> = conflicts
        .iter()
        .map(|c| {
            format!(
                "{{field: {}, current: {}, proposed: {}, seen: {}}}",
                quote(&c.field), quote(&c.current), quote(&c.proposed), quote(&c.seen)
            )
        })
        .collect();
    format!("conflicts: [{}]", items.join(", "))
}

/// 把 `field` 的值换成 `value`: 标量字段换整行; `rel:<name>` 换 related 里那一项的 rel。
fn apply_value(fm: &str, field: &str, value: &str) -> String {
    if let Some(name) = field.strip_prefix("rel:") {
        return fm
            .lines()
            .map(|line| {
                if !line.trim_start().starts_with("related:") {
                    return line.to_string();
                }
                let inner = line.trim().trim_start_matches("related:").trim().trim_start_matches('[').trim_end_matches(']');
                let items: Vec<String> = split_top_level(inner)
                    .into_iter()
                    .map(|item| {
                        let t = item.trim().to_string();
                        if t.starts_with('{') && item_name(&t).as_deref() == Some(name) {
                            format!("{{name: {}, rel: {}}}", quote(name), quote(value))
                        } else {
                            t
                        }
                    })
                    .collect();
                format!("related: [{}]", items.join(", "))
            })
            .collect::<Vec<_>>()
            .join("\n");
    }
    fm.lines()
        .map(|line| {
            if line.trim_start().starts_with(&format!("{field}:")) {
                format!("{field}: {value}")
            } else {
                line.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn item_name(item: &str) -> Option<String> {
    quoted_pairs(item.trim_start_matches('{').trim_end_matches('}'))
        .into_iter()
        .find(|(k, _)| k == "name")
        .map(|(_, v)| v)
        .or_else(|| {
            // 写侧偶尔不带引号: {name: 中电福富, rel: 隶属}
            item.trim_matches(|c| c == '{' || c == '}')
                .split(',')
                .find_map(|kv| kv.trim().strip_prefix("name:").map(|v| v.trim().trim_matches('"').to_string()))
        })
}

/// 纯函数: 在文件内容上落一个决定。返回新内容; 找不到那条冲突 → None。
pub(crate) fn resolve_in_content(content: &str, field: &str, take_proposed: bool) -> Option<String> {
    let rest = content.strip_prefix("---\n")?;
    let end = rest.find("\n---")?;
    let (fm, body) = (&rest[..end], &rest[end + 4..]);
    let conflicts = parse_conflicts(fm);
    let target = conflicts.iter().find(|c| c.field == field)?.clone();
    let mut new_fm = fm.lines().filter(|l| !l.trim_start().starts_with("conflicts:")).collect::<Vec<_>>().join("\n");
    if take_proposed {
        new_fm = apply_value(&new_fm, field, &target.proposed);
    }
    let remaining: Vec<WikiConflict> = conflicts.into_iter().filter(|c| c.field != field).collect();
    if !remaining.is_empty() {
        new_fm = format!("{}\n{}", new_fm.trim_end(), render(&remaining));
    }
    Some(mark_authored_by_employee(&format!("---\n{new_fm}\n---{body}")))
}

#[derive(Debug, Serialize)]
pub struct WikiConflictResolveResult {
    pub rel_path: String,
    pub remaining: usize,
}

/// 员工在关系工作台点了「用现在的」或「改成新的」。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_resolve_conflict(
    rel_path: String,
    field: String,
    take_proposed: bool,
) -> Result<WikiConflictResolveResult, String> {
    if rel_path.contains("..") || !rel_path.starts_with("wiki/") {
        return Err(format!("rel_path 白名单不通过: {rel_path}"));
    }
    let home = catfish_home()?;
    let abs = home.join(&rel_path);
    let content = fs::read_to_string(&abs).map_err(|e| format!("读 {abs:?} 失败: {e}"))?;
    let new_content = resolve_in_content(&content, &field, take_proposed)
        .ok_or_else(|| format!("{rel_path} 里没有 {field} 这条冲突 (可能已经处理过)"))?;
    fs::write(&abs, &new_content).map_err(|e| format!("写 {abs:?} 失败: {e}"))?;
    let (fm, _) = new_content.split_once("\n---").map(|(a, b)| (a.to_string(), b.to_string())).unwrap_or_default();
    Ok(WikiConflictResolveResult { rel_path, remaining: parse_conflicts(&fm).len() })
}

#[cfg(test)]
mod tests {
    use super::*;

    const FM: &str = "type: entity\ntitle: X\nentity_type: org\nrelated: [{name: \"A\", rel: \"隶属\"}, {name: \"B\", rel: \"持有\"}]\nconflicts: [{field: \"entity_type\", current: \"org\", proposed: \"department\", seen: \"journal:2026-09-17\"}, {field: \"rel:A\", current: \"隶属\", proposed: \"协作\", seen: \"\"}]";

    #[test]
    fn parses_python_written_line() {
        let c = parse_conflicts(FM);
        assert_eq!(c.len(), 2);
        assert_eq!(c[0], WikiConflict { field: "entity_type".into(), current: "org".into(), proposed: "department".into(), seen: "journal:2026-09-17".into() });
        assert_eq!(c[1].field, "rel:A");
        assert_eq!(c[1].proposed, "协作");
    }

    #[test]
    fn no_line_means_no_conflicts() {
        assert!(parse_conflicts("type: entity\ntitle: X").is_empty());
        assert!(parse_conflicts("conflicts: []").is_empty());
    }

    #[test]
    fn escaped_quotes_in_values() {
        let c = parse_conflicts("conflicts: [{field: \"entity_type\", current: \"a \\\"b\\\"\", proposed: \"c\", seen: \"\"}]");
        assert_eq!(c[0].current, "a \"b\"");
    }

    #[test]
    fn take_proposed_rewrites_scalar_and_drops_that_conflict() {
        let content = format!("---\n{FM}\n---\n\n正文。\n");
        let out = resolve_in_content(&content, "entity_type", true).unwrap();
        assert!(out.contains("entity_type: department"), "{out}");
        assert!(out.contains("rel: \"隶属\""), "关系那条没动: {out}");
        let (fm, _) = out.trim_start_matches("---\n").split_once("\n---").unwrap();
        let left = parse_conflicts(fm);
        assert_eq!(left.len(), 1);
        assert_eq!(left[0].field, "rel:A");
        assert!(out.contains("authored_by: employee"));
        assert!(out.ends_with("\n\n正文。\n"), "正文原样: {out:?}");
    }

    #[test]
    fn take_proposed_rewrites_relation() {
        let content = format!("---\n{FM}\n---\n\n正文。\n");
        let out = resolve_in_content(&content, "rel:A", true).unwrap();
        assert!(out.contains("{name: \"A\", rel: \"协作\"}"), "{out}");
        assert!(out.contains("{name: \"B\", rel: \"持有\"}"), "{out}");
        assert!(out.contains("entity_type: org"));
    }

    #[test]
    fn keep_current_only_drops_the_conflict() {
        let content = format!("---\n{FM}\n---\n\n正文。\n");
        let out = resolve_in_content(&content, "rel:A", false).unwrap();
        assert!(out.contains("rel: \"隶属\""));
        assert!(!out.contains("rel:A"));
        assert!(out.contains("proposed: \"department\""), "另一条还在");
    }

    #[test]
    fn last_conflict_removes_the_line() {
        let content = "---\ntype: entity\ntitle: X\nentity_type: org\nconflicts: [{field: \"entity_type\", current: \"org\", proposed: \"department\", seen: \"\"}]\n---\n\n正文。\n";
        let out = resolve_in_content(content, "entity_type", false).unwrap();
        assert!(!out.contains("conflicts:"), "{out}");
    }

    #[test]
    fn unknown_field_is_none() {
        let content = format!("---\n{FM}\n---\n\n正文。\n");
        assert!(resolve_in_content(&content, "rel:Z", true).is_none());
    }
}
