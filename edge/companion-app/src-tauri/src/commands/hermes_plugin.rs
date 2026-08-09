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
// include_str!() 编译时把 plugin 的全部 .py + 1 个 .yaml 嵌进 binary. 路径相对本 .rs:
//   edge/companion-app/src-tauri/src/commands/hermes_plugin.rs
//   → ../../../../hermes-plugins/catfish-xcatfish-user/<file>
//
// ⚠⚠ 8/9 P0: **拆出新的 sibling 模块, 必须同时加到下面这张表。**
//
// 事故: plugin.py 按军规拆分协议抽出了 plugin_weixin_zh / plugin_wechat_qr /
// plugin_memory_gate 三个 sibling, 并在 plugin.py **模块级**做
// `_import_sibling("plugin_weixin_zh")` re-export。但这张烘焙表没跟着加。
//
// 后果不是"新功能不生效", 是**整个 plugin 死掉**:
//   1. Companion 启动同步 baked → ~/.hermes/plugins/ (只写表里这几个文件)
//   2. hermes 加载 plugin → plugin.py 模块级 _import_sibling("plugin_weixin_zh")
//   3. 三段 fallback 全落空 → `raise ImportError("plugin_weixin_zh.py 不存在")`
//   4. plugin 加载失败 → **P1-P11 一个都没打上**
//      (X-Catfish-User 多租户注入 / P7 proxy / CORS / picker model override)
//
// 8/9 在鸿波本机实测: ~/.hermes/plugins/catfish-xcatfish-user/ 只有 8 个 .py,
// 直接 exec_module 那份已装的 plugin.py → ImportError。也就是说 8/8 19:11
// 那次同步之后, 这个 plugin 一直是死的。
//
// **这条比"漏个文件"严重的地方在于失败方向**: 拆分协议本身是对的 (re-export
// 保 import 兼容), 但这个 plugin 多一条隐藏要求 —— sibling 还得进二进制。
// 拆的人不会想到要来改 Rust。所以下面加了 tests::baked_files_覆盖仓库里所有_py
// 把这条钉死: 仓库里多一个 .py 而表里没有 → cargo test 红。
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
// ── 8/9 补: plugin.py 拆出来的 sibling, 之前漏了 (见上面 P0 说明) ──
const BAKED_PLUGIN_WEIXIN_ZH: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_weixin_zh.py");
const BAKED_PLUGIN_WECHAT_QR: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_wechat_qr.py");
const BAKED_PLUGIN_MEMORY_GATE: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_memory_gate.py");
const BAKED_ACTIVITY_PROBE: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/activity_probe.py");
const BAKED_MODEL_AUTHORITY: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/model_authority.py");
const BAKED_ROUTE_AUTH: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_route_auth.py");

/// Plugin 的全部文件 (filename, baked content).
///
/// 加新文件到 `edge/hermes-plugins/catfish-xcatfish-user/*.py` 就必须加这里,
/// 由 `tests::baked_files_覆盖仓库里所有_py` 守着。
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
    ("plugin_weixin_zh.py", BAKED_PLUGIN_WEIXIN_ZH),
    ("plugin_wechat_qr.py", BAKED_PLUGIN_WECHAT_QR),
    ("plugin_memory_gate.py", BAKED_PLUGIN_MEMORY_GATE),
    ("activity_probe.py", BAKED_ACTIVITY_PROBE),
    ("model_authority.py", BAKED_MODEL_AUTHORITY),
    ("plugin_route_auth.py", BAKED_ROUTE_AUTH),
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
    let home = crate::util::paths::home_env().ok()?;
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

    // 3) ensure ~/.hermes/.env 有 API_SERVER_KEY —— 决定聊天走不走 hermes
    if let Err(e) = ensure_api_server_key() {
        log::warn!("[P3.5.82] API_SERVER_KEY 配置失败 ({e:#}), 聊天会退化成直连 gateway");
    }
}

/// P3.5.82 (7/29): 保证 `~/.hermes/.env` 里有 `API_SERVER_KEY` + `API_SERVER_ENABLED`.
///
/// ── 为什么这一步决定"鲶鱼记不记得你" ────────────────────────────────
///
/// 聊天有两条路, 分界线就是这个 key:
///   有 key → `hermes_api_config` 的 `enabled = enabled_raw && key.is_some()` 成立
///            → 前端 `useHermes = true` → Companion → hermes → gateway
///   没 key → `enabled` 被强制 false → Companion 直连 gateway, **不经 hermes**
///
/// 而**记忆是 hermes 侧写的**: catfish-memory 的 `sync_turn` 是 hermes agent loop
/// 每轮结束后的钩子, 不经 hermes 就不触发; gateway 侧的写入能力 5/23 已主动删除
/// (memory_distill.py 747 行 + session_summarizer.py 527 行, 见 gateway app.py 注释),
/// 理由是"中央边缘分离, gateway 不再读写员工本机数据".
///
/// 于是没有 key 的机器上, 鲶鱼**能读旧记忆但永远不产生新记忆** —— 而新员工的
/// USER.md / memories/ 本来就是空的 (identity_bundle.rs: "员工个人数据, 没就是没").
/// 表现是"用起来一切正常, 但用多久都不会更懂你", 现场根本看不出哪里坏了。
///
/// 这个 key 原来只有 `scripts/setup-catfish-edge.sh` 会生成, 而那个脚本从自己所在
/// 的**仓库路径**推导依赖 (`CATFISH_REPO/edge/hermes-plugins/...`), 员工只有一个
/// dmg、没有源码, 结构上跑不了。所以边缘能力实际上从没进过员工安装包。
///
/// ── 幂等 ────────────────────────────────────────────────────────────
///
/// 已有 key **一律保留**, 不 rotate。setup-catfish-edge.sh 是每次生成新 key 的
/// (它的场景是"手动轮换"), 但装机路径不能这样: 换了 key 而正在跑的 hermes 内存里
/// 还是旧的, Companion 调 8642 直接 401, 而且要等 hermes 重启才自愈。
fn ensure_api_server_key() -> Result<()> {
    let home = crate::util::paths::home_env().context("拿 HOME")?;
    let hermes = PathBuf::from(&home).join(".hermes");
    if !hermes.exists() {
        log::debug!("[P3.5.82] {} 不存在 (hermes 未装) · skip", hermes.display());
        return Ok(());
    }
    let env_path = hermes.join(".env");
    let text = fs::read_to_string(&env_path).unwrap_or_default();

    let existing = text
        .lines()
        .find_map(|l| l.strip_prefix("API_SERVER_KEY="))
        .map(str::trim)
        .filter(|v| !v.is_empty());

    let key = match existing {
        Some(k) => {
            log::debug!("[P3.5.82] API_SERVER_KEY 已存在 (len={}) · 保留不换", k.len());
            k.to_string()
        }
        None => {
            let k = gen_api_server_key();
            log::info!("[P3.5.82] API_SERVER_KEY 不存在 · 已生成 (len={})", k.len());
            k
        }
    };

    let mut out = replace_or_append_env_line(&text, "API_SERVER_KEY", &key);
    out = replace_or_append_env_line(&out, "API_SERVER_ENABLED", "true");

    if out == text {
        return Ok(()); // 没变化就不写盘, 免得每次启动都动 mtime
    }
    fs::write(&env_path, out).with_context(|| format!("写 {}", env_path.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&env_path, fs::Permissions::from_mode(0o600));
    }
    log::info!(
        "[P3.5.82] ✓ {} 已配 API_SERVER_KEY + API_SERVER_ENABLED=true \
         (hermes 下次启动生效, 之后聊天经 hermes, 记忆开始积累)",
        env_path.display()
    );
    Ok(())
}

/// 64 位十六进制随机 key, 跟 setup-catfish-edge.sh 的 `gen_key` 同规格.
fn gen_api_server_key() -> String {
    use rand::RngCore;
    let mut buf = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut buf);
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// dotenv 行级替换 / 追加. 保留注释和其它变量.
///
/// 跟 `server_config.rs` / `hermes_jwt_sync.rs` 里的同名函数是同一套语义 ——
/// 三处各有一份是既有的重复, 这次不顺手合并: 合并要动那两个已经验证过的调用点,
/// 交付前不做无关改动。合并这件事记在技术债里。
fn replace_or_append_env_line(text: &str, key: &str, value: &str) -> String {
    let prefix = format!("{key}=");
    let mut lines: Vec<String> = text.lines().map(str::to_string).collect();
    let mut replaced = false;
    for line in lines.iter_mut() {
        if line.starts_with(&prefix) {
            *line = format!("{key}={value}");
            replaced = true;
            break;
        }
    }
    if !replaced {
        lines.push(format!("{key}={value}"));
    }
    let mut out = lines.join("\n");
    if !out.ends_with('\n') {
        out.push('\n');
    }
    out
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

/// P3.5.82 (7/29): API_SERVER_KEY 装机配置的回归测试。
///
/// 这个 key 决定聊天走不走 hermes, 而记忆只在 hermes 那条路上写 —— 配错了的
/// 表现是"一切正常但永远不积累记忆", 现场看不出来, 所以必须有测试兜住。
#[cfg(test)]
mod tests_api_server_key {
    use super::replace_or_append_env_line;

    #[test]
    fn appends_when_absent() {
        // 员工机首装: install.sh 从模板 cp 的 .env 里没有这两项
        let input = "TAVILY_API_KEY=tvly-x\nAPI_SERVER_PORT=8642\n";
        let out = replace_or_append_env_line(input, "API_SERVER_KEY", "abc123");
        assert!(out.contains("API_SERVER_KEY=abc123"));
        assert!(out.contains("TAVILY_API_KEY=tvly-x"), "别的 key 被动了");
        assert!(out.contains("API_SERVER_PORT=8642"));
    }

    #[test]
    fn replaces_in_place_not_duplicate() {
        // 追加出两行的话 dotenv 取哪行看实现 —— 又是"看着配对了其实没生效"
        let input = "API_SERVER_KEY=old\nOTHER=1\n";
        let out = replace_or_append_env_line(input, "API_SERVER_KEY", "new");
        assert_eq!(out.matches("API_SERVER_KEY=").count(), 1);
        assert!(out.contains("API_SERVER_KEY=new"));
        assert!(!out.contains("old"));
    }

    #[test]
    fn keeps_comments_and_blank_structure() {
        // .env 里有注释说明各字段用途, 装机改写不该把它们吃掉
        let input = "# hermes API server\nAPI_SERVER_ENABLED=false\n# 上游 key\nTAVILY_API_KEY=x\n";
        let out = replace_or_append_env_line(input, "API_SERVER_ENABLED", "true");
        assert!(out.contains("# hermes API server"));
        assert!(out.contains("# 上游 key"));
        assert!(out.contains("API_SERVER_ENABLED=true"));
        assert!(!out.contains("API_SERVER_ENABLED=false"));
    }

    #[test]
    fn generated_key_is_64_hex() {
        // 跟 setup-catfish-edge.sh 的 gen_key 同规格 (32 字节 → 64 hex)
        let k = super::gen_api_server_key();
        assert_eq!(k.len(), 64, "长度跟脚本生成的不一致");
        assert!(k.chars().all(|c| c.is_ascii_hexdigit()));
        // 两次不能一样 —— 用死值等于所有员工共用一个密钥
        assert_ne!(k, super::gen_api_server_key());
    }

    #[test]
    fn empty_env_file_gets_both_keys() {
        // hermes 装了但 .env 是空文件 (install.sh 的 `touch` 分支)
        let out = replace_or_append_env_line("", "API_SERVER_KEY", "k");
        let out = replace_or_append_env_line(&out, "API_SERVER_ENABLED", "true");
        assert!(out.contains("API_SERVER_KEY=k"));
        assert!(out.contains("API_SERVER_ENABLED=true"));
        assert!(out.ends_with('\n'), "dotenv 末尾必须有换行");
    }
}

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

    /// 仓库里每个 .py 都得在 BAKED_FILES 里 —— 8/9 那次 P0 的闸。
    ///
    /// 漏一个的后果不是"新功能不生效": plugin.py 模块级会
    /// `_import_sibling("<漏掉的>")`, 三段 fallback 全落空 → ImportError →
    /// **整个 plugin 加载失败, P1-P11 一个都不打**。8/8 19:11 到 8/9 之间
    /// 鸿波本机就是这个状态。
    ///
    /// 拆分协议 (抽 sibling + re-export) 本身没错, 是这个 plugin 多一条隐藏
    /// 要求: sibling 还得进二进制。拆的人不会想到要改 Rust, 所以用测试钉住。
    #[test]
    fn baked_files_覆盖仓库里所有_py() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../hermes-plugins/catfish-xcatfish-user");
        let entries = match fs::read_dir(&dir) {
            Ok(e) => e,
            // 只在完整 catfish 仓库里跑得动; 单独发的 crate 目录没有这层就跳过
            Err(_) => return,
        };
        let baked: Vec<&str> = BAKED_FILES.iter().map(|(n, _)| *n).collect();
        let mut missing: Vec<String> = Vec::new();
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.ends_with(".py") {
                continue;
            }
            if !baked.contains(&name.as_str()) {
                missing.push(name);
            }
        }
        missing.sort();
        assert!(
            missing.is_empty(),
            "这些 .py 在仓库里但没进 BAKED_FILES: {missing:?}\n\
             → Companion 同步时不会写它们, plugin.py 的 _import_sibling 会抛 \
             ImportError, 整个 plugin 加载失败 (P1-P11 全不打)。\n\
             修法: 在上面加一条 include_str! 常量 + 往 BAKED_FILES 补一行。"
        );
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
