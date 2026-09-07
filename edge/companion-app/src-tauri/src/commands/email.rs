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
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::services::catfish_paths;
use crate::services::email_config;
use crate::services::email_scheduler;
use crate::services::process;
use crate::services::phishing_scan::PhishingScanResult;

/// 一次 list 最多返多少封。EmailTab 的 MAX_EMAIL_LIST_LIMIT 跟这个值对齐。
///
/// 8/15: 抽成命名常量, 因为它跟 email_scheduler::URGENCY_CACHE_MAX 之间有一条
/// **必须成立的大小关系** —— 评级缓存装不下整个列表时, email_classify_now
/// 返回的 map 会缺条目, 前端把缺的当"没评过"再评一遍, 形成永动机 (8/15 实测
/// 83 分钟 2470 万 token)。见 email_scheduler.rs 上那条测试。
pub(crate) const EMAIL_LIST_MAX: u32 = 500;

/// 创建邮件 CLI 子进程，并传递桌面端无法继承的邮件配置。
///
/// macOS/Windows 的 GUI 启动通常没有 shell 环境。Foxmail 自定义目录因此
/// 不能只依赖 PowerShell 中临时设置的环境变量，必须从 Companion 配置传给
/// 每一次 CLI 调用。配置了根目录时同时明确选 Foxmail，避免 Outlook COM
/// 探测失败污染 Foxmail 结果。
pub(crate) fn email_command(bin: &Path) -> Command {
    let mut command = process::background_command(bin);
    if cfg!(target_os = "windows") {
        if let Some(root) = email_config::email_config().foxmail_root.as_deref() {
            command
                .env("CATFISH_FOXMAIL_ROOT", root)
                .env("CATFISH_EMAIL_CLIENT", "foxmail-win");
        } else if std::env::var_os("CATFISH_FOXMAIL_ROOT").is_some() {
            // 直接从 shell 启动 Companion 的临时 override 仍然可用；这里只
            // 补选客户端，避免 catfish-email 再去尝试 Outlook。
            command.env("CATFISH_EMAIL_CLIENT", "foxmail-win");
        }
    }
    command
}

fn email_component_missing_error() -> String {
    let log_dir = if cfg!(target_os = "windows") {
        r"%LOCALAPPDATA%\hermes\logs\"
    } else if cfg!(target_os = "macos") {
        "~/Library/Logs/com.catfish.companion/"
    } else {
        "应用日志目录"
    };
    format!(
        "邮件组件未安装。请重启鲶鱼 Companion —— 启动时会自动补装；若重启后仍提示，请把 {log_dir} 里的日志发给 IT。"
    )
}

/// Apple Mail 数据目录读不读得到 —— 用来提示"有账号但少了几个"。
///
/// # 为什么要有这条
///
/// 8/8 鸿波实撞: 邮件页只出 Foxmail 一个账号, 授予完全磁盘访问权限后变成 5 个。
/// 也就是说 `~/Library/Mail` 里一直有数据, 只是**进程读不到**。
///
/// macOS 的 TCC 对这个目录的表现很坑: `stat` 过得去 (所以 `exists()` 返 true),
/// 但 `read_dir()` 抛 PermissionError。于是 catfish-email 那边判成"这台机器没在用
/// Apple Mail", 安静跳过 —— 从不崩了 (5db6e8f 修的), 但**界面上一个字都不说**,
/// 员工只会觉得"怎么少了几个邮箱", 完全没法把它跟系统权限联系起来。
/// 跟今天早上那个 advisor 静默 404 是同一类病: 降级了但没人知道。
///
/// # 为什么放在 Rust 而不是 Python 那边
///
/// FDA 是授给 Companion.app 的, 它自己就能判 —— 不需要穿过 catfish-email
/// 再把结果传回来。而且 `catfish_email/__main__.py` 已经 906 行超军规红线,
/// 不该再往里加子命令。
///
/// # 返回
///
/// - `"ok"`        目录能读 (或者本来就没有这个目录 —— 那就是真没用 Apple Mail)
/// - `"no_access"` 目录在但读不到 → **就是权限问题**, 前端据此挂提示
/// - `"n/a"`       非 macOS
#[tauri::command]
pub async fn email_mail_dir_status() -> Result<String, String> {
    if !cfg!(target_os = "macos") {
        return Ok("n/a".to_string());
    }
    let home = crate::util::paths::home_env().map_err(|_| "拿不到 HOME".to_string())?;
    let dir = PathBuf::from(home).join("Library").join("Mail");
    // 注意判断顺序: 先 read_dir 再看 exists。
    // 反过来写在 TCC 下会误判 —— exists() 那一步就已经"成功"了, 看不出问题。
    match std::fs::read_dir(&dir) {
        Ok(_) => Ok("ok".to_string()),
        Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
            log::info!("[email] {} 读不到 (缺完全磁盘访问权限)", dir.display());
            Ok("no_access".to_string())
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok("ok".to_string()),
        Err(e) => {
            // 其它错 (网络卷不可达之类) 不当权限问题报 —— 提示指错方向比不提示更糟。
            log::debug!("[email] 探 {} 出错 (不当权限问题): {e}", dir.display());
            Ok("ok".to_string())
        }
    }
}

/// 拉未读邮件简报. 返 raw JSON 字符串, 前端自己解析 (避开 Rust/TS 类型重复维护).
///
/// limit 默认 10 — 简报卡只显前 5, 多取 5 是 buffer (前端再 slice).
/// 客户端调用超时由各平台的邮件适配器处理, 不在 Tauri 层重复加一层固定超时.
#[tauri::command]
pub async fn email_digest_fetch(limit: Option<u32>) -> Result<String, String> {
    let bin = catfish_paths::catfish_email_bin().ok_or_else(email_component_missing_error)?;

    let n = limit.unwrap_or(10).clamp(1, 100);
    let out = email_command(&bin)
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
    let bin = catfish_paths::catfish_email_bin().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;

    let mut args = vec!["check".to_string(), "--json".to_string()];
    if let Some(a) = account.as_ref() {
        if !a.trim().is_empty() {
            args.push("--account".to_string());
            args.push(a.trim().to_string());
        }
    }

    let out = email_command(&bin)
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
    let bin = catfish_paths::catfish_email_bin().ok_or_else(email_component_missing_error)?;

    let n = limit.unwrap_or(50).clamp(1, EMAIL_LIST_MAX);
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

    let out = email_command(&bin)
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
/// `mark_read=false` 供只读上下文场景使用，不改变邮件已读状态。
#[tauri::command]
pub async fn email_read_message(
    id: String,
    mark_read: Option<bool>,
) -> Result<String, String> {
    let bin = catfish_paths::catfish_email_bin().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let mut args = vec!["read", "--id", id.as_str(), "--json"];
    if mark_read == Some(false) {
        args.push("--no-mark-read");
    }
    let out = email_command(&bin)
        .args(args)
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
    let bin = catfish_paths::catfish_email_bin().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let out = email_command(&bin)
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
    let bin = catfish_paths::catfish_email_bin().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let out = email_command(&bin)
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
    let bin = catfish_paths::catfish_email_bin().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let mut args: Vec<&str> = vec!["mark-read", "--id", &id, "--json"];
    // 默认 read=true (标已读). false → --unread
    if read == Some(false) {
        args.push("--unread");
    }
    let out = email_command(&bin)
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
    let bin = catfish_paths::catfish_email_bin().ok_or_else(|| {
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

    let out = email_command(&bin)
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
    let bin = catfish_paths::catfish_email_bin().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let out = email_command(&bin)
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
    let bin = catfish_paths::catfish_email_bin().ok_or_else(|| {
        "catfish-email CLI 没装".to_string()
    })?;
    let out = email_command(&bin)
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

/// 回复拟稿专用：在本地导出并解析附件预览，完成后删除临时原文件。
///
/// 原始附件不进入 ~/.catfish/uploads，也不直接发送给模型；只返回现有
/// parse_file.py 生成的 bounded preview。解析失败只影响该附件，不影响拟稿。
#[tauri::command]
pub async fn email_attachment_preview(
    id: String,
    filename: String,
) -> Result<crate::commands::file_parse::ParseFileResult, String> {
    let path = email_export_attachment(id, filename).await?;
    let parsed = crate::commands::file_parse::parse_file(path.clone()).await;
    let _ = std::fs::remove_file(&path);
    let _ = std::fs::remove_file(format!("{path}.parsed.txt"));
    // catfish-email 为每个附件创建独立的 /tmp/catfish-email-att-* 目录；
    // 解析后连空目录也清掉，避免拟稿多次运行持续占用磁盘。
    if let Some(parent) = std::path::Path::new(&path).parent() {
        if parent
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.starts_with("catfish-email-att-"))
        {
            let _ = std::fs::remove_dir(parent);
        }
    }
    parsed
}
