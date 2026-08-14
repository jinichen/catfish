//! Skill 的安装 / 卸载 / zip 导入 / 还原.
//!
//! 8/14 从 commands/skills.rs 拆出来。
//!
//! 这一段的设计原则原样保留在下面 E7 phase 2 那段注释里 —— 卸载不真删、
//! 信任分层、路径校验、CLI wrap、PATH 兜底。搬文件不改其中任何一条。

use std::path::{Path, PathBuf};

use serde::Serialize;

use super::skills_list::{home_dir, parse_skill_md, scan_skills_root_filtered_full};

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

pub(crate) fn install_skill_from_url_blocking(url: String) -> Result<InstallResult, String> {
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

pub(crate) fn uninstall_skill_blocking(skill_path: String) -> Result<UninstallResult, String> {
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

// ─────────────────────────────────────────────────────────────────
// P3.3.23 (6/11): 装外部 skill zip (ClawHub / Anthropic .skill / 任何 SKILL.md zip)
//
// 既有 install_skill_from_url 走 `npx -y skills add <url>` 装到 ~/.hermes/skills/,
// 这条新路装 ~/.catfish/skills/<ns>/<slug>/ — 跟 RecMode 教学产物同路径, manifesto
// 公理 1 (员工主权): 外部 skill 装本机, 员工可删可改; 不污染 hermes 上游.
//
// 三层路径必须: scan_skills_root_filtered_full 扫的是 <root>/<ns>/<skill>/SKILL.md.
// 默认 ns="external" 区分 RecMode 教学产物 (落 ~/.catfish/skills/department 等).
//
// 安全 (基础, 严谨 scan 留 P3.3.24):
//   - 文件白名单: .md / .markdown / .json / .txt / .yaml / .yml (拒可执行 / 二进制)
//   - 总解压大小 ≤ 100 MB (防 zip bomb)
//   - mangled_name() 解 zip slip (../escape)
//   - SKILL.md 必须存在 (zip 根 或 第一级子目录)
// ─────────────────────────────────────────────────────────────────

/// P3.3.23: 装外部 skill 结果. installedPath = 装好的 skill 目录, files = 解出来
/// 多少个文件 (跳的不算), warnings = 跳的非白名单文件名 (给员工自查).
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallSkillFromZipResult {
    pub success: bool,
    pub installed_path: String,
    pub files_count: usize,
    pub warnings: Vec<String>,
}

const SKILL_ZIP_MAX_UNPACKED: u64 = 100 * 1024 * 1024; // 100 MB 防 bomb
const SKILL_ZIP_MAX_INPUT: usize = 50 * 1024 * 1024; // 50 MB 输入上限

/// 把 SKILL.md frontmatter 的 name 转 ascii slug (中文 / emoji 滤掉, 空格 -> -).
fn slug_from_name(name: &str) -> String {
    let mut s = String::new();
    for c in name.chars() {
        if c.is_ascii_alphanumeric() {
            s.push(c.to_ascii_lowercase());
        } else if c == ' ' || c == '-' || c == '_' {
            s.push('-');
        }
    }
    // 缩 dash 连发
    while s.contains("--") {
        s = s.replace("--", "-");
    }
    s.trim_matches('-').to_string()
}

pub(crate) fn install_skill_from_zip_blocking(
    zip_bytes: Vec<u8>,
    namespace: Option<String>,
) -> Result<InstallSkillFromZipResult, String> {
    // 1. namespace validate (默认 external)
    let ns = namespace.unwrap_or_else(|| "external".to_string());
    if ns.is_empty()
        || !ns
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err(format!(
            "namespace 只许 a-z 0-9 _ -, 不能空 (传了: {ns:?})"
        ));
    }

    // 2. zip 输入大小防护
    if zip_bytes.len() > SKILL_ZIP_MAX_INPUT {
        return Err(format!(
            "zip 太大 ({:.1} MB > {} MB 上限)",
            zip_bytes.len() as f64 / 1024.0 / 1024.0,
            SKILL_ZIP_MAX_INPUT / 1024 / 1024
        ));
    }

    // 3. temp 目录 — 解到这里, 验完再 rename 到 target. 失败不留半截 skill.
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let temp = std::env::temp_dir().join(format!("catfish_skill_install_{ts}"));
    std::fs::create_dir_all(&temp).map_err(|e| format!("temp 创建失败: {e}"))?;

    // helper 清 temp (任何路径退出都清)
    let cleanup = |temp: &Path| {
        let _ = std::fs::remove_dir_all(temp);
    };

    // 4. 解 zip
    let cursor = std::io::Cursor::new(zip_bytes);
    let mut archive = match zip::ZipArchive::new(cursor) {
        Ok(a) => a,
        Err(e) => {
            cleanup(&temp);
            return Err(format!("zip 解析失败 (可能不是 zip): {e}"));
        }
    };

    const ALLOWED_EXTS: &[&str] = &[
        ".md", ".markdown", ".json", ".txt", ".yaml", ".yml",
    ];
    let mut total_unpacked = 0u64;
    let mut files_count = 0usize;
    let mut warnings: Vec<String> = Vec::new();

    for i in 0..archive.len() {
        let mut entry = match archive.by_index(i) {
            Ok(e) => e,
            Err(e) => {
                cleanup(&temp);
                return Err(format!("zip entry {i} 读失败: {e}"));
            }
        };
        // mangled_name 把 absolute / ../ 等坏路径变安全 (zip slip 防护)
        let raw_path = match entry.enclosed_name() {
            Some(p) => p.to_path_buf(),
            None => {
                warnings.push(format!(
                    "跳过不安全路径: {}",
                    entry.name()
                ));
                continue;
            }
        };

        let dest = temp.join(&raw_path);

        if entry.is_dir() {
            if let Err(e) = std::fs::create_dir_all(&dest) {
                cleanup(&temp);
                return Err(format!("创建目录失败 {dest:?}: {e}"));
            }
            continue;
        }

        // 文件白名单
        let lower = raw_path.to_string_lossy().to_lowercase();
        if !ALLOWED_EXTS.iter().any(|ext| lower.ends_with(ext)) {
            warnings.push(format!("跳过非白名单文件: {}", raw_path.display()));
            continue;
        }

        // bomb 防护
        total_unpacked += entry.size();
        if total_unpacked > SKILL_ZIP_MAX_UNPACKED {
            cleanup(&temp);
            return Err(format!(
                "解压总大小超 {} MB (zip bomb 防护)",
                SKILL_ZIP_MAX_UNPACKED / 1024 / 1024
            ));
        }

        if let Some(parent) = dest.parent() {
            if let Err(e) = std::fs::create_dir_all(parent) {
                cleanup(&temp);
                return Err(format!("创父目录失败 {parent:?}: {e}"));
            }
        }
        let mut out = match std::fs::File::create(&dest) {
            Ok(f) => f,
            Err(e) => {
                cleanup(&temp);
                return Err(format!("创文件失败 {dest:?}: {e}"));
            }
        };
        if let Err(e) = std::io::copy(&mut entry, &mut out) {
            cleanup(&temp);
            return Err(format!("写文件失败 {dest:?}: {e}"));
        }
        files_count += 1;
    }

    // 5. 找 SKILL.md (zip 根 / 第一级子目录, 二者其一)
    let skill_dir: PathBuf = if temp.join("SKILL.md").exists() {
        temp.clone()
    } else {
        let mut found: Option<PathBuf> = None;
        if let Ok(iter) = std::fs::read_dir(&temp) {
            for e in iter.flatten() {
                let p = e.path();
                if p.is_dir() && p.join("SKILL.md").exists() {
                    found = Some(p);
                    break;
                }
            }
        }
        match found {
            Some(p) => p,
            None => {
                cleanup(&temp);
                return Err(
                    "zip 里找不到 SKILL.md (检查: 不是 Anthropic skill 格式? 或路径深超过 2 级)"
                        .into(),
                );
            }
        }
    };

    // 6. 算 slug
    //   优先: skill_dir 的 basename (zip 内自带的目录名)
    //   兜底: SKILL.md frontmatter 的 name 转 slug
    //   再兜底: timestamp
    let slug = if skill_dir != temp {
        skill_dir
            .file_name()
            .and_then(|s| s.to_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| format!("imported-{ts}"))
    } else {
        // SKILL.md 直接在 zip 根, 从 frontmatter 抽 name
        let manifest = skill_dir.join("SKILL.md");
        match parse_skill_md(&manifest) {
            Some(entry) => {
                let s = slug_from_name(&entry.name);
                if s.is_empty() {
                    format!("imported-{ts}")
                } else {
                    s
                }
            }
            None => format!("imported-{ts}"),
        }
    };

    // 7. target 路径 + 防覆盖
    let home = match home_dir() {
        Some(h) => h,
        None => {
            cleanup(&temp);
            return Err("HOME 找不到".into());
        }
    };
    let target_ns = home.join(".catfish").join("skills").join(&ns);
    let target = target_ns.join(&slug);
    if target.exists() {
        cleanup(&temp);
        return Err(format!(
            "已存在 (先卸载或换 namespace): {}",
            target.display()
        ));
    }
    if let Err(e) = std::fs::create_dir_all(&target_ns) {
        cleanup(&temp);
        return Err(format!("创 namespace 目录失败 {target_ns:?}: {e}"));
    }

    // 8. 移到 target. rename 同分区原子, 跨分区会失败 — fallback 走 copy+remove.
    if let Err(rename_err) = std::fs::rename(&skill_dir, &target) {
        // fallback: 递归复制
        if let Err(copy_err) = copy_dir_recursive(&skill_dir, &target) {
            cleanup(&temp);
            return Err(format!(
                "rename 失败 ({rename_err}), copy fallback 也失败: {copy_err}"
            ));
        }
    }
    cleanup(&temp);

    Ok(InstallSkillFromZipResult {
        success: true,
        installed_path: target.to_string_lossy().into_owned(),
        files_count,
        warnings,
    })
}

/// rename 跨分区失败时的 fallback — 递归复制目录树.
fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<(), String> {
    std::fs::create_dir_all(dst).map_err(|e| format!("创 dst {dst:?}: {e}"))?;
    for entry in std::fs::read_dir(src).map_err(|e| format!("读 src {src:?}: {e}"))? {
        let entry = entry.map_err(|e| format!("entry: {e}"))?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        let ft = entry
            .file_type()
            .map_err(|e| format!("file_type {from:?}: {e}"))?;
        if ft.is_dir() {
            copy_dir_recursive(&from, &to)?;
        } else {
            std::fs::copy(&from, &to).map_err(|e| format!("copy {from:?} → {to:?}: {e}"))?;
        }
    }
    Ok(())
}

pub(crate) fn restore_skill_blocking(trash_path: String, original_path: String) -> Result<(), String> {
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

