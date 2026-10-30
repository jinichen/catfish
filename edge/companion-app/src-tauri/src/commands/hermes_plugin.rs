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
//! 1. **include_str!() 编译时内嵌** plugin 的全部 Python 文件 + 1 个 yaml (进 Companion
//!    binary, 跟 SOUL ~25KB 加起来在几百 KB 量级, 可接受)
//!
//!    这里原来写死"9 个" —— 8/9 补到 14 个、8/15 补到 18 个的时候都没人改它。
//!    真实数量看 `BAKED_FILES.len()`, 别在正文里再写死一个会过期的数。
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
//! 4. **plugin 文件真变了才重启 hermes** (8/9, 就是原来那条 TODO P3.5.56.1):
//!    hermes 只在进程启动时加载 plugin, 所以"同步过去"不等于"生效"。老行为是
//!    从不重启、等 hermes 下次自然重启 —— 结果是装了新包之后新端点 404, 而且
//!    **完全静默** (没报错, 只是功能不存在)。8/9 一天内漏了两次。
//!    现在 sync 返回"内容有没有真的变", 变了才 `launchctl kickstart -k`。
//!    没变不动 —— 不白白打断 hermes 正在跑的 turn。
//!
//! 5. **Escape hatch**: `CATFISH_HERMES_PLUGIN_NO_BOOTSTRAP=1` env 跳过全部 (开发者调试用).

use anyhow::{Context, Result};
use serde_yaml::Value as YamlValue;
use std::fs;
use std::path::{Path, PathBuf};

// ─────────────────────────────────────────────
// P3.5.56: baked-in plugin source files
// BAKED_* 常量 + BAKED_FILES 表: 8/21 纯搬迁到 hermes_plugin_baked.rs
// (本文件越 800 行红线)。拆 sibling 必须同步加表的规矩不变, 守护测试在下面。
use super::hermes_plugin_baked::BAKED_FILES;

const PLUGIN_NAME: &str = "catfish-xcatfish-user";

/// hermes gateway 的 launchd label。
///
/// 实测来源: 员工跑 `hermes gateway stop` 时 launchd 报
/// `Could not find service "ai.hermes.gateway" in domain for user gui: 501`。
/// CHANGELOG:1875 也记着 `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway`。
#[cfg(target_os = "macos")]
const HERMES_LAUNCHD_LABEL: &str = "ai.hermes.gateway";

// ─────────────────────────────────────────────
// 路径 helpers
// ─────────────────────────────────────────────

pub(super) fn hermes_home() -> Option<PathBuf> {
    // 跟 identity_bundle.rs / curator_config.rs 同源
    if let Ok(env) = std::env::var("HERMES_HOME") {
        return Some(PathBuf::from(env));
    }
    let home = crate::util::paths::home_env().ok()?;
    Some(crate::services::catfish_paths::hermes_home_for(&PathBuf::from(home)))
}

fn plugin_dir() -> Option<PathBuf> {
    hermes_home().map(|h| h.join("plugins").join(PLUGIN_NAME))
}

pub(super) fn config_yaml_path() -> Option<PathBuf> {
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
/// 8/9: plugin 文件内容真变了会自动 `launchctl kickstart -k` 重启 hermes ——
/// 不重启新 plugin 代码不生效, 而那个失败是静默的 (见 restart_hermes_gateway)。
/// 内容没变就不动。
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
    let mut plugin_changed = match sync_plugin_files(&plugin_path) {
        Ok(changed) => changed,
        Err(e) => {
            log::warn!("[P3.5.56] sync plugin 文件失败 ({}), 跳", e);
            return;
        }
    };

    // 1b) 红线策略插件 catfish-policy (10/21): 同一套同步 + 启用, 变了一起重启
    plugin_changed |= super::hermes_policy_plugin::bootstrap_policy_plugin();

    // 1c) 专家机器人 profile 里那份 (10/30): 主 gateway 进程也会加载它, 不同步就一直是旧版
    plugin_changed |= super::hermes_profile_plugins::sync_profile_copies(PLUGIN_NAME, BAKED_FILES);

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
    if let Err(e) = super::hermes_plugin_env::ensure_api_server_key() {
        log::warn!("[P3.5.82] API_SERVER_KEY 配置失败 ({e:#}), 聊天会退化成直连 gateway");
    }

    // 4) plugin 文件真的变了才重启 hermes (8/9)
    //
    // 只在"变了"时重启, 不是每次启动都重启 —— 后者会白白打断 hermes 正在跑的
    // turn (微信那边可能有人在对话)。变了的那一刻新旧代码已经不一致, 不重启
    // 才是坏状态。
    //
    // 放在最后: config.yaml 的 plugins.enabled 和 .env 的 API_SERVER_KEY 都
    // 处理完再重启, 免得 hermes 起来时读到写了一半的配置。
    // (原 5) Windows 心跳补丁 hermes_windows_watchdog 已退役: hermes v0.22 上游 shutdown_watchdog.py
    // 自己按 os.name 分支了 (Windows 走 TCP loopback), 10/14 删。)
    if plugin_changed {
        restart_hermes_gateway();
    }
}

/// 按状态分发, 真正写 plugin 9 个文件到 plugin_dir.
/// 同步 baked plugin 文件到 `~/.hermes/plugins/catfish-xcatfish-user/`。
///
/// 返回 **是否有文件内容真的变了** —— 上层拿它决定要不要重启 hermes
/// (见 `restart_hermes_gateway`)。健康软链那条路直接返 `false`: 开发者
/// deploy.sh 软链的目录我们不动, 也就谈不上"变了"。
fn sync_plugin_files(plugin_path: &Path) -> Result<bool> {
    sync_baked_files(plugin_path, BAKED_FILES)
}

/// `sync_plugin_files` 的通用版: 按给定的烘焙文件表同步一个插件目录。
/// catfish-policy 也走这条 (见 hermes_policy_plugin.rs)。
pub(super) fn sync_baked_files(plugin_path: &Path, files: &[(&str, &str)]) -> Result<bool> {
    let kind = classify_existing(plugin_path);
    match kind {
        ExistingKind::HealthySymlink => {
            log::debug!(
                "[P3.5.56] {} 是健康软链 (开发者 deploy.sh 路径), 不动",
                plugin_path.display()
            );
            // 不动 = 没变 → 不触发重启 (开发者本机改源码是即时生效的)
            return Ok(false);
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

    // 8/9: 返回"内容有没有真的变", 给上层决定要不要重启 hermes。
    //
    // 原来无条件全量重写、返 Result<()>, 上层不知道变没变, 于是**永远不重启**
    // (见 bootstrap_hermes_plugin 的老注释)。而 hermes 只在启动时加载 plugin,
    // 结果是: 装了新包 → 文件同步过去了 → 但 hermes 还跑着旧的 → 新端点 404,
    // **没有任何报错**。8/9 一天内因为这个漏了两次。
    //
    // 逐个比对内容再写, 顺带少写没变的文件 (每次启动全量重写 15 个文件也没必要)。
    let mut changed: Vec<&str> = Vec::new();
    let mut total_bytes = 0usize;
    for (name, content) in files {
        let dst = plugin_path.join(name);
        total_bytes += content.len();
        // 已存在且内容一致 → 不动。读失败 (不存在/权限) 一律当"要写"。
        if let Ok(existing) = fs::read_to_string(&dst) {
            if existing == *content {
                continue;
            }
        }
        // tmp + rename atomic 写, 防 hermes daemon 半读半写
        let tmp = dst.with_extension("tmp");
        fs::write(&tmp, content).with_context(|| format!("写临时文件 {} 失败", tmp.display()))?;
        fs::rename(&tmp, &dst)
            .with_context(|| format!("rename {} → {} 失败", tmp.display(), dst.display()))?;
        changed.push(name);
    }

    if changed.is_empty() {
        log::debug!(
            "[P3.5.56] plugin 文件跟 baked 一致, 没动 ({} 个文件, {} bytes)",
            files.len(),
            total_bytes
        );
    } else {
        log::info!(
            "[P3.5.56] sync baked → {} · 改了 {}/{} 个: {:?}",
            plugin_path.display(),
            changed.len(),
            files.len(),
            changed
        );
    }
    Ok(!changed.is_empty())
}

/// plugin 文件变了之后重启 hermes gateway —— 不重启新代码不生效 (8/9).
///
/// ── 为什么必须重启 ──────────────────────────────────────────────────
///
/// hermes 只在**进程启动时**加载 plugin。Companion 把文件同步过去不等于生效:
/// 装了新包 → 文件是新的 → hermes 还跑着旧的 → 新端点 404。而这个失败**完全
/// 静默** —— 没有报错, 只是新功能不存在。8/9 一天内漏了两次, 两次都是花了
/// 十几分钟才想起来"哦要重启 hermes"。
///
/// 老注释写的是"不抢 hermes 控制权, 等它下次自然重启" (本文件 line 31-34 的
/// TODO P3.5.56.1 就是这条)。那个顾虑成立 —— hermes 是 launchctl 管的独立服务,
/// Companion 不该乱动。但**只在文件真的变了时重启一次**跟"乱动"是两回事:
/// 那一刻新旧代码已经不一致了, 不重启才是坏状态。
///
/// ── 先请 hermes 自己重启, launchctl 只做退路 (10/25) ─────────────────
///
/// 原来直接 `launchctl kickstart -k`。launchd 管的是包着网关的 osascript 外壳, -k 杀掉外壳后
/// 立刻拉起新的, 而旧网关还在收尾 (约 1 秒) —— 新网关看到它还活着, 报「A gateway already owns
/// this host」退出, launchd 按 ThrottleInterval (30s) 才再拉: 每次重启网关断 25~70 秒
/// (gateway.log 10/22 起 14 次)。员工那边就是升级 / 插件更新后一阵子发消息、微信都没反应。
///
/// `hermes gateway restart` 会给网关发 SIGUSR1: 网关等进行中的对话收尾, 自己干净退出, launchd 再拉起
/// —— 10/25 本机实测断 9 秒, 没有撞上。命令发出请求就返回 (不等新网关起来): 收尾可能要很久, 等不得,
/// 更不能等超时了再 kickstart -k —— 那会把正在收尾的对话杀掉。所以只有命令本身跑不起来才退回 launchctl。
///
/// 老顾虑「GUI app 的 PATH 里没有 hermes」: 这里用 venv 里 hermes 的绝对路径, 不靠 PATH。
///
/// launchctl 的 label 来自实测: 员工跑 `hermes gateway stop` 时 launchd 报的是
/// `Could not find service "ai.hermes.gateway" in domain for user gui: 501`。
/// CHANGELOG:1875 也记着同一条命令。
///
/// ── 失败不阻塞 ──────────────────────────────────────────────────────
///
/// 没装 LaunchAgent (员工手动前台跑 hermes) / kickstart 返非 0 → 只 warn,
/// 并把手动命令打出来。Companion 启动不该因为这个挂掉。
#[cfg(target_os = "macos")]
pub(crate) fn restart_hermes_gateway() {
    use std::process::Command;

    match request_hermes_restart() {
        Ok(cli) => {
            log::info!("[P3.5.56] plugin 变了 → 已请 hermes 重启网关 (等进行中的对话收尾后重启; {cli})");
            return;
        }
        Err(e) => log::warn!("[P3.5.56] `hermes gateway restart` 不可用, 退回 launchctl kickstart: {e}"),
    }

    // 拿 uid 走 `id -u` 而不是加一个 libc 依赖 —— 为一个整数引 crate 不值,
    // 而且 unsafe { libc::getuid() } 在这条路径上没有任何收益。
    // /usr/bin/id 走绝对路径: GUI app 的 PATH 极简, 不能指望 `id` 在里面。
    let uid = match Command::new("/usr/bin/id").arg("-u").output() {
        Ok(out) if out.status.success() => {
            String::from_utf8_lossy(&out.stdout).trim().to_string()
        }
        _ => {
            log::warn!(
                "[P3.5.56] plugin 变了但拿不到 uid, 没法重启 hermes\n\
                 → 新 plugin 代码**还没生效**。手动跑: \
                 hermes gateway stop && hermes gateway start"
            );
            return;
        }
    };
    let target = format!("gui/{uid}/{HERMES_LAUNCHD_LABEL}");

    // -k = 已在跑就先杀再起; 没在跑就直接起
    match Command::new("/bin/launchctl")
        .args(["kickstart", "-k", &target])
        .output()
    {
        Ok(out) if out.status.success() => {
            log::info!("[P3.5.56] plugin 变了 → 已重启 hermes gateway ({target})");
        }
        Ok(out) => {
            let stderr = String::from_utf8_lossy(&out.stderr);
            log::warn!(
                "[P3.5.56] plugin 变了但重启 hermes 失败 ({}): {}\n\
                 → 新 plugin 代码**还没生效**。手动跑: \
                 hermes gateway stop && hermes gateway start",
                out.status,
                stderr.trim()
            );
        }
        Err(e) => {
            log::warn!(
                "[P3.5.56] plugin 变了但调不起 launchctl ({e})\n\
                 → 新 plugin 代码**还没生效**。手动跑: \
                 hermes gateway stop && hermes gateway start"
            );
        }
    }
}

/// 请 hermes 自己重启网关: venv 里 hermes 的 `gateway restart`。网关等进行中的对话收尾后干净重启,
/// 命令发出请求就返回, 不等新网关起来。Ok 带 hermes 的路径 (写日志用)。mac / Windows 共用。
fn request_hermes_restart() -> Result<String, String> {
    run_hermes_cli(&["gateway", "restart"])
}

/// 跑 venv 里 hermes 的一条命令 (HERMES_HOME 指好)。Ok 带 hermes 的路径 (写日志用)。
pub(super) fn run_hermes_cli(args: &[&str]) -> Result<String, String> {
    let home = hermes_home().ok_or("找不到 hermes 目录")?;
    let cli = crate::services::catfish_paths::hermes_venv_tool(&home.join("hermes-agent"), "hermes");
    if !cli.exists() {
        return Err(format!("hermes 还没装好 ({} 不在)", cli.display()));
    }
    let out = crate::services::process::python_command(&cli)
        .args(args)
        .env("HERMES_HOME", &home)
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("起不了 {} ({e})", cli.display()))?;
    if !out.status.success() {
        return Err(format!("{} 失败: {}", out.status, String::from_utf8_lossy(&out.stderr).trim()));
    }
    Ok(cli.display().to_string())
}

/// 非 macOS: 没有 launchd, 只走 hermes 自己的 CLI `hermes gateway restart`。
///
/// 9/23: 原来这里只打一行 "请手动重启"。Windows 上插件文件同步过去了、但 hermes
/// 不重启就不加载 —— 8/9 在 mac 上修掉的"新端点 404 且完全静默"在 Windows 上
/// 原样存在 (鸿波机器 /api/catfish/room-link/pending 一直 404)。
///
/// 10/27: Windows 的 `hermes gateway restart` 是先停再起, 进行中的回答只给 30 秒就打断 (鸿波机器
/// 08:59 / 11:45 两次「说到一半停了」)。所以放到后台线程里, 先等手头的回答写完再重启
/// (services/hermes_idle.rs), 不卡调用方 (启动 bootstrap 还要接着报「完成」)。
#[cfg(not(target_os = "macos"))]
pub(crate) fn restart_hermes_gateway() {
    let spawned = std::thread::Builder::new()
        .name("hermes-restart-when-idle".into())
        .spawn(|| {
            crate::services::hermes_idle::wait_until_idle(
                crate::services::hermes_idle::MAX_WAIT,
                "重启 hermes 网关",
            );
            match request_hermes_restart() {
                Ok(cli) => log::info!("[P3.5.56] plugin 变了 → 已请 hermes 重启网关 ({cli})"),
                Err(e) => log::warn!(
                    "[P3.5.56] plugin 变了但 `hermes gateway restart` 没成: {e}\n\
                     → 新 plugin 代码**还没生效** (hermes 还没装好的话, 装完启动时会加载)。手动跑: hermes gateway restart"
                ),
            }
            super::hermes_gateway_health::ensure_gateway_up("插件更新重启后");
        });
    if let Err(e) = spawned {
        log::warn!("[P3.5.56] 起不了等空闲重启的线程 ({e}), hermes 网关没重启");
    }
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
    ensure_named_plugin_enabled_at(path, PLUGIN_NAME)
}

/// 通用版: 确保 config.yaml plugins.enabled 含 `name` (catfish-policy 也走这条)。
pub(super) fn ensure_named_plugin_enabled_at(path: &Path, name: &str) -> Result<bool> {
    // 读 / parse / 默认空 root
    let mut root: YamlValue = if path.exists() {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("读 {} 失败", path.display()))?;
        serde_yaml::from_str(&raw).unwrap_or_else(|_| YamlValue::Mapping(Default::default()))
    } else {
        YamlValue::Mapping(Default::default())
    };

    // 防御: 员工显式 disabled 就尊重, 不强 enable
    let plugin_yaml = YamlValue::from(name);
    if let Some(disabled) = root.get("plugins").and_then(|p| p.get("disabled")) {
        if let Some(list) = disabled.as_sequence() {
            if list.contains(&plugin_yaml) {
                log::warn!(
                    "[P3.5.56] config.yaml plugins.disabled 含 {}, 尊重员工配置, 不强 enable",
                    name
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
        let changed = sync_plugin_files(&plugin_path).unwrap();
        assert!(changed, "从无到有必须算'变了'(要重启 hermes)");
        // 文件全在
        for (name, _) in BAKED_FILES {
            assert!(
                plugin_path.join(name).is_file(),
                "{} 没写出来",
                name
            );
        }
    }

    /// 8/9 这次改动的核心判据: **内容没变就不能返 true**。
    ///
    /// 返错了的代价是每次 Companion 启动都白重启一次 hermes —— 会打断正在跑的
    /// turn (微信那边可能有人在对话)。所以"第二次跑返 false"必须钉死。
    #[test]
    fn sync_plugin_files_第二次跑不算变() {
        let tmp = TempDir::new().unwrap();
        let plugin_path = tmp.path().join("plugins").join("catfish-xcatfish-user");

        assert!(sync_plugin_files(&plugin_path).unwrap(), "第一次: 从无到有");
        assert!(
            !sync_plugin_files(&plugin_path).unwrap(),
            "第二次内容一致却报'变了' → 每次启动都会白重启 hermes"
        );
    }

    /// 反过来: 真被改脏了就必须报"变了", 否则新代码永远不生效 (静默 404)。
    #[test]
    fn sync_plugin_files_内容被改过就算变() {
        let tmp = TempDir::new().unwrap();
        let plugin_path = tmp.path().join("plugins").join("catfish-xcatfish-user");
        sync_plugin_files(&plugin_path).unwrap();
        assert!(!sync_plugin_files(&plugin_path).unwrap(), "先确认稳定态");

        // 模拟"装了旧包 / 被人手改过"
        fs::write(plugin_path.join("plugin.py"), "STALE").unwrap();
        assert!(
            sync_plugin_files(&plugin_path).unwrap(),
            "内容跟 baked 不一致却报'没变' → hermes 不重启, 新代码静默不生效"
        );
        // 而且要真的写回去
        let after = fs::read_to_string(plugin_path.join("plugin.py")).unwrap();
        assert_ne!(after, "STALE");
    }

    /// 缺文件 (今天那个 P0 的形状) 也必须算变。
    #[test]
    fn sync_plugin_files_缺文件算变() {
        let tmp = TempDir::new().unwrap();
        let plugin_path = tmp.path().join("plugins").join("catfish-xcatfish-user");
        sync_plugin_files(&plugin_path).unwrap();
        fs::remove_file(plugin_path.join("model_authority.py")).unwrap();
        assert!(
            sync_plugin_files(&plugin_path).unwrap(),
            "少一个 sibling 却报'没变' —— 8/9 那个 P0 就是这个形状"
        );
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
        let changed = sync_plugin_files(&plugin_path).unwrap();
        assert!(!changed, "健康软链不动 → 不该触发重启 (开发者本机改源码即时生效)");
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

