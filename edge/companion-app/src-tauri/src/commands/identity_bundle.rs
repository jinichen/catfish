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
//!
//! # P3.5.55 (6/21 鸿波 catch "客户没 catfish 源 → SOUL 软链 dangling → 身份退化")
//!
//! `edge/identity/install.sh` 用 `ln -s` 把 ~/.hermes/SOUL.md 软链到
//! `catfish/edge/identity/SOUL.md`. 开发者本机 catfish git clone 存在 → 软链 OK.
//! 但**客户场景** (Companion.dmg 装机, 没 catfish git clone) → 软链 target 不存在
//! → dangling → `Path.exists() = false` → `read_file_silent` 返 "" → bundle 全空
//! → gateway `build_identity_content` 返 "" → `inject_identity_if_needed` 看 content 空
//! 静默跳过注入 → 上游 LLM 收到无 system → 鲶鱼退化成 ChatGPT/Hermes 默认人格
//! (出现 "Nous Research" / "AI 助手" 字样).
//!
//! 修法 (鸿波 6/21 拍 "Companion baked-in default SOUL"): include_str!() 把 4 个
//! SOUL 文件**编译时内嵌**进 Companion binary. `read_file_or_baked` fs 读非空用 fs
//! (开发者改即生效), 空/缺失用 baked (客户兜底). 加 `bootstrap_soul_files()`
//! 启动时自检 ~/.hermes/SOUL*.md, 不存在/dangling 时写 baked 内容 — 让 hermes
//! 自己路径 (catfish gateway 外) 也能读到 SOUL.md.
//!
//! USER.md / memories/ **不** baked — 是员工个人数据, 没就是没.

use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};

// ─────────────────────────────────────────────
// P3.5.55: baked-in default SOUL files
// ─────────────────────────────────────────────
//
// include_str!() 编译时把 4 个 SOUL 文件嵌进 binary, 路径相对本 .rs 文件:
//   edge/companion-app/src-tauri/src/commands/identity_bundle.rs
//   → ../../../../identity/SOUL.md
// 文件实际大小 SOUL ~15KB / SOUL_FFCS ~2.5KB / SOUL_BROWSER ~3.5KB /
// SOUL_EXECUTE_CODE ~4KB, 总 ~25KB 加进 binary, 可接受 (Companion 本身几十 MB).
//
// 编译时若 source 缺 (catfish 仓库不全) → cargo build 报错, 早发现.
const BAKED_SOUL: &str = include_str!("../../../../identity/SOUL.md");
const BAKED_SOUL_FFCS: &str = include_str!("../../../../identity/SOUL_FFCS.md");
const BAKED_SOUL_BROWSER: &str = include_str!("../../../../identity/SOUL_BROWSER.md");
const BAKED_SOUL_EXECUTE_CODE: &str = include_str!("../../../../identity/SOUL_EXECUTE_CODE.md");

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

/// P3.5.55: fs 读非空用 fs, 否则 baked 兜底.
///
/// 行为:
///   - fs 文件存在 + 非空 → 用 fs 内容 (开发者改 SOUL.md 即生效)
///   - fs 文件不存在 / dangling / 空 → 用 baked 编译时嵌入的默认内容
///
/// 适用: SOUL.md / SOUL_FFCS.md / SOUL_BROWSER.md / SOUL_EXECUTE_CODE.md
/// (鲶鱼品牌身份, 客户场景必须有). 不适用 USER.md / memories/ (员工个人数据).
fn read_file_or_baked(path: &Path, baked: &'static str) -> String {
    let fs_content = read_file_silent(path);
    if !fs_content.trim().is_empty() {
        return fs_content;
    }
    baked.to_string()
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
        // P3.5.55: SOUL 4 件走 fs-或-baked 兜底, USER.md / memories/ 仍 fs-only
        // (员工个人, 没就是没, 不该 bake 默认).
        // soul_customer 只 FFCS 客户在 bake, 别家 (BYD/MEITUAN/...) 仍 fs-only.
        let baked_customer = if cust == "FFCS" { BAKED_SOUL_FFCS } else { "" };
        Ok::<IdentityBundle, String>(IdentityBundle {
            soul: read_file_or_baked(&home.join("SOUL.md"), BAKED_SOUL),
            soul_customer: read_file_or_baked(
                &home.join(format!("SOUL_{cust}.md")),
                baked_customer,
            ),
            soul_browser: read_file_or_baked(
                &home.join("SOUL_BROWSER.md"),
                BAKED_SOUL_BROWSER,
            ),
            soul_execute_code: read_file_or_baked(
                &home.join("SOUL_EXECUTE_CODE.md"),
                BAKED_SOUL_EXECUTE_CODE,
            ),
            user_memory: read_file_silent(&home.join("USER.md")),
            memory_dir: read_memories_concat(&home),
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

// ─────────────────────────────────────────────
// P3.5.55: 启动时自检 + dump baked SOUL 到 ~/.hermes/
// ─────────────────────────────────────────────

/// 把 baked SOUL files 写到 ~/.hermes/<name>.md, 当文件不存在或软链 dangling 时.
///
/// 调用时机: Companion 启动 setup hook (lib.rs).
///
/// 为啥这个独立步骤 (除了 identity_bundle 的 fs-or-baked fallback):
///   - identity_bundle 走 catfish gateway 路径 (Companion → gateway → 上游 LLM)
///   - 但 hermes 自己也读 ~/.hermes/SOUL.md (路径 1: Companion → hermes private model
///     → hermes 调上游 LLM, identity inject 走 hermes 内部, 不经 catfish gateway)
///   - 这条路 baked fallback 帮不上忙 — hermes 直接看 fs 是空 → 退化 Nous 身份
///   - 必须 fs 真有 SOUL.md 内容 hermes 才能注入
///
/// 行为:
///   - fs 文件存在 + 非空 + 不是 dangling 软链 → 不动 (尊重员工/install.sh 已装的)
///   - fs 文件不存在 (无任何条目) → 写 baked
///   - fs 是 dangling 软链 (lstat 软链存在但 stat target 不存在) → 删软链, 写 baked
///   - fs 是空文件 (0 字节) → 覆盖写 baked
///
/// 失败 (~/.hermes/ 创不出 / 权限) → log::warn 不挂启动.
pub fn bootstrap_soul_files() {
    let home = match hermes_home() {
        Some(h) => h,
        None => {
            log::warn!("[P3.5.55] HOME 没设, 跳 SOUL bootstrap");
            return;
        }
    };
    if let Err(e) = fs::create_dir_all(&home) {
        log::warn!("[P3.5.55] 建 ~/.hermes/ 失败 ({e}), 跳 SOUL bootstrap");
        return;
    }

    let cust = customer_label();
    // (filename, baked content)
    let baked_files: &[(String, &str)] = &[
        ("SOUL.md".into(), BAKED_SOUL),
        ("SOUL_BROWSER.md".into(), BAKED_SOUL_BROWSER),
        ("SOUL_EXECUTE_CODE.md".into(), BAKED_SOUL_EXECUTE_CODE),
        // FFCS 客户 SOUL — 别家客户 (BYD/MEITUAN) 没 baked, 跳过
        // (员工那边自己维护 catfish/edge/identity/SOUL_<X>.md 走软链)
        (
            format!("SOUL_{cust}.md"),
            if cust == "FFCS" { BAKED_SOUL_FFCS } else { "" },
        ),
    ];

    for (name, baked) in baked_files {
        if baked.is_empty() {
            continue;
        }
        let dst = home.join(name);
        if soul_file_is_healthy(&dst) {
            continue;
        }
        // dangling 软链或空文件 — 先清掉再写
        // (fs::remove_file 对软链是 unlink 软链本身, 不动 target)
        match fs::symlink_metadata(&dst) {
            Ok(_) => {
                if let Err(e) = fs::remove_file(&dst) {
                    log::warn!(
                        "[P3.5.55] 删旧 {} 失败 ({}), 跳此文件",
                        dst.display(),
                        e
                    );
                    continue;
                }
            }
            Err(_) => {} // 不存在, 直接写
        }
        match fs::write(&dst, baked) {
            Ok(()) => log::info!(
                "[P3.5.55] dump baked {} ({} bytes, 客户场景兜底身份)",
                dst.display(),
                baked.len()
            ),
            Err(e) => log::warn!("[P3.5.55] 写 {} 失败 ({})", dst.display(), e),
        }
    }
}

/// 文件是否"健康": 存在 + 非空 + 不是 dangling 软链.
///
/// `Path::exists()` 跟 symlink, dangling 返 false (这就是我们要识别的状态).
/// 实文件 + 0 bytes 不算健康 (得 dump baked).
fn soul_file_is_healthy(path: &Path) -> bool {
    if !path.exists() {
        return false;
    }
    match fs::metadata(path) {
        Ok(m) => m.is_file() && m.len() > 0,
        Err(_) => false,
    }
}
