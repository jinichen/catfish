//! Skills + MCP servers 列表 —— 读两套来源:
//!   1. ~/.hermes/skills/                — Hermes 加载的所有 skill (LLM 自学 / 内置)
//!   2. <catfish_root>/skills/           — catfish 工程审定 skill (公文 / 合规模板)
//!
//! 两套都显示在仪表盘 SKILLS 区, 用 namespace 前缀区分:
//!   - hermes 的: 直接用原 namespace (productivity / data-science / ...)
//!   - catfish 的: 加 "🐟 " 前缀强调来源 (🐟 catfish:department)
//!
//! 鸿波 2026-04-29 反馈: 仪表盘看不到 leadership-briefing — 这是因为它在
//! catfish/skills/ 不在 ~/.hermes/skills/. 这次改完两套都能看见.
//!
//! Skills 目录结构 (两套通用)：
//!   <root>/
//!     <namespace>/             apple / github / department / ...
//!       <skill-name>/
//!         SKILL.md             YAML frontmatter + body
//!         scripts/             skill 自带脚本 (hermes 用)
//!         script.py            skill render 入口 (catfish 用)
//!
//! MCP servers 在 config.yaml 的 `mcp_servers:` 段下：
//!   mcp_servers:
//!     <name>:
//!       command: <path>        stdio 启动命令
//!       args: [...]            可选
//!       env: {...}             可选

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::services::catfish_paths;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillEntry {
    pub name: String,
    pub description: String,
    pub version: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillNamespace {
    pub namespace: String,
    pub skills: Vec<SkillEntry>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct McpServerEntry {
    pub name: String,
    pub command: String,
    pub args: Vec<String>,
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

/// 解析 SKILL.md 的 YAML frontmatter
fn parse_skill_md(path: &Path) -> Option<SkillEntry> {
    let text = std::fs::read_to_string(path).ok()?;
    let trimmed = text.trim_start();
    if !trimmed.starts_with("---") {
        // 没 frontmatter，用文件名兜底
        let name = path
            .parent()
            .and_then(|p| p.file_name())
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        return Some(SkillEntry {
            name,
            description: "(无 SKILL.md frontmatter)".into(),
            version: None,
        });
    }
    // 找 frontmatter 边界：---\n....---\n
    let after_first = trimmed.strip_prefix("---")?.trim_start_matches('\n');
    let end_idx = after_first.find("\n---")?;
    let frontmatter = &after_first[..end_idx];

    let value: serde_yaml::Value = serde_yaml::from_str(frontmatter).ok()?;
    let name = value
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("(unknown)")
        .to_string();
    let description = value
        .get("description")
        .and_then(|v| v.as_str())
        .unwrap_or("(no description)")
        .to_string();
    let version = value
        .get("version")
        .and_then(|v| v.as_str())
        .map(String::from);
    Some(SkillEntry {
        name,
        description,
        version,
    })
}

/// 6/2 BL-SKILLS-CARD-CLASSIFY-FROZEN (鸿波 6/2 下午抓的真问题):
/// SKILL.md 是不是"教学产物" (RecMode 录屏 + LLM propose_skill → catfish_freeze_skill).
///
/// 区分逻辑: 跟 catfish_tool_bridge/skill_freeze_template.py:239-247 真模板锚定.
/// 模板 frontmatter 默认 `version: "0.1.0-frozen"`, description 含
/// `"由 catfish_freeze_skill 自动凝固"`. 两个 marker 同时检 → 抗员工手改 version
/// (e.g. 改成 "0.2.0" 升级) 但 description 仍留 freeze 痕迹的 case.
///
/// 真用途:
/// - 5/21 前 freeze 默认 target=workspace → 落 <catfish_root>/skills/, 老教学产物
///   (eis-login/eis-checkin/eis-checkout v0.1.0-frozen 5/12) 留在 git workspace
/// - 5/21 后 default 改 target=local → 落 ~/.catfish/skills/
/// - 不能光按路径分类 — 要看 frontmatter marker
///
/// 教学产物归 "我录的" (MySkillsCard), 工程审定 (leadership-briefing/weekly-report)
/// 归 "已装的" (SkillsMcpCard).
fn is_recmode_frozen_skill(manifest_path: &Path) -> bool {
    let Ok(text) = std::fs::read_to_string(manifest_path) else {
        return false;
    };
    let trimmed = text.trim_start();
    if !trimmed.starts_with("---") {
        return false;
    }
    let Some(after_first) = trimmed.strip_prefix("---") else { return false; };
    let after_first = after_first.trim_start_matches('\n');
    let Some(end_idx) = after_first.find("\n---") else { return false; };
    let frontmatter = &after_first[..end_idx];
    let Ok(value): Result<serde_yaml::Value, _> = serde_yaml::from_str(frontmatter) else {
        return false;
    };
    // marker 1: version 字符串含 "frozen" (默认 "0.1.0-frozen", 但允许员工手改 e.g.
    //          "1.0.0-frozen" 升级保留 freeze 痕迹).
    let version_has_frozen = value
        .get("version")
        .and_then(|v| v.as_str())
        .map(|s| s.contains("frozen"))
        .unwrap_or(false);
    // marker 2: description 含 "由 catfish_freeze_skill 自动凝固"
    //          (skill_freeze_template _SKILL_MD_TEMPLATE 真写的标识)
    let description_has_marker = value
        .get("description")
        .and_then(|v| v.as_str())
        .map(|s| s.contains("由 catfish_freeze_skill 自动凝固"))
        .unwrap_or(false);
    version_has_frozen || description_has_marker
}

/// 扫一个 skills root 目录, 返回所有 namespace + skill.
///
/// `ns_prefix` 给 namespace 名加前缀 (例如 "🐟 catfish:" 让 catfish skill 视觉
/// 区别于 hermes). 空字符串 = 不加前缀.
///
/// 6/2 BL-SKILLS-CARD-CLASSIFY-FROZEN: `filter` 可选 — 给每个 SKILL.md path 调,
/// 返 true 才收. None = 全收. 用来给 list_my/list_installed 按 frozen marker 分流.
fn scan_skills_root_filtered(
    root: &Path,
    ns_prefix: &str,
    filter: Option<&dyn Fn(&Path) -> bool>,
) -> Vec<SkillNamespace> {
    let mut result = Vec::new();
    if !root.exists() {
        return result;
    }
    let Ok(entries) = std::fs::read_dir(root) else {
        return result;
    };

    for ns_entry in entries.flatten() {
        let ns_path = ns_entry.path();
        if !ns_path.is_dir() {
            continue;
        }
        let ns_name = match ns_path.file_name().and_then(|s| s.to_str()) {
            Some(n) if !n.starts_with('.') => n.to_string(),
            _ => continue,
        };

        let mut skills = Vec::new();
        if let Ok(skill_iter) = std::fs::read_dir(&ns_path) {
            for skill_entry in skill_iter.flatten() {
                let skill_path = skill_entry.path();
                if !skill_path.is_dir() {
                    continue;
                }
                let manifest = skill_path.join("SKILL.md");
                if !manifest.exists() {
                    continue;
                }
                // 6/2 filter 决定收不收 (frozen 教学产物 vs 工程审定 分流)
                if let Some(f) = filter {
                    if !f(&manifest) {
                        continue;
                    }
                }
                if let Some(entry) = parse_skill_md(&manifest) {
                    skills.push(entry);
                }
            }
        }

        if !skills.is_empty() {
            skills.sort_by(|a, b| a.name.cmp(&b.name));
            result.push(SkillNamespace {
                namespace: format!("{ns_prefix}{ns_name}"),
                skills,
            });
        }
    }

    result
}

/// 兼容老 caller — 等价 scan_skills_root_filtered(root, ns_prefix, None).
fn scan_skills_root(root: &Path, ns_prefix: &str) -> Vec<SkillNamespace> {
    scan_skills_root_filtered(root, ns_prefix, None)
}

// 6/2 BL-SKILLS-CARD-SPLIT (鸿波 6/2 凌晨拍 方案 C): 真员工生成的 skill 跟内置/装的
// skill 来源不同, Dashboard 拆成 2 张卡 — "🐟 我录的" (主, 含共享按钮) + "已装的"
// (次, 排错 / power-user 查). 真物理位置 3 套, 路由表:
//
//   ~/.catfish/skills/              → "我录的" (RecMode 真生成 + propose_skill 落地)
//   <catfish_root>/skills/          → "已装的" - 团队审定 (catfish 仓库公文/合规模板)
//   ~/.hermes/skills/               → "已装的" - 内置 + marketplaces + LLM 自学
//
// 老 list_skills_blocking 现 = list_installed_skills_blocking (合并扫 catfish 仓库 + hermes),
// list_skills() Tauri 命令保留作 backward compat 返同一份 (实际无外部 caller, 但保
// 一刻 safety net).

/// 扫 ~/.catfish/skills/ + <catfish_root>/skills/ 里 frozen 教学产物 — 员工自己生成的.
///
/// 6/2 BL-SKILLS-CARD-CLASSIFY-FROZEN: 5/21 前 freeze 默认 target=workspace 落
/// <catfish_root>/skills/ (e.g. eis-login v0.1.0-frozen 5/12), 5/21 后 default
/// 改 local 落 ~/.catfish/skills/. 老教学产物**留在 git workspace**, 不搬, 但
/// 列表分类要正确. 用 SKILL.md frontmatter marker 区分 (is_recmode_frozen_skill).
///
/// 共享按钮 (BL-SKILLS-SHARE 5/27 backlog) 的真对象就是这套.
fn list_my_skills_blocking() -> Result<Vec<SkillNamespace>, String> {
    let mut result = Vec::new();

    // 源 1: ~/.catfish/skills/ — aggregator save_skill RPC + propose_skill + freeze
    // target=local (5/21 后 default) 落地处. **全收**, 不过滤 — 这路径只装教学产物.
    let local_skills_root = if let Some(home_env) = std::env::var_os("CATFISH_HOME") {
        PathBuf::from(home_env).join("skills")
    } else if let Some(home) = home_dir() {
        home.join(".catfish").join("skills")
    } else {
        return Ok(result);
    };
    let mut local_namespaces = scan_skills_root(&local_skills_root, "");

    // 源 2: <catfish_root>/skills/ 里**含 frozen marker 的教学产物** — 5/21 前
    // freeze target=workspace 老历史. 用 is_recmode_frozen_skill 过滤, 工程审定的
    // (leadership-briefing/weekly-report) 留给 list_installed_skills_blocking.
    if let Some(catfish_root) = catfish_paths::catfish_root() {
        let workspace_skills_root = catfish_root.join("skills");
        let frozen_filter: &dyn Fn(&Path) -> bool = &is_recmode_frozen_skill;
        let mut workspace_frozen = scan_skills_root_filtered(
            &workspace_skills_root,
            "",  // 不加 🐟 前缀, 跟 ~/.catfish/skills/ 同视觉等级 (都是员工产出)
            Some(frozen_filter),
        );
        // 跟 local 同 namespace 时合并 entries (员工可能同时有 5/12 老 eis-login
        // 在 workspace 和 5/30 新教学版在 local — 视为同 namespace 下不同 skill)
        for ns in workspace_frozen.drain(..) {
            if let Some(existing) = local_namespaces.iter_mut().find(|n| n.namespace == ns.namespace) {
                existing.skills.extend(ns.skills);
                existing.skills.sort_by(|a, b| a.name.cmp(&b.name));
            } else {
                local_namespaces.push(ns);
            }
        }
    }

    local_namespaces.sort_by(|a, b| a.namespace.cmp(&b.namespace));
    result.extend(local_namespaces);
    Ok(result)
}

/// 扫 catfish 仓库 (**只非 frozen**) + ~/.hermes/skills/ — 团队审定 + 内置 +
/// marketplaces 装的. 给"已装的 skill / MCP" 次卡用.
///
/// 6/2 BL-SKILLS-CARD-CLASSIFY-FROZEN: <catfish_root>/skills/ 里 frozen 教学产物
/// 归 "我录的" (list_my_skills_blocking), 这里**跳过它们**避免重复. 用 NOT
/// is_recmode_frozen_skill filter.
fn list_installed_skills_blocking() -> Result<Vec<SkillNamespace>, String> {
    let mut result = Vec::new();

    // 1. catfish 工程审定 skill — 顶部显示, 带 🐟 前缀. **过滤掉 frozen 教学产物**.
    if let Some(catfish_root) = catfish_paths::catfish_root() {
        let catfish_skills_root = catfish_root.join("skills");
        let not_frozen_filter: &dyn Fn(&Path) -> bool = &|p| !is_recmode_frozen_skill(p);
        let mut catfish_namespaces = scan_skills_root_filtered(
            &catfish_skills_root,
            "🐟 catfish:",
            Some(not_frozen_filter),
        );
        catfish_namespaces.sort_by(|a, b| a.namespace.cmp(&b.namespace));
        result.extend(catfish_namespaces);
    }

    // 2. hermes skills (LLM 自学 + 内置 + marketplaces 装的) — 全收, 这路径不混
    // 教学产物 (freeze 不落 ~/.hermes/skills/).
    if let Some(home) = home_dir() {
        let hermes_skills = home.join(".hermes").join("skills");
        let mut hermes_namespaces = scan_skills_root(&hermes_skills, "");
        hermes_namespaces.sort_by(|a, b| a.namespace.cmp(&b.namespace));
        result.extend(hermes_namespaces);
    }

    Ok(result)
}

/// @deprecated 6/2 BL-SKILLS-CARD-SPLIT: 老 caller 兼容, 内部转 installed. 没真 caller
/// (Companion 已经走拆分后的 2 命令), 留作 safety net 防意外耦合.
fn list_skills_blocking() -> Result<Vec<SkillNamespace>, String> {
    list_installed_skills_blocking()
}

fn list_mcp_servers_blocking() -> Result<Vec<McpServerEntry>, String> {
    let Some(home) = home_dir() else {
        return Ok(vec![]);
    };
    let cfg_path = home.join(".hermes").join("config.yaml");
    let Ok(text) = std::fs::read_to_string(&cfg_path) else {
        return Ok(vec![]);
    };
    let Ok(value) = serde_yaml::from_str::<serde_yaml::Value>(&text) else {
        return Ok(vec![]);
    };

    let Some(mcp_map) = value.get("mcp_servers").and_then(|v| v.as_mapping()) else {
        return Ok(vec![]);
    };

    let mut result = Vec::new();
    for (k, v) in mcp_map {
        let name = match k.as_str() {
            Some(s) => s.to_string(),
            None => continue,
        };
        let command = v
            .get("command")
            .and_then(|x| x.as_str())
            .unwrap_or("(无 command)")
            .to_string();
        let args = v
            .get("args")
            .and_then(|x| x.as_sequence())
            .map(|seq| {
                seq.iter()
                    .filter_map(|x| x.as_str().map(String::from))
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        result.push(McpServerEntry {
            name,
            command,
            args,
        });
    }
    result.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(result)
}

/// 6/2 BL-SKILLS-CARD-SPLIT: 拆分后两条新命令.
#[tauri::command]
pub async fn list_my_skills() -> Result<Vec<SkillNamespace>, String> {
    tokio::task::spawn_blocking(list_my_skills_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn list_installed_skills() -> Result<Vec<SkillNamespace>, String> {
    tokio::task::spawn_blocking(list_installed_skills_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

/// @deprecated 6/2 BL-SKILLS-CARD-SPLIT: 内部转 list_installed_skills 兼容老 caller.
/// 没真 caller (Companion 已拆 2 命令), 保留作 safety net.
#[tauri::command]
pub async fn list_skills() -> Result<Vec<SkillNamespace>, String> {
    tokio::task::spawn_blocking(list_skills_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn list_mcp_servers() -> Result<Vec<McpServerEntry>, String> {
    tokio::task::spawn_blocking(list_mcp_servers_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}


// ── 6/2 BL-SKILLS-CARD-CLASSIFY-FROZEN tests ───────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn _write_skill_md(dir: &Path, frontmatter: &str) -> PathBuf {
        std::fs::create_dir_all(dir).unwrap();
        let path = dir.join("SKILL.md");
        let mut f = std::fs::File::create(&path).unwrap();
        write!(f, "---\n{frontmatter}\n---\n\n# body\n").unwrap();
        path
    }

    #[test]
    fn test_recmode_frozen_marker_version_frozen() {
        let tmp = tempfile::tempdir().unwrap();
        // 5/12 ship 的 eis-login 真 frontmatter 模式 — version 含 "frozen"
        let frontmatter = r#"name: eis-login
version: "0.1.0-frozen"
description: 登录 EIS"#;
        let path = _write_skill_md(tmp.path(), frontmatter);
        assert!(is_recmode_frozen_skill(&path));
    }

    #[test]
    fn test_recmode_frozen_marker_description_freeze_phrase() {
        let tmp = tempfile::tempdir().unwrap();
        // version 没 frozen 字串但 description 含 "由 catfish_freeze_skill 自动凝固"
        // → 员工手改 version 升级 (e.g. "0.2.0"), 仍归"教学产物"
        let frontmatter = r#"name: upgraded-skill
version: "0.2.0"
description: |
  ⭐ 我已升级版本

  ⚙️ 由 catfish_freeze_skill 自动凝固 (2026-05-12 13:26:39, BL-MM9-FREEZE)."#;
        let path = _write_skill_md(tmp.path(), frontmatter);
        assert!(is_recmode_frozen_skill(&path));
    }

    #[test]
    fn test_recmode_frozen_marker_engineering_skill_not_marked() {
        let tmp = tempfile::tempdir().unwrap();
        // leadership-briefing 真 frontmatter 模式 — 工程审定, 不该被认成教学产物
        let frontmatter = r#"name: leadership-briefing
version: "1.1.0"
description: 生成公司领导汇报 docx, 严格按真实公文样式 (鸿波 PDF 样板复刻)"#;
        let path = _write_skill_md(tmp.path(), frontmatter);
        assert!(!is_recmode_frozen_skill(&path));
    }

    #[test]
    fn test_recmode_frozen_marker_no_frontmatter() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("SKILL.md");
        std::fs::write(&path, "# Just body, no frontmatter\n").unwrap();
        assert!(!is_recmode_frozen_skill(&path));
    }

    #[test]
    fn test_recmode_frozen_marker_missing_file() {
        let path = PathBuf::from("/nonexistent/path/SKILL.md");
        assert!(!is_recmode_frozen_skill(&path));
    }

    #[test]
    fn test_scan_skills_root_filter_picks_only_frozen() {
        let tmp = tempfile::tempdir().unwrap();
        let department = tmp.path().join("department");
        // 1 个 frozen (eis-login) + 1 个工程审定 (leadership-briefing)
        _write_skill_md(
            &department.join("eis-login"),
            r#"name: eis-login
version: "0.1.0-frozen"
description: 登录 EIS"#,
        );
        _write_skill_md(
            &department.join("leadership-briefing"),
            r#"name: leadership-briefing
version: "1.1.0"
description: 工程审定"#,
        );
        let frozen_filter: &dyn Fn(&Path) -> bool = &is_recmode_frozen_skill;
        let only_frozen = scan_skills_root_filtered(
            tmp.path(),
            "",
            Some(frozen_filter),
        );
        // 应该只收到 eis-login (frozen), leadership-briefing 被过滤
        assert_eq!(only_frozen.len(), 1);
        assert_eq!(only_frozen[0].namespace, "department");
        assert_eq!(only_frozen[0].skills.len(), 1);
        assert_eq!(only_frozen[0].skills[0].name, "eis-login");
    }

    #[test]
    fn test_scan_skills_root_filter_inverted_picks_only_engineering() {
        let tmp = tempfile::tempdir().unwrap();
        let department = tmp.path().join("department");
        _write_skill_md(
            &department.join("eis-login"),
            r#"name: eis-login
version: "0.1.0-frozen"
description: 登录 EIS"#,
        );
        _write_skill_md(
            &department.join("leadership-briefing"),
            r#"name: leadership-briefing
version: "1.1.0"
description: 工程审定"#,
        );
        // 非 frozen filter — list_installed 用
        let not_frozen_filter: &dyn Fn(&Path) -> bool = &|p| !is_recmode_frozen_skill(p);
        let only_engineering = scan_skills_root_filtered(
            tmp.path(),
            "🐟 catfish:",
            Some(not_frozen_filter),
        );
        assert_eq!(only_engineering.len(), 1);
        assert_eq!(only_engineering[0].namespace, "🐟 catfish:department");
        assert_eq!(only_engineering[0].skills[0].name, "leadership-briefing");
    }

    #[test]
    fn test_scan_skills_root_no_filter_collects_all() {
        let tmp = tempfile::tempdir().unwrap();
        let department = tmp.path().join("department");
        _write_skill_md(
            &department.join("a"),
            r#"name: a
version: "0.1.0-frozen""#,
        );
        _write_skill_md(
            &department.join("b"),
            r#"name: b
version: "1.0.0""#,
        );
        let all = scan_skills_root(tmp.path(), "");
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].skills.len(), 2);
    }
}
