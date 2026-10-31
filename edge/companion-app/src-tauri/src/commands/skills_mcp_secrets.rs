//! 接入 MCP 时填的密钥 (环境变量 / 远程请求头) 放哪儿 (10/31)。
//!
//! Hermes 只从自己的环境和 `<hermes>/.env` 取密钥 (agent/secret_scope.py), 读不了系统钥匙串,
//! 所以密钥写进 `<hermes>/.env` (仅本人可读, 跟模型 API Key 同一个地方), `config.yaml` 里只留
//! `${CATFISH_MCP_<名字>_<变量>}` 引用, 配置文件里没有明文。Hermes 连接前会把引用换成真值
//! (tools/mcp_tool_config.py `_interpolate_env_vars`, 试连的 `hermes mcp test` 也一样)。
//!
//! 卸载 MCP 时连同它的密钥一起删; 「撤销」把配置和密钥原样放回。

use std::path::Path;

use serde::{Deserialize, Serialize};

/// 一行 `.env`: 变量名 + 原值 (没加引号)
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Secret {
    pub var: String,
    pub value: String,
}

fn sanitize(s: &str) -> String {
    s.chars().map(|c| if c.is_ascii_alphanumeric() { c.to_ascii_uppercase() } else { '_' }).collect()
}

/// 名字里连续的 `_` 合成一个, 再用 `__` 跟变量名隔开 —— 这样 `a` 的前缀 `CATFISH_MCP_A__`
/// 不会碰到 `a_b` 的 `CATFISH_MCP_A_B__…` (卸载 a 时不会误删 a_b 的密钥)
fn prefix(server: &str) -> String {
    let mut name = String::new();
    for c in sanitize(server).chars() {
        if !(c == '_' && name.ends_with('_')) {
            name.push(c);
        }
    }
    format!("CATFISH_MCP_{}__", name.trim_matches('_'))
}

/// 某个 MCP 的某个变量 / 请求头在 `.env` 里叫什么
pub(crate) fn var_name(server: &str, key: &str) -> String {
    format!("{}{}", prefix(server), sanitize(key))
}

/// python-dotenv 的写法: 单引号原样 (值里没单引号时), 否则双引号 + 转义
fn quote(value: &str) -> String {
    if !value.contains('\'') {
        format!("'{value}'")
    } else {
        format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
    }
}

fn unquote(raw: &str) -> String {
    let r = raw.trim();
    if r.len() >= 2 && r.starts_with('\'') && r.ends_with('\'') {
        return r[1..r.len() - 1].to_string();
    }
    if r.len() >= 2 && r.starts_with('"') && r.ends_with('"') {
        return r[1..r.len() - 1].replace("\\\"", "\"").replace("\\\\", "\\");
    }
    r.to_string()
}

fn env_path(hermes_home: &Path) -> std::path::PathBuf {
    hermes_home.join(".env")
}

fn write_env(path: &Path, text: &str) -> Result<(), String> {
    std::fs::write(path, text).map_err(|e| format!("写 Hermes 的 .env 失败: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

/// 写入 (同名覆盖, 其他行原样保留)
pub(crate) fn save(hermes_home: &Path, secrets: &[Secret]) -> Result<(), String> {
    if secrets.is_empty() {
        return Ok(());
    }
    for s in secrets {
        if s.value.contains('\n') || s.value.contains('\r') {
            return Err(format!("{} 的值里不能有换行", s.var));
        }
    }
    let path = env_path(hermes_home);
    let mut text = std::fs::read_to_string(&path).unwrap_or_default();
    for s in secrets {
        text = crate::services::hermes_jwt_sync::replace_or_append_env_line(&text, &s.var, &quote(&s.value));
    }
    write_env(&path, &text)
}

/// 删掉某个 MCP 的全部密钥, 返回删掉的 (撤销时原样放回)
pub(crate) fn remove_for(hermes_home: &Path, server: &str) -> Result<Vec<Secret>, String> {
    let path = env_path(hermes_home);
    let Ok(text) = std::fs::read_to_string(&path) else { return Ok(vec![]) };
    let p = prefix(server);
    let mut removed = vec![];
    let kept: Vec<&str> = text
        .lines()
        .filter(|l| {
            if let Some((k, v)) = l.split_once('=') {
                if k.trim().starts_with(&p) {
                    removed.push(Secret { var: k.trim().to_string(), value: unquote(v) });
                    return false;
                }
            }
            true
        })
        .collect();
    if !removed.is_empty() {
        let mut out = kept.join("\n");
        out.push('\n');
        write_env(&path, &out)?;
    }
    Ok(removed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 写进_env_引号正确_删除只删自己的() {
        let t = tempfile::tempdir().unwrap();
        std::fs::write(t.path().join(".env"), "OPENAI_API_KEY=sk-x\n").unwrap();
        let a = Secret { var: var_name("amap", "AMAP_KEY"), value: "abc#1 2".into() };
        let b = Secret { var: var_name("amap", "Authorization"), value: "Bearer it's".into() };
        save(t.path(), &[a.clone(), b.clone()]).unwrap();
        let text = std::fs::read_to_string(t.path().join(".env")).unwrap();
        assert!(text.contains("OPENAI_API_KEY=sk-x"));
        assert!(text.contains("CATFISH_MCP_AMAP__AMAP_KEY='abc#1 2'"), "{text}");
        assert!(text.contains("CATFISH_MCP_AMAP__AUTHORIZATION=\"Bearer it's\""), "{text}");
        // 别的 MCP 的不动
        save(t.path(), &[Secret { var: var_name("amap_b", "K"), value: "v".into() }]).unwrap();
        let removed = remove_for(t.path(), "amap").unwrap();
        assert_eq!(removed, vec![a, b]);
        let text = std::fs::read_to_string(t.path().join(".env")).unwrap();
        assert!(text.contains("OPENAI_API_KEY=sk-x") && text.contains("CATFISH_MCP_AMAP_B__K='v'") && !text.contains("AMAP__AMAP_KEY"), "{text}");
        assert!(save(t.path(), &[Secret { var: "X".into(), value: "a\nb".into() }]).is_err());
    }
}
