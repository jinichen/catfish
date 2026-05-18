//! 邮件简报 — BL-COMPANION-EMAIL-DIGEST (5/18 鸿波).
//!
//! 设计:
//!   - Companion 不读邮件正文 / 不缓存邮件内容到本地 (隐私 + Mail.app 已是 inbox UI).
//!   - 这里只 shell out `catfish-email list --unread --json`, 拿"未读邮件元数据"
//!     (subject / sender / date / id) 给 Dashboard 卡片渲染.
//!   - 每次调用都 fork 子进程, 简单, 不维护 daemon.
//!   - step1 没 scheduler, 手动点刷新 / 切 tab 触发. scheduler / LLM 评级 / 桌宠
//!     集成留 step2-3.
//!
//! 红线:
//!   - 不写文件 / 不持久化邮件数据 → 重启 Companion 简报清空, 重新 fetch
//!   - 出错不阻塞 — 没装 catfish-email / Mail.app 没开 / Automation 没权限,
//!     都返 Err(String), 前端显错误不挂卡

use std::path::PathBuf;
use std::process::Command;

/// 找 catfish-email 二进制. 优先用 ~/.local/bin (catfish-email install.sh 软链到此),
/// 兜底 PATH 查找.
fn find_catfish_email() -> Option<PathBuf> {
    if let Ok(home) = std::env::var("HOME") {
        let candidate = PathBuf::from(home).join(".local/bin/catfish-email");
        if candidate.exists() {
            return Some(candidate);
        }
    }
    // PATH 兜底
    if let Ok(out) = Command::new("which").arg("catfish-email").output() {
        if out.status.success() {
            let path_str = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !path_str.is_empty() {
                return Some(PathBuf::from(path_str));
            }
        }
    }
    None
}

/// 拉未读邮件简报. 返 raw JSON 字符串, 前端自己解析 (避开 Rust/TS 类型重复维护).
///
/// limit 默认 10 — 简报卡只显前 5, 多取 5 是 buffer (前端再 slice).
/// timeout 用 osascript subprocess 内置 30s, 不在 Tauri 层加.
#[tauri::command]
pub async fn email_digest_fetch(limit: Option<u32>) -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装. 装: cd ~/person_task/catfish/edge/email-agent && bash install.sh"
            .to_string()
    })?;

    let n = limit.unwrap_or(10).clamp(1, 100);
    let out = Command::new(&bin)
        .args(["list", "--unread", "--json"])
        .arg("--limit")
        .arg(n.to_string())
        .output()
        .map_err(|e| format!("catfish-email 调用失败: {e}"))?;

    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        // exit 1 = adapter 不可用 (Mail.app 没开 / 没权限) — 前端按"先开 Mail.app" 提示
        return Err(if stderr.is_empty() {
            format!("catfish-email 退出码 {:?}, 没 stderr", out.status.code())
        } else {
            stderr
        });
    }

    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if stdout.trim().is_empty() {
        return Err("catfish-email 没输出 (可能没账号)".to_string());
    }
    Ok(stdout)
}

/// 拉账号列表. 用于"配了几个邮箱". 卡片 header 显 "5 账号 · 12 未读".
#[tauri::command]
pub async fn email_accounts_fetch() -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let out = Command::new(&bin)
        .args(["accounts", "--json"])
        .output()
        .map_err(|e| format!("catfish-email 调用失败: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("catfish-email accounts 退出码 {:?}", out.status.code())
        } else {
            stderr
        });
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}
