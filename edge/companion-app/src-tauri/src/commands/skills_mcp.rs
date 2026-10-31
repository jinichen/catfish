//! MCP servers —— 读和写 `~/.hermes/config.yaml` 的 `mcp_servers:` 段.
//!
//! 8/14 从 commands/skills.rs 拆出来。
//!
//! 为什么读和写放一起、而不是"读跟列举走、写单独一个文件": 这几个函数碰的是
//! **同一个文件的同一段** (`~/.hermes/config.yaml` 里的 `mcp_servers`)。
//! 真正的边界在那儿, 不在"读 vs 写"。
//!
//! config.yaml 里的形状 (两种):
//!   本机程序:  <name>: { command, args?, env? }
//!   远程网址:  <name>: { url, headers?, auth: oauth? }     (10/31 起界面能接; oauth = 浏览器登录授权)
//! env / headers 里的密钥只写 `${CATFISH_MCP_...}` 引用, 真值在 Hermes 的 .env (skills_mcp_secrets)。

use serde::{Deserialize, Serialize};

use super::skills_list::home_dir;
use super::skills_mcp_secrets::{self as secrets, Secret};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct McpServerEntry {
    pub name: String,
    pub command: String,
    pub args: Vec<String>,
    /// 远程 MCP 的网址 (本机程序为 None)
    pub url: Option<String>,
    /// 要网页登录授权 (auth: oauth) —— 列表上给「重新登录」按钮
    pub oauth: bool,
    /// E7.P1 (6/6): catfish-tools 或 catfish-* 前缀 = 核心 MCP (主链路依赖), 不能删.
    /// 员工自加的 (e.g. slack-mcp) is_protected=false.
    pub is_protected: bool,
}

/// 员工在「接入 MCP」里填的 (键值对: 环境变量 / 请求头)
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct McpAddRequest {
    pub name: String,
    #[serde(default)]
    pub command: Option<String>,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub env: Vec<(String, String)>,
    #[serde(default)]
    pub headers: Vec<(String, String)>,
    /// 远程 MCP 要网页登录授权 (OAuth); 令牌由 Hermes 存在 <hermes>/mcp-tokens/
    #[serde(default)]
    pub oauth: bool,
}

/// 卸载时拿走的东西 (配置原样 + 密钥), 「撤销」原样放回
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RemovedMcp {
    pub name: String,
    pub entry_yaml: String,
    pub secrets: Vec<Secret>,
}

fn is_core(name: &str) -> bool {
    name.starts_with("catfish-") || name == "catfish-tools"
}

fn hermes_home() -> Result<std::path::PathBuf, String> {
    let home = home_dir().ok_or("找不到 HOME")?;
    Ok(crate::services::catfish_paths::hermes_home_for(&home))
}

fn load_config() -> Result<(std::path::PathBuf, serde_yaml::Value), String> {
    let cfg_path = hermes_home()?.join("config.yaml");
    let text = std::fs::read_to_string(&cfg_path).map_err(|e| format!("读 ~/.hermes/config.yaml 失败: {e}"))?;
    let value = serde_yaml::from_str(&text).map_err(|e| format!("解析 config.yaml 失败: {e}"))?;
    Ok((cfg_path, value))
}

fn save_config(cfg_path: &std::path::Path, value: &serde_yaml::Value) -> Result<(), String> {
    let new_text = serde_yaml::to_string(value).map_err(|e| format!("序列化失败: {e}"))?;
    std::fs::write(cfg_path, new_text).map_err(|e| format!("写 config.yaml 失败: {e}"))
}

fn mcp_map_mut(value: &mut serde_yaml::Value) -> Result<&mut serde_yaml::Mapping, String> {
    let mapping = value.as_mapping_mut().ok_or("config.yaml 顶层不是 mapping")?;
    let key = serde_yaml::Value::String("mcp_servers".into());
    if !mapping.contains_key(&key) {
        mapping.insert(key.clone(), serde_yaml::Value::Mapping(serde_yaml::Mapping::new()));
    }
    mapping.get_mut(&key).and_then(|v| v.as_mapping_mut()).ok_or_else(|| "mcp_servers 段不是 mapping".into())
}

fn s(v: &str) -> serde_yaml::Value {
    serde_yaml::Value::String(v.to_string())
}

pub(crate) fn list_mcp_servers_blocking() -> Result<Vec<McpServerEntry>, String> {
    let Ok((_, value)) = load_config() else {
        return Ok(vec![]);
    };
    let Some(mcp_map) = value.get("mcp_servers").and_then(|v| v.as_mapping()) else {
        return Ok(vec![]);
    };
    let mut result = Vec::new();
    for (k, v) in mcp_map {
        let Some(name) = k.as_str().map(String::from) else { continue };
        let url = v.get("url").and_then(|x| x.as_str()).map(String::from);
        let command = v
            .get("command")
            .and_then(|x| x.as_str())
            .map(String::from)
            .unwrap_or_else(|| if url.is_some() { "(远程)".into() } else { "(无 command)".into() });
        let args = v
            .get("args")
            .and_then(|x| x.as_sequence())
            .map(|seq| seq.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let is_protected = is_core(&name);
        let oauth = v.get("auth").and_then(|x| x.as_str()) == Some("oauth");
        result.push(McpServerEntry { name, command, args, url, oauth, is_protected });
    }
    result.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(result)
}

/// 写一条 MCP。`path_env` = npx 类用本机 Node 时要加的 PATH (skills_mcp_launch)。
/// 键值对里的值一律当密钥: 写进 .env, 配置里只留 `${…}` 引用。
pub(crate) fn add_mcp_server_blocking(req: &McpAddRequest, path_env: Option<String>) -> Result<(), String> {
    let name = req.name.trim();
    if name.is_empty() {
        return Err("名字不能空".into());
    }
    if is_core(name) {
        return Err(format!("拒绝: catfish-* 是核心 MCP 保留命名, 不允许员工自加 (改用别的名字): {name}"));
    }
    let url = req.url.as_deref().map(str::trim).filter(|u| !u.is_empty());
    let command = req.command.as_deref().map(str::trim).filter(|c| !c.is_empty());
    match (url, command) {
        (Some(u), _) if !(u.starts_with("https://") || u.starts_with("http://")) => {
            return Err("网址要以 https:// 或 http:// 开头".into())
        }
        (None, None) => return Err("填启动命令或网址".into()),
        _ => {}
    }
    for (k, _) in req.env.iter().chain(req.headers.iter()) {
        if k.trim().is_empty() {
            return Err("变量名 / 请求头名不能空".into());
        }
    }
    let (cfg_path, mut value) = load_config()?;
    let mcp_map = mcp_map_mut(&mut value)?;
    if mcp_map.contains_key(s(name)) {
        return Err(format!("MCP {name} 已存在, 先移除再加"));
    }
    let mut secret_list = vec![];
    let mut refs = |pairs: &[(String, String)]| -> serde_yaml::Mapping {
        let mut m = serde_yaml::Mapping::new();
        for (k, v) in pairs {
            let var = secrets::var_name(name, k.trim());
            secret_list.push(Secret { var: var.clone(), value: v.clone() });
            m.insert(s(k.trim()), s(&format!("${{{var}}}")));
        }
        m
    };
    let mut entry = serde_yaml::Mapping::new();
    if let Some(u) = url {
        entry.insert(s("url"), s(u));
        if req.oauth {
            entry.insert(s("auth"), s("oauth"));
        }
        let headers = refs(&req.headers);
        if !headers.is_empty() {
            entry.insert(s("headers"), serde_yaml::Value::Mapping(headers));
        }
    } else if let Some(c) = command {
        entry.insert(s("command"), s(c));
        if !req.args.is_empty() {
            entry.insert(s("args"), serde_yaml::Value::Sequence(req.args.iter().map(|a| s(a)).collect()));
        }
        let mut env = refs(&req.env);
        // 10/31: npx 类 MCP 用本机 Node 的绝对路径时, 它的目录要在这个 MCP 的 PATH 上
        if let Some(path) = path_env {
            env.insert(s("PATH"), s(&path));
        }
        if !env.is_empty() {
            entry.insert(s("env"), serde_yaml::Value::Mapping(env));
        }
    }
    secrets::save(&hermes_home()?, &secret_list)?;
    mcp_map.insert(s(name), serde_yaml::Value::Mapping(entry));
    save_config(&cfg_path, &value)
}

pub(crate) fn remove_mcp_server_blocking(name: String) -> Result<RemovedMcp, String> {
    if is_core(&name) {
        return Err(format!("拒绝: catfish-* 是核心 MCP, 不能删 (主链路依赖): {name}"));
    }
    let (cfg_path, mut value) = load_config()?;
    let mcp_map = mcp_map_mut(&mut value)?;
    let entry = mcp_map.remove(s(&name)).ok_or_else(|| format!("MCP {name} 不存在"))?;
    let entry_yaml = serde_yaml::to_string(&entry).map_err(|e| e.to_string())?;
    save_config(&cfg_path, &value)?;
    let hermes = hermes_home()?;
    let removed_secrets = secrets::remove_for(&hermes, &name)?;
    // OAuth 令牌不留 (撤销后要用就点「重新登录」)
    super::skills_mcp_launch::remove_oauth_tokens(&hermes, &name);
    Ok(RemovedMcp { name, entry_yaml, secrets: removed_secrets })
}

/// 撤销卸载: 配置和密钥原样放回
pub(crate) fn restore_mcp_server_blocking(removed: RemovedMcp) -> Result<(), String> {
    let entry: serde_yaml::Value = serde_yaml::from_str(&removed.entry_yaml).map_err(|e| e.to_string())?;
    let (cfg_path, mut value) = load_config()?;
    let mcp_map = mcp_map_mut(&mut value)?;
    if mcp_map.contains_key(s(&removed.name)) {
        return Err(format!("MCP {} 已经又加上了", removed.name));
    }
    secrets::save(&hermes_home()?, &removed.secrets)?;
    mcp_map.insert(s(&removed.name), entry);
    save_config(&cfg_path, &value)
}
