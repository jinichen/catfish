//! hermes config.yaml 的自愈 —— 把 catfish-tools MCP 和代理白名单补回去。
//!
//! 2026-08-15 从 autostart.rs 切出来。纯搬迁, 逻辑一行未改。
//! `mcp_autofix_tests` 那 172 行测试跟着 patch_hermes_config 一起过来了。
//!
//! 它修的是 7/27 实盘挖出来的两个坑:
//!   · catfish-tools 掉了 → 全部 catfish_* tool 对 LLM 不可见, 而且不报错
//!   · fake-IP 代理触发 hermes 的 SSRF 误拦 → 浏览器类工具全挂

use crate::services::catfish_paths;

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
    // find_agent_browser 只有测试在用 —— 放文件顶上的话, 非测试编译时
    // mcp_autofix_tests 被 cfg 掉, 它就成了一条 unused import 警告。
    // ⚠ 这里得写 crate::services::, 不能写 super:: —— mod 内部的 super
    // 指的是 autostart_mcp 本身, 差一层。
    use crate::services::autostart_deps::find_agent_browser;

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
