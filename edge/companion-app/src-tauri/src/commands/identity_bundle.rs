//! BL-IDENTITY-INJECT-DECOUPLE (5/26 P0 SaaS 准备):
//! 把员工本机 ~/.hermes/ 下 SOUL / USER / memories 6 个文件读好打包,
//! 让 Companion 通过 /v1/chat/completions body 字段 `_catfish_identity_bundle`
//! 透传给 gateway, gateway 不再自己读员工本机 fs.
//!
//! # 背景
//!
//! gateway `identity_inject.py` 老逻辑: Path.home() / ".hermes" / "SOUL.md" 等.
//! SaaS 后 gateway 跑客户机房, 读不到员工 mac, 鲶鱼退化成 ChatGPT (无人格 / 无记忆).
//! 5/26 audit 把 identity_inject 标 ALLOWLIST 待 Companion prefetch 化.
//!
//! # 设计
//!
//! 一个原子命令 `identity_bundle()` 返 6 字段 dict:
//!   - soul              ~/.hermes/SOUL.md
//!   - soul_customer     ~/.hermes/SOUL_<CUSTOMER>.md (默认 FFCS, env CATFISH_CUSTOMER override)
//!   - soul_browser      ~/.hermes/SOUL_BROWSER.md      (BL-SOUL-SCENARIO P2 场景段)
//!   - soul_execute_code ~/.hermes/SOUL_EXECUTE_CODE.md (场景段)
//!   - user_memory       ~/.hermes/USER.md
//!   - memory_dir        concat 拼好的 ~/.hermes/memories/*.md (按文件名排序)
//!
//! 文件不存在 / 读失败 → 该字段返 "" (空字符串, gateway 端 build_identity_content
//! 看到空就 skip 该段, 行为跟 fs 兜底完全一致).
//!
//! # 大小
//!
//! SOUL.md 通常 1-3KB, SOUL_<CUSTOMER>.md 1-3KB, 场景段各 1-2KB, USER.md 1-5KB,
//! memories/ 5-30KB. 全合并通常 10-50KB. JSON body 装得下, header 装不下 (header
//! 通常 8-16KB 限制), 所以走 body 字段.
//!
//! # 跟 proactive_context 区别
//!
//! 两个命令都读员工本机, 但 trigger 时机不同:
//! - identity_bundle: 每次 /v1/chat/completions 前调一次, gateway 拿来注 system prompt
//! - proactive_context: 主动闲聊定时 / 信号触发时调, gateway 拿来生成 starter

use serde::Serialize;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Serialize)]
pub struct IdentityBundle {
    pub soul: String,
    pub soul_customer: String,
    pub soul_browser: String,
    pub soul_execute_code: String,
    pub user_memory: String,
    pub memory_dir: String,
}

fn hermes_home() -> Option<PathBuf> {
    // 跟 gateway identity_inject._hermes_home 同源: env HERMES_HOME override > ~/.hermes
    if let Ok(env) = std::env::var("HERMES_HOME") {
        return Some(PathBuf::from(env));
    }
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".hermes"))
}

fn read_file_silent(path: &std::path::Path) -> String {
    if !path.exists() {
        return String::new();
    }
    fs::read_to_string(path).unwrap_or_default()
}

fn customer_label() -> String {
    // 跟 gateway 同源: env CATFISH_CUSTOMER override > "ffcs" 默认 (FFCS 大写)
    let raw = std::env::var("CATFISH_CUSTOMER").unwrap_or_else(|_| "ffcs".to_string());
    raw.trim().to_uppercase()
}

fn read_memories_concat(home: &std::path::Path) -> String {
    let mem_dir = home.join("memories");
    if !mem_dir.is_dir() {
        return String::new();
    }
    let mut entries: Vec<_> = match fs::read_dir(&mem_dir) {
        Ok(it) => it.flatten().collect(),
        Err(_) => return String::new(),
    };
    // 按文件名排序 (跟 gateway identity_inject._read_memory_dir 同模式)
    entries.sort_by_key(|e| e.file_name());

    let mut parts: Vec<String> = Vec::new();
    for entry in entries {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let name = match path.file_name().and_then(|n| n.to_str()) {
            Some(n) => n,
            None => continue,
        };
        if !name.ends_with(".md") {
            continue;
        }
        let stem = name.strip_suffix(".md").unwrap_or(name);
        let content = read_file_silent(&path).trim().to_string();
        if !content.is_empty() {
            parts.push(format!("## {stem}\n\n{content}"));
        }
    }
    parts.join("\n\n")
}

#[tauri::command]
pub async fn identity_bundle() -> Result<IdentityBundle, String> {
    tokio::task::spawn_blocking(|| {
        let home = match hermes_home() {
            Some(h) => h,
            None => {
                // HOME 没设 (理论不应该), 返全空 bundle, 让 gateway fallback fs
                return Ok::<IdentityBundle, String>(IdentityBundle {
                    soul: String::new(),
                    soul_customer: String::new(),
                    soul_browser: String::new(),
                    soul_execute_code: String::new(),
                    user_memory: String::new(),
                    memory_dir: String::new(),
                });
            }
        };

        let cust = customer_label();
        Ok::<IdentityBundle, String>(IdentityBundle {
            soul: read_file_silent(&home.join("SOUL.md")),
            soul_customer: read_file_silent(&home.join(format!("SOUL_{cust}.md"))),
            soul_browser: read_file_silent(&home.join("SOUL_BROWSER.md")),
            soul_execute_code: read_file_silent(&home.join("SOUL_EXECUTE_CODE.md")),
            user_memory: read_file_silent(&home.join("USER.md")),
            memory_dir: read_memories_concat(&home),
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}
