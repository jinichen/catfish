//! P3.5.56 (6/21 鸿波 "有坑就要立刻填平"): Companion 启动自动装 catfish-xcatfish-user plugin.
//!
//! # 背景 (跟 SOUL P3.5.55 同款问题)
//!
//! 现状: plugin 装机靠员工手动 `bash edge/hermes-plugins/catfish-xcatfish-user/deploy.sh`,
//! 客户场景没装这一步 → ~/.hermes/plugins/catfish-xcatfish-user/ 不存在 → hermes daemon
//! plugin discover 找不到 → catfish 19 个 monkey-patch (P1-P19, P3.5.47-53 sprint 全部)
//! 全失效 → 鲶鱼退化无多租户 header / 无 picker chain / 无 RBAC / 无 P15 approval / 无 P19 SSE.
//!
//! 跟 SOUL.md 同款"装机要自动" 问题, plugin 这一层更严重 — SOUL 缺只是身份退化, plugin
//! 缺是整个 catfish-on-hermes 集成挂.
//!
//! # 设计 (catfish 是 plugin 唯一 source of truth, 跟 SOUL P3.5.55 思路一致)
//!
//! 1. **include_str!() 编译时内嵌** plugin 9 个 Python 文件 + 1 个 yaml (~200KB 进 Companion
//!    binary, 跟 SOUL ~25KB 加起来 <250KB, 可接受)
//!
//! 2. **`bootstrap_hermes_plugin()` Companion setup hook 调用**: 检查
//!    ~/.hermes/plugins/catfish-xcatfish-user/ 状态, 主动同步 baked → fs (overwrite,
//!    保证跟 catfish 一致):
//!    - HealthySymlink (开发者本机 deploy.sh 软链 + target 存在) → 不动 (改即生效路径)
//!    - DanglingSymlink (客户) → 删软链, 创实目录写 baked
//!    - RegularDir (老 Companion 写的) → 9 个文件逐个 overwrite (保证最新)
//!    - Missing → 创目录写 baked
//!
//! 3. **`ensure_plugin_enabled_in_config()` 顺带改 config.yaml**: ~/.hermes/config.yaml
//!    plugins.enabled 列表必须含 catfish-xcatfish-user 否则 hermes plugin loader (
//!    hermes_cli/plugins.py:198) 不加载. Companion 自动 ensure 含, 不在就加 (跟
//!    curator_config::ensure_default 同款 serde_yaml::Value pattern).
//!
//! 4. **不自动重启 hermes daemon**: hermes 不由 Companion 起 (是 launchctl 管的, 见
//!    lib.rs autostart 注释). Companion 写完 plugin 文件, 等 hermes 下次自然重启
//!    (员工机器重启 / hermes daemon 自维护 / 手动 kickstart) 自动生效. 不抢 hermes
//!    控制权. (TODO P3.5.56.1 可选: 检测新装/升级时主动 launchctl kickstart.)
//!
//! 5. **Escape hatch**: `CATFISH_HERMES_PLUGIN_NO_BOOTSTRAP=1` env 跳过全部 (开发者调试用).

use anyhow::{Context, Result};
use serde_yaml::Value as YamlValue;
use std::fs;
use std::path::{Path, PathBuf};

// ─────────────────────────────────────────────
// P3.5.56: baked-in plugin source files
// ─────────────────────────────────────────────
//
// include_str!() 编译时把 plugin 9 个 .py + 1 个 .yaml 嵌进 binary. 路径相对本 .rs 文件:
//   edge/companion-app/src-tauri/src/commands/hermes_plugin.rs
//   → ../../../../hermes-plugins/catfish-xcatfish-user/<file>
//
// 文件实际大小:
//   __init__.py            15915 bytes
//   plugin.py             102652 bytes  ← 主代码 (11+ monkey-patch)
//   plugin.yaml              887 bytes
//   resolver.py             2380 bytes
//   session_registry.py     2431 bytes
//   session_search_router.py 14287 bytes
//   memory_router.py       22246 bytes
//   memory_enforce.py      16556 bytes
//   hermes_token_renewal.py 11850 bytes
//   总计                  ~189KB 进 binary, 可接受
//
// 编译时若 source 缺 (catfish 仓库不全) → cargo build 报错早发现.
const BAKED_INIT: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/__init__.py");
const BAKED_PLUGIN: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin.py");
const BAKED_PLUGIN_YAML: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin.yaml");
const BAKED_RESOLVER: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/resolver.py");
const BAKED_SESSION_REGISTRY: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/session_registry.py");
const BAKED_SESSION_SEARCH_ROUTER: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/session_search_router.py");
const BAKED_MEMORY_ROUTER: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/memory_router.py");
const BAKED_MEMORY_ENFORCE: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/memory_enforce.py");
const BAKED_HERMES_TOKEN_RENEWAL: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/hermes_token_renewal.py");

/// Plugin 9 个文件 (filename, baked content).
const BAKED_FILES: &[(&str, &str)] = &[
    ("__init__.py", BAKED_INIT),
    ("plugin.py", BAKED_PLUGIN),
    ("plugin.yaml", BAKED_PLUGIN_YAML),
    ("resolver.py", BAKED_RESOLVER),
    ("session_registry.py", BAKED_SESSION_REGISTRY),
    ("session_search_router.py", BAKED_SESSION_SEARCH_ROUTER),
    ("memory_router.py", BAKED_MEMORY_ROUTER),
    ("memory_enforce.py", BAKED_MEMORY_ENFORCE),
    ("hermes_token_renewal.py", BAKED_HERMES_TOKEN_RENEWAL),
];

const PLUGIN_NAME: &str = "catfish-xcatfish-user";

// ─────────────────────────────────────────────
// 路径 helpers
// ─────────────────────────────────────────────

fn hermes_home() -> Option<PathBuf> {
    // 跟 identity_bundle.rs / curator_config.rs 同源
    if let Ok(env) = std::env::var("HERMES_HOME") {
        return Some(PathBuf::from(env));
    }
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".hermes"))
}

fn plugin_dir() -> Option<PathBuf> {
    hermes_home().map(|h| h.join("plugins").join(PLUGIN_NAME))
}

fn config_yaml_path() -> Option<PathBuf> {
    hermes_home().map(|h| h.join("config.yaml"))
}

// ─────────────────────────────────────────────
// 4 状态分类 (跟 identity_bundle 同款思路, 但 entry 是 directory 不是 file)
// ─────────────────────────────────────────────

enum ExistingKind {
    /// 不存在 (lstat 返 Err)
    Missing,
    /// 软链 + target 是目录 + 存在 — 开发者 deploy.sh 软链, 不动 (改即生效)
    HealthySymlink,
    /// 软链 + dangling (target 不存在) — 客户场景, 删后写 baked
    DanglingSymlink,
    /// 实目录 — 老 Companion 写过的或 deploy.sh copy 历史, overwrite 9 个文件
    RegularDir,
    /// 其他 (实文件 / unsupported) — 删后写 baked
    Other,
}

fn classify_existing(path: &Path) -> ExistingKind {
    let lmeta = match fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(_) => return ExistingKind::Missing,
    };
    if lmeta.file_type().is_symlink() {
        // path.exists() 跟 symlink → target 在 → true, dangling → false
        return if path.exists() {
            // 进一步: target 必须是目录, 不是文件 (软链到 .py 文件之类的)
            match fs::metadata(path) {
                Ok(m) if m.is_dir() => ExistingKind::HealthySymlink,
                _ => ExistingKind::Other,
            }
        } else {
            ExistingKind::DanglingSymlink
        };
    }
    if lmeta.is_dir() {
        ExistingKind::RegularDir
    } else {
        ExistingKind::Other
    }
}

// ─────────────────────────────────────────────
// 主入口
// ─────────────────────────────────────────────

/// Companion 启动自动装 / 同步 catfish-xcatfish-user plugin.
///
/// 行为表 (按 ExistingKind 分发):
///   - HealthySymlink: 不动 (开发者 deploy.sh 软链 + target 存在 → 改即生效)
///   - DanglingSymlink (客户场景): 删软链, 创实目录, 写 9 baked 文件
///   - RegularDir (老 Companion 写的): 9 文件逐个 overwrite (强制跟 catfish 一致)
///   - Missing: 创目录, 写 9 baked 文件
///   - Other (实文件 / 别的): 删, 创目录, 写 9 baked 文件
///
/// 配套调 `ensure_plugin_enabled_in_config()` 让 ~/.hermes/config.yaml plugins.enabled
/// 含 catfish-xcatfish-user (否则 hermes plugin loader 即使软链了也不加载).
///
/// 不重启 hermes daemon — hermes 不由 Companion 起 (launchctl 管的). Companion 写完
/// 等 hermes 下次自然重启 / 员工手动 kickstart 生效.
///
/// Escape hatch: `CATFISH_HERMES_PLUGIN_NO_BOOTSTRAP=1` env 跳过全部.
///
/// 失败 (~/.hermes/ 创不出 / 权限) → log::warn, 不挂启动.
pub fn bootstrap_hermes_plugin() {
    if std::env::var("CATFISH_HERMES_PLUGIN_NO_BOOTSTRAP").is_ok() {
        log::info!("[P3.5.56] CATFISH_HERMES_PLUGIN_NO_BOOTSTRAP 设, 跳 plugin bootstrap (调试用)");
        return;
    }

    let plugin_path = match plugin_dir() {
        Some(p) => p,
        None => {
            log::warn!("[P3.5.56] HOME 没设, 跳 plugin bootstrap");
            return;
        }
    };

    // 1) 同步 plugin 文件
    if let Err(e) = sync_plugin_files(&plugin_path) {
        log::warn!("[P3.5.56] sync plugin 文件失败 ({}), 跳", e);
        return;
    }

    // 2) ensure config.yaml plugins.enabled 含 catfish-xcatfish-user
    match ensure_plugin_enabled_in_config() {
        Ok(true) => log::info!(
            "[P3.5.56] 写入 config.yaml plugins.enabled += {}",
            PLUGIN_NAME
        ),
        Ok(false) => log::debug!(
            "[P3.5.56] config.yaml plugins.enabled 已含 {}, 不动",
            PLUGIN_NAME
        ),
        Err(e) => log::warn!("[P3.5.56] config.yaml 改写失败 ({}), plugin 可能不生效", e),
    }
}

/// 按状态分发, 真正写 plugin 9 个文件到 plugin_dir.
fn sync_plugin_files(plugin_path: &Path) -> Result<()> {
    let kind = classify_existing(plugin_path);
    match kind {
        ExistingKind::HealthySymlink => {
            log::debug!(
                "[P3.5.56] {} 是健康软链 (开发者 deploy.sh 路径), 不动",
                plugin_path.display()
            );
            return Ok(());
        }
        ExistingKind::DanglingSymlink => {
            fs::remove_file(plugin_path).with_context(|| {
                format!("删 dangling 软链 {} 失败", plugin_path.display())
            })?;
            log::info!("[P3.5.56] 删 dangling 软链 {}", plugin_path.display());
        }
        ExistingKind::RegularDir => {
            // 不删整个目录 (可能有 __pycache__ / tests / 别的). 只 overwrite 9 个文件.
        }
        ExistingKind::Other => {
            fs::remove_file(plugin_path).with_context(|| {
                format!("删旧文件 {} 失败", plugin_path.display())
            })?;
            log::info!("[P3.5.56] 删旧 entry {} (非目录)", plugin_path.display());
        }
        ExistingKind::Missing => {}
    }

    // 创目录 (idempotent: 已存在不报错)
    fs::create_dir_all(plugin_path)
        .with_context(|| format!("创建目录 {} 失败", plugin_path.display()))?;

    let mut wrote = 0usize;
    let mut total_bytes = 0usize;
    for (name, content) in BAKED_FILES {
        let dst = plugin_path.join(name);
        // tmp + rename atomic 写, 防 hermes daemon 半读半写
        let tmp = dst.with_extension("tmp");
        fs::write(&tmp, content).with_context(|| format!("写临时文件 {} 失败", tmp.display()))?;
        fs::rename(&tmp, &dst)
            .with_context(|| format!("rename {} → {} 失败", tmp.display(), dst.display()))?;
        wrote += 1;
        total_bytes += content.len();
    }
    log::info!(
        "[P3.5.56] sync baked → {} ({} 文件, {} bytes, catfish source-of-truth)",
        plugin_path.display(),
        wrote,
        total_bytes
    );
    Ok(())
}

// ─────────────────────────────────────────────
// config.yaml plugins.enabled 维护
// ─────────────────────────────────────────────

/// 确保 ~/.hermes/config.yaml plugins.enabled 列表含 catfish-xcatfish-user.
///
/// 跟 curator_config::ensure_default 同款 _at + 公开版 pattern, 测试用 _at 直传 path
/// (env var 全局会跟别的并行测试 race).
///
/// 行为:
///   - config.yaml 不存在 → 创最小 yaml `plugins: {enabled: [catfish-xcatfish-user]}`
///   - plugins 段不存在 → 加 `plugins: {enabled: [catfish-xcatfish-user]}` (合并到 root)
///   - plugins.enabled 不存在 → 在 plugins 下加 `enabled: [catfish-xcatfish-user]`
///   - plugins.enabled 是列表但不含 catfish-xcatfish-user → append
///   - 已含 → 不动 (返 Ok(false))
///   - plugins.disabled 含 catfish-xcatfish-user → **不动** (尊重员工显式 disable, log warn)
///
/// 返 Ok(true) = 改了, Ok(false) = 已 OK / 员工 disabled.
fn ensure_plugin_enabled_in_config_at(path: &Path) -> Result<bool> {
    // 读 / parse / 默认空 root
    let mut root: YamlValue = if path.exists() {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("读 {} 失败", path.display()))?;
        serde_yaml::from_str(&raw).unwrap_or_else(|_| YamlValue::Mapping(Default::default()))
    } else {
        YamlValue::Mapping(Default::default())
    };

    // 防御: 员工显式 disabled 就尊重, 不强 enable
    let plugin_yaml = YamlValue::from(PLUGIN_NAME);
    if let Some(disabled) = root.get("plugins").and_then(|p| p.get("disabled")) {
        if let Some(list) = disabled.as_sequence() {
            if list.contains(&plugin_yaml) {
                log::warn!(
                    "[P3.5.56] config.yaml plugins.disabled 含 {}, 尊重员工配置, 不强 enable",
                    PLUGIN_NAME
                );
                return Ok(false);
            }
        }
    }

    // 当前 enabled 列表
    let already_enabled = root
        .get("plugins")
        .and_then(|p| p.get("enabled"))
        .and_then(|e| e.as_sequence())
        .map(|list| list.contains(&plugin_yaml))
        .unwrap_or(false);
    if already_enabled {
        return Ok(false);
    }

    // 改: 把 catfish-xcatfish-user 加到 plugins.enabled
    let root_map = root
        .as_mapping_mut()
        .context("config.yaml root 不是 mapping (yaml 格式坏了?)")?;

    // 找或建 plugins 段
    let plugins_key = YamlValue::from("plugins");
    let plugins_node = root_map
        .entry(plugins_key.clone())
        .or_insert(YamlValue::Mapping(Default::default()));
    if !plugins_node.is_mapping() {
        log::warn!(
            "[P3.5.56] config.yaml plugins 段不是 mapping (是 {:?}), 覆盖. 旧值丢, 注意.",
            plugins_node
        );
        *plugins_node = YamlValue::Mapping(Default::default());
    }
    let plugins_map = plugins_node.as_mapping_mut().expect("just ensured mapping");

    // 找或建 enabled 列表
    let enabled_key = YamlValue::from("enabled");
    let enabled_node = plugins_map
        .entry(enabled_key.clone())
        .or_insert(YamlValue::Sequence(Vec::new()));
    if !enabled_node.is_sequence() {
        log::warn!(
            "[P3.5.56] config.yaml plugins.enabled 不是列表 (是 {:?}), 覆盖. 旧值丢.",
            enabled_node
        );
        *enabled_node = YamlValue::Sequence(Vec::new());
    }
    let enabled_seq = enabled_node.as_sequence_mut().expect("just ensured sequence");
    enabled_seq.push(plugin_yaml);

    // 写回 (atomic tmp + rename, 跟 curator_config 同款)
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("创建目录 {} 失败", parent.display()))?;
    }
    let out = serde_yaml::to_string(&root).context("序列化 yaml 失败")?;
    let tmp = path.with_extension("yaml.tmp");
    fs::write(&tmp, out).with_context(|| format!("写临时文件 {} 失败", tmp.display()))?;
    fs::rename(&tmp, path)
        .with_context(|| format!("rename {} → {} 失败", tmp.display(), path.display()))?;
    Ok(true)
}

/// 公开版: 用默认 config_yaml_path() (env 解析). 生产代码用这个.
fn ensure_plugin_enabled_in_config() -> Result<bool> {
    let path = config_yaml_path().context("HOME 没设")?;
    ensure_plugin_enabled_in_config_at(&path)
}

// ─────────────────────────────────────────────
// 测试
// ─────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn baked_files_all_non_empty() {
        for (name, content) in BAKED_FILES {
            assert!(!content.is_empty(), "{} baked 内容空, include_str! 路径错?", name);
        }
    }

    #[test]
    fn ensure_plugin_enabled_creates_minimal_yaml() {
        let tmp = TempDir::new().unwrap();
        let path = tmp.path().join("config.yaml");
        // 文件不存在 → 应建最小 yaml
        let changed = ensure_plugin_enabled_in_config_at(&path).unwrap();
        assert!(changed);
        let raw = fs::read_to_string(&path).unwrap();
        assert!(raw.contains("catfish-xcatfish-user"), "yaml = {raw}");
        // 第二次跑应该 idempotent
        let changed2 = ensure_plugin_enabled_in_config_at(&path).unwrap();
        assert!(!changed2);
    }

    #[test]
    fn ensure_plugin_enabled_appends_to_existing() {
        let tmp = TempDir::new().unwrap();
        let path = tmp.path().join("config.yaml");
        fs::write(
            &path,
            "plugins:\n  enabled:\n    - other-plugin\nother_section: foo\n",
        )
        .unwrap();
        let changed = ensure_plugin_enabled_in_config_at(&path).unwrap();
        assert!(changed);
        let raw = fs::read_to_string(&path).unwrap();
        assert!(raw.contains("catfish-xcatfish-user"), "yaml = {raw}");
        assert!(raw.contains("other-plugin"), "其他 plugin 应保留: {raw}");
        assert!(raw.contains("other_section"), "其他段应保留: {raw}");
    }

    #[test]
    fn ensure_plugin_enabled_respects_disabled() {
        let tmp = TempDir::new().unwrap();
        let path = tmp.path().join("config.yaml");
        fs::write(
            &path,
            "plugins:\n  enabled: []\n  disabled:\n    - catfish-xcatfish-user\n",
        )
        .unwrap();
        // 员工显式 disabled → 不强 enable
        let changed = ensure_plugin_enabled_in_config_at(&path).unwrap();
        assert!(!changed);
    }

    #[test]
    fn sync_plugin_files_creates_dir_when_missing() {
        let tmp = TempDir::new().unwrap();
        let plugin_path = tmp.path().join("plugins").join("catfish-xcatfish-user");
        sync_plugin_files(&plugin_path).unwrap();
        // 9 个文件全在
        for (name, _) in BAKED_FILES {
            assert!(
                plugin_path.join(name).is_file(),
                "{} 没写出来",
                name
            );
        }
    }

    #[test]
    fn sync_plugin_files_overwrites_old_regular_dir() {
        let tmp = TempDir::new().unwrap();
        let plugin_path = tmp.path().join("plugins").join("catfish-xcatfish-user");
        fs::create_dir_all(&plugin_path).unwrap();
        // 写一个老 __init__.py 模拟 "老 Companion baked V1"
        fs::write(plugin_path.join("__init__.py"), "OLD V1").unwrap();
        sync_plugin_files(&plugin_path).unwrap();
        // 老 V1 应该被覆盖成 baked
        let after = fs::read_to_string(plugin_path.join("__init__.py")).unwrap();
        assert_ne!(after, "OLD V1", "regular dir 应该被 overwrite");
        assert!(after.len() > 1000, "__init__.py 应该是真 baked 内容");
    }

    #[test]
    #[cfg(unix)]
    fn sync_plugin_files_skips_healthy_symlink() {
        use std::os::unix::fs::symlink;
        let tmp = TempDir::new().unwrap();
        let real_dir = tmp.path().join("real-plugin-source");
        fs::create_dir_all(&real_dir).unwrap();
        fs::write(real_dir.join("__init__.py"), "REAL SOURCE").unwrap();
        let plugin_path = tmp.path().join("plugins").join("catfish-xcatfish-user");
        fs::create_dir_all(plugin_path.parent().unwrap()).unwrap();
        symlink(&real_dir, &plugin_path).unwrap();
        sync_plugin_files(&plugin_path).unwrap();
        // 健康软链 → 不动, __init__.py 仍是 REAL SOURCE
        let after = fs::read_to_string(plugin_path.join("__init__.py")).unwrap();
        assert_eq!(after, "REAL SOURCE", "健康软链不应被 overwrite");
    }

    #[test]
    #[cfg(unix)]
    fn sync_plugin_files_replaces_dangling_symlink() {
        use std::os::unix::fs::symlink;
        let tmp = TempDir::new().unwrap();
        let nonexistent = tmp.path().join("does-not-exist");
        let plugin_path = tmp.path().join("plugins").join("catfish-xcatfish-user");
        fs::create_dir_all(plugin_path.parent().unwrap()).unwrap();
        symlink(&nonexistent, &plugin_path).unwrap();
        sync_plugin_files(&plugin_path).unwrap();
        // dangling 软链应该被删 + 创实目录写 baked
        assert!(plugin_path.is_dir(), "应该变实目录");
        assert!(plugin_path.join("__init__.py").is_file());
    }
}
