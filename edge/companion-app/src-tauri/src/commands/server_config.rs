//! P28 (6/5 鸿波) — server config read/write 让员工 Companion UI 改 gateway URL/token,
//! 不用 vim ~/.catfish/*.yaml.
//!
//! 写 2 个文件 (跟 P23-P26 一致):
//!   - ~/.catfish/companion.yaml         (endpoints.gateway_url)
//!   - ~/.catfish/memory_plugin.yaml     (gateway.url + gateway.token, chmod 600)
//!
//! 读: 优先 yaml, env, default. write 时尽量保留 yaml comment (用 serde_yaml round-trip 会丢
//! 注释 — 这里走 regex 替换关键 line 法, 保留头部说明).
//!
//! caller 改完得手动重启 Companion + hermes (yaml 进程只读一次, 不 hot-reload).

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerConfig {
    /// gateway base URL (e.g. http://127.0.0.1:8999 或 http://10.10.40.50:8999)
    pub gateway_url: String,
    /// catfish-memory plugin → gateway 内部 token (sensitive)
    pub gateway_token: String,
    /// 当前 token 是否从 env 拿 (yaml 空时回退到 env)
    pub token_source: String, // "yaml" | "env" | "none"
    /// P29 (6/5): identity-server URL (OIDC issuer, `catfish login` 走这).
    /// 读自 ~/.catfish/companion.yaml oidc.issuer 或 env CATFISH_OIDC_ISSUER.
    pub identity_url: String,
    // P3.4.1 (6/13 hb): secret_broker_url 字段砍 — 中央服务删,
    // OAuth token 改 Companion 本机存 (~/.catfish/mcp/oauth-tokens/).
    /// P3.5.80 (7/28 鸿波达华现场): catfish-web 中央门户 URL
    /// (~/.catfish/companion.yaml endpoints.web_url).
    ///
    /// 为什么补这个字段: Dashboard 顶部那排门户链接 (资源市场 / 部门 / 审计 /
    /// Admin / 系统管理) 走的是 endpoints.rs 的 web_url, 跟 gateway_url、
    /// oidc.issuer 是**三个独立配置**. 而本文件原来只读写前两个, web_url
    /// 谁也写不了 —— 于是永远落到 endpoints.rs 的兜底
    /// `http://127.0.0.1:5173`, 链接全部指向员工自己的机器, 点了打不开.
    ///
    /// 现场表现极具迷惑性: 员工在"服务器配置"里把 IP 改对了、gateway 和
    /// identity 都通了, 门户链接却还是 127.0.0.1 —— 界面看起来配好了,
    /// 实际有一项根本没地方配.
    ///
    /// ⚠ 空字符串 = **未配置**, 不是"默认值". 这里刻意不返回
    /// `http://127.0.0.1:5173`, 否则 UI 会显示得像已经配好了,
    /// 又是一个"看着绿实际没配"的坑. 空串让 UI 能如实告诉员工后果.
    pub web_url: String,
}

fn catfish_home() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| "找不到 HOME".to_string())?;
    Ok(PathBuf::from(home).join(".catfish"))
}

/// Naive YAML field reader — 抓 top-level "section:" 下的 "key: value" 字符串.
/// 不支持 nested deep / multiline. 只用于 gateway.url / gateway.token / endpoints.gateway_url.
fn read_yaml_field(text: &str, section: &str, key: &str) -> Option<String> {
    let mut in_section = false;
    for line in text.lines() {
        let trimmed = line.trim_end();
        if trimmed.starts_with(&format!("{section}:")) {
            in_section = true;
            continue;
        }
        // 下一个 top-level (非空格开头, 含冒号) 退出 section
        if in_section && !line.starts_with(char::is_whitespace) && trimmed.contains(':') && !trimmed.is_empty() {
            in_section = false;
        }
        if in_section {
            let stripped = trimmed.trim();
            if let Some(rest) = stripped.strip_prefix(&format!("{key}:")) {
                let val = rest.trim().trim_matches('"').trim_matches('\'');
                return Some(val.to_string());
            }
        }
    }
    None
}

/// 替换 yaml 真 "key: value" 行. 不存在的话直接追加到 section 末.
/// 保留所有 comment + 其它字段.
fn replace_or_insert_yaml_field(
    text: &str,
    section: &str,
    key: &str,
    new_value: &str,
) -> String {
    let mut lines: Vec<String> = text.lines().map(|s| s.to_string()).collect();
    let mut in_section = false;
    let mut section_start_idx: Option<usize> = None;
    let mut last_section_line_idx: Option<usize> = None;
    let mut replaced = false;

    for (i, line) in lines.iter_mut().enumerate() {
        let trimmed = line.trim_end();
        if trimmed.starts_with(&format!("{section}:")) {
            in_section = true;
            section_start_idx = Some(i);
            continue;
        }
        // 下一个 top-level 退出 section
        if in_section && !line.starts_with(char::is_whitespace) && trimmed.contains(':') && !trimmed.is_empty() {
            in_section = false;
        }
        if in_section {
            last_section_line_idx = Some(i);
            let stripped = trimmed.trim_start();
            if stripped.starts_with(&format!("{key}:")) {
                // 保留前缀空格 indent
                let indent: String = line.chars().take_while(|c| c.is_whitespace()).collect();
                // 含特殊字符或空 → 双引号
                let quoted = if new_value.is_empty() || new_value.contains([' ', ':', '#']) {
                    format!("\"{new_value}\"")
                } else {
                    new_value.to_string()
                };
                *line = format!("{indent}{key}: {quoted}");
                replaced = true;
                break;
            }
        }
    }

    if !replaced {
        let quoted = if new_value.is_empty() || new_value.contains([' ', ':', '#']) {
            format!("\"{new_value}\"")
        } else {
            new_value.to_string()
        };
        let new_line = format!("  {key}: {quoted}");
        if let Some(insert_after) = last_section_line_idx.or(section_start_idx) {
            lines.insert(insert_after + 1, new_line);
        } else {
            // 整 section 不存在 — 追加在末尾
            lines.push(format!("{section}:"));
            lines.push(new_line);
        }
    }

    lines.join("\n") + "\n"
}

#[tauri::command]
pub fn read_server_config() -> Result<ServerConfig, String> {
    let home = catfish_home()?;
    let companion = home.join("companion.yaml");
    let memplugin = home.join("memory_plugin.yaml");

    // 1. gateway URL: companion.yaml endpoints.gateway_url 优先
    let mut url = String::new();
    if let Ok(text) = fs::read_to_string(&companion) {
        if let Some(v) = read_yaml_field(&text, "endpoints", "gateway_url") {
            url = v;
        }
    }
    if url.is_empty() {
        if let Ok(text) = fs::read_to_string(&memplugin) {
            if let Some(v) = read_yaml_field(&text, "gateway", "url") {
                url = v;
            }
        }
    }
    if url.is_empty() {
        url = "http://127.0.0.1:8999".to_string();
    }

    // 2. token: env > memory_plugin.yaml
    let mut token = String::new();
    let mut source = "none";
    if let Ok(env_v) = std::env::var("CATFISH_INTERNAL_DEV_TOKEN") {
        if !env_v.is_empty() {
            token = env_v;
            source = "env";
        }
    }
    if token.is_empty() {
        if let Ok(text) = fs::read_to_string(&memplugin) {
            if let Some(v) = read_yaml_field(&text, "gateway", "token") {
                if !v.is_empty() {
                    token = v;
                    source = "yaml";
                }
            }
        }
    }

    // P29: identity-server URL (OIDC issuer)
    let mut identity_url = String::new();
    if let Ok(text) = fs::read_to_string(&companion) {
        if let Some(v) = read_yaml_field(&text, "oidc", "issuer") {
            identity_url = v;
        }
    }
    if identity_url.is_empty() {
        identity_url = std::env::var("CATFISH_OIDC_ISSUER").unwrap_or_default();
    }
    if identity_url.is_empty() {
        identity_url = "http://127.0.0.1:8998".to_string();
    }

    // P3.4.1 (6/13 hb): secret_broker_url 砍, 服务删了不需要展示给员工

    // P3.5.80 (7/28): catfish-web 中央门户 URL.
    // 取值顺序跟 endpoints.rs::build() 的前两级对齐 (yaml > env), 但**不补**
    // 它的第三级兜底 http://127.0.0.1:5173 —— 那个兜底是"没配时实际会发生
    // 什么", 不是"配置值". 这里返回空串, 让 UI 区分得出"配了 127.0.0.1"
    // 和"根本没配", 前者是员工的选择, 后者是漏配.
    //
    // 注: endpoints.rs 还支持 yaml 的 web_host + web_port 组合写法. 本命令
    // 不读那对字段 —— UI 只提供 web_url 单一入口, 免得两处写法打架.
    // 已经用 host/port 写法的老配置不受影响 (endpoints.rs 照旧认), 只是
    // 在这个界面上显示为"未配置"; 员工一旦从界面保存, 会写入 web_url,
    // 而 web_url 在 endpoints.rs 里优先级更高, 结果仍然正确.
    let mut web_url = String::new();
    if let Ok(text) = fs::read_to_string(&companion) {
        if let Some(v) = read_yaml_field(&text, "endpoints", "web_url") {
            web_url = v;
        }
    }
    if web_url.is_empty() {
        web_url = std::env::var("CATFISH_WEB_URL").unwrap_or_default();
    }

    Ok(ServerConfig {
        gateway_url: url,
        gateway_token: token,
        token_source: source.to_string(),
        identity_url,
        web_url,
    })
}

#[tauri::command(rename_all = "camelCase")]
pub fn write_server_config(
    gateway_url: String,
    gateway_token: String,
    identity_url: Option<String>,
    // P3.4.1 (6/13 hb): secret_broker_url 参数留, 但忽略 — 给前端旧 build 兼容,
    // 老 ServerConfigCard 调时仍传值, 服务端不再写入 yaml. 下次 UI clean
    // 时可彻底删.
    secret_broker_url: Option<String>,
    // P3.5.80 (7/28): catfish-web 中央门户 URL → endpoints.web_url.
    //
    // 用 Option 而不是 String, 是因为 ServerSetupCard (登录门那张卡) 调本命令
    // 时**不传这个参数** —— 它只管 gateway + identity, 员工登录前还不需要门户.
    // 若声明成 String, 它那次调用会把已配好的 web_url 擦成空.
    // None / Some("") 一律"保持原值不动", 跟 identity_url 同语义.
    web_url: Option<String>,
) -> Result<(), String> {
    let _ = secret_broker_url; // 显式忽略
    let home = catfish_home()?;
    fs::create_dir_all(&home).map_err(|e| format!("建 {home:?} 失败: {e}"))?;
    let companion = home.join("companion.yaml");
    let memplugin = home.join("memory_plugin.yaml");

    // 校验
    let trimmed_url = gateway_url.trim();
    if !trimmed_url.starts_with("http://") && !trimmed_url.starts_with("https://") {
        return Err(format!(
            "gateway_url 必须 http:// 或 https:// 开头: {trimmed_url}"
        ));
    }
    let url_clean = trimmed_url.trim_end_matches('/');

    // 1. companion.yaml: endpoints.gateway_url + (可选) oidc.issuer + endpoints.secret_broker_url
    let mut companion_text = fs::read_to_string(&companion).unwrap_or_else(|_| {
        "endpoints:\n  gateway_url: http://127.0.0.1:8999\n".to_string()
    });
    companion_text = replace_or_insert_yaml_field(
        &companion_text,
        "endpoints",
        "gateway_url",
        url_clean,
    );

    // P29: identity_url → oidc.issuer (oauth.rs 读这)
    if let Some(id_url) = identity_url.as_ref() {
        let trimmed = id_url.trim().trim_end_matches('/');
        if !trimmed.is_empty() {
            if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
                return Err(format!("identity_url 必须 http:// 或 https:// 开头: {trimmed}"));
            }
            companion_text = replace_or_insert_yaml_field(
                &companion_text,
                "oidc",
                "issuer",
                trimmed,
            );
        }
    }

    // P3.4.1 (6/13 hb): secret_broker_url 不再写 yaml, secret-broker 服务删了

    // P3.5.80 (7/28): web_url → endpoints.web_url (endpoints.rs 读这个).
    // 跟上面 identity_url 同样的"空则不动"语义, 原因见参数处注释.
    if let Some(w_url) = web_url.as_ref() {
        let trimmed = w_url.trim().trim_end_matches('/');
        if !trimmed.is_empty() {
            if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
                return Err(format!("web_url 必须 http:// 或 https:// 开头: {trimmed}"));
            }
            companion_text = replace_or_insert_yaml_field(
                &companion_text,
                "endpoints",
                "web_url",
                trimmed,
            );
        }
    }

    fs::write(&companion, companion_text)
        .map_err(|e| format!("写 {companion:?} 失败: {e}"))?;

    // 2. memory_plugin.yaml: gateway.url + gateway.token (chmod 600)
    let memplugin_text = fs::read_to_string(&memplugin).unwrap_or_else(|_| {
        "gateway:\n  url: http://127.0.0.1:8999\n  token: \"\"\n".to_string()
    });
    let with_url = replace_or_insert_yaml_field(
        &memplugin_text,
        "gateway",
        "url",
        url_clean,
    );
    let with_token = replace_or_insert_yaml_field(
        &with_url,
        "gateway",
        "token",
        gateway_token.trim(),
    );
    fs::write(&memplugin, with_token)
        .map_err(|e| format!("写 {memplugin:?} 失败: {e}"))?;

    // chmod 600 防 token 泄露 (macOS/Linux only)
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = fs::Permissions::from_mode(0o600);
        let _ = fs::set_permissions(&memplugin, perms);
    }

    // 3. ~/.hermes/.env: CATFISH_GATEWAY_URL
    //
    // BL-HERMES-ENV-SYNC (7/18 鸿波 catch): catfish-xcatfish-user plugin (跑在 hermes 内)
    // 从 env `CATFISH_GATEWAY_URL` 读 gateway URL (plugin.py:78, memory_enforce.py:104).
    // 面板之前只写 companion.yaml + memory_plugin.yaml (comment 里说 "plugin 读
    // memory_plugin.yaml" 是 stale — plugin 早已 refactor 用 env), 员工改面板改 IP
    // 后 hermes plugin 仍用**老 env** 或 **default 127.0.0.1:8999**, memory/role 相关
    // 调错 gateway.
    //
    // 加写 hermes .env 保 line-level replace, 不影响别的 env vars (API_SERVER_KEY /
    // OPENAI_API_KEY 等). hermes 重启后 load_hermes_dotenv 读新值.
    let hermes_env_path = home
        .parent()
        .ok_or_else(|| "home 目录无 parent (不该发生)".to_string())?
        .join(".hermes")
        .join(".env");
    if hermes_env_path.exists() {
        // 已有 .env: line-level replace 或追加
        let old = fs::read_to_string(&hermes_env_path)
            .map_err(|e| format!("读 {hermes_env_path:?} 失败: {e}"))?;
        let new_text = replace_or_append_env_line(&old, "CATFISH_GATEWAY_URL", url_clean);
        fs::write(&hermes_env_path, new_text)
            .map_err(|e| format!("写 {hermes_env_path:?} 失败: {e}"))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&hermes_env_path, fs::Permissions::from_mode(0o600));
        }
    } else {
        // ~/.hermes/.env 不存在 (hermes 未装或非标 layout) — silent skip.
        // 员工装 dmg 会走 hermes install, .env 自动生成; 此时 write_server_config
        // 若在 install 前跑, 跳过合理. hermes install 后员工再点"保存"就会写入.
        log::debug!(
            "[server_config] {hermes_env_path:?} 不存在, 跳过 CATFISH_GATEWAY_URL 写入"
        );
    }

    // ── P3.5.81 (7/29 达华交付前夜): 把 URL 同步进 hermes 的 3 个文件 ──────
    //
    // 上面那段只写了 `.env` 的 `CATFISH_GATEWAY_URL`(catfish plugin 读的),
    // 而 hermes 真正发 LLM 请求用的三处地址一处都没写:
    //   `.env` 的 OPENAI_BASE_URL · config.yaml 的 model.base_url ·
    //   auth.json 的 credential_pool[].base_url
    //
    // 下面紧接着的 hermes_jwt_sync::sync_all 会把**凭证**同步进这同样 3 个
    // 文件 —— 也就是说这段代码一直知道"服务器变了要动这 3 个文件", 只是
    // 每次都只改了凭证、没改地址. 结果是 hermes 拿着**新 token** 调**旧地址**.
    //
    // 7/28 达华联调实证: 表象是聊天框一直转圈, agent.log 里 Connection error
    // 重试三轮, 而 gateway 侧完全没有请求记录 —— 请求根本没发到新服务器.
    // 排查耗掉整晚, 因为 `.env` 里 `CATFISH_GATEWAY_URL` 显示的是新 IP,
    // grep 一下有命中就以为地址已经改对了.
    if let Err(e) = crate::services::hermes_jwt_sync::sync_base_url(url_clean) {
        log::warn!("[server_config] hermes URL sync 挂 (不阻塞面板保存): {e:#}");
    }

    // ── P3.5.81 (7/29): identity 地址也要立刻落到 hermes .env ────────────
    //
    // hermes 侧的 token 自动续期 (`hermes_token_renewal`) 从 env 读
    // `CATFISH_IDENTITY_URL` 决定去哪换新 token. 正常路径是 Companion 启动时
    // 由 `sync_service_token_to_env` 写入, 但**员工在面板改完服务器到重启
    // Companion 之间有一段窗口** —— 这期间续期若触发, 会拿着**旧 identity**
    // 签一个新 token 覆盖掉好的那个, 新服务器验签直接 401.
    //
    // 这个窗口以前不致命是因为续期根本签不出来(secret 没写入, 见
    // hermes_jwt_sync::HERMES_CLI_SECRET 处注释). 现在 secret 会写进去了,
    // 续期真的能签 —— 所以地址必须在**同一次保存里**一起更新, 不能等重启.
    if let Some(id_url) = identity_url.as_ref() {
        let trimmed = id_url.trim().trim_end_matches('/');
        if !trimmed.is_empty() && hermes_env_path.exists() {
            match fs::read_to_string(&hermes_env_path) {
                Ok(old) => {
                    let new_text =
                        replace_or_append_env_line(&old, "CATFISH_IDENTITY_URL", trimmed);
                    if let Err(e) = fs::write(&hermes_env_path, new_text) {
                        log::warn!("[server_config] 写 CATFISH_IDENTITY_URL 挂 (不阻塞): {e}");
                    } else {
                        log::info!("[server_config] ✓ .env CATFISH_IDENTITY_URL → {trimmed}");
                    }
                }
                Err(e) => log::warn!("[server_config] 读 .env 挂 (不阻塞): {e}"),
            }
        }
    }

    // ── P3.5.81 (7/29): 让新地址当场生效 ──────────────────────────────
    //
    // 上面写的都是**文件**. 而 `services::endpoints` 在进程内缓存了一份地址,
    // 原实现是 OnceLock —— 读一次就冻结, 于是文件改对了、跑着的进程仍用旧值,
    // 界面只能挂一句"改完要退出重新打开才生效".
    //
    // 那句提示在现场很容易被漏读: 员工改完发现没变化, 第一反应是"没保存成功",
    // 于是反复改反复存 —— 而每次都真的写盘了, 状态越查越乱.
    //
    // 现在保存后立刻重载, Companion 侧当场生效. (hermes 跑在另一个进程里,
    // 仍要重启才会读到新的 .env —— 那是进程边界, 不是这里能解决的.)
    let ep = crate::services::endpoints::reload();
    log::info!(
        "[server_config] 地址已当场生效 · gateway={} web={}",
        ep.gateway_base(),
        ep.web_url
    );

    // ── P3.5.81: 作废模型选择 ─────────────────────────────────────────
    //
    // 模型名绑定的是**某台服务器的目录**, 换服务器后旧模型名在新服务器上
    // 必然 404. 清掉后由新服务器的 catalog.default 决定, 见 clear_model_selection.
    crate::services::picker_config::clear_model_selection();

    // BL-HERMES-JWT-SYNC (7/19 Task #15 鸿波): 面板改 IP 时 · 顺手 sync JWT 3 处
    // (~/.hermes/.env OPENAI_API_KEY + config.yaml model.api_key + auth.json reset).
    // hermes daemon 用老 JWT 调新 IP gateway → 401 → WeChat 显英文. 军规大坑.
    // 7/19 前只写 CATFISH_GATEWAY_URL (1 处) · JWT 3 处漏 · 员工 1 天后 100% 撞英文.
    if let Some(jwt) = crate::services::oauth::current_access_token() {
        if let Err(e) = crate::services::hermes_jwt_sync::sync_all(&jwt) {
            log::warn!(
                "[server_config] hermes_jwt_sync 挂 (不阻塞面板保存): {e:#}"
            );
        }
    } else {
        log::debug!(
            "[server_config] current_access_token 空 (未 SSO 登录) · 跳过 hermes JWT sync"
        );
    }

    Ok(())
}

/// 保 line-level replace / 追加一个 KEY=VALUE 到 dotenv 文本. 保留 comments + 别的 vars.
///
/// - 若原文有 `^KEY=...` 行, 整行替换 (KEY=new_value)
/// - 若原文无, 追加到末尾 (前面确保有换行)
///
/// 不 escape value (VALUE 是 URL, 内部无引号 / 换行, safe).
fn replace_or_append_env_line(text: &str, key: &str, value: &str) -> String {
    let mut lines: Vec<String> = text.lines().map(|s| s.to_string()).collect();
    let mut replaced = false;
    let prefix = format!("{key}=");
    for line in lines.iter_mut() {
        if line.starts_with(&prefix) {
            *line = format!("{key}={value}");
            replaced = true;
            break;
        }
    }
    if !replaced {
        // 追加 · 保底 · trailing newline 存在
        if !text.is_empty() && !text.ends_with('\n') {
            lines.push(String::new());
        }
        lines.push(format!("{key}={value}"));
    }
    let mut out = lines.join("\n");
    if !out.ends_with('\n') {
        out.push('\n');
    }
    out
}

/// P3.5.80 (7/28): web_url 读写的回归测试.
///
/// 在防什么: `endpoints.web_url` 决定 Dashboard 那排中央门户链接跳哪去,
/// 但它跟 gateway_url / oidc.issuer 是三个独立配置, 而本文件原来只覆盖前两个,
/// 于是门户链接永远指向 endpoints.rs 的兜底 http://127.0.0.1:5173.
/// 现场表现是"界面上 IP 都改对了、服务也都通了, 链接却还是 127.0.0.1".
#[cfg(test)]
mod tests_web_url_yaml {
    use super::{read_yaml_field, replace_or_insert_yaml_field};

    /// 典型现场配置: endpoints 段已有 gateway_url, 后面跟着 oidc 段.
    fn sample() -> String {
        "endpoints:\n  gateway_url: http://192.168.31.199:8999\noidc:\n  issuer: http://192.168.31.199:8998\n".to_string()
    }

    #[test]
    fn insert_web_url_into_existing_endpoints_section() {
        let out = replace_or_insert_yaml_field(
            &sample(),
            "endpoints",
            "web_url",
            "https://192.168.31.199",
        );
        // 必须插在 endpoints 段内, 不能掉进 oidc 段
        let ep_idx = out.find("endpoints:").expect("endpoints 段没了");
        let web_idx = out.find("web_url:").expect("web_url 没写进去");
        let oidc_idx = out.find("oidc:").expect("oidc 段被破坏");
        assert!(ep_idx < web_idx && web_idx < oidc_idx,
            "web_url 落在了错误的段里:\n{out}");
        // 既有字段不能动
        assert!(out.contains("gateway_url: http://192.168.31.199:8999"));
        assert!(out.contains("issuer: http://192.168.31.199:8998"));
    }

    #[test]
    fn write_then_read_roundtrip() {
        // URL 含 ':' → 写入时会加双引号, 读回来必须把引号去掉,
        // 否则 endpoints.rs 拿到的是带引号的字符串 (serde_yaml 其实能处理,
        // 但这个界面自己 read 回显时会露出引号, 员工以为配错了).
        let out = replace_or_insert_yaml_field(
            &sample(),
            "endpoints",
            "web_url",
            "https://192.168.31.199",
        );
        let back = read_yaml_field(&out, "endpoints", "web_url");
        assert_eq!(back.as_deref(), Some("https://192.168.31.199"));
    }

    #[test]
    fn overwrite_existing_web_url() {
        let first = replace_or_insert_yaml_field(
            &sample(), "endpoints", "web_url", "https://old.example.com");
        let second = replace_or_insert_yaml_field(
            &first, "endpoints", "web_url", "https://192.168.31.199");
        assert_eq!(
            read_yaml_field(&second, "endpoints", "web_url").as_deref(),
            Some("https://192.168.31.199")
        );
        assert!(!second.contains("old.example.com"), "旧值没被替换掉:\n{second}");
        // 不能写成两行
        assert_eq!(second.matches("web_url:").count(), 1, "web_url 重复了:\n{second}");
    }

    #[test]
    fn no_endpoints_section_creates_one() {
        let input = "oidc:\n  issuer: http://192.168.31.199:8998\n";
        let out = replace_or_insert_yaml_field(
            &input, "endpoints", "web_url", "https://192.168.31.199");
        assert_eq!(
            read_yaml_field(&out, "endpoints", "web_url").as_deref(),
            Some("https://192.168.31.199")
        );
        assert!(out.contains("issuer: http://192.168.31.199:8998"), "oidc 段被弄丢:\n{out}");
    }

    #[test]
    fn read_absent_web_url_returns_none() {
        // 未配置必须是 None → read_server_config 返空串 → UI 显示"未配置".
        // 若这里误返 Some("") 之类, UI 会显示成已配置, 又是一个假绿灯.
        assert_eq!(read_yaml_field(&sample(), "endpoints", "web_url"), None);
    }

    #[test]
    fn gateway_url_untouched_when_writing_web_url() {
        // ServerSetupCard 只传 gateway+identity 时不会走到 web_url 分支;
        // 反过来这里确认写 web_url 不会动 gateway_url.
        let out = replace_or_insert_yaml_field(
            &sample(), "endpoints", "web_url", "https://192.168.31.199");
        assert_eq!(
            read_yaml_field(&out, "endpoints", "gateway_url").as_deref(),
            Some("http://192.168.31.199:8999")
        );
    }
}

#[cfg(test)]
mod tests_env_line {
    use super::replace_or_append_env_line;

    #[test]
    fn replace_existing_key() {
        let input = "OTHER=1\nCATFISH_GATEWAY_URL=http://old\nMORE=2\n";
        let out = replace_or_append_env_line(input, "CATFISH_GATEWAY_URL", "http://new");
        assert!(out.contains("CATFISH_GATEWAY_URL=http://new"));
        assert!(!out.contains("http://old"));
        assert!(out.contains("OTHER=1"));
        assert!(out.contains("MORE=2"));
    }

    #[test]
    fn append_when_absent() {
        let input = "OTHER=1\n";
        let out = replace_or_append_env_line(input, "CATFISH_GATEWAY_URL", "http://new");
        assert!(out.contains("OTHER=1"));
        assert!(out.ends_with("CATFISH_GATEWAY_URL=http://new\n"));
    }

    #[test]
    fn append_when_empty() {
        let out = replace_or_append_env_line("", "CATFISH_GATEWAY_URL", "http://new");
        assert_eq!(out, "CATFISH_GATEWAY_URL=http://new\n");
    }
}
