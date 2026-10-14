//! 10/14 hermes v0.22: hermes 连鲶鱼网关的 `model:` 写法换成 `provider: custom` + `key_env`。
//!
//! # 为什么要换
//!
//! 员工机一直写的是
//!
//! ```text
//! model:
//!   provider: openai-api
//!   base_url: http://127.0.0.1:8999/v1
//!   api_key: <员工 1 小时令牌>
//! ```
//!
//! 在 v0.21 上 `openai-api` 实际用的是 `.env` 里 `OPENAI_API_KEY` (30 天服务令牌,
//! sync_service_token_to_env 写的), config 里那个 1 小时令牌只是备用 —— 所以 Companion
//! 关着、令牌过期了, 微信照样能聊。
//!
//! v0.22 把 `openai-api` 改成"只连官方 OpenAI、只从环境变量拿密钥"的内置 provider
//! (providers.py overlay: base_url 强制 api.openai.com, transport=codex_responses), 员工机上
//! 直接报 `No usable credentials found for provider 'openai-api'`, 一条都聊不了 (10/14 实测)。
//! 连自定义 OpenAI 兼容端点的正规写法是 `provider: custom`; 而 custom 取密钥是
//! **config 里的 `api_key` 优先**, 没有才看 `key_env` 指的环境变量 (10/14 实测)。照搬
//! `api_key` 的话, 等于把"平时用 30 天令牌"反过来变成"平时用 1 小时令牌" —— Companion
//! 关掉一小时后微信全部 401。
//!
//! 所以 v0.22 上写成
//!
//! ```text
//! model:
//!   provider: custom
//!   base_url: http://127.0.0.1:8999/v1
//!   key_env: OPENAI_API_KEY      ← 跟 v0.21 的实际行为一样: 平时用 30 天服务令牌
//!   (不写 api_key)
//! ```
//!
//! 员工令牌另存 `<hermes home>/catfish-companion-token` (权限 600), 给 Windows 上
//! Companion 聊天那段插件 (windows_chat_credentials.py) 用 —— 跟原来读 `model.api_key` 是同一个用途。
//!
//! # 只在 v0.22 上换
//!
//! v0.21 不认 `key_env` (实测: custom + key_env 拿不到密钥)。判据是安装戳
//! `hermes-agent/install-stamp.json` —— v0.22 起打包才写, 有它就是 v0.22+。升级过渡期
//! (Companion 新、hermes 还没换) 保持老写法, hermes 换完下一次同步自动切过来。
//!
//! Codex (`provider: openai-codex`) 那条路不碰 —— 跟 ensure_gateway_model_route 同一条规矩。

use anyhow::{Context, Result};
use std::fs;
use std::path::Path;

use super::hermes_jwt_sync::replace_model_field;

/// 存员工令牌的文件 (在每个 hermes home / profile 目录下)。
pub const COMPANION_TOKEN_FILE: &str = "catfish-companion-token";
/// custom provider 的密钥从这个环境变量取 (.env 里的 30 天服务令牌)。
pub const KEY_ENV: &str = "OPENAI_API_KEY";

/// 装的 hermes 是不是 v0.22+ (有安装戳)。看主 hermes home —— profile 目录共用同一份安装。
pub fn installed_hermes_is_v022() -> bool {
    crate::services::catfish_paths::hermes_home()
        .map(|home| {
            home.join("hermes-agent")
                .join(crate::commands::hermes_install_health::INSTALL_STAMP)
                .is_file()
        })
        .unwrap_or(false)
}

/// 删掉顶层 `model:` 段下的某个字段 (整行)。没有就原样返回。
pub(super) fn remove_model_field(text: &str, field: &str) -> String {
    let prefix = format!("{field}:");
    let mut in_model = false;
    let mut out: Vec<&str> = Vec::new();
    for line in text.lines() {
        let t = line.trim_end();
        if !line.starts_with(char::is_whitespace) && !t.is_empty() {
            in_model = t == "model:" || (t.starts_with("model:") && !t.starts_with("model::"));
            out.push(line);
            continue;
        }
        if in_model && t.trim_start().starts_with(&prefix) {
            continue;
        }
        out.push(line);
    }
    let mut s = out.join("\n");
    if !s.ends_with('\n') {
        s.push('\n');
    }
    s
}

fn read_model_provider(text: &str) -> String {
    let mut in_model = false;
    for line in text.lines() {
        let t = line.trim_end();
        if !line.starts_with(char::is_whitespace) {
            in_model = t == "model:" || (t.starts_with("model:") && !t.starts_with("model::"));
            continue;
        }
        if in_model {
            if let Some(v) = t.trim_start().strip_prefix("provider:") {
                return v.trim().trim_matches('"').trim_matches('\'').to_string();
            }
        }
    }
    String::new()
}

/// v0.22 写法: provider custom + key_env, 去掉 config 里的 api_key。Codex 那条路原样返回。
pub(super) fn apply_v022_route(text: &str) -> String {
    if read_model_provider(text) == "openai-codex" {
        return text.to_string();
    }
    let out = replace_model_field(text, "provider", "custom");
    let out = replace_model_field(&out, "key_env", KEY_ENV);
    remove_model_field(&out, "api_key")
}

/// 只校正主 hermes home 的 `model:` 写法 (不动令牌)。v0.21 / 没装 / 没有 config 时什么都不做。
pub fn reapply_main_route() -> Result<()> {
    if !installed_hermes_is_v022() {
        return Ok(());
    }
    let Some(home) = crate::services::catfish_paths::hermes_home() else {
        return Ok(());
    };
    let path = home.join("config.yaml");
    let Ok(text) = fs::read_to_string(&path) else {
        return Ok(());
    };
    let new = apply_v022_route(&text);
    if new != text {
        fs::write(&path, new).with_context(|| format!("写 {}", path.display()))?;
    }
    Ok(())
}

/// 员工令牌写到 `<home>/catfish-companion-token` (权限 600)。
pub(super) fn write_companion_token(home: &Path, jwt: &str) -> Result<()> {
    let path = home.join(COMPANION_TOKEN_FILE);
    let tmp = home.join(format!("{COMPANION_TOKEN_FILE}.tmp"));
    fs::write(&tmp, jwt.trim()).with_context(|| format!("写 {}", tmp.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600));
    }
    fs::rename(&tmp, &path).with_context(|| format!("rename → {}", path.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const V021: &str = "model:\n  api_key: eyJ.old.jwt\n  api_mode: chat_completions\n  base_url: http://127.0.0.1:8999/v1\n  \
provider: openai-api\n  default: catfish-auto\nweb:\n  backend: tavily\n  api_key: tvly-keep\n";

    #[test]
    fn v022_route_drops_inline_key_and_points_at_env() {
        let out = apply_v022_route(V021);
        assert!(out.contains("  provider: custom\n"), "{out}");
        assert!(out.contains("  key_env: OPENAI_API_KEY\n"), "{out}");
        assert!(!out.contains("eyJ.old.jwt"), "config 里不能再留员工令牌 (会压过 key_env): {out}");
        assert!(out.contains("  base_url: http://127.0.0.1:8999/v1\n"));
        assert!(out.contains("  default: catfish-auto\n"));
        // 别的段同名字段不碰
        assert!(out.contains("  api_key: tvly-keep\n"), "{out}");
        // 幂等
        assert_eq!(apply_v022_route(&out), out);
    }

    #[test]
    fn codex_route_is_left_alone() {
        let codex = "model:\n  provider: openai-codex\n  default: gpt-5\n  api_key: x\n";
        assert_eq!(apply_v022_route(codex), codex);
    }

    #[test]
    fn missing_model_section_gets_one() {
        let out = apply_v022_route("web:\n  backend: tavily\n");
        assert!(out.contains("model:\n"), "{out}");
        assert!(out.contains("provider: custom") && out.contains("key_env: OPENAI_API_KEY"), "{out}");
    }

    #[test]
    fn token_file_is_written_atomically() {
        let dir = tempfile::tempdir().unwrap();
        write_companion_token(dir.path(), " eyJ.new \n").unwrap();
        assert_eq!(fs::read_to_string(dir.path().join(COMPANION_TOKEN_FILE)).unwrap(), "eyJ.new");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(dir.path().join(COMPANION_TOKEN_FILE)).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o600);
        }
    }
}
