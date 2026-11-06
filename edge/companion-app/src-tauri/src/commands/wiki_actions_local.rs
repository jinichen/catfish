//! 11/06 鸿波「本机装了的技能都列进来」: 知识库「关联行动」下拉原来只有内置注册表里写死的 6 个,
//! 员工自己教会 / 装的技能 (~/.catfish/skills/、专家包) 一个都选不到。
//!
//! 这里把 catfish_run_skill 能找到的技能 (同 wiki_actions::skill_roots 那组根, 两级 <ns>/<name>/SKILL.md)
//! 自动变成行动: id = `skill.local:<ns>:<name>` (只由路径决定, 知识页里存的 id 不会因为改标题失效),
//! 名字 / 说明取技能自己的标题和一句话说明。已在注册表里的 (按 skill_path) 不重复列; 标了 deprecated 的不列。

use std::fs;
use std::path::PathBuf;

use super::wiki_actions::ActionDefinition;

const ID_PREFIX: &str = "skill.local";

fn valid_part(part: &str) -> bool {
    !part.is_empty()
        && part.len() <= 50
        && part.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
        && part != "."
        && part != ".."
}

fn deprecated(text: &str) -> bool {
    let Some(rest) = text.trim_start().strip_prefix("---") else { return false };
    let fm = rest.split("\n---").next().unwrap_or("");
    fm.lines().any(|l| {
        let l = l.trim();
        l == "deprecated: true" || l == "deprecated: \"true\""
    })
}

/// 扫各根下的 `<ns>/<name>/SKILL.md`。先扫到的根优先 (跟 catfish_run_skill 找技能的顺序一致)。
pub(crate) fn discover(roots: &[PathBuf]) -> Vec<ActionDefinition> {
    let mut out: Vec<ActionDefinition> = Vec::new();
    for root in roots {
        let Ok(namespaces) = fs::read_dir(root) else { continue };
        for ns in namespaces.flatten() {
            let ns_name = ns.file_name().to_string_lossy().to_string();
            if !valid_part(&ns_name) || !ns.path().is_dir() {
                continue;
            }
            let Ok(skills) = fs::read_dir(ns.path()) else { continue };
            for skill in skills.flatten() {
                let name = skill.file_name().to_string_lossy().to_string();
                let manifest = skill.path().join("SKILL.md");
                if !valid_part(&name) || !manifest.is_file() {
                    continue;
                }
                let skill_path = format!("{ns_name}/{name}");
                if out.iter().any(|a| a.skill_path.as_deref() == Some(skill_path.as_str())) {
                    continue;
                }
                if fs::read_to_string(&manifest).map(|t| deprecated(&t)).unwrap_or(false) {
                    continue;
                }
                let (label, description) = match super::skills_list::parse_skill_md(&manifest) {
                    Some(entry) => (entry.title, entry.summary),
                    None => (name.clone(), String::new()),
                };
                out.push(ActionDefinition {
                    id: format!("{ID_PREFIX}:{ns_name}:{name}"),
                    label: if label.trim().is_empty() { name.clone() } else { label },
                    description,
                    executor: "skill".into(),
                    skill_path: Some(skill_path),
                    approval: "required".into(),
                    enabled: true,
                    platforms: vec![],
                    timeout_seconds: 600,
                });
            }
        }
    }
    out.sort_by(|a, b| a.label.cmp(&b.label));
    out
}

/// 把本机技能追加到注册表后面: 注册表里已有同一个 skill_path / 同 id 的跳过 (注册表的名字和设置优先)。
pub(crate) fn append_missing(actions: &mut Vec<ActionDefinition>, discovered: Vec<ActionDefinition>) {
    for item in discovered {
        let dup = actions.iter().any(|a| {
            a.id == item.id
                || a.skill_path.as_deref().map(|p| p.trim().trim_matches('/')) == item.skill_path.as_deref()
        });
        if !dup {
            actions.push(item);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(root: &std::path::Path, rel: &str, body: &str) {
        let dir = root.join(rel);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("SKILL.md"), body).unwrap();
    }

    #[test]
    fn discovers_installed_skills_skips_deprecated_and_dups() {
        let tmp = std::env::temp_dir().join(format!("catfish-wiki-local-{}", std::process::id()));
        let (a, b) = (tmp.join("a"), tmp.join("b"));
        write(&a, "personal/video-audio-summary", "---\nname: video-audio-summary\ntitle: \"视频音频转写摘要\"\ndescription: 转写后出摘要\n---\n正文");
        write(&a, "personal/old-one", "---\nname: old-one\ntitle: 旧的\ndeprecated: true\n---\n");
        write(&b, "personal/video-audio-summary", "---\nname: x\ntitle: 后面的根\n---\n");
        write(&b, "department/weekly-report", "---\nname: weekly-report\ntitle: 周报\n---\n");
        fs::create_dir_all(a.join("personal/no-manifest")).unwrap();
        let found = discover(&[a.clone(), b.clone()]);
        let ids: Vec<&str> = found.iter().map(|x| x.id.as_str()).collect();
        assert!(ids.contains(&"skill.local:personal:video-audio-summary"));
        assert!(!ids.iter().any(|i| i.contains("old-one") || i.contains("no-manifest")));
        let v = found.iter().find(|x| x.id.ends_with("video-audio-summary")).unwrap();
        assert_eq!(v.label, "视频音频转写摘要");
        assert_eq!(v.skill_path.as_deref(), Some("personal/video-audio-summary"));
        assert_eq!(v.approval, "required");

        let mut registry = vec![ActionDefinition {
            id: "skill.weekly-report".into(),
            label: "生成员工周报".into(),
            description: String::new(),
            executor: "skill".into(),
            skill_path: Some("department/weekly-report".into()),
            approval: "required".into(),
            enabled: true,
            platforms: vec![],
            timeout_seconds: 600,
        }];
        append_missing(&mut registry, found);
        assert_eq!(registry.iter().filter(|x| x.skill_path.as_deref() == Some("department/weekly-report")).count(), 1);
        assert!(registry.iter().any(|x| x.id == "skill.local:personal:video-audio-summary"));
        let _ = fs::remove_dir_all(&tmp);
    }
}
