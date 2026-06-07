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
    /// 6/2 BL-SKILLS-PUBLISH-WIRE: 真 skill 目录绝对路径 (含 SKILL.md 的父目录).
    /// 给 MySkillsCard 的"共享" 按钮调 catfish_skill_publish(skill_path=...) 用.
    /// camelCase serde 让 TS 看到 `path` (单字段不变).
    pub path: String,
    /// E7.P1 (6/6): 团队审定 / 内置 标记. true = 不能让员工删 (catfish_root/skills/
    /// 下的工程审定模板, e.g. leadership-briefing). false = 用户装的 (~/.hermes/skills/
    /// + ~/.claude/skills/), 或员工自己录的 (~/.catfish/skills/), 可删可改.
    /// 由 scan_skills_root_filtered 按 root 路径决定, 后端逻辑唯一来源 (前端只读).
    pub is_protected: bool,
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
    /// E7.P1 (6/6): catfish-tools 或 catfish-* 前缀 = 核心 MCP (主链路依赖), 不能删.
    /// 员工自加的 (e.g. slack-mcp) is_protected=false.
    pub is_protected: bool,
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

/// 解析 SKILL.md 的 YAML frontmatter.
///
/// 6/2 BL-SKILLS-PUBLISH-WIRE: 返 SkillEntry.path = SKILL.md 父目录 (skill 真根目录),
/// MySkillsCard 共享按钮拿来直接传 catfish_skill_publish(skill_path=...).
#[allow(dead_code)]
fn parse_skill_md(manifest_path: &Path) -> Option<SkillEntry> {
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

fn scan_skills_root_filtered_full(
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
        // E7.P1 (6/6): catfish-* 前缀 = 主链路 MCP, 不能删. 员工自加的 (slack-mcp 等)
        // is_protected=false (phase 2 加卸载按钮).
        let is_protected = name.starts_with("catfish-") || name == "catfish-tools";
        result.push(McpServerEntry {
            name,
            command,
            args,
            is_protected,
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

// ── E7 phase 2 (6/6 鸿波 setup): skill 安装/卸载, MCP 接入/移除 ─────────
//
// 设计原则:
//   1. 安全 — 卸载不真删, 移到 ~/.catfish/.trash/skills/<ts>/, 5 秒 undo
//   2. 信任分层 — is_protected=true 的 skill / MCP 拒绝卸载 (catfish 仓库 + catfish-* MCP)
//   3. 路径校验 — uninstall_skill 必须在合法 root (~/.hermes/, ~/.claude/, ~/.catfish/),
//      防止前端误传任意路径删 user files
//   4. CLI wrap — install 走 `npx -y skills add <url>` (跟员工 terminal 装 taste-skill 同 path)
//   5. PATH 兜底 — npx 在 mac 上常在 /usr/local/bin / /opt/homebrew/bin / ~/.nvm/, 加 augmented PATH

/// E7 phase 2 (6/6): skill 安装结果. stdout / stderr 全返让员工排错.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallResult {
    pub success: bool,
    pub stdout: String,
    pub stderr: String,
    pub exit_code: Option<i32>,
}

/// 卸载返 trash 路径 — 给前端 undo button 用 (5 秒内可恢复).
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UninstallResult {
    /// trash dir 内 skill 完整新位置 (用 restore_skill 调回).
    pub trash_path: String,
    /// 原 skill 位置 (restore 时移回).
    pub original_path: String,
}

/// PATH augment — mac 下 npx 常在 homebrew / nvm. 直接 exec npx 可能 PATH 找不到.
fn augmented_path() -> String {
    let current = std::env::var("PATH").unwrap_or_default();
    let extra = [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/usr/bin",
        "/bin",
    ];
    // nvm 默认路径 — 不 glob (没装 nvm 时 entry 不存在不影响 exec)
    let mut paths = current;
    for p in extra {
        if !paths.split(':').any(|x| x == p) {
            if !paths.is_empty() {
                paths.push(':');
            }
            paths.push_str(p);
        }
    }
    // nvm latest — 用 ~/.nvm/versions/node/*/bin 第一个 (没装也 OK)
    if let Some(home) = home_dir() {
        let nvm = home.join(".nvm").join("versions").join("node");
        if let Ok(entries) = std::fs::read_dir(&nvm) {
            for e in entries.flatten() {
                let bin = e.path().join("bin");
                if bin.exists() {
                    if !paths.is_empty() {
                        paths.push(':');
                    }
                    paths.push_str(&bin.to_string_lossy());
                }
            }
        }
    }
    paths
}

/// 校验 skill_path 在合法 root 下. 防前端误传 `/etc/passwd` 等任意路径删 user file.
/// 合法 root: ~/.hermes/skills/, ~/.claude/skills/, ~/.catfish/skills/.
/// catfish 仓库 skills/ 不算合法 (那是 protected, 不应进 uninstall).
fn is_legal_skill_root(skill_path: &Path) -> bool {
    let Some(home) = home_dir() else {
        return false;
    };
    let legal_roots = [
        home.join(".hermes").join("skills"),
        home.join(".claude").join("skills"),
        home.join(".catfish").join("skills"),
    ];
    let canonical = match std::fs::canonicalize(skill_path) {
        Ok(p) => p,
        Err(_) => return false,
    };
    legal_roots.iter().any(|root| {
        std::fs::canonicalize(root)
            .map(|r| canonical.starts_with(&r))
            .unwrap_or(false)
    })
}

fn install_skill_from_url_blocking(url: String) -> Result<InstallResult, String> {
    // 基础校验 — url 至少 http(s) 或 github: 前缀, 防员工误传"rm -rf /"
    if !url.starts_with("http://")
        && !url.starts_with("https://")
        && !url.starts_with("github:")
        && !url.starts_with("@")
    {
        return Err(format!(
            "url 格式不识 (要 http(s)://… / github:… / @scope/pkg): {url}"
        ));
    }
    let path = augmented_path();
    let output = std::process::Command::new("npx")
        .args(["-y", "skills", "add", &url])
        .env("PATH", &path)
        .output()
        .map_err(|e| format!("启 npx 失败 (检 PATH / Node 装了吗): {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    Ok(InstallResult {
        success: output.status.success(),
        stdout,
        stderr,
        exit_code: output.status.code(),
    })
}

fn uninstall_skill_blocking(skill_path: String) -> Result<UninstallResult, String> {
    let skill_p = PathBuf::from(&skill_path);
    if !skill_p.exists() {
        return Err(format!("skill 路径不存在: {skill_path}"));
    }
    if !is_legal_skill_root(&skill_p) {
        return Err(format!(
            "拒绝: skill 路径不在合法 root (~/.hermes/, ~/.claude/, ~/.catfish/): {skill_path}"
        ));
    }
    // trash dir: ~/.catfish/.trash/skills/<ts>/<original-basename>
    let home = home_dir().ok_or("找不到 HOME")?;
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let trash_root = home
        .join(".catfish")
        .join(".trash")
        .join("skills")
        .join(format!("{ts}"));
    std::fs::create_dir_all(&trash_root)
        .map_err(|e| format!("创 trash 目录失败: {e}"))?;
    let basename = skill_p
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| "unknown".into());
    let trash_dest = trash_root.join(&basename);
    std::fs::rename(&skill_p, &trash_dest)
        .map_err(|e| format!("移到 trash 失败: {e}"))?;
    Ok(UninstallResult {
        trash_path: trash_dest.to_string_lossy().into_owned(),
        original_path: skill_path,
    })
}

fn restore_skill_blocking(trash_path: String, original_path: String) -> Result<(), String> {
    let trash_p = PathBuf::from(&trash_path);
    if !trash_p.exists() {
        return Err(format!("trash 路径不存在 (可能已被 GC 清): {trash_path}"));
    }
    let orig_p = PathBuf::from(&original_path);
    if orig_p.exists() {
        return Err(format!("原位置已被占 (有同名 skill 存在了): {original_path}"));
    }
    // 确保原目录的父级存在
    if let Some(parent) = orig_p.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建原父目录失败: {e}"))?;
    }
    std::fs::rename(&trash_p, &orig_p)
        .map_err(|e| format!("还原失败: {e}"))?;
    Ok(())
}

fn add_mcp_server_blocking(
    name: String,
    command: String,
    args: Vec<String>,
) -> Result<(), String> {
    if name.is_empty() {
        return Err("name 不能空".into());
    }
    if name.starts_with("catfish-") || name == "catfish-tools" {
        return Err(format!(
            "拒绝: catfish-* 是核心 MCP 保留命名, 不允许员工自加 (改用别的名字): {name}"
        ));
    }
    let home = home_dir().ok_or("找不到 HOME")?;
    let cfg_path = home.join(".hermes").join("config.yaml");
    let text = std::fs::read_to_string(&cfg_path)
        .map_err(|e| format!("读 ~/.hermes/config.yaml 失败: {e}"))?;
    let mut value: serde_yaml::Value = serde_yaml::from_str(&text)
        .map_err(|e| format!("解析 config.yaml 失败: {e}"))?;
    // 找 mcp_servers map, 没就建
    let mapping = value
        .as_mapping_mut()
        .ok_or("config.yaml 顶层不是 mapping")?;
    let key = serde_yaml::Value::String("mcp_servers".into());
    if !mapping.contains_key(&key) {
        mapping.insert(
            key.clone(),
            serde_yaml::Value::Mapping(serde_yaml::Mapping::new()),
        );
    }
    let mcp_map = mapping
        .get_mut(&key)
        .and_then(|v| v.as_mapping_mut())
        .ok_or("mcp_servers 段不是 mapping")?;
    let mcp_name_key = serde_yaml::Value::String(name.clone());
    if mcp_map.contains_key(&mcp_name_key) {
        return Err(format!("MCP {name} 已存在, 先移除再加"));
    }
    let mut entry = serde_yaml::Mapping::new();
    entry.insert(
        serde_yaml::Value::String("command".into()),
        serde_yaml::Value::String(command),
    );
    if !args.is_empty() {
        let args_seq: Vec<serde_yaml::Value> = args
            .into_iter()
            .map(serde_yaml::Value::String)
            .collect();
        entry.insert(
            serde_yaml::Value::String("args".into()),
            serde_yaml::Value::Sequence(args_seq),
        );
    }
    mcp_map.insert(mcp_name_key, serde_yaml::Value::Mapping(entry));
    let new_text = serde_yaml::to_string(&value)
        .map_err(|e| format!("序列化失败: {e}"))?;
    std::fs::write(&cfg_path, new_text)
        .map_err(|e| format!("写 config.yaml 失败: {e}"))?;
    Ok(())
}

fn remove_mcp_server_blocking(name: String) -> Result<(), String> {
    if name.starts_with("catfish-") || name == "catfish-tools" {
        return Err(format!(
            "拒绝: catfish-* 是核心 MCP, 不能删 (主链路依赖): {name}"
        ));
    }
    let home = home_dir().ok_or("找不到 HOME")?;
    let cfg_path = home.join(".hermes").join("config.yaml");
    let text = std::fs::read_to_string(&cfg_path)
        .map_err(|e| format!("读 ~/.hermes/config.yaml 失败: {e}"))?;
    let mut value: serde_yaml::Value = serde_yaml::from_str(&text)
        .map_err(|e| format!("解析 config.yaml 失败: {e}"))?;
    let mapping = value
        .as_mapping_mut()
        .ok_or("config.yaml 顶层不是 mapping")?;
    let key = serde_yaml::Value::String("mcp_servers".into());
    let mcp_map = mapping
        .get_mut(&key)
        .and_then(|v| v.as_mapping_mut())
        .ok_or("mcp_servers 段不存在")?;
    let mcp_name_key = serde_yaml::Value::String(name.clone());
    if mcp_map.remove(&mcp_name_key).is_none() {
        return Err(format!("MCP {name} 不存在"));
    }
    let new_text = serde_yaml::to_string(&value)
        .map_err(|e| format!("序列化失败: {e}"))?;
    std::fs::write(&cfg_path, new_text)
        .map_err(|e| format!("写 config.yaml 失败: {e}"))?;
    Ok(())
}

#[tauri::command]
pub async fn install_skill_from_url(url: String) -> Result<InstallResult, String> {
    tokio::task::spawn_blocking(move || install_skill_from_url_blocking(url))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn uninstall_skill(skill_path: String) -> Result<UninstallResult, String> {
    tokio::task::spawn_blocking(move || uninstall_skill_blocking(skill_path))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn restore_skill(
    trash_path: String,
    original_path: String,
) -> Result<(), String> {
    tokio::task::spawn_blocking(move || restore_skill_blocking(trash_path, original_path))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn add_mcp_server(
    name: String,
    command: String,
    args: Vec<String>,
) -> Result<(), String> {
    tokio::task::spawn_blocking(move || add_mcp_server_blocking(name, command, args))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn remove_mcp_server(name: String) -> Result<(), String> {
    tokio::task::spawn_blocking(move || remove_mcp_server_blocking(name))
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
