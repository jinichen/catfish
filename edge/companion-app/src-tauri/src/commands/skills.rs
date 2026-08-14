//! Skills + MCP servers 的 Tauri command 层 —— 只做 spawn_blocking 转发.
//!
//! 8/14 拆分 (原 1249 行, 越过 800 红线)。实现搬到了三个兄弟模块:
//!
//!   skills_list.rs     扫描 / 解析 / 列举
//!   skills_install.rs  安装 / 卸载 / zip 导入 / 还原
//!   skills_mcp.rs      MCP servers 的读和写
//!
//! 这里只留 command —— lib.rs 注册的那 9 个路径 (`commands::skills::xxx`)
//! 因此一个都没变, 拆分对调用方完全不可见。
//!
//! ── 以下是原文件头, 讲两套 skills 来源的, 原样保留 ──
//!
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


use super::skills_install::{
    InstallResult, InstallSkillFromZipResult, UninstallResult, install_skill_from_url_blocking,
    install_skill_from_zip_blocking, restore_skill_blocking, uninstall_skill_blocking,
};
use super::skills_list::{
    SkillNamespace, list_installed_skills_blocking, list_my_skills_blocking,
};
use super::skills_mcp::{
    McpServerEntry, add_mcp_server_blocking, list_mcp_servers_blocking, remove_mcp_server_blocking,
};

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

// 7/17 BL-DEADCODE-SWEEP: list_skills tauri command 死链已删.

#[tauri::command]
pub async fn list_mcp_servers() -> Result<Vec<McpServerEntry>, String> {
    tokio::task::spawn_blocking(list_mcp_servers_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn install_skill_from_url(url: String) -> Result<InstallResult, String> {
    tokio::task::spawn_blocking(move || install_skill_from_url_blocking(url))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

/// P3.3.23 (6/11): 装外部 skill zip (ClawHub / Anthropic .skill 文件).
/// zipBytes 走 Vec<u8> (前端 FileReader.readAsArrayBuffer → Array.from(Uint8Array) → Rust).
/// namespace 默认 "external", 装到 ~/.catfish/skills/<ns>/<slug>/.
#[tauri::command]
pub async fn install_skill_from_zip(
    zip_bytes: Vec<u8>,
    namespace: Option<String>,
) -> Result<InstallSkillFromZipResult, String> {
    tokio::task::spawn_blocking(move || install_skill_from_zip_blocking(zip_bytes, namespace))
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
