//! Codex 后端的总开关 —— **默认关**。
//!
//! # 为什么要有这个开关
//!
//! Codex 模型走的是员工本机 ChatGPT 登录, 请求直接到 OpenAI:
//!
//! - **不经中央网关**, 所以配额、审计、RBAC、tools sanitizer、SOUL 注入全部不生效
//! - **数据出端**, 跟这套产品「数据零出端 / 不基于客户数据训练」的红线直接冲突
//!
//! 也就是说它是**旁路**, 是本机开发时的临时手段, 不是交付给客户的能力。
//! 但在 8/8 之前它没有任何闸: 只要本机装了 ChatGPT.app 并登录, Codex 模型就自动
//! 出现在对话的模型选择器里, 点一下就把 hermes 全局切过去了。
//!
//! # 8/8 实际撞到的
//!
//! 在对话里选了 `gpt-5.6-luna`, `codex_backend_select_model` 把 `~/.hermes/config.yaml`
//! 改成 `provider: openai-codex` / `default: gpt-5.6-luna` / `openai_runtime: codex_app_server`。
//! 对话本身没事 (hermes 直连本机 Codex), 但 catfish 自己的后台调用是**写死打网关**的
//! (`catfish_memory_helpers.py:125` 的 `_DEFAULT_GATEWAY_URL`), 它们从 `model.default`
//! 读到 `gpt-5.6-luna` 发给网关 —— 网关只有那 6 个 catfish-*, 于是:
//!
//! ```text
//! catfish-memory summarize: HTTP 404 ({"detail":"model not found: gpt-5.6-luna"})
//! ```
//!
//! 早安页整个不可用, 记忆不再写入。而界面上没有任何地方说明这两件事是连着的 ——
//! 第一反应是「hermes 参数又丢了」, 查了一圈才找到是模型选择连带改了全局配置。
//!
//! # 判据
//!
//! 按优先级:
//!
//! 1. 进程环境变量 `CATFISH_CODEX_ENABLED` —— 开发时 `CATFISH_CODEX_ENABLED=1 open -a ...`
//!    单次生效, 不落盘, 不会忘了关
//! 2. `~/.hermes/.env` 里的 `CATFISH_CODEX_ENABLED=` —— 需要长期开着的开发机用
//! 3. 两处都没有 → **关**
//!
//! 默认关是刻意的: 这个开关的语义是"我知道我在旁路, 且这台机器不是客户机"。
//! 让它默认开、靠人记得关掉, 跟保密要求的方向是反的 —— 漏关一台就是数据出端,
//! 而且没有任何现象能让人发现。

use std::path::PathBuf;

/// 开关的键名。进程环境变量和 `~/.hermes/.env` 用同一个名字, 免得两处叫法不一样。
pub const CODEX_ENABLED_KEY: &str = "CATFISH_CODEX_ENABLED";

/// 把一个字符串解释成布尔。
///
/// 认 `1 / true / yes / on` (大小写不敏感) 为开, **其余一律为关** —— 包括空串、
/// `0`、拼错的值。宁可误关不可误开: 误关的表现是"Codex 模型不出现", 一眼看得见;
/// 误开的表现是数据静默出端, 没人会发现。
fn parse_flag(raw: &str) -> bool {
    matches!(
        raw.trim().trim_matches('"').trim_matches('\'').to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// 从一份 dotenv 文本里取这个键。line-based, 跟本仓其它地方读 `.env` 的做法一致
/// (见 `services/hermes_api_config.rs::read_hermes_env_api_key`) —— 不引 dotenv 依赖,
/// 也不做变量展开 (`.env` 里写 `$(...)` 不会被执行, 这一点 7 月已经踩过一次)。
fn flag_from_dotenv(content: &str) -> Option<bool> {
    let prefix = format!("{CODEX_ENABLED_KEY}=");
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') {
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix(prefix.as_str()) {
            return Some(parse_flag(rest));
        }
    }
    None
}

fn hermes_env_path() -> Option<PathBuf> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(PathBuf::from(home).join(".hermes").join(".env"))
}

/// Codex 后端现在允许用吗。
///
/// 每次调都重新读 —— 这个开关不该需要重启 Companion 才生效, 而且它只在
/// catalog 轮询和几个命令入口被调, 读一个几 KB 的文件不值得做缓存
/// (缓存反而会带来"改了没生效"的困惑)。
pub fn codex_enabled() -> bool {
    if let Ok(raw) = std::env::var(CODEX_ENABLED_KEY) {
        return parse_flag(&raw);
    }
    hermes_env_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|c| flag_from_dotenv(&c))
        .unwrap_or(false)
}

/// 关着的时候给用户看的话。不说"功能不可用"这种没信息量的话 —— 说清楚它为什么
/// 关着、以及要打开该动哪里。
pub fn disabled_reason() -> String {
    format!(
        "Codex 后端已关闭 (旁路能力, 默认不启用)。\
         它走本机 ChatGPT 登录直连 OpenAI, 不经中央网关 —— \
         配额/审计/RBAC 不生效, 且数据出端。\
         确实要在本机开发时用: 在 ~/.hermes/.env 里加 {CODEX_ENABLED_KEY}=1, \
         或用 {CODEX_ENABLED_KEY}=1 启动 Companion。"
    )
}

#[cfg(test)]
#[path = "codex_gate_tests.rs"]
mod tests;
