//! BL-ADVISOR-DRAFTS (5/21 Phase 7 第 2 步): 草稿存储.
//!
//! 设计稿 §6.1: ~/.catfish/outputs/<YYYY-MM-DD>/<task-type>-<key>.md
//!
//! 跟 BL-CENTRAL-EDGE (5/17): 草稿在员工本机, 不出端. catfish 不替员工发, 只起草到 outputs/,
//! 员工自己点开看/改/复制后自己发.
//!
//! 这一层 Rust 提供:
//!   - draft_save: LLM tool 调完 (e.g. draft_email_reply) 把草稿内容写到 outputs/<date>/
//!   - draft_read: UI 展开 DraftPreview 时读全文
//!   - draft_list_today: 列今天所有草稿元数据 (filename + size + mtime)
//!   - draft_open_in_editor: 调系统默认编辑器打开 (员工自己改 + 复制后发)
//!   - recent_outputs_list (5/26): 跨日期扫 outputs/<*>/<*>, 过去 N 小时改的文件,
//!     给 chat timeout toast 用 (BL-X: 替代砍掉的 gateway recent_outputs.list_recent)

use std::path::PathBuf;
use chrono::Utc;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DraftRef {
    /// 文件名 (不含目录), e.g. "reply-laoli-balanced.md"
    pub filename: String,
    /// 完整路径 (debug 用 / 员工可拷)
    pub abs_path: String,
    /// ISO-8601 mtime
    pub modified_at: String,
    /// 大小字节
    pub bytes: u64,
}

// ── 路径 helpers ─────────────────────────────────────────────────────

fn outputs_root() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("outputs"))
}

fn today_dir() -> Result<PathBuf, String> {
    let date = Utc::now().format("%Y-%m-%d").to_string();
    Ok(outputs_root()?.join(date))
}

fn date_dir(date: &str) -> Result<PathBuf, String> {
    // date 必须是 YYYY-MM-DD 格式 — 防 path traversal
    if !date.chars().all(|c| c.is_ascii_digit() || c == '-') || date.len() != 10 {
        return Err(format!("date 格式非法 (期望 YYYY-MM-DD): {date}"));
    }
    Ok(outputs_root()?.join(date))
}

fn safe_filename(filename: &str) -> Result<&str, String> {
    // 防 path traversal: 不允许 / .. 等
    if filename.contains('/') || filename.contains("..") || filename.contains('\\') {
        return Err(format!("filename 含非法字符: {filename}"));
    }
    if filename.is_empty() || filename.len() > 200 {
        return Err(format!("filename 长度非法: {}", filename.len()));
    }
    Ok(filename)
}

// ── Tauri commands ───────────────────────────────────────────────────

/// LLM tool 写草稿. 路径自动 outputs/<today>/<filename>. 内容覆盖式写.
///
/// filename 限制: 不含 / \\ ..; 长度 1-200.
/// 返回写入的绝对路径 (前端 UI 用来显示 + 调 open_in_editor).
#[tauri::command]
pub async fn draft_save(filename: String, content: String) -> Result<String, String> {
    let fname = safe_filename(&filename)?;
    let dir = today_dir()?;
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("创建 outputs/<today>/ 失败: {e}"))?;

    let path = dir.join(fname);
    let tmp = path.with_extension(format!(
        "{}.tmp",
        path.extension().and_then(|s| s.to_str()).unwrap_or("part")
    ));

    std::fs::write(&tmp, content)
        .map_err(|e| format!("写 draft tmp 失败: {e}"))?;
    std::fs::rename(&tmp, &path)
        .map_err(|e| format!("rename draft 失败: {e}"))?;
    Ok(path.to_string_lossy().to_string())
}

/// 读草稿内容.
///
/// date: "YYYY-MM-DD" — 限定格式防 path traversal.
/// filename: 同 safe_filename 检查.
#[tauri::command]
pub async fn draft_read(date: String, filename: String) -> Result<String, String> {
    let fname = safe_filename(&filename)?;
    let dir = date_dir(&date)?;
    let path = dir.join(fname);
    if !path.exists() {
        return Err(format!("草稿不存在: {}", path.display()));
    }
    std::fs::read_to_string(&path)
        .map_err(|e| format!("读 draft 失败: {e}"))
}

/// 列今天所有草稿 (mtime 倒序). 没目录 / 空目录 → 空 Vec.
#[tauri::command]
pub async fn draft_list_today() -> Result<Vec<DraftRef>, String> {
    let dir = today_dir()?;
    if !dir.exists() {
        return Ok(Vec::new());
    }
    let entries = std::fs::read_dir(&dir)
        .map_err(|e| format!("read_dir 失败: {e}"))?;
    let mut out: Vec<DraftRef> = Vec::new();
    for e in entries.flatten() {
        let p = e.path();
        if !p.is_file() {
            continue;
        }
        let filename = match p.file_name().and_then(|n| n.to_str()) {
            Some(n) => n.to_string(),
            None => continue,
        };
        // 跳 tmp 半成品
        if filename.ends_with(".tmp") {
            continue;
        }
        let meta = match e.metadata() {
            Ok(m) => m,
            Err(_) => continue,
        };
        let modified_at = match meta.modified() {
            Ok(t) => {
                let dt: chrono::DateTime<chrono::Utc> = t.into();
                dt.to_rfc3339()
            }
            Err(_) => continue,
        };
        out.push(DraftRef {
            filename,
            abs_path: p.to_string_lossy().to_string(),
            modified_at,
            bytes: meta.len(),
        });
    }
    out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
    Ok(out)
}

/// 调系统默认编辑器打开草稿. macOS = `open <path>`, 跟 Finder 双击同效.
///
/// 5/22 鸿波二修: 之前 `open` 命令对不存在的文件返 exit 1, 但前端 `console.warn` 吞了,
/// 员工看到 UI 显"草稿已打开" 而实际没弹编辑器. 加 pre-check 文件存在, 给具体错让
/// 前端能告诉员工是 LLM 编了 path 还是真打不开.
#[tauri::command]
pub async fn draft_open_in_editor(abs_path: String) -> Result<(), String> {
    // 防滥用: 只允许 outputs/ 下的路径
    let outputs = outputs_root()?;
    let outputs_str = outputs.to_string_lossy().to_string();
    if !abs_path.starts_with(&outputs_str) {
        return Err(format!("路径不在 outputs/ 下, 拒打开: {abs_path}"));
    }
    // 防 path traversal
    if abs_path.contains("..") {
        return Err(format!("路径含 ..: {abs_path}"));
    }

    // 5/22 二修: pre-check 文件存在 — open 命令对不存在文件虽然返非 0,
    // 但 stderr 可能空, 错信息不直观. 这里给清晰的中文错让前端能 hint
    // "LLM 编了路径没真落盘"
    let path = std::path::Path::new(&abs_path);
    if !path.exists() {
        return Err(format!(
            "文件不存在: {abs_path}. \
            可能 LLM 输出 draftPath 但没真调 catfish_draft_email_reply 落盘. \
            可在 chat 让 catfish 重新起草."
        ));
    }
    if !path.is_file() {
        return Err(format!("不是文件 (是目录?): {abs_path}"));
    }

    #[cfg(target_os = "macos")]
    {
        let out = std::process::Command::new("open")
            .arg(&abs_path)
            .output()
            .map_err(|e| format!("调 open 失败: {e}"))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr);
            return Err(format!(
                "macOS open 返非 0 (.md 默认应用关联可能挂): {}. \
                试在 Finder 双击 {abs_path} 看默认应用是啥.",
                if stderr.trim().is_empty() {
                    "(stderr 为空)".to_string()
                } else {
                    stderr.to_string()
                }
            ));
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        return Err("draft_open_in_editor 当前只支持 macOS".to_string());
    }
    Ok(())
}

// ── BL-X (5/26): chat timeout 自显本地 outputs ──────────────────────
//
// 5/26 audit 砍掉 gateway recent_outputs.list_recent (gateway 不再扫员工
// ~/.catfish/outputs/). 替代方案: Companion (跑员工 mac) 自己扫, 在 chat
// timeout 时 toast 列过去 N 小时改过的文件, 让员工看到鲶鱼写过哪些东西
// (而不是误以为白干).
//
// 跟 draft_list_today 区别: 这个跨日期目录 (outputs/<date>/), 按 mtime ≤ N
// 小时过滤, 不限当天.

/// `recent_outputs_list(24)` → 过去 24 小时改过的 outputs 文件, mtime 倒序.
///
/// 扫 `~/.catfish/outputs/*/` 下所有文件 (跨日期目录), 不递归更深.
/// 没目录 / 空 → 空 Vec. .tmp 跳过.
#[tauri::command]
pub async fn recent_outputs_list(hours: u64) -> Result<Vec<DraftRef>, String> {
    let root = outputs_root()?;
    if !root.exists() {
        return Ok(Vec::new());
    }
    let cutoff = std::time::SystemTime::now()
        .checked_sub(std::time::Duration::from_secs(hours.saturating_mul(3600)))
        .ok_or_else(|| "hours 太大 SystemTime 减法溢出".to_string())?;

    let mut out: Vec<DraftRef> = Vec::new();
    // 一层日期目录 (outputs/<YYYY-MM-DD>/)
    let date_dirs = std::fs::read_dir(&root)
        .map_err(|e| format!("read_dir {} 失败: {e}", root.display()))?;
    for date_entry in date_dirs.flatten() {
        let date_path = date_entry.path();
        if !date_path.is_dir() {
            continue;
        }
        // 二层文件
        let files = match std::fs::read_dir(&date_path) {
            Ok(it) => it,
            Err(_) => continue,
        };
        for file_entry in files.flatten() {
            let p = file_entry.path();
            if !p.is_file() {
                continue;
            }
            let filename = match p.file_name().and_then(|n| n.to_str()) {
                Some(n) => n.to_string(),
                None => continue,
            };
            if filename.ends_with(".tmp") {
                continue;
            }
            let meta = match file_entry.metadata() {
                Ok(m) => m,
                Err(_) => continue,
            };
            let mtime = match meta.modified() {
                Ok(t) => t,
                Err(_) => continue,
            };
            if mtime < cutoff {
                continue;
            }
            let dt: chrono::DateTime<chrono::Utc> = mtime.into();
            out.push(DraftRef {
                filename,
                abs_path: p.to_string_lossy().to_string(),
                modified_at: dt.to_rfc3339(),
                bytes: meta.len(),
            });
        }
    }
    out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
    Ok(out)
}

// ── P3.3.62 (6/13 鸿波): 草稿接 Mail.app Drafts 链路 ─────────────────
//
// 三·沟通能力闭环: advisor LLM 调 catfish_draft_email_reply tool → 落
// ~/.catfish/outputs/<today>/reply-*.md (advisor_drafts.py:65)
// → 员工看见 TodayDraftsCard → 一键放 Mail.app 草稿箱 (调 email_create_draft)
// → 员工自己审 / 改 / 发. AI 永不代发 (manifesto 公理 4 红线).
//
// 这一层提供:
//   - draft_parse_md: 读 .md, parse advisor header (kind / tone / recipient /
//     subject / thread_id / created_at) + body
//   - draft_delete_md: 删本机草稿 (path-traversal guard, UI 8s confirming)

/// 草稿 markdown parse 出来的结构化字段.
///
/// kind 区分 3 类 advisor tool 产出 (advisor_drafts.py):
///   - "reply" (reply-*.md): 有 recipient / subject / thread_id, 能放 Mail.app
///   - "meeting-brief" (meeting-brief-*.md): 有 event 标题, 不能放邮件
///   - "followup" (followup-*.md): 项目催办, 不能直接放邮件
///   - "unknown": 其它员工手贴的 .md, 仅作通用展开
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ParsedDraft {
    /// 草稿类型 ("reply" / "meeting-brief" / "followup" / "unknown")
    pub kind: String,
    /// tone (仅 reply 有, e.g. "balanced" / "strict")
    pub tone: Option<String>,
    /// 收件人 (reply 才有)
    pub recipient: Option<String>,
    /// 邮件主题 (reply 才有)
    pub subject: Option<String>,
    /// thread_id (reply 才有)
    pub thread_id: Option<String>,
    /// 会议标题 (meeting-brief 才有)
    pub event_title: Option<String>,
    /// 项目名 (followup 才有)
    pub project: Option<String>,
    /// header 里 "catfish 起草时间" (ISO-8601)
    pub created_at: Option<String>,
    /// 合规提示 (reply 才有, 可空)
    pub compliance_notes: Vec<String>,
    /// 待员工确认 (meeting-brief 才有, 可空)
    pub uncertain_points: Vec<String>,
    /// 正文 (---  分割后的 body, 不含 header)
    pub body: String,
}

/// guard: 路径必须在 ~/.catfish/outputs/ 下, 不含 .., 文件存在.
fn guard_outputs_path(abs_path: &str) -> Result<std::path::PathBuf, String> {
    let outputs = outputs_root()?;
    let outputs_str = outputs.to_string_lossy().to_string();
    // 关键: 前缀必须带尾 "/", 否则 "<...>/outputs-evil/foo" 会假阳通过.
    let outputs_prefix = format!("{}/", outputs_str);
    if !abs_path.starts_with(&outputs_prefix) {
        return Err(format!("路径不在 outputs/ 下, 拒操作: {abs_path}"));
    }
    if abs_path.contains("..") {
        return Err(format!("路径含 ..: {abs_path}"));
    }
    let p = std::path::PathBuf::from(abs_path);
    if !p.exists() {
        return Err(format!("文件不存在: {abs_path}"));
    }
    if !p.is_file() {
        return Err(format!("不是文件: {abs_path}"));
    }
    Ok(p)
}

/// 从 markdown 行抽 `- key: value` 形式的 header 字段 (容错: 含中文 key).
///
/// 输入 line 例: "- 收件人: alice@example.com"
/// 返 ("收件人", "alice@example.com"); 非 `- k: v` 形式 → None.
fn parse_header_line(line: &str) -> Option<(String, String)> {
    let s = line.trim_start();
    let s = s.strip_prefix("- ")?;
    let (k, v) = s.split_once(':')?;
    Some((k.trim().to_string(), v.trim().to_string()))
}

/// parse advisor 起草的 .md (3 类 + unknown fallback).
///
/// 格式 (advisor_drafts.py:54):
///   # 邮件回信草稿 (balanced)         ← title 行, 含 tone
///   - 收件人: ...                      ← header keys
///   - 主题: ...
///   - thread_id: ...
///   - catfish 起草时间: ...
///   (可选)
///   ## 合规提示                         ← 子 section
///   - ...
///   ---                                ← 分割
///   (body)
#[tauri::command]
pub async fn draft_parse_md(abs_path: String) -> Result<ParsedDraft, String> {
    let path = guard_outputs_path(&abs_path)?;
    let raw = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 .md 失败: {e}"))?;

    // kind 从文件名前缀判
    let filename = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("");
    let kind = if filename.starts_with("reply-") {
        "reply"
    } else if filename.starts_with("meeting-brief-") {
        "meeting-brief"
    } else if filename.starts_with("followup-") {
        "followup"
    } else {
        "unknown"
    }
    .to_string();

    // 分 header 段 / body 段 — 用第一个独立 "---" 行
    let (header_part, body_part) = match raw.split_once("\n---\n") {
        Some((h, b)) => (h, b.trim_start_matches('\n')),
        None => (raw.as_str(), ""), // 没 ---, 退化: 当全是 header / 当全是 body 都不太对
                                    // 退化为: 整个当 body, header 解析空
    };

    let mut tone: Option<String> = None;
    let mut recipient: Option<String> = None;
    let mut subject: Option<String> = None;
    let mut thread_id: Option<String> = None;
    let mut event_title: Option<String> = None;
    let mut project: Option<String> = None;
    let mut created_at: Option<String> = None;
    let mut compliance_notes: Vec<String> = Vec::new();
    let mut uncertain_points: Vec<String> = Vec::new();

    // 第一行 `# ... (tone)` 抽 tone (reply 才有 — meeting-brief / followup 不带括号)
    let first_line = header_part.lines().next().unwrap_or("");
    if kind == "reply" {
        if let Some(start) = first_line.rfind('(') {
            if let Some(end) = first_line[start..].find(')') {
                let t = first_line[start + 1..start + end].trim().to_string();
                if !t.is_empty() {
                    tone = Some(t);
                }
            }
        }
    }

    // 扫 header 行 — 兼容 "## 合规提示" / "## ⚠️ 待员工确认的内容点" 子 section
    let mut current_subsection: Option<&str> = None;
    for line in header_part.lines() {
        let trimmed = line.trim_end();
        if trimmed.starts_with("## ") {
            // 判子 section 类型
            current_subsection = if trimmed.contains("合规") {
                Some("compliance")
            } else if trimmed.contains("待员工确认") {
                Some("uncertain")
            } else {
                None
            };
            continue;
        }
        if let Some((k, v)) = parse_header_line(trimmed) {
            // 子 section 里的 `- xxx` 进对应 vec; header 主区 `- key: value` 进字段
            match current_subsection {
                Some("compliance") => compliance_notes.push(format!("{k}: {v}")),
                Some("uncertain") => uncertain_points.push(format!("{k}: {v}")),
                _ => match k.as_str() {
                    "收件人" => recipient = Some(v),
                    "主题" => subject = Some(v),
                    "thread_id" => thread_id = Some(v),
                    "会议" => event_title = Some(v),
                    "event_id" => {} // 元数据, 不暴露给 UI
                    "项目" => project = Some(v),
                    "源决议" => {} // 仅 followup, 暂不展示
                    "catfish 起草时间" => created_at = Some(v),
                    _ => {}
                },
            }
        } else if current_subsection.is_some() && trimmed.starts_with("- ") {
            // 子 section 里的 "- 纯字符串"  (无 key: value)
            let item = trimmed.trim_start_matches("- ").to_string();
            match current_subsection {
                Some("compliance") => compliance_notes.push(item),
                Some("uncertain") => uncertain_points.push(item),
                _ => {}
            }
        }
    }

    Ok(ParsedDraft {
        kind,
        tone,
        recipient,
        subject,
        thread_id,
        event_title,
        project,
        created_at,
        compliance_notes,
        uncertain_points,
        body: body_part.trim_end().to_string(),
    })
}

/// 删本机草稿 (员工在 TodayDraftsCard 点 8s confirming 红钮后调).
///
/// 红线:
///   - 仅 outputs/ 下文件能删 (guard_outputs_path)
///   - 不递归, 不删目录
///   - 删完写一条 audit (decisions.jsonl 同条) — TODO 留给后续, 当前先直删
#[tauri::command]
pub async fn draft_delete_md(abs_path: String) -> Result<(), String> {
    let path = guard_outputs_path(&abs_path)?;
    std::fs::remove_file(&path)
        .map_err(|e| format!("删 draft 失败 ({}): {e}", path.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::test_env::ENV_LOCK;

    #[test]
    fn safe_filename_rejects_traversal() {
        assert!(safe_filename("ok-file.md").is_ok());
        assert!(safe_filename("../escape.md").is_err());
        assert!(safe_filename("dir/file.md").is_err());
        assert!(safe_filename("").is_err());
        assert!(safe_filename(&"x".repeat(201)).is_err());
    }

    #[test]
    fn date_dir_rejects_malformed() {
        assert!(date_dir("2026-05-22").is_ok());
        assert!(date_dir("../etc").is_err());
        assert!(date_dir("2026/05/22").is_err());  // / 被过滤
        assert!(date_dir("20260522").is_err());   // 长度不对
    }

    // ── P3.3.62: parse_header_line ────────────────────────────────────

    #[test]
    fn parse_header_line_basic() {
        assert_eq!(
            parse_header_line("- 收件人: alice@example.com"),
            Some(("收件人".into(), "alice@example.com".into()))
        );
        assert_eq!(
            parse_header_line("- thread_id: msg-123"),
            Some(("thread_id".into(), "msg-123".into()))
        );
    }

    #[test]
    fn parse_header_line_with_colon_in_value() {
        // 值里含 : 不应被切, split_once 只切第一个
        assert_eq!(
            parse_header_line("- 主题: Re: 关于 Q4 评审"),
            Some(("主题".into(), "Re: 关于 Q4 评审".into()))
        );
    }

    #[test]
    fn parse_header_line_rejects_non_list() {
        assert_eq!(parse_header_line("普通行"), None);
        assert_eq!(parse_header_line("# 标题"), None);
        assert_eq!(parse_header_line("## 子段"), None);
        assert_eq!(parse_header_line("- 无冒号"), None);
    }

    // ── draft_parse_md: 跨 advisor_drafts.py 3 类 ─────────────────────

    /// 仿照 advisor_drafts.py:54 draft_email_reply 输出格式造 reply 草稿.
    fn make_reply_md() -> String {
        "# 邮件回信草稿 (balanced)\n\
         \n\
         - 收件人: alice@example.com\n\
         - 主题: Re: Q4 评审\n\
         - thread_id: msg-abc-123\n\
         - catfish 起草时间: 2026-06-13T10:00:00+08:00\n\
         \n\
         ## 合规提示\n\
         \n\
         - 涉及报价, 请确认对外口径\n\
         \n\
         ---\n\
         \n\
         Alice 你好,\n\
         \n\
         Q4 评审材料已附上, 请查收.\n\
         \n\
         鸿波\n"
            .into()
    }

    fn make_meeting_brief_md() -> String {
        "# 会议汇报材料草稿\n\
         \n\
         - 会议: Q4 经管会\n\
         - event_id: cal-evt-9\n\
         - catfish 起草时间: 2026-06-13T09:00:00+08:00\n\
         \n\
         ## ⚠️ 待员工确认的内容点\n\
         \n\
         - 营收口径是含税还是不含税\n\
         - 客户数是签约 vs 活跃\n\
         \n\
         ---\n\
         \n\
         ## 经营回顾\n\
         \n\
         本季度营收 X 元 (含税)...\n"
            .into()
    }

    fn make_followup_md() -> String {
        "# 项目催办名单 — 鲶鱼线\n\
         \n\
         - 项目: 鲶鱼线\n\
         - 源决议: 6/5 周会 #3\n\
         - catfish 起草时间: 2026-06-13T11:00:00+08:00\n\
         \n\
         ---\n\
         \n\
         ## 张三\n\
         \n\
         请补 P3.3.51 单测覆盖.\n"
            .into()
    }

    /// 把生成的 .md 写到临时 outputs/<today>/ 后跑 draft_parse_md.
    ///
    /// 注意: guard_outputs_path 检查 outputs_root() 是 $HOME/.catfish/outputs/,
    /// 单测不能动员工本机, 这里用 setenv HOME 改到 tempdir.
    async fn parse_md_in_tmp(filename: &str, content: &str) -> ParsedDraft {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("HOME", tmp.path());
        let outputs = outputs_root().expect("outputs_root");
        let date_dir = outputs.join("2026-06-13");
        std::fs::create_dir_all(&date_dir).expect("mkdir");
        let path = date_dir.join(filename);
        std::fs::write(&path, content).expect("write");
        draft_parse_md(path.to_string_lossy().into())
            .await
            .expect("parse")
    }

    #[tokio::test]
    async fn parse_reply_full_fields() {
        let p = parse_md_in_tmp("reply-alice-balanced.md", &make_reply_md()).await;
        assert_eq!(p.kind, "reply");
        assert_eq!(p.tone.as_deref(), Some("balanced"));
        assert_eq!(p.recipient.as_deref(), Some("alice@example.com"));
        assert_eq!(p.subject.as_deref(), Some("Re: Q4 评审"));
        assert_eq!(p.thread_id.as_deref(), Some("msg-abc-123"));
        assert_eq!(
            p.created_at.as_deref(),
            Some("2026-06-13T10:00:00+08:00")
        );
        assert_eq!(p.compliance_notes.len(), 1);
        assert!(p.compliance_notes[0].contains("涉及报价"));
        assert!(p.body.contains("Alice 你好"));
        assert!(p.body.contains("Q4 评审材料"));
        // body 里不该混 header
        assert!(!p.body.contains("收件人:"));
        assert!(!p.body.contains("## 合规提示"));
    }

    #[tokio::test]
    async fn parse_meeting_brief() {
        let p = parse_md_in_tmp("meeting-brief-Q4经管会.md", &make_meeting_brief_md()).await;
        assert_eq!(p.kind, "meeting-brief");
        assert_eq!(p.tone, None); // meeting-brief 没 tone
        assert_eq!(p.event_title.as_deref(), Some("Q4 经管会"));
        assert_eq!(p.uncertain_points.len(), 2);
        assert!(p.uncertain_points[0].contains("营收口径"));
        assert!(p.body.contains("经营回顾"));
    }

    #[tokio::test]
    async fn parse_followup() {
        let p = parse_md_in_tmp("followup-鲶鱼线.md", &make_followup_md()).await;
        assert_eq!(p.kind, "followup");
        assert_eq!(p.project.as_deref(), Some("鲶鱼线"));
        assert!(p.body.contains("张三"));
        assert!(p.body.contains("P3.3.51"));
    }

    #[tokio::test]
    async fn parse_unknown_falls_back() {
        let p = parse_md_in_tmp("random-note.md", "just some text\n").await;
        assert_eq!(p.kind, "unknown");
        // 没 --- 退化: body 进退化路径
        // (split_once 找不到 \n---\n, 退化 body_part = "")
        // 不强检 body, 主要看 kind 正确
    }

    #[tokio::test]
    async fn parse_handles_subject_with_colon() {
        // 主题里含 ":" — split_once(":") 必须只切第一个
        let md = "# 邮件回信草稿 (formal)\n\n\
                  - 收件人: bob@x.com\n\
                  - 主题: Re: Re: 跨季对账\n\
                  - thread_id: t-1\n\
                  - catfish 起草时间: 2026-06-13T10:00:00+08:00\n\
                  \n---\n\n\
                  body...\n";
        let p = parse_md_in_tmp("reply-bob-formal.md", md).await;
        assert_eq!(p.subject.as_deref(), Some("Re: Re: 跨季对账"));
    }

    // ── draft_delete_md / guard_outputs_path 红线 ──────────────────────

    #[tokio::test]
    async fn delete_rejects_path_outside_outputs() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("HOME", tmp.path());
        // 放一个非 outputs/ 下的文件
        let outside = tmp.path().join("evil.txt");
        std::fs::write(&outside, "x").expect("write");
        let err = draft_delete_md(outside.to_string_lossy().into())
            .await
            .expect_err("应拒绝");
        assert!(err.contains("不在 outputs/"), "实际 err: {err}");
        // 文件不动
        assert!(outside.exists());
    }

    #[tokio::test]
    async fn delete_rejects_traversal() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("HOME", tmp.path());
        let outputs = outputs_root().unwrap();
        let date_dir = outputs.join("2026-06-13");
        std::fs::create_dir_all(&date_dir).expect("mkdir");
        // 关键: 真在 outputs 上一级放个文件, 然后从 outputs/.. 引用它,
        // 若 guard 只 starts_with 不 reject .., 就会被穿越逃出去
        let escape_path = outputs.parent().unwrap().join("escape.md");
        std::fs::write(&escape_path, "should-not-touch").expect("write");
        // 构造 outputs/.../../../escape.md 形式 (含 ..)
        let bad = format!("{}/../escape.md", outputs.display());
        let err = draft_delete_md(bad).await.expect_err("应拒绝");
        assert!(
            err.contains("..") || err.contains("不在 outputs"),
            "实际 err: {err}"
        );
        // 关键: escape.md 不能被删
        assert!(escape_path.exists());
    }

    #[tokio::test]
    async fn delete_succeeds_for_valid_draft() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("HOME", tmp.path());
        let outputs = outputs_root().unwrap();
        let date_dir = outputs.join("2026-06-13");
        std::fs::create_dir_all(&date_dir).expect("mkdir");
        let path = date_dir.join("reply-x-y.md");
        std::fs::write(&path, "hi").expect("write");
        assert!(path.exists());
        draft_delete_md(path.to_string_lossy().into())
            .await
            .expect("应成功");
        assert!(!path.exists());
    }
}
