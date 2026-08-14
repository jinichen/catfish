//! MCP servers —— 读和写 `~/.hermes/config.yaml` 的 `mcp_servers:` 段.
//!
//! 8/14 从 commands/skills.rs 拆出来。
//!
//! 为什么读和写放一起、而不是"读跟列举走、写单独一个文件": 三个函数碰的是
//! **同一个文件的同一段** (`~/.hermes/config.yaml` 里的 `mcp_servers`)。
//! 真正的边界在那儿, 不在"读 vs 写"。
//!
//! config.yaml 里的形状:
//!   mcp_servers:
//!     <name>:
//!       command: <path>        stdio 启动命令
//!       args: [...]            可选
//!       env: {...}             可选

use serde::Serialize;

use super::skills_list::home_dir;

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

pub(crate) fn list_mcp_servers_blocking() -> Result<Vec<McpServerEntry>, String> {
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

pub(crate) fn add_mcp_server_blocking(
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

pub(crate) fn remove_mcp_server_blocking(name: String) -> Result<(), String> {
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
