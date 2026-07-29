//! 应用启动时自动拉起关键服务 —— 让员工不需要手动按"启动"。
//!
//! 设计:
//!   - tool-bridge: client-side MCP server, 跟 Companion lifecycle 绑定 → Companion 管
//!   - gateway: 服务端基础设施, **不归 Companion 管** (5/22 鸿波拍板)
//!
//! 5/22 BL-COMPANION-DECOUPLE-GATEWAY (鸿波): 服务端 vs 客户端职责清分.
//!   - **服务端** (gateway): 由 launchctl plist (本机 dev) 或客户 IT (生产) 管.
//!     Companion 不 spawn / 不 watchdog. UI health check + 提示员工.
//!   - **客户端** (3 个本地服务, Companion 全管):
//!     - tool-bridge (MCP server, hermes-cli socket)
//!     - local-search (员工文件 FTS watcher)
//!     - chrome (隔离 Chrome 实例 + CDP for LLM 浏览器操作)
//!
//!   解决的老问题:
//!     - autostart + watchdog 撞 PID file / 多进程 (5/22 17561+57521 同跑)
//!     - 50 员工 Mac 各跑独立 gateway 资源浪费
//!     - 职责倒置 (UI 进程拉服务端基础设施)
//!
//!   chrome autostart 决策 (5/22 鸿波拍 "默认 autostart"):
//!   LLM 操作浏览器是 catfish 核心场景 (EIS / 周报 / 资质等), 员工不该手动
//!   每次点 'chrome 启动' 按钮. 默认 spawn, 窗口可手动最小化.

use std::path::Path;

use crate::services::{catfish_paths, endpoints, process};

/// autostart 入口 —— Tauri setup hook 里 spawn 一次, 起 3 个本地客户端服务.
///
/// 5/22 鸿波拍: gateway 解耦 (launchctl 管), 这层只管 3 个 client-side service.
///
/// P3.4.7c (6/15 鸿波): 加 maybe_run_weekly_reset 钩子 — 跨过周一就自动 reset
/// current_todos.md (本周待办文件: 已完成清, 未完成带入新一周).
pub fn schedule_autostart() {
    tauri::async_runtime::spawn(async move {
        ensure_catfish_tools_mcp_registered();
        ensure_tool_bridge_running().await;
        ensure_local_search_running().await;
        ensure_chrome_running().await;
        check_runtime_deps().await;
        maybe_run_weekly_reset().await;
    });
}

// ============================================================
// 运行时依赖自检 (BL-DEPS-SILENT-DEGRADE 7/27 鸿波定)
// ============================================================

/// 检查那些"缺了不会报错、只会悄悄变差"的外部依赖。只打日志,不阻塞启动。
///
/// # 为什么要这个
///
/// 7/27 一晚上挖出来的坑里，有两个属于同一类：**依赖没了,但没人知道**。
///
/// 1. `jieba` 不在 hermes venv 里 → style_fingerprint 静默退化成字符二元组。
///    同一批公文语料实测差距:
///      无 jieba: 覆盖 绩材 台账 兄弟 显缺 范围 补充 占优 弃投 项施
///      有 jieba: 资质 完成 服务 对标 施工 工作 调用 情况 公司 其中
///    这个 top_words 是要注进 system prompt 让 LLM 模仿员工用词的,喂碎片
///    等于喂噪音。而代码里是 `except ImportError: 走 char-2gram`,一声不吭。
///
/// 2. `agent-browser` 没装 → hermes 的 browser_navigate 走 npx fallback,
///    npx 去 npm registry 下载,受限网络下干等 26 秒超时。而 UI 当时还把
///    失败渲染成 ✓,鸿波和我一起往 CDP / 页面渲染方向查了好几轮。
///
/// # 为什么必须真跑一次,不能只看文件在不在
///
/// 鸿波实盘出现过一个中间态: `npm i -g agent-browser` 装完了,`which` 找得到
/// `/opt/homebrew/bin/agent-browser`,但 npm 的 allow-scripts 拦掉了 postinstall,
/// symlink 还指着不完整的东西 —— 跑起来照样超时。
///
/// hermes 自己在 `_find_agent_browser` 里踩过同一个坑,注释写着:
///   "A bare shutil.which hit is NOT trusted: ... leaves a dangling link that
///    which still reports but exec fails on with exit 127"
///
/// 所以这里一律**执行一次**再下结论。
///
/// # 为什么只打日志
///
/// 鸿波 7/27 定的:不上面板。这些是 IT 侧该处理的环境问题,不是员工每天要看的
/// 东西;弹窗只会让人学会忽略弹窗。日志里点名,排查时第一眼就能看到。
pub async fn check_runtime_deps() {
    tokio::task::spawn_blocking(|| {
        check_jieba_installed();
        check_agent_browser_runnable();
    })
    .await
    .ok();
}

/// jieba 在不在 tool-bridge 实际运行的那个解释器里。
fn check_jieba_installed() {
    let Some(python) = catfish_paths::tool_bridge_python() else {
        return;
    };
    let ok = std::process::Command::new(&python)
        .args(["-c", "import jieba"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if ok {
        log::info!("deps: jieba ✓ ({})", python.display());
        return;
    }
    log::warn!(
        concat!(
            "deps: ⚠ jieba 不在 {py}\n",
            "     后果: 文书风格 (style_fingerprint) 的中文分词静默退化成字符二元组,\n",
            "     top_words 会变成「覆盖 绩材 台账 兄弟 显缺」这类无意义碎片,\n",
            "     而它是要注进 system prompt 让 LLM 模仿员工用词的。Dashboard 上\n",
            "     「jieba 分词」那栏会显 ❌,但没人会天天去看。\n",
            "     修: {py} -m pip install jieba\n",
            "     (hermes 升级重建 venv 后会再次丢失 —— 这条检查就是为那时准备的)"
        ),
        py = python.display(),
    );
}

/// agent-browser 能不能真跑起来 (hermes 的 browser_* 全靠它)。
fn check_agent_browser_runnable() {
    let Some(bin) = find_agent_browser() else {
        log::warn!(
            concat!(
                "deps: ⚠ 找不到 agent-browser\n",
                "     后果: hermes 的 browser_navigate / browser_click 等全部不可用 ——\n",
                "     它会 fallback 到 `npx agent-browser`,npx 再去 npm registry 下载,\n",
                "     受限网络下就是干等到超时 (实测 26s),错误信息还只说 timed out。\n",
                "     修: npm i -g agent-browser\n",
                "     (catfish 自己的 catfish_browser_* 走 Playwright 直连 CDP,不依赖它)"
            )
        );
        return;
    };
    // 关键: 真执行一次。见函数头注释里 postinstall / dangling symlink 那段。
    match std::process::Command::new(&bin).arg("--version").output() {
        Ok(o) if o.status.success() => {
            let ver = String::from_utf8_lossy(&o.stdout).trim().to_string();
            log::info!("deps: agent-browser ✓ {} ({})", ver, bin.display());
        }
        Ok(o) => log::warn!(
            concat!(
                "deps: ⚠ agent-browser 存在但跑不起来 (exit {code:?}): {bin}\n",
                "     多半是 npm 的 allow-scripts 拦了 postinstall,symlink 还指着\n",
                "     不完整的东西 —— `which` 查得到,执行就废。\n",
                "     修: node $(npm root -g)/agent-browser/scripts/postinstall.js"
            ),
            code = o.status.code(),
            bin = bin.display(),
        ),
        Err(e) => log::warn!("deps: ⚠ agent-browser 执行失败 {}: {e}", bin.display()),
    }
}

/// 找 agent-browser 二进制。
///
/// Companion 是 GUI app,继承的 PATH 通常只有 `/usr/bin:/bin:/usr/sbin:/sbin`,
/// **不含** Homebrew 和 npm global 的目录 —— 所以不能只靠 `which`,得显式找。
fn find_agent_browser() -> Option<std::path::PathBuf> {
    let mut candidates: Vec<std::path::PathBuf> = vec![
        "/opt/homebrew/bin/agent-browser".into(), // Apple Silicon Homebrew
        "/usr/local/bin/agent-browser".into(),    // Intel Homebrew / npm 默认 prefix
    ];
    if let Ok(home) = crate::util::paths::home_env() {
        let h = std::path::PathBuf::from(home);
        candidates.push(h.join(".npm-global/bin/agent-browser"));
        candidates.push(h.join(".nvm/versions/node/current/bin/agent-browser"));
    }
    if let Ok(path) = std::env::var("PATH") {
        for dir in path.split(':') {
            candidates.push(std::path::Path::new(dir).join("agent-browser"));
        }
    }
    candidates.into_iter().find(|p| p.exists())
}

// ============================================================
// hermes config 自愈 (7/27 鸿波实盘挖出来的两个坑)
//   BL-MCP-CATFISH-TOOLS-MISSING —— catfish 工具对 LLM 全程不可见
//   BL-BROWSER-FAKEIP-BLOCKED    —— fake-IP 代理触发 hermes SSRF 误拦
// ============================================================

/// hermes 通过这个名字加载 catfish 的 MCP server, 拿到全部 catfish_* tool.
const CATFISH_TOOLS_MCP_NAME: &str = "catfish-tools";

/// 保证 ~/.hermes/config.yaml 里两处关键配置正确. 改动过返 true.
///
/// # 为什么需要这个
///
/// 鸿波 7/27 报"浏览器打不开", 顺着 gateway 日志挖到根因:
///
/// P3.5.80 (7/28): 原来这段日志是 4 空格缩进 —— Markdown 里那等于代码块,
/// rustdoc 会当 Rust 拿去 doctest 编译, 于是 `cargo test` 长期红一条.
/// 换成显式 `text` fence.
/// ```text
/// P3.5.70 sanitize entry: tools_count=30
/// ALL=['browser_back','browser_cdp',...,'write_file']   ← 一个 catfish_* 都没有
/// ```
///
/// 发给 LLM 的工具全是 hermes builtin. catfish 那 72 个 tool
/// (catfish_browser_* / catfish_run_skill / catfish_search_* / ...) **全程不可见**.
/// 翻 ~/.hermes/sessions/request_dump_*.json, 7/18 到 7/27 每一份都是 catfish_*=0,
/// 不是突然坏的, 是一直如此.
///
/// 直接后果不止少工具: gateway 的 BL-FIX4 去重 (tools_sanitizer.py:360) 前提是
/// "请求里存在 catfish_browser_*", 一个都没有 → 去重不触发 → hermes builtin
/// browser_navigate 暴露给 LLM. 而那正是 catfish_tools_browser.py 开头记的 4/27 老坑:
/// "✓ 调用成功但页面没真换, 模型幻觉'已打开'". 鸿波看到的就是这个 —— 模型还顺嘴
/// 编了套"沙箱出站封锁"的说辞 (catfish 根本没有那种代码).
///
/// # 为什么会缺
///
/// 谁都不装, 谁都不能装, 而所有代码都假设它在:
///   - local-search/hermes-skill/install.sh 装的是 catfish-local-search, 不是这个
///   - mcp_server.py 的 docstring 只说手工跑 `hermes mcp add catfish-tools`
///   - commands/skills.rs:946 add_mcp_server **明确拒绝** name 以 catfish- 开头
///     ("核心 MCP 保留命名, 不允许员工自加"), 员工想自己补都补不了
///   - autostart 管 tool-bridge / local-search / chrome, 唯独不管 MCP 注册
///
/// 既然它被定义成"主链路依赖、员工不能删"(skills.rs:1000), 那就该由平台自己保证
/// 它在 —— 而不是指望某次装机脚本跑对了.
///
/// # 不重启 hermes
///
/// Companion 不管 hermes 生命周期 (launchd 管). 这里只保证配置正确, hermes
/// 下次启动自然加载. 首次修复需要员工手动重启一次 hermes, 日志里给了命令.
pub fn ensure_catfish_tools_mcp_registered() -> bool {
    let Some(cfg_path) = catfish_paths::hermes_config_path() else {
        log::warn!("autostart: 找不到 ~/.hermes/config.yaml, 跳过 catfish-tools MCP 检查");
        return false;
    };
    if !cfg_path.exists() {
        // hermes 还没初始化过 — 别替它建配置, 建了反而可能跟 hermes 首次写入打架
        log::info!("autostart: {} 还不存在 (hermes 没初始化过), 跳过 MCP 检查", cfg_path.display());
        return false;
    }

    let Some(dir) = catfish_paths::tool_bridge_dir() else {
        log::warn!("autostart: 找不到 tool-bridge 目录, 没法注册 catfish-tools MCP");
        return false;
    };
    let Some(python) = catfish_paths::tool_bridge_python() else {
        log::warn!("autostart: 找不到 hermes venv python, 没法注册 catfish-tools MCP");
        return false;
    };
    // mcp_server.py 需要 mcp SDK (hermes venv 自带) + 能 import catfish_tool_bridge.
    // PYTHONPATH 指 src/ 免得要求员工跑 pip install -e (跟 spawn tool-bridge 同款).
    let pythonpath = dir.join("src").to_string_lossy().to_string();
    let command = python.to_string_lossy().to_string();

    let text = match std::fs::read_to_string(&cfg_path) {
        Ok(t) => t,
        Err(e) => {
            log::warn!("autostart: 读 {} 失败: {e}", cfg_path.display());
            return false;
        }
    };
    let new_text = match patch_hermes_config(&text, &command, &pythonpath, |p| {
        std::path::Path::new(p).exists()
    }) {
        Ok(Some(t)) => t,
        Ok(None) => return false, // 已经都对了, 绝大多数情况走这
        Err(why) => {
            // 配置形态不认识就报出来, 别自作主张覆盖员工的文件
            log::warn!("autostart: {} 不动它 ({why})", cfg_path.display());
            return false;
        }
    };

    // 备份再写 —— config.yaml 里有 model.api_key 等要命的东西
    let backup = cfg_path.with_extension("yaml.bak-mcp-autofix");
    let _ = std::fs::write(&backup, &text);

    match std::fs::write(&cfg_path, new_text) {
        Ok(()) => {
            // 用 concat! 而不是 `\n\` 续行: 续行只跳 ASCII 空白, 想让缩进留在
            // 字符串里就得用全角空格顶住 —— 那会触发 rustc 的
            // "whitespace symbol '\u{3000}' is not skipped" 警告 (7/27 实测 8 条).
            // concat! 每行独立字面量, 缩进就是普通空格, 干净无警告.
            log::warn!(
                concat!(
                    "autostart: ⚠ 修补了 ~/.hermes/config.yaml (备份: {bak})\n",
                    "  ① mcp_servers.{name} —— catfish 全部 catfish_* 工具\n",
                    "     (catfish_browser_* / catfish_run_skill / catfish_search_* ...)\n",
                    "     进 hermes 的唯一通道, 缺了 LLM 只剩 hermes builtin 那 30 个.\n",
                    "     command={command}  args=[-m catfish_tool_bridge.mcp_server]\n",
                    "  ② security.allow_private_urls=true —— fake-IP 模式代理会把外网\n",
                    "     域名解析到 198.18.0.x, hermes SSRF 检查判成内网地址全部拦掉.\n",
                    "     云 metadata 端点 (169.254.169.254) 仍然永远拦, 不受影响.\n",
                    "  ★ 需要重启 hermes 才生效: hermes gateway stop && hermes gateway start",
                ),
                name = CATFISH_TOOLS_MCP_NAME,
                command = command,
                bak = backup.display(),
            );
            true
        }
        Err(e) => {
            log::warn!("autostart: 写 {} 失败: {e}", cfg_path.display());
            false
        }
    }
}

/// 纯函数: 给定 config.yaml 文本, 返回补好的新文本 (两处一起补).
///
/// 1. `mcp_servers.catfish-tools` —— catfish 全部工具进 hermes 的唯一通道
/// 2. `security.allow_private_urls` —— 见下面的说明
///
/// `Ok(None)` = 两处都已经对了, 不用改.
/// `Err(_)`   = 配置形态不认识 (解析失败 / 顶层不是 mapping), 调用方别动文件.
///
/// `command_exists` 注入进来是为了可测 —— 单测不依赖真实文件系统.
///
/// # 为什么要开 security.allow_private_urls
///
/// BL-BROWSER-FAKEIP-BLOCKED (7/27 鸿波实盘): 他让鲶鱼开网页, 两个 tool 都返绿勾
/// 但页面纹丝不动. 翻 ~/.hermes/logs/agent.log 才看到真实返回:
///
/// ```text
/// tools.url_safety: Blocked request to private/internal address:
///   www.sohu.com   -> 198.18.0.78
///   www.google.com -> 198.18.0.76
///   raw.githubusercontent.com -> 198.18.0.15
/// ```
///
/// 每个外网域名都解析到 198.18.0.x 且编号递增 —— 典型的 **fake-IP 模式代理**
/// (Clash / Surge / sing-box), 给每个域名分配一个虚拟 IP 由代理转发.
/// 198.18.0.0/15 是 RFC 2544 基准测试保留段, Python ipaddress 判 is_private=True,
/// 于是 hermes 的 SSRF 检查全部拦掉.
///
/// hermes 自己的源码开头 (tools/url_safety.py:8-11) 逐字写了这个场景:
///   "can be globally disabled via `security.allow_private_urls: true` for
///    environments where DNS resolves external domains to private/benchmark-range
///    IPs (OpenWrt routers, corporate proxies, VPNs that use 198.18.0.0/15 ...)"
///
/// 即便开了, 云 metadata 端点 (169.254.169.254 / metadata.google.internal) 仍然
/// **永远**被拦 —— 那是 `_ALWAYS_BLOCKED_NETWORKS`, 不受这个开关影响.
/// 这也正是 catfish 自己 `_check_ssrf_safe()` 的立场: 只拦 metadata, 不拦内网 IP
/// (央企客户的 10.10.40.102 EIS / 192.168 / 172.16 本来就是日常正常 URL).
fn patch_hermes_config(
    text: &str,
    command: &str,
    pythonpath: &str,
    command_exists: impl Fn(&str) -> bool,
) -> Result<Option<String>, String> {
    let mut value: serde_yaml::Value =
        serde_yaml::from_str(text).map_err(|e| format!("解析失败: {e}"))?;
    let root = value
        .as_mapping_mut()
        .ok_or_else(|| "顶层不是 mapping".to_string())?;

    let servers_key = serde_yaml::Value::String("mcp_servers".into());
    let name_key = serde_yaml::Value::String(CATFISH_TOOLS_MCP_NAME.into());

    let mcp_ok = root
        .get(&servers_key)
        .and_then(|v| v.as_mapping())
        .and_then(|m| m.get(&name_key))
        .and_then(|v| v.as_mapping())
        .and_then(|e| e.get(serde_yaml::Value::String("command".into())))
        .and_then(|v| v.as_str())
        .map(&command_exists)
        .unwrap_or(false);

    let ssrf_key = serde_yaml::Value::String("security".into());
    let allow_key = serde_yaml::Value::String("allow_private_urls".into());
    let ssrf_ok = root
        .get(&ssrf_key)
        .and_then(|v| v.as_mapping())
        .and_then(|m| m.get(&allow_key))
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    if mcp_ok && ssrf_ok {
        return Ok(None);
    }

    // ── security.allow_private_urls ──
    if !ssrf_ok {
        if !root.contains_key(&ssrf_key) {
            root.insert(
                ssrf_key.clone(),
                serde_yaml::Value::Mapping(serde_yaml::Mapping::new()),
            );
        }
        let sec = root
            .get_mut(&ssrf_key)
            .and_then(|v| v.as_mapping_mut())
            .ok_or_else(|| "security 段不是 mapping".to_string())?;
        sec.insert(allow_key, serde_yaml::Value::Bool(true));
    }

    if mcp_ok {
        // MCP 那半边已经对了, 只补了 SSRF 开关
        return serde_yaml::to_string(&value)
            .map(Some)
            .map_err(|e| format!("序列化失败: {e}"));
    }

    let mut entry = serde_yaml::Mapping::new();
    entry.insert(
        serde_yaml::Value::String("command".into()),
        serde_yaml::Value::String(command.to_string()),
    );
    entry.insert(
        serde_yaml::Value::String("args".into()),
        serde_yaml::Value::Sequence(vec![
            serde_yaml::Value::String("-m".into()),
            serde_yaml::Value::String("catfish_tool_bridge.mcp_server".into()),
        ]),
    );
    let mut env_map = serde_yaml::Mapping::new();
    env_map.insert(
        serde_yaml::Value::String("PYTHONPATH".into()),
        serde_yaml::Value::String(pythonpath.to_string()),
    );
    entry.insert(
        serde_yaml::Value::String("env".into()),
        serde_yaml::Value::Mapping(env_map),
    );

    if !root.contains_key(&servers_key) {
        root.insert(
            servers_key.clone(),
            serde_yaml::Value::Mapping(serde_yaml::Mapping::new()),
        );
    }
    let servers = root
        .get_mut(&servers_key)
        .and_then(|v| v.as_mapping_mut())
        .ok_or_else(|| "mcp_servers 段不是 mapping".to_string())?;
    servers.insert(name_key, serde_yaml::Value::Mapping(entry));

    serde_yaml::to_string(&value)
        .map(Some)
        .map_err(|e| format!("序列化失败: {e}"))
}

#[cfg(test)]
mod mcp_autofix_tests {
    use super::*;

    /// 鸿波 7/27 的真实 config.yaml 形态 (零注释, 无 mcp_servers 段)
    const REAL_CONFIG: &str = r#"model:
  api_key: eyJhbGciOi.FAKE.TOKEN
  base_url: http://127.0.0.1:8999/v1
  provider: openai-api
  default: catfish-auto
web:
  backend: tavily
plugins:
  enabled:
  - catfish-xcatfish-user
session:
  auto_reset: true
"#;

    #[test]
    fn adds_mcp_section_when_missing() {
        let out = patch_hermes_config(REAL_CONFIG, "/venv/bin/python", "/tb/src", |_| true)
            .unwrap()
            .expect("缺 mcp_servers 时该返回新文本");
        let v: serde_yaml::Value = serde_yaml::from_str(&out).unwrap();
        let entry = v
            .get("mcp_servers")
            .and_then(|m| m.get("catfish-tools"))
            .expect("catfish-tools 该被写进去");
        assert_eq!(entry.get("command").unwrap().as_str().unwrap(), "/venv/bin/python");
        let args: Vec<&str> = entry
            .get("args")
            .unwrap()
            .as_sequence()
            .unwrap()
            .iter()
            .map(|x| x.as_str().unwrap())
            .collect();
        assert_eq!(args, vec!["-m", "catfish_tool_bridge.mcp_server"]);
        assert_eq!(
            entry.get("env").unwrap().get("PYTHONPATH").unwrap().as_str().unwrap(),
            "/tb/src"
        );
    }

    #[test]
    fn preserves_other_sections() {
        // config.yaml 里有 model.api_key 等要命的东西, 补 MCP 不能碰它们
        let out = patch_hermes_config(REAL_CONFIG, "/venv/bin/python", "/tb/src", |_| true)
            .unwrap()
            .unwrap();
        let v: serde_yaml::Value = serde_yaml::from_str(&out).unwrap();
        assert_eq!(
            v.get("model").unwrap().get("api_key").unwrap().as_str().unwrap(),
            "eyJhbGciOi.FAKE.TOKEN"
        );
        assert_eq!(
            v.get("model").unwrap().get("base_url").unwrap().as_str().unwrap(),
            "http://127.0.0.1:8999/v1"
        );
        assert!(v.get("plugins").is_some());
        assert!(v.get("session").is_some());
    }

    #[test]
    fn noop_when_everything_already_correct() {
        let cfg = format!(
            "{REAL_CONFIG}mcp_servers:\n  catfish-tools:\n    command: /venv/bin/python\n\
             security:\n  allow_private_urls: true\n"
        );
        let out = patch_hermes_config(&cfg, "/venv/bin/python", "/tb/src", |_| true).unwrap();
        assert!(out.is_none(), "两处都对 → 不该改文件");
    }

    #[test]
    fn adds_ssrf_toggle_even_when_mcp_ok() {
        // MCP 已注册但 SSRF 开关没开 —— 只补开关, 别动 MCP
        // BL-BROWSER-FAKEIP-BLOCKED: fake-IP 代理把外网域名解析到 198.18.0.x
        let cfg = format!(
            "{REAL_CONFIG}mcp_servers:\n  catfish-tools:\n    command: /venv/bin/python\n"
        );
        let out = patch_hermes_config(&cfg, "/venv/bin/python", "/tb/src", |_| true)
            .unwrap()
            .expect("缺 SSRF 开关时该返回新文本");
        let v: serde_yaml::Value = serde_yaml::from_str(&out).unwrap();
        assert_eq!(
            v.get("security").unwrap().get("allow_private_urls").unwrap().as_bool(),
            Some(true)
        );
        // MCP 那边原样保留
        assert_eq!(
            v.get("mcp_servers").unwrap().get("catfish-tools").unwrap()
                .get("command").unwrap().as_str().unwrap(),
            "/venv/bin/python"
        );
    }

    #[test]
    fn adds_both_when_both_missing() {
        let out = patch_hermes_config(REAL_CONFIG, "/venv/bin/python", "/tb/src", |_| true)
            .unwrap()
            .unwrap();
        let v: serde_yaml::Value = serde_yaml::from_str(&out).unwrap();
        assert!(v.get("mcp_servers").unwrap().get("catfish-tools").is_some());
        assert_eq!(
            v.get("security").unwrap().get("allow_private_urls").unwrap().as_bool(),
            Some(true)
        );
    }

    #[test]
    fn keeps_existing_security_keys() {
        // security 段下可能有别的 key, 不能被挤掉
        let cfg = format!("{REAL_CONFIG}security:\n  some_other_flag: false\n");
        let out = patch_hermes_config(&cfg, "/venv/bin/python", "/tb/src", |_| true)
            .unwrap()
            .unwrap();
        let v: serde_yaml::Value = serde_yaml::from_str(&out).unwrap();
        let sec = v.get("security").unwrap();
        assert_eq!(sec.get("allow_private_urls").unwrap().as_bool(), Some(true));
        assert_eq!(sec.get("some_other_flag").unwrap().as_bool(), Some(false));
    }

    #[test]
    fn rewrites_when_command_no_longer_exists() {
        // venv 重建 / 路径变了 → 老 command 指向的解释器没了, 得改成当前的
        let cfg = format!(
            "{REAL_CONFIG}mcp_servers:\n  catfish-tools:\n    command: /gone/python\n"
        );
        let out = patch_hermes_config(&cfg, "/new/python", "/tb/src", |p| p == "/new/python")
            .unwrap()
            .expect("解释器没了该重写");
        let v: serde_yaml::Value = serde_yaml::from_str(&out).unwrap();
        assert_eq!(
            v.get("mcp_servers").unwrap().get("catfish-tools").unwrap()
                .get("command").unwrap().as_str().unwrap(),
            "/new/python"
        );
    }

    #[test]
    fn keeps_other_mcp_servers() {
        // 员工自己装的 MCP (catfish-local-search 等) 不能被挤掉
        let cfg = format!(
            "{REAL_CONFIG}mcp_servers:\n  catfish-local-search:\n    command: /x/py\n"
        );
        let out = patch_hermes_config(&cfg, "/venv/bin/python", "/tb/src", |_| true)
            .unwrap()
            .unwrap();
        let v: serde_yaml::Value = serde_yaml::from_str(&out).unwrap();
        let servers = v.get("mcp_servers").unwrap().as_mapping().unwrap();
        assert!(servers.contains_key(serde_yaml::Value::String("catfish-local-search".into())));
        assert!(servers.contains_key(serde_yaml::Value::String("catfish-tools".into())));
    }

    #[test]
    fn finds_agent_browser_in_homebrew_paths() {
        // Companion 是 GUI app, 继承的 PATH 通常不含 Homebrew / npm global ——
        // 7/27 鸿波的 agent-browser 就装在 /opt/homebrew/bin。只靠 which 会漏。
        let found = find_agent_browser();
        // 沙箱/CI 上大概率没装, 这里只断言"不 panic 且返回的路径确实存在"
        if let Some(p) = found {
            assert!(p.exists(), "返回的候选必须真实存在: {}", p.display());
            assert!(p.ends_with("agent-browser"));
        }
    }

    #[test]
    fn errors_on_unparseable_config() {
        // 坏配置不是我们能修的 —— 返 Err 让调用方别动文件
        assert!(patch_hermes_config("::: not yaml :::", "/p", "/s", |_| true).is_err());
    }
}

// 5/22 gateway 解耦: ensure_gateway_running 删 (~70 行 spawn 逻辑), gateway 由
// launchctl/客户 IT 管. P39 收尾: gateway_start/stop/get_dev_token 3 command 也
// 删, 只留 gateway_status 探活. 见 git blame.

// ============================================================
// tool-bridge
// ============================================================

pub async fn ensure_tool_bridge_running() {
    // P3.5.196 (7/7 鸿波军规审判): 每次都 pkill+spawn, 保证 tool-bridge 加载最新代码.
    //
    // # 老逻辑的问题
    // 老 ensure: pid_file 那个 PID 活着就 early return. 副作用跟 local-search 一样 (见
    // ensure_local_search_running P3.4.2 comment):
    //   - 老 tool-bridge 不知道 catfish plugin.py / tool schema 更新了
    //   - 老 tool-bridge 不知道 hermes API 变了 (P25 monkey-patch 签名对不上)
    //   - PID alive ≠ 服务健康 (进程 alive 但 tool 调用 TypeError, watchdog 检测不到)
    //
    // 鸿波 7/7 实测撞过: 早上改了 catfish plugin.py (P3.5.192 has_host_access fix)
    // + 加了 email_read/attachment tool (P3.5.194), 但 tool-bridge 从 12:04 就没重启,
    // 加载的是改动前 code. 员工反馈"chat 未知错误", 原因是 monkey-patch 用老签名调
    // 新 hermes API. 手动 pkill + hermes restart 才好. 军规: 让 Companion 冷启动就
    // 自动重启 tool-bridge, 不依赖员工记忆.
    //
    // # 新逻辑
    // 每次 Companion 启动 pkill -f 'catfish_tool_bridge --socket' 清孤儿, 再 spawn 新的.
    // 启动慢 3-5s (初始 import hermes), 可预测.
    //
    // caller:
    //   - autostart (schedule_autostart): app 冷启动时调, pkill 上次残留 + fresh spawn
    //   - watchdog: 5s tick 时 !is_alive 才调 (进程真死了), pkill 无匹配静默无害
    pkill_tool_bridge();

    let dir = match catfish_paths::tool_bridge_dir() {
        Some(d) => d,
        None => {
            log::warn!("autostart: tool-bridge dir not found, skipping");
            return;
        }
    };
    let python = match catfish_paths::tool_bridge_python() {
        Some(p) => p,
        None => {
            log::warn!("autostart: hermes venv python not found — \
                tool-bridge can't start (聊天将无法调工具, Gemini 会退化到 tool_code)");
            return;
        }
    };
    let log_path = match catfish_paths::tool_bridge_log_path() {
        Some(p) => p,
        None => return,
    };
    let pid_file = match catfish_paths::tool_bridge_pid_file() {
        Some(p) => p,
        None => return,
    };
    let socket_path = match catfish_paths::tool_bridge_socket() {
        Some(p) => p,
        None => return,
    };

    // socket 文件残留清理 —— 上次没正常 stop 会留死文件
    let _ = std::fs::remove_file(&socket_path);

    let pythonpath = dir.join("src").to_string_lossy().to_string();
    let cfg = process::SpawnConfig {
        program: python,
        args: vec![
            "-m".into(),
            "catfish_tool_bridge".into(),
            "--socket".into(),
            socket_path.to_string_lossy().to_string(),
        ],
        log_path,
        working_dir: dir,
        env: vec![("PYTHONPATH".into(), pythonpath)],
    };

    match process::spawn_detached(cfg) {
        Ok(handle) => {
            if let Err(e) = std::fs::write(&pid_file, handle.pid.to_string()) {
                log::warn!("autostart: tool-bridge started but failed to write PID: {e}");
            } else {
                log::info!("autostart: tool-bridge started (PID {})", handle.pid);
            }
        }
        Err(e) => {
            log::warn!("autostart: tool-bridge spawn failed: {e}");
        }
    }
}

// ============================================================
// local-search (5/22 鸿波: 加进 autostart, 文件 FTS watcher)
// ============================================================

pub async fn ensure_local_search_running() {
    // P3.4.2 (6/15 鸿波): 强制清旧进程后重启 — 不再"在跑就 return".
    //
    // # 老逻辑的问题
    // 老 ensure: pid_file 那个 PID 活着就 early return. 副作用:
    //   - 老 watcher 不知道 ~/.catfish/search-scope.yaml 改了 (没 SIGHUP / reload)
    //   - 老 watcher 不知道 catfish_search 包代码更新了 (Python module 已载入)
    //   - pid_file 只记 1 个 PID, 历史 race 留下的孤儿进程不被清理
    //
    // 鸿波本机实测撞过: 进程 5/8 起跑 1 个多月, 后续 yaml 加 ~/Documents 实时索引
    // 不生效, 因为老 watcher 用 5/8 的 yaml.
    //
    // # 新逻辑
    // 每次 Companion 启动 pkill -f 'catfish_search.cli watch' 清孤儿, 再 spawn 新的.
    //
    // BL-SEARCH-NO-BOOTSTRAP (7/27 鸿波实盘): 这里原来写着"启动慢 5-15s
    // (初始 reconcile)" —— **那是假的**. watcher.py:run_watch 只做
    // observer.schedule() + 处理文件变化事件, 全文没有一处调 run_index,
    // 从来就没有 reconcile.
    //
    // 后果: local_search 从装上那天起没做过全量索引. 索引库里只有 watcher
    // 运行期间碰巧被改动过的文件 —— 鸿波 yaml 里配了 ~/Documents / ~/Desktop /
    // ~/Downloads / ~/.catfish/uploads, 四个各 0 条, 60332 条全来自
    // ~/person_task (活跃开发目录, git checkout / npm ci 天天动文件).
    //
    // 修法(7/27): 不在这里加 reconcile —— 每次开 Companion 都全量 stat 判重
    // 太慢且绝大多数情况白跑. 改成 watch 自己在**库为空**时 bootstrap 一次
    // (watcher.py:_bootstrap_if_empty). 员工加目录 / 手动重建走
    // commands::local_search::local_search_index.
    //
    // pkill 仅 macOS/Linux 有, Windows 暂不支持 (Companion 当前只 macOS 发布).
    pkill_local_search_watchers();

    let dir = match catfish_paths::local_search_dir() {
        Some(d) => d,
        None => {
            log::warn!("autostart: local-search dir not found, skipping");
            return;
        }
    };
    let python = match catfish_paths::local_search_python() {
        Some(p) => p,
        None => {
            log::warn!("autostart: local-search python not found");
            return;
        }
    };
    let log_path = match catfish_paths::local_search_log_path() {
        Some(p) => p,
        None => return,
    };
    let pid_file = match catfish_paths::local_search_pid_file() {
        Some(p) => p,
        None => return,
    };

    // 跟 commands/local_search.rs:local_search_start 同款 spawn: src/ → PYTHONPATH,
    // 避免员工跑 pip install -e .
    let pythonpath = dir.join("src").to_string_lossy().to_string();
    let cfg = process::SpawnConfig {
        program: python,
        args: vec![
            "-m".into(),
            "catfish_search.cli".into(),
            "watch".into(),
        ],
        log_path,
        working_dir: dir,
        env: vec![("PYTHONPATH".into(), pythonpath)],
    };

    match process::spawn_detached(cfg) {
        Ok(handle) => {
            if let Err(e) = std::fs::write(&pid_file, handle.pid.to_string()) {
                log::warn!("autostart: local-search started but failed to write PID: {e}");
            } else {
                log::info!("autostart: local-search started (PID {})", handle.pid);
            }
        }
        Err(e) => {
            log::warn!("autostart: local-search spawn failed: {e}");
        }
    }
}

// ============================================================
// chrome (5/22 鸿波: 默认 autostart, LLM 浏览器场景核心)
// ============================================================

pub async fn ensure_chrome_running() {
    if pid_alive(catfish_paths::chrome_pid_file().as_deref(), "remote-debugging-port") {
        log::info!("autostart: chrome already running");
        return;
    }

    let chrome_bin = match catfish_paths::find_chrome() {
        Some(p) => p,
        None => {
            log::warn!(
                "autostart: 找不到 Chrome / Chromium, 跳过 chrome autostart \
                 (LLM 浏览器操作场景不可用, 装 Google Chrome 后 Companion 重启自动起)"
            );
            return;
        }
    };
    let user_data_dir = match catfish_paths::chrome_user_data_dir() {
        Some(p) => p,
        None => return,
    };
    let log_path = match catfish_paths::chrome_log_path() {
        Some(p) => p,
        None => return,
    };
    let pid_file = match catfish_paths::chrome_pid_file() {
        Some(p) => p,
        None => return,
    };
    let working_dir = user_data_dir
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(std::env::temp_dir);

    let chrome_port = endpoints::endpoints().chrome_port;
    // 跟 commands/chrome.rs:chrome_launch 同款 spawn (隔离 user-data-dir + CDP 端口).
    // 5/22 鸿波: 默认 autostart 时窗口仍会显示, 员工可手动最小化. 不上 headless,
    // 因为 LLM 操作时员工要看屏幕确认 (5 内部初衷"催 不代行" — 透明可监督).
    let cfg = process::SpawnConfig {
        program: chrome_bin,
        args: vec![
            format!("--remote-debugging-port={chrome_port}"),
            format!("--user-data-dir={}", user_data_dir.display()),
            "--no-first-run".into(),
            "--no-default-browser-check".into(),
            "--disable-features=DialMediaRouteProvider".into(),
            // Chrome 138+ 默认 CDP WebSocket Origin 白名单, hermes browser tool 默认空 Origin
            "--remote-allow-origins=*".into(),
            "about:blank".into(),
        ],
        log_path,
        working_dir,
        env: vec![],
    };

    match process::spawn_detached(cfg) {
        Ok(handle) => {
            if let Err(e) = std::fs::write(&pid_file, handle.pid.to_string()) {
                log::warn!("autostart: chrome started but failed to write PID: {e}");
            } else {
                log::info!("autostart: chrome started (PID {})", handle.pid);
            }
        }
        Err(e) => {
            log::warn!("autostart: chrome spawn failed: {e}");
        }
    }
}

// ============================================================
// helpers
// ============================================================

/// P3.4.2 (6/15 鸿波): pkill 所有 catfish_search.cli watch 进程 (含孤儿).
///
/// 跟 pid_alive 配套使用: 既然不能 reload yaml / 不能热更代码, 干脆每次 Companion
/// 启动都把所有 watcher 都杀掉, 然后由 ensure_local_search_running spawn 新的.
///
/// 用 pkill -f 模糊匹配 cmdline. 'catfish_search.cli watch' 这串够特异, 不会
/// 误杀别的进程. 失败静默 (没 pkill 命令 / 没匹配进程都不算错).
///
/// macOS / Linux only. Windows 暂不处理 (Companion 当前只 macOS).
///
/// pub: commands/local_search.rs:local_search_start 也调 (UI 重启路径).
pub fn pkill_local_search_watchers() {
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        let out = std::process::Command::new("pkill")
            .args(["-f", "catfish_search.cli watch"])
            .output();
        match out {
            Ok(o) if o.status.success() => {
                log::info!(
                    "autostart: pkill 清掉旧 local-search watcher (确保新进程用最新 yaml + 代码)"
                );
                // pkill 完后 fsnotify subscribe 释放需要一小段, 给 0.5s 缓冲
                std::thread::sleep(std::time::Duration::from_millis(500));
            }
            Ok(_) => {
                // pkill 返非 0 通常是"无匹配进程" (exit 1), 这是正常情况
                log::debug!("autostart: pkill local-search 无匹配进程 (首次启动 / 已清干净)");
            }
            Err(e) => {
                log::warn!("autostart: pkill local-search 失败 (不阻塞 spawn): {e}");
            }
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        log::debug!("autostart: pkill_local_search_watchers 跳过 (非 unix)");
    }
}

/// P3.5.196 (7/7 鸿波军规审判): pkill 所有 catfish_tool_bridge --socket 进程 (含孤儿).
///
/// 跟 pid_alive 配套使用: 跟 pkill_local_search_watchers 同 pattern (P3.4.2).
/// 用途:
///   - autostart 冷启动前 pkill 上次残留进程, 保证新 spawn 加载最新 code
///   - 未来可用于 UI restart 按钮 (commands 里 wrap 一下即可)
///
/// 用 pkill -f 模糊匹配 cmdline. 'catfish_tool_bridge --socket' 这串够特异 (跟
/// hermes 里 mcp_server 子进程 'catfish_tool_bridge.mcp_server' 区分开), 不误杀.
/// 失败静默 (没 pkill 命令 / 无匹配都不算错).
///
/// macOS / Linux only. Windows 暂不处理 (Companion 当前只 macOS).
///
/// pub: 让 commands/ 也能调 (未来 UI restart 按钮).
pub fn pkill_tool_bridge() {
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        let out = std::process::Command::new("pkill")
            .args(["-f", "catfish_tool_bridge --socket"])
            .output();
        match out {
            Ok(o) if o.status.success() => {
                log::info!(
                    "autostart: pkill 清掉旧 tool-bridge (确保新进程用最新 catfish code + hermes 版本)"
                );
                // pkill 完后 unix socket 释放需要一小段, 给 0.5s 缓冲
                std::thread::sleep(std::time::Duration::from_millis(500));
            }
            Ok(_) => {
                // pkill 返非 0 通常是"无匹配进程" (exit 1), 首次启动或已清干净都正常
                log::debug!("autostart: pkill tool-bridge 无匹配进程 (首次启动 / 已清干净)");
            }
            Err(e) => {
                log::warn!("autostart: pkill tool-bridge 失败 (不阻塞 spawn): {e}");
            }
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        log::debug!("autostart: pkill_tool_bridge 跳过 (非 unix)");
    }
}

// ============================================================
// P3.4.7c (6/15 鸿波): 每周日 00:00 自动 reset current_todos.md
// ============================================================

/// 启动钩子: 判断 + 跑 weekly reset.
///
/// 算法:
///   - should_run_weekly_reset 读 ~/.catfish/.weekly_reset_last marker, 跟今天本
///     周一比较 — 跨过周一了就跑.
///   - 没跑 reset 的情况包括: marker 不存在 (首次启动) / marker 比本周一更早.
///   - current_todos_weekly_reset 内部: 改写 current_todos.md (删 - [x] 行) +
///     audit chain append (跟 P3.3.51 同模式) + 更新 marker.
///
/// 失败静默 (log.warn) — 不阻塞 Companion 其他启动流程.
async fn maybe_run_weekly_reset() {
    use crate::commands::journal;

    if !journal::should_run_weekly_reset() {
        log::debug!("autostart: 本周已 reset 过 current_todos.md, 跳过");
        return;
    }

    log::info!("autostart: 触发 current_todos.md 周末 reset (跨过周一 / 首次启动)");
    match journal::current_todos_weekly_reset().await {
        Ok(report) if report.skipped => {
            log::info!(
                "autostart: 周末 reset 跳过 ({}) — marker 仍写, 下周再判",
                report.skipped_reason
            );
        }
        Ok(report) => {
            log::info!(
                "autostart: 周末 reset 完成 — 删 {} 已完成, 留 {} 未完成带入新一周",
                report.completed_removed,
                report.pending_kept
            );
        }
        Err(e) => {
            log::warn!("autostart: 周末 reset 失败 (不阻塞): {e}");
        }
    }
}

fn pid_alive(pid_file: Option<&Path>, cmdline_substr: &str) -> bool {
    pid_file
        .and_then(|p| {
            if p.exists() {
                process::read_pid_file_alive_strict(p, cmdline_substr)
            } else {
                None
            }
        })
        .is_some()
}
