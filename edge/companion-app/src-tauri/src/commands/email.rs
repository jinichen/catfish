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

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Command;

use crate::services::email_scheduler;
use crate::services::phishing_scan::PhishingScanResult;

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

/// P3.5.204.c (7/9 鸿波 catch "客户端还没同步的邮件, 在鲶鱼里无法激活客户端去同步"):
/// 触发客户端立即从邮箱服务器 fetch 新邮件. 员工在 Companion 看不到新邮件时点
/// 刷新按钮, 立即触发 catfish-email check 让 Apple Mail 去 IMAP/POP 拉一次.
/// account 空 = 全账号同步; 指定 = 只同步该账号.
#[tauri::command]
pub async fn email_check_new(account: Option<String>) -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;

    let mut args = vec!["check".to_string(), "--json".to_string()];
    if let Some(a) = account.as_ref() {
        if !a.trim().is_empty() {
            args.push("--account".to_string());
            args.push(a.trim().to_string());
        }
    }

    let out = Command::new(&bin)
        .args(&args)
        .output()
        .map_err(|e| format!("catfish-email check 调用失败: {e}"))?;

    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    // check 返 0 表示至少一个 adapter 触发成功; 非 0 表示全失败 (stderr 有信息).
    if !out.status.success() && stdout.trim().is_empty() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("email check 退出码 {:?}", out.status.code())
        } else {
            stderr
        });
    }
    Ok(stdout)
}

/// 拉邮件列表 (all, 不只 unread). EmailTab 邮件 tab 完整 inbox 浏览用.
/// unread_only=true → 只未读 (跟 step1 简报卡同行为); =false → 全部 (已读 + 未读混)
///
/// P3.5.204.b (7/9 鸿波 catch "回复过的邮件还是看不到已回复标志"): 加 folder
/// 参数支持. 老默认 "Inbox" — EmailTab 拉 items 只有收件箱, isReplied 算法找
/// R.in_reply_to 时 R (回复邮件, 存 Sent 已发送) 不在 list 里, 判定永远 false.
/// 员工传 folder=Sent 单独拉 Sent 邮件, 合并 items+sentItems 算 repliedMap.
#[tauri::command]
pub async fn email_list_fetch(
    unread_only: bool,
    limit: Option<u32>,
    folder: Option<String>,
) -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装. 装: cd ~/person_task/catfish/edge/email-agent && bash install.sh"
            .to_string()
    })?;

    let n = limit.unwrap_or(50).clamp(1, 500);
    let mut args = vec!["list".to_string(), "--json".to_string()];
    if unread_only {
        args.push("--unread".to_string());
    }
    if let Some(f) = folder.as_ref() {
        if !f.trim().is_empty() {
            args.push("--folder".to_string());
            args.push(f.trim().to_string());
        }
    }
    args.push("--limit".to_string());
    args.push(n.to_string());

    let out = Command::new(&bin)
        .args(&args)
        .output()
        .map_err(|e| format!("catfish-email 调用失败: {e}"))?;

    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("catfish-email 退出码 {:?}, 没 stderr", out.status.code())
        } else {
            stderr
        });
    }

    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if stdout.trim().is_empty() {
        return Ok("[]".to_string());
    }
    Ok(stdout)
}

/// 读单封邮件全文 (含 body_text / body_html / 附件元数据).
///
/// 5/18 BL-EMAIL-MARK-READ: CLI 的 `read` 子命令现在默认自动标已读 (跟主流邮件
/// 客户端一致). Companion 点开邮件 → catfish-email read → Mail.app/Foxmail
/// 那侧的 read status 也跟着翻 → 用户下次回到客户端看到已读. 返回的 JSON
/// is_read 字段也会反映新状态, 前端可乐观更新列表.
/// 如果用户想"窥视但不标记", 走单独的 hermes prompt 让 LLM 解释为啥, 这里不
/// 提供 --no-mark-read 开关 (Companion 是面向用户的客户端, 点了就是看了).
#[tauri::command]
pub async fn email_read_message(id: String) -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let out = Command::new(&bin)
        .args(["read", "--id", &id, "--json"])
        .output()
        .map_err(|e| format!("catfish-email 调用失败: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("catfish-email read 退出码 {:?}", out.status.code())
        } else {
            stderr
        });
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

/// 5/18 BL-EMAIL-COMPOSE-SEND: 把 Drafts 里的草稿真发出去.
///
/// **红线**: 这个 Tauri 命令本身不做"是不是人发的" 校验, 是 React
/// EmailComposePanel 必须通过两步 confirm 才允许调到这里. AI 永远只能起草
/// (email_create_draft), 真 send 必须人工点按钮.
#[tauri::command]
pub async fn email_send_message(id: String) -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let out = Command::new(&bin)
        .args(["send", "--id", &id, "--json"])
        .output()
        .map_err(|e| format!("catfish-email 调用失败: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        // 退出码 4 = NotSupported (Foxmail), 错误消息有引导文案
        return Err(if stderr.is_empty() {
            format!("catfish-email send 退出码 {:?}", out.status.code())
        } else {
            stderr
        });
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

/// 5/18 BL-EMAIL-DELETE: 把邮件移到客户端 Trash 文件夹 (软删, 不彻底).
///
/// Apple Mail: AS `delete <msg>` 移到 Trash, 跟员工按 ⌫ 同效果.
/// Foxmail Mac: 不支持 (返 4 退出码, 提示员工去 Foxmail 自己删).
///
/// 红线: 永远不彻底物理删 — Trash 30 天内可恢复, 跟主流邮件客户端对齐.
#[tauri::command]
pub async fn email_delete_message(id: String) -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let out = Command::new(&bin)
        .args(["delete", "--id", &id, "--json"])
        .output()
        .map_err(|e| format!("catfish-email 调用失败: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        // 退出码 4 = 不支持的 adapter (Foxmail) — 错误消息含引导文案, 前端可直接显
        return Err(if stderr.is_empty() {
            format!("catfish-email delete 退出码 {:?}", out.status.code())
        } else {
            stderr
        });
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

/// 5/18 BL-EMAIL-MARK-READ: 单独标已读/未读 (不读正文).
///
/// 场景: 用户在 EmailTab 列表里右键 "标已读" / 批量勾选 → 标已读, 不需要拉
/// 正文. 也用于已读后又想标回未读的反向操作.
#[tauri::command]
pub async fn email_mark_read(id: String, read: Option<bool>) -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let mut args: Vec<&str> = vec!["mark-read", "--id", &id, "--json"];
    // 默认 read=true (标已读). false → --unread
    if read == Some(false) {
        args.push("--unread");
    }
    let out = Command::new(&bin)
        .args(&args)
        .output()
        .map_err(|e| format!("catfish-email 调用失败: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("catfish-email mark-read 退出码 {:?}", out.status.code())
        } else {
            stderr
        });
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

/// 起草邮件落 Mail.app Drafts (不发送 — 红线). BL-COMPANION-EMAIL-TAB-STEP2 (5/18).
///
/// body 通过 stdin / tmp file 传给 CLI 避 shell 转义. 这里用 tmp file 更稳.
#[tauri::command]
pub async fn email_create_draft(
    to: String,
    cc: Option<String>,
    bcc: Option<String>,
    subject: String,
    body: String,
    in_reply_to: Option<String>,
    account: Option<String>,
) -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;

    // body 写 tmp file 避免 shell 转义 (员工正文可能含引号/换行/特殊字符)
    let tmp_dir = std::env::temp_dir();
    let tmp_path = tmp_dir.join(format!(
        "catfish-email-draft-{}-{}.txt",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis()).unwrap_or(0),
    ));
    std::fs::write(&tmp_path, &body)
        .map_err(|e| format!("写 body tmp 文件失败: {e}"))?;

    let mut args = vec![
        "draft".to_string(),
        "--to".to_string(), to,
        "--subject".to_string(), subject,
        "--body-file".to_string(), tmp_path.to_string_lossy().to_string(),
        "--json".to_string(),
    ];
    if let Some(c) = cc.filter(|s| !s.is_empty()) {
        args.push("--cc".to_string()); args.push(c);
    }
    if let Some(b) = bcc.filter(|s| !s.is_empty()) {
        args.push("--bcc".to_string()); args.push(b);
    }
    if let Some(r) = in_reply_to.filter(|s| !s.is_empty()) {
        args.push("--in-reply-to".to_string()); args.push(r);
    }
    if let Some(a) = account.filter(|s| !s.is_empty()) {
        args.push("--account".to_string()); args.push(a);
    }

    let out = Command::new(&bin)
        .args(&args)
        .output()
        .map_err(|e| format!("catfish-email draft 调用失败: {e}"))?;

    // 清 tmp file (失败不阻塞)
    let _ = std::fs::remove_file(&tmp_path);

    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("draft 退出码 {:?}", out.status.code())
        } else {
            stderr
        });
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
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

/// P3.3.58 (6/12 鸿波): 批量查邮件钓鱼扫描结果. 给前端列表 / 详情 UI 用.
/// 没扫过的 id 在返 map 中不出现 (前端按需 fallback "未扫描").
#[tauri::command]
pub async fn email_phishing_get(ids: Vec<String>) -> Result<HashMap<String, PhishingScanResult>, String> {
    let mut out = HashMap::new();
    for id in ids {
        if let Some(r) = email_scheduler::phishing_for_message(&id) {
            out.insert(id, r);
        }
    }
    Ok(out)
}

/// P3.3.53 (6/13 鸿波): 给前端 detail pane 打开邮件时调 — 拿到 body 后扫
/// 政治敏感. yaml political.enabled=false 时整套引擎跳 (engine_enabled=false 返).
/// 结果存 POLITICAL_STORE, 后续重复打开同 id 不重复扫.
#[tauri::command]
pub async fn email_political_scan_now(
    id: String,
    subject: String,
    sender: String,
    body: String,
) -> Result<crate::services::political_scan::PoliticalScanResult, String> {
    // 已经扫过 → 直接返
    if let Some(r) = email_scheduler::political_for_message(&id) {
        return Ok(r);
    }
    let msg = crate::services::political_scan::MessageData {
        id: &id,
        subject: &subject,
        body_text: &body,
    };
    let result = crate::services::political_scan::scan_rules(&msg);
    email_scheduler::political_store_insert(&id, &result);
    crate::services::political_scan::persist_audit(&result, &subject, &sender).await;
    Ok(result)
}

/// P3.3.53: 批量查邮件政治敏感扫描结果. 跟 email_phishing_get 同款.
#[tauri::command]
pub async fn email_political_get(
    ids: Vec<String>,
) -> Result<HashMap<String, crate::services::political_scan::PoliticalScanResult>, String> {
    let mut out = HashMap::new();
    for id in ids {
        if let Some(r) = email_scheduler::political_for_message(&id) {
            out.insert(id, r);
        }
    }
    Ok(out)
}

/// P3.5.103 (6/24 鸿波 catch "附件不能点"): 导出邮件附件到本地 tmp 文件, 返 path.
///
/// CLI `catfish-email attachment --id X --filename Y --json` 输出 {"path": "..."}.
/// 前端拿到 path 调 open_file Tauri command (file.rs:101) 系统默认 app 打开.
///
/// 错误兜底跟其他 email_* command 同款: stderr 不空时透出, 否则用退出码.
#[tauri::command]
pub async fn email_export_attachment(id: String, filename: String) -> Result<String, String> {
    let bin = find_catfish_email().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let out = Command::new(&bin)
        .args(["attachment", "--id", &id, "--filename", &filename, "--json"])
        .output()
        .map_err(|e| format!("catfish-email 调用失败: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("catfish-email attachment 退出码 {:?}", out.status.code())
        } else {
            stderr
        });
    }
    // parse JSON {"path": "..."}
    let stdout_str = String::from_utf8_lossy(&out.stdout).to_string();
    let val: serde_json::Value = serde_json::from_str(stdout_str.trim())
        .map_err(|e| format!("catfish-email JSON 解析失败: {e}: {stdout_str}"))?;
    let path = val
        .get("path")
        .and_then(|v| v.as_str())
        .ok_or_else(|| format!("catfish-email 返没 path 字段: {stdout_str}"))?;
    Ok(path.to_string())
}
