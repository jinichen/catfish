//! Skill 的解析与列举 —— 扫两套 skills 目录, 拼成仪表盘要的结构.
//!
//! 8/14 从 commands/skills.rs 拆出来 (那个文件 1249 行, 越过 800 红线)。
//! 切法按**职责**不按行数:
//!
//!   skills_list.rs     扫描 / 解析 / 列举          ← 本文件
//!   skills_install.rs  安装 / 卸载 / zip 导入 / 还原
//!   skills_mcp.rs      MCP servers 的读和写
//!   skills.rs          只剩 9 个 #[tauri::command] 薄封装
//!
//! 依赖是**单向**的: 安装和 MCP 用本文件的 home_dir / parse_skill_md /
//! scan_skills_root_filtered_full, 反过来没有。所以这个文件可以单独看懂。
//!
//! Skills 目录结构 (两套通用):
//!   <root>/
//!     <namespace>/             apple / github / department / ...
//!       <skill-name>/
//!         SKILL.md             YAML frontmatter + body
//!         scripts/             skill 自带脚本 (hermes 用)
//!         script.py            skill render 入口 (catfish 用)

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::services::catfish_paths;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillEntry {
    pub name: String,
    pub description: String,
    pub version: Option<String>,
    /// 6/2 BL-SKILLS-PUBLISH-WIRE: 真 skill 目录绝对路径 (含 SKILL.md 的父目录).
    /// 给 MySkillsCard 的"共享" 按钮调 catfish_skill_publish(skill_path=...) 用.
    /// camelCase serde 让 TS 看到 `path` (单字段不变).
    pub path: String,
    /// E7.P1 (6/6): 团队审定 / 内置 标记. true = 不能让员工删 (catfish_root/skills/
    /// 下的工程审定模板, e.g. leadership-briefing). false = 用户装的 (`~/.hermes/skills/`
    /// 加 `~/.claude/skills/`), 或员工自己录的 (~/.catfish/skills/), 可删可改.
    /// 由 scan_skills_root_filtered 按 root 路径决定, 后端逻辑唯一来源 (前端只读).
    pub is_protected: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillNamespace {
    pub namespace: String,
    pub skills: Vec<SkillEntry>,
}

pub(crate) fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

/// 解析 SKILL.md 的 YAML frontmatter.
///
/// 6/2 BL-SKILLS-PUBLISH-WIRE: 返 SkillEntry.path = SKILL.md 父目录 (skill 真根目录),
/// MySkillsCard 共享按钮拿来直接传 catfish_skill_publish(skill_path=...).
#[allow(dead_code)]
pub(crate) fn parse_skill_md(manifest_path: &Path) -> Option<SkillEntry> {
    parse_skill_md_with_protected(manifest_path, false)
}

/// E7.P1 (6/6): 跟 parse_skill_md 同, 但传入 is_protected (由 scan_skills_root 决定).
/// catfish_root/skills/ → protected=true (工程审定不能删), 其它路径 false (员工录的 / 装的).
fn parse_skill_md_with_protected(manifest_path: &Path, is_protected: bool) -> Option<SkillEntry> {
    let skill_dir_path = manifest_path
        .parent()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    let text = std::fs::read_to_string(manifest_path).ok()?;
    let trimmed = text.trim_start();
    if !trimmed.starts_with("---") {
        // 没 frontmatter，用文件名兜底
        let name = manifest_path
            .parent()
            .and_then(|p| p.file_name())
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        return Some(SkillEntry {
            name,
            description: "(无 SKILL.md frontmatter)".into(),
            version: None,
            path: skill_dir_path,
            is_protected,
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
        path: skill_dir_path,
        is_protected,
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

/// P3.3.33 (6/12): 判断 namespace 是不是 "publish / zip install 装的", 反过来"我录的".
///
/// convention 黑名单 (跟 catfish-companion-app 各 install 流程默认 namespace 约定一致):
///   - "department" — P3.3.18 部门 wiki publish 装的部门 share skill
///   - "external"   — P3.3.23 zip 装的外部 skill (ClawHub / Anthropic .skill 等)
///
/// "我录的" RecMode freeze 走 namespace=<skill 名> 或员工自起 ns, 不会用 'department'
/// 或 'external' 这种系统约定名 (即便起了, 视为"装的"也合理).
///
/// 长期更稳: RecMode freeze 时给 SKILL.md frontmatter 写 source: recorded 字段, 用
/// source marker 而不是 namespace 黑名单. 留 P3.3.34 做.
fn is_installed_namespace(name: &str) -> bool {
    matches!(name, "department" | "external")
}

/// 扫一个 skills root 目录, 返回所有 namespace + skill.
///
/// `ns_prefix` 给 namespace 名加前缀 (例如 "🐟 catfish:" 让 catfish skill 视觉
/// 区别于 hermes). 空字符串 = 不加前缀.
///
/// 6/2 BL-SKILLS-CARD-CLASSIFY-FROZEN: `filter` 可选 — 给每个 SKILL.md path 调,
/// 返 true 才收. None = 全收. 用来给 list_my/list_installed 按 frozen marker 分流.
/// E7.P1 (6/6): 加 `is_protected` 参数, 透传给 parse_skill_md_with_protected.
/// catfish_root/skills/ 树调时 is_protected=true, 其它 root 都 false.
fn scan_skills_root_filtered(
    root: &Path,
    ns_prefix: &str,
    filter: Option<&dyn Fn(&Path) -> bool>,
) -> Vec<SkillNamespace> {
    scan_skills_root_filtered_full(root, ns_prefix, filter, false)
}

pub(crate) fn scan_skills_root_filtered_full(
    root: &Path,
    ns_prefix: &str,
    filter: Option<&dyn Fn(&Path) -> bool>,
    is_protected: bool,
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
                if let Some(entry) = parse_skill_md_with_protected(&manifest, is_protected) {
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
// 7/17 BL-DEADCODE-SWEEP: 老 list_skills_blocking / list_skills tauri 命令死链已删.
// 现只保留 list_my_skills_blocking 和 list_installed_skills_blocking.

/// 扫 ~/.catfish/skills/ + <catfish_root>/skills/ 里 frozen 教学产物 — 员工自己生成的.
///
/// 6/2 BL-SKILLS-CARD-CLASSIFY-FROZEN: 5/21 前 freeze 默认 target=workspace 落
/// <catfish_root>/skills/ (e.g. eis-login v0.1.0-frozen 5/12), 5/21 后 default
/// 改 local 落 ~/.catfish/skills/. 老教学产物**留在 git workspace**, 不搬, 但
/// 列表分类要正确. 用 SKILL.md frontmatter marker 区分 (is_recmode_frozen_skill).
///
/// 共享按钮 (BL-SKILLS-SHARE 5/27 backlog) 的真对象就是这套.
pub(crate) fn list_my_skills_blocking() -> Result<Vec<SkillNamespace>, String> {
    let mut result = Vec::new();

    // 源 1: ~/.catfish/skills/ — aggregator save_skill RPC + propose_skill + freeze
    // target=local (5/21 后 default) 落地处.
    //
    // P3.3.33 (6/12): 老版**全收**假设挂了 — P3.3.18 (6/10) 部门 wiki publish 装
    //   department/ ns + P3.3.23 (6/11) zip 装外部 skill 装 external/ ns 后,
    //   ~/.catfish/skills/ 不再只装教学产物.
    //
    //   不能用 frozen marker 过滤 — eis-* 是别同事录的 publish 给部门, 装回本机
    //   仍带 frozen marker, 不是"我自己录的". 改 namespace 黑名单 (convention):
    //     - department / external → 装的 (归 list_installed)
    //     - 其他 ns → 我录的 (归 list_my, freeze target=local 默认走 namespace=<skill 名>)
    //   长期更稳应该 RecMode freeze 时写 source marker, 留 P3.3.34 做.
    let local_skills_root = if let Some(home_env) = std::env::var_os("CATFISH_HOME") {
        PathBuf::from(home_env).join("skills")
    } else if let Some(home) = home_dir() {
        home.join(".catfish").join("skills")
    } else {
        return Ok(result);
    };
    let mut local_namespaces = scan_skills_root(&local_skills_root, "");
    // P3.3.33: 排除 publish / zip install 的 namespace, 它们归 list_installed
    local_namespaces.retain(|ns| !is_installed_namespace(&ns.namespace));

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
pub(crate) fn list_installed_skills_blocking() -> Result<Vec<SkillNamespace>, String> {
    let mut result = Vec::new();

    // 1. catfish 工程审定 skill — 顶部显示, 带 🐟 前缀. **过滤掉 frozen 教学产物**.
    // E7.P1 (6/6): is_protected=true — 不能让员工删 (git 仓库管控, 删了下次 pull 还回来).
    if let Some(catfish_root) = catfish_paths::catfish_root() {
        let catfish_skills_root = catfish_root.join("skills");
        let not_frozen_filter: &dyn Fn(&Path) -> bool = &|p| !is_recmode_frozen_skill(p);
        let mut catfish_namespaces = scan_skills_root_filtered_full(
            &catfish_skills_root,
            "🐟 catfish:",
            Some(not_frozen_filter),
            true,  // protected
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

    // 3. P3.3.33 (6/12): ~/.catfish/skills/ 里 publish / zip install 的 namespace.
    //   P3.3.18 部门 wiki publish 装 department/, P3.3.23 zip 装外部 skill 装
    //   external/. 老 6/2 分类漏这条让它们被误归"我的技能". 加进 list_installed.
    //   用 is_installed_namespace 黑名单 (跟 list_my 同 convention, 互补).
    let local_skills_root_for_installed = if let Some(home_env) = std::env::var_os("CATFISH_HOME") {
        Some(PathBuf::from(home_env).join("skills"))
    } else {
        home_dir().map(|h| h.join(".catfish").join("skills"))
    };
    if let Some(local_root) = local_skills_root_for_installed {
        let mut local_installed = scan_skills_root(&local_root, "");
        // 只收 publish / zip install 的 namespace (跟 list_my 互补)
        local_installed.retain(|ns| is_installed_namespace(&ns.namespace));
        local_installed.sort_by(|a, b| a.namespace.cmp(&b.namespace));
        result.extend(local_installed);
    }

    Ok(result)
}

// 7/17 BL-DEADCODE-SWEEP: list_skills_blocking 死链已删
// (前端 useSkillsAndMcp hook / fetchSkills / list_skills tauri command 全删).

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

    /// 6/2 BL-SKILLS-PUBLISH-WIRE: SkillEntry.path 真填 skill 目录绝对路径,
    /// MySkillsCard 共享按钮调 catfish_skill_publish(skill_path=...) 用.
    #[test]
    fn test_skill_entry_path_filled_with_skill_dir() {
        let tmp = tempfile::tempdir().unwrap();
        let department = tmp.path().join("department");
        let skill_dir = department.join("eis-login");
        _write_skill_md(
            &skill_dir,
            r#"name: eis-login
version: "0.1.0-frozen"
description: 登录 EIS"#,
        );
        let result = scan_skills_root(tmp.path(), "");
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].skills.len(), 1);
        let entry = &result[0].skills[0];
        assert_eq!(entry.name, "eis-login");
        // path 应该是 skill_dir 真绝对路径, MySkillsCard 直接传给 publish tool
        assert_eq!(entry.path, skill_dir.to_string_lossy().to_string());
    }
}
