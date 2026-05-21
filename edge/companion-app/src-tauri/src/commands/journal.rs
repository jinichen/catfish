//! 日记 TODO 抽取 — BL-JOURNAL-TODO-EXTRACT (5/20 鸿波).
//!
//! 读 ~/.catfish/employee_journal.md, 抽未完成 TODO 项给 BriefingCard 渲染.
//!
//! step1 (本提交) regex 抽取, 两种 pattern:
//!   - `- [ ] 任务描述`   markdown checkbox 未完成
//!   - `TODO: 任务描述`   / `待办: 任务描述`  行内标记
//! 已完成 `- [x] ...` 自动跳过.
//!
//! step2 (后续): LLM 调 catfish-private-vision 从自然语言句子里抽 ("明天要给老李
//! 写汇报"这种没标 TODO 的). LLM 走 gateway loopback, charter "数据不出 Mac".
//!
//! 红线 (5/17 BL-CENTRAL-EDGE-BOUNDARY):
//!   - 必须边缘端读 (Companion Rust = 边缘合规), 中央 gateway 禁读 ~/.catfish/
//!   - 不缓存到本地新文件 — journal 本来就是员工写的 md, 直接现读

use std::fs;
use std::path::PathBuf;

use regex::Regex;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct JournalTodo {
    pub text: String,
    /// 行号 (1-based) — 万一员工想跳到 journal 编辑可用
    pub line: u32,
    /// 抽取来源: "checkbox" | "inline"
    pub source: String,
    /// 所在日期段标题 (e.g., "2026-05-19" 或 "本周周报"), 没匹配到段返空
    pub section: String,
    /// 5/21 加: 员工 markdown 里 ⭐ / 🔝 / "重点:" 前缀 → is_priority=true.
    /// text 字段已去除前缀; UI 看到 is_priority 自己打 ⭐ 标. 早安播报 priority 项排序置顶.
    /// 跟 Python core.py is_priority 字段对齐.
    pub is_priority: bool,
}

fn journal_path() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".catfish/employee_journal.md"))
}

/// 读最近 N 字节 journal 内容 (BL-JOURNAL-TODO-EXTRACT step2 5/20).
///
/// 给 LLM 抽自然语言 TODO 用 — regex 只能抽显式 `- [ ]` / `TODO:`, LLM 看上下文
/// 能找"明天要给老李写汇报"这种隐式待办.
///
/// 限 5KB 防 context 爆 + 走 utf-8 char boundary 不切坏中文.
/// 没文件 → 返空字符串 (调用方 LLM 啥也抽不到自然返空).
#[tauri::command]
pub async fn journal_read_recent() -> Result<String, String> {
    const MAX_BYTES: usize = 5000;
    let path = journal_path().ok_or_else(|| "HOME 没设".to_string())?;
    if !path.exists() {
        return Ok(String::new());
    }
    let text = fs::read_to_string(&path)
        .map_err(|e| format!("读 {} 失败: {}", path.display(), e))?;

    if text.len() <= MAX_BYTES {
        return Ok(text);
    }
    // 从尾部往前 MAX_BYTES, 找最近 utf-8 char 边界 (不切坏中文)
    let start = text.len() - MAX_BYTES;
    let mut safe_start = start;
    while safe_start < text.len() && !text.is_char_boundary(safe_start) {
        safe_start += 1;
    }
    // 再往前找最近的换行 (从段中间切开员工看不懂上下文)
    let tail = &text[safe_start..];
    if let Some(nl) = tail.find('\n') {
        Ok(tail[nl + 1..].to_string())
    } else {
        Ok(tail.to_string())
    }
}

// ── BL-JOURNAL-TODO-EDIT-CHAT Stage 1 (5/20): journal CRUD 接口 ──
//
// 给后续 LLM tool calling 改 journal 用. 也可前端直接调 (备用 GUI button).
//
// 安全设计:
//   - 双重定位 line + text_hint, 不一致拒绝 (员工自己改了 journal 行号偏移防误伤)
//   - 只改 - [ ] / - [x] / 删整行, 不动其他内容 (regex 严格)
//   - add 走 append 不 splice, 不破坏既有结构
//   - 失败原子化: read → modify in memory → write back (不留中间状态)

/// 改 - [ ] → - [x] (标完成). line 1-based, text_hint 取该行 text 前 30 字 substring.
/// 不匹配 → 返 Err 让 LLM 重拉 journal_todos_fetch 再试.
#[tauri::command]
pub async fn journal_mark_todo_done(line: u32, text_hint: String) -> Result<String, String> {
    let path = journal_path().ok_or_else(|| "HOME 没设".to_string())?;
    if !path.exists() {
        return Err(format!("journal 不存在: {}", path.display()));
    }
    let text = fs::read_to_string(&path).map_err(|e| format!("读 journal 失败: {e}"))?;
    let mut lines: Vec<String> = text.lines().map(String::from).collect();

    let idx = (line as usize).checked_sub(1).ok_or_else(|| "line 必须 >= 1".to_string())?;
    let line_str = lines.get(idx).ok_or_else(|| format!("journal 只有 {} 行, line {} 越界", lines.len(), line))?;

    // text_hint 防误伤: 必须出现在该行
    if !line_str.contains(&text_hint) {
        return Err(format!(
            "text_hint 跟 line {} 不匹配 (journal 可能被员工自己改了, 调 journal_todos_fetch 重拉再试). 该行内容: {:?}",
            line, line_str.chars().take(60).collect::<String>()
        ));
    }

    // 必须是未完成 checkbox 行 (- [ ] / * [ ] / + [ ])
    let checkbox_pattern = regex::Regex::new(r"^(\s*[-*+]\s*)\[\s\](\s+.+)$").unwrap();
    let new_line = match checkbox_pattern.captures(line_str) {
        Some(cap) => {
            format!("{}[x]{}", &cap[1], &cap[2])
        }
        None => {
            return Err(format!(
                "line {} 不是未完成 checkbox 行 (要求 '- [ ] xxx' 格式). 内容: {:?}",
                line, line_str.chars().take(60).collect::<String>()
            ));
        }
    };

    lines[idx] = new_line;
    let new_content = lines.join("\n") + if text.ends_with('\n') { "\n" } else { "" };
    fs::write(&path, &new_content).map_err(|e| format!("写 journal 失败: {e}"))?;
    Ok(format!("✅ 标已完成: line {} \"{}\"", line, text_hint))
}

/// 删 TODO 整行 (员工说"不做了 / 取消"). 同双重定位.
#[tauri::command]
pub async fn journal_delete_todo(line: u32, text_hint: String) -> Result<String, String> {
    let path = journal_path().ok_or_else(|| "HOME 没设".to_string())?;
    if !path.exists() {
        return Err(format!("journal 不存在: {}", path.display()));
    }
    let text = fs::read_to_string(&path).map_err(|e| format!("读 journal 失败: {e}"))?;
    let mut lines: Vec<String> = text.lines().map(String::from).collect();

    let idx = (line as usize).checked_sub(1).ok_or_else(|| "line 必须 >= 1".to_string())?;
    let line_str = lines.get(idx).ok_or_else(|| format!("journal 只有 {} 行, line {} 越界", lines.len(), line))?;

    if !line_str.contains(&text_hint) {
        return Err(format!(
            "text_hint 跟 line {} 不匹配 (journal 可能被员工改过, 调 journal_todos_fetch 重拉). 该行内容: {:?}",
            line, line_str.chars().take(60).collect::<String>()
        ));
    }

    // 只允许删 checkbox 行 / 行内 TODO 标记行 (防 LLM 误删段标题)
    let is_checkbox = regex::Regex::new(r"^\s*[-*+]\s*\[[ xX]\]\s+.+$").unwrap().is_match(line_str);
    let is_inline_todo = regex::Regex::new(r"(?i)(?:^|\s)(?:todo|待办)\s*[:：\-]").unwrap().is_match(line_str);
    if !is_checkbox && !is_inline_todo {
        return Err(format!(
            "line {} 不是 TODO 行 (不许删非 TODO 内容防误伤). 内容: {:?}",
            line, line_str.chars().take(60).collect::<String>()
        ));
    }

    let deleted = lines.remove(idx);
    let new_content = lines.join("\n") + if text.ends_with('\n') { "\n" } else { "" };
    fs::write(&path, &new_content).map_err(|e| format!("写 journal 失败: {e}"))?;
    Ok(format!("🗑 已删: \"{}\"", deleted.chars().take(60).collect::<String>()))
}

/// 追加新 TODO 到 journal 末尾, 标准 markdown checkbox 格式.
/// section 给定 → 找该 section 下追加; 没给 → 追加到文件末尾.
#[tauri::command]
pub async fn journal_add_todo(text: String, section: Option<String>) -> Result<String, String> {
    let path = journal_path().ok_or_else(|| "HOME 没设".to_string())?;

    // 没文件先建空文件
    if !path.exists() {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("建 ~/.catfish 失败: {e}"))?;
        }
        fs::write(&path, "").map_err(|e| format!("建 journal 失败: {e}"))?;
    }

    let mut content = fs::read_to_string(&path).map_err(|e| format!("读 journal 失败: {e}"))?;
    let trimmed_text = text.trim();
    let new_todo = format!("- [ ] {}", trimmed_text);

    // 幂等性检查 (BL-CATFISH-TODO-SYNC v0.1.7, 5/20): 同 text 的 - [ ] / - [x]
    // 已存在就不重复加. 防 catfish-todo-sync plugin 多次 sync 同 todos array 重复.
    // 跟 Python core.py add_todo 算法对齐, 兼容 - * + 三种 bullet + 缩进.
    let dup_pattern = format!(
        r"(?m)^\s*[-*+]\s*\[[ xX]\]\s+{}\s*$",
        regex::escape(trimmed_text)
    );
    if let Ok(re) = regex::Regex::new(&dup_pattern) {
        if re.is_match(&content) {
            return Ok(format!(
                "⏭ 已存在跳过: \"{}\"",
                trimmed_text.chars().take(60).collect::<String>()
            ));
        }
    }

    let final_content = match section {
        Some(sec) if !sec.trim().is_empty() => {
            // 找 ## sec 段, 在该段最后插
            let section_marker = format!("## {}", sec.trim());
            if let Some(sec_start) = content.find(&section_marker) {
                // 找下一个 ## 段 (或文件末尾) 作 sec 结束
                let after_sec = sec_start + section_marker.len();
                let sec_end = content[after_sec..]
                    .find("\n## ")
                    .map(|i| after_sec + i)
                    .unwrap_or(content.len());
                let before = &content[..sec_end];
                let after = &content[sec_end..];
                // 末尾确保有换行
                let sep = if before.ends_with('\n') { "" } else { "\n" };
                format!("{before}{sep}{new_todo}\n{after}")
            } else {
                // section 不存在 → 文件末尾新建该 section
                let sep = if content.ends_with('\n') || content.is_empty() { "" } else { "\n" };
                format!("{content}{sep}\n## {}\n{new_todo}\n", sec.trim())
            }
        }
        _ => {
            // 没指定 section → 直接追加
            let sep = if content.ends_with('\n') || content.is_empty() { "" } else { "\n" };
            content = format!("{content}{sep}{new_todo}\n");
            content
        }
    };

    fs::write(&path, &final_content).map_err(|e| format!("写 journal 失败: {e}"))?;
    Ok(format!("📝 已加: \"{}\"", text.trim().chars().take(60).collect::<String>()))
}

/// 抽今天 / 最近的未完成 TODO. 返 JSON array. 没文件返 [].
///
/// 性能: 50KB journal regex 扫一遍 < 5ms, 不用 spawn_blocking.
#[tauri::command]
pub async fn journal_todos_fetch() -> Result<String, String> {
    let path = journal_path().ok_or_else(|| "HOME 没设".to_string())?;
    if !path.exists() {
        // 没 journal 不算错, 返空数组
        return Ok("[]".to_string());
    }

    let text = fs::read_to_string(&path)
        .map_err(|e| format!("读 {} 失败: {}", path.display(), e))?;

    let todos = extract_todos(&text);
    serde_json::to_string(&todos).map_err(|e| format!("序列化 TODO 失败: {e}"))
}

/// 抽 TODO 主逻辑 — pub(crate) 方便单测.
pub(crate) fn extract_todos(text: &str) -> Vec<JournalTodo> {
    // - [ ] xxx  (中括号可能两边带空格, x 必小写 = 未完成)
    let checkbox_re = Regex::new(r"^\s*[-*+]\s*\[\s\]\s+(.+?)\s*$").unwrap();
    // 行内 TODO: / 待办: / TODO - / 待办 - (兼容多种写法)
    let inline_re = Regex::new(r"(?i)(?:^|\s)(?:todo|待办)\s*[:：\-]\s*(.+?)\s*$").unwrap();
    // 段标题 (## YYYY-MM-DD - xxx / ## 任意)
    let section_re = Regex::new(r"^##\s+(.+?)\s*$").unwrap();
    // 5/21 加: priority 前缀 (⭐ / 🔝 / 重点: / 重点：). text 去前缀 + is_priority=true
    let priority_re = Regex::new(r"^(?:⭐|🔝|重点[:：]?\s*)").unwrap();

    let mut todos = Vec::new();
    let mut current_section = String::new();

    let strip_priority = |s: &str| -> (String, bool) {
        let stripped = priority_re.replace(s, "").trim().to_string();
        let orig_trimmed = s.trim();
        let is_pri = stripped != orig_trimmed;
        (stripped, is_pri)
    };

    for (idx, line) in text.lines().enumerate() {
        let lineno = (idx + 1) as u32;

        // 段标题
        if let Some(cap) = section_re.captures(line) {
            current_section = cap.get(1).map(|m| m.as_str().to_string()).unwrap_or_default();
            continue;
        }

        // checkbox 未完成
        if let Some(cap) = checkbox_re.captures(line) {
            if let Some(m) = cap.get(1) {
                let (text, is_priority) = strip_priority(m.as_str());
                todos.push(JournalTodo {
                    text,
                    line: lineno,
                    source: "checkbox".to_string(),
                    section: current_section.clone(),
                    is_priority,
                });
                continue;
            }
        }

        // 行内 TODO: / 待办:  — 但要跳过 checkbox 已经匹配的行
        // (避免 `- [ ] TODO: xxx` 被双抽)
        if let Some(cap) = inline_re.captures(line) {
            // 排除已 checkbox 行 (上面 continue 了所以这里不会重)
            // 排除 already-done checkbox: `- [x] TODO: xxx`
            if line.contains("[x]") || line.contains("[X]") {
                continue;
            }
            if let Some(m) = cap.get(1) {
                let (text, is_priority) = strip_priority(m.as_str());
                todos.push(JournalTodo {
                    text,
                    line: lineno,
                    source: "inline".to_string(),
                    section: current_section.clone(),
                    is_priority,
                });
            }
        }
    }

    todos
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_checkbox_unchecked() {
        let md = "- [ ] 给老李写汇报\n- [x] 已完成的事";
        let out = extract_todos(md);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].text, "给老李写汇报");
        assert_eq!(out[0].source, "checkbox");
    }

    #[test]
    fn test_inline_todo_zh() {
        let md = "今天: 待办: 修复 P0 bug";
        let out = extract_todos(md);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].text, "修复 P0 bug");
        assert_eq!(out[0].source, "inline");
    }

    #[test]
    fn test_section_tracked() {
        let md = "## 2026-05-19 - 周一\n- [ ] 任务 A\n## 2026-05-20\n- [ ] 任务 B";
        let out = extract_todos(md);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].section, "2026-05-19 - 周一");
        assert_eq!(out[1].section, "2026-05-20");
    }

    #[test]
    fn test_completed_skipped() {
        let md = "- [x] 已完成\n- [ ] 待办";
        let out = extract_todos(md);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].text, "待办");
    }

    #[test]
    fn test_empty_journal() {
        assert!(extract_todos("").is_empty());
    }

    // ── 5/21 加: is_priority 解析 ──────────────────────────────

    #[test]
    fn test_priority_star_prefix() {
        let out = extract_todos("- [ ] ⭐ 完成季度汇报");
        assert_eq!(out.len(), 1);
        assert!(out[0].is_priority);
        assert_eq!(out[0].text, "完成季度汇报"); // 去前缀
    }

    #[test]
    fn test_priority_top_emoji_prefix() {
        let out = extract_todos("- [ ] 🔝 给老李回复");
        assert!(out[0].is_priority);
        assert_eq!(out[0].text, "给老李回复");
    }

    #[test]
    fn test_priority_chinese_prefix() {
        let out = extract_todos("- [ ] 重点: 跑完整周报\n- [ ] 重点:写邮件");
        assert_eq!(out.len(), 2);
        assert!(out.iter().all(|t| t.is_priority));
        assert_eq!(out[0].text, "跑完整周报");
        assert_eq!(out[1].text, "写邮件");
    }

    #[test]
    fn test_priority_default_false() {
        let out = extract_todos("- [ ] 普通任务");
        assert_eq!(out.len(), 1);
        assert!(!out[0].is_priority);
    }

    #[test]
    fn test_priority_inline_todo() {
        let out = extract_todos("TODO: ⭐ 重点任务");
        assert_eq!(out.len(), 1);
        assert!(out[0].is_priority);
        assert_eq!(out[0].text, "重点任务");
    }

    // ── BL-JOURNAL-TODO-EDIT-CHAT Stage 1 (5/20): journal CRUD ──
    // 注: #[tauri::command] async fn 单测难 (Tauri runtime 依赖), 这里测核心逻辑
    // 走 in-memory string manipulation, 跟 Rust command 函数同 regex / 同算法.

    fn mark_done_inline(content: &str, line: u32, text_hint: &str) -> Result<String, String> {
        let mut lines: Vec<String> = content.lines().map(String::from).collect();
        let idx = (line as usize).checked_sub(1).ok_or("line 必须 >= 1")?;
        let line_str = lines.get(idx).ok_or("越界")?;
        if !line_str.contains(text_hint) {
            return Err("text_hint 不匹配".into());
        }
        let re = regex::Regex::new(r"^(\s*[-*+]\s*)\[\s\](\s+.+)$").unwrap();
        let new_line = re.captures(line_str)
            .map(|cap| format!("{}[x]{}", &cap[1], &cap[2]))
            .ok_or("非 checkbox 行")?;
        lines[idx] = new_line;
        Ok(lines.join("\n") + if content.ends_with('\n') { "\n" } else { "" })
    }

    #[test]
    fn test_mark_done_basic() {
        let md = "- [ ] 给老李写汇报\n- [ ] 修 P0 bug\n";
        let out = mark_done_inline(md, 1, "给老李").unwrap();
        assert!(out.contains("- [x] 给老李写汇报"));
        assert!(out.contains("- [ ] 修 P0 bug")); // 别误伤第二行
    }

    #[test]
    fn test_mark_done_text_hint_mismatch_rejected() {
        let md = "- [ ] 给老李写汇报\n";
        let result = mark_done_inline(md, 1, "不存在的字");
        assert!(result.is_err());
    }

    #[test]
    fn test_mark_done_already_done_rejected() {
        // - [x] 已完成的行不许再 mark, 避免 LLM 重复操作
        let md = "- [x] 已完成\n";
        let result = mark_done_inline(md, 1, "已完成");
        assert!(result.is_err());
    }

    #[test]
    fn test_mark_done_line_out_of_range() {
        let md = "- [ ] 只有一行\n";
        let result = mark_done_inline(md, 5, "只有");
        assert!(result.is_err());
    }

    #[test]
    fn test_mark_done_preserves_indent() {
        // markdown 嵌套 list 缩进必须保留
        let md = "  - [ ] 子任务\n";
        let out = mark_done_inline(md, 1, "子任务").unwrap();
        assert!(out.contains("  - [x] 子任务"));
    }

    // ── journal_read_recent 工具函数 utf-8 边界 ──
    // 没法跑 #[tauri::command] async fn 单测 (Tauri runtime 依赖), 但核心逻辑
    // = 找 utf-8 boundary + 找最近 \n. 这里测最常见 case: 中文不被切坏.
    #[test]
    fn test_utf8_boundary_does_not_cut_chinese() {
        // 模拟超 5KB 内容含中文, 截断后必须能 from_utf8
        let mut s = String::new();
        for _ in 0..1000 {
            s.push_str("汉字测试abc\n");  // 每行 7 utf-8 bytes (3*4 = 12 字节 + abc + \n)
        }
        // 找一个 char boundary
        let start = s.len() - 4900;
        let mut safe = start;
        while safe < s.len() && !s.is_char_boundary(safe) {
            safe += 1;
        }
        // 关键 invariant: safe_start 是 char boundary, slice 不 panic
        let _tail = &s[safe..];
        // 通过即 OK
    }
}
