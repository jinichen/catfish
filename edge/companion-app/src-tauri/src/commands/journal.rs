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
    /// 抽取来源 markdown 形式: "checkbox" | "inline"
    pub source: String,
    /// 所在日期段标题 (e.g., "2026-05-19" 或 "本周周报"), 没匹配到段返空
    pub section: String,
    /// 5/21 加: 员工 markdown 里 ⭐ / 🔝 / "重点:" 前缀 → is_priority=true.
    /// text 字段已去除前缀; UI 看到 is_priority 自己打 ⭐ 标. 早安播报 priority 项排序置顶.
    /// 跟 Python core.py is_priority 字段对齐.
    pub is_priority: bool,
    /// P3.4.7a (6/15 鸿波): 来源文件 — "weekly" (current_todos.md 本周待办) |
    /// "journal" (employee_journal.md 流水帐, 向后兼容).
    ///
    /// 背景: 鸿波 hermes MEMORY 里写过 "current_todos.md 本周待办文件, 每周日 reset"
    /// 这条设计 — catfish 代码里 0 引用 ("current_todos" grep 0 命中). P3.4.7
    /// 落地这条业务设计, journal_todos_fetch 双源合并, weekly 优先.
    ///
    /// 默认值 "" — extract_todos 内部不知道来源, 由 journal_todos_fetch 后置覆盖.
    /// 单测 cover extract_todos 时不动 origin (单测断言 origin 字段不破坏).
    #[serde(default)]
    pub origin: String,
}

/// employee_journal.md — 流水帐, 早期 BL-JOURNAL-TODO-EXTRACT (5/20) 用作 TODO 源.
/// P3.4.7a (6/15): 退到向后兼容位置, 主 TODO 源换 current_todos.md.
fn journal_path() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".catfish/employee_journal.md"))
}

/// P3.4.7a (6/15 鸿波): current_todos.md — 本周待办主 TODO 源.
///
/// 鸿波 hermes MEMORY 里写过的设计: "每周日 reset, 过周未完成的 TODO 自动带入新一周,
/// 已完成的清掉". reset 逻辑在 P3.4.7c 做, 这里只给路径.
fn current_todos_path() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".catfish/current_todos.md"))
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
///
/// P3.4.7b (6/15 鸿波): origin 路由 — 双源 TODO 后 line 号在两文件下歧义, 必须按 origin 路由:
///   - origin="weekly" → ~/.catfish/current_todos.md
///   - origin="journal" → ~/.catfish/employee_journal.md
///   - origin=None (老 caller 兼容) → 双文件试, 先 weekly 后 journal
#[tauri::command]
pub async fn journal_mark_todo_done(
    line: u32,
    text_hint: String,
    origin: Option<String>,
) -> Result<String, String> {
    dispatch_todo_op(origin.as_deref(), |path| {
        try_mark_done_on_file(path, line, &text_hint)
    })
}

/// 删 TODO 整行 (员工说"不做了 / 取消"). 同双重定位.
///
/// P3.4.7b (6/15 鸿波): origin 路由跟 journal_mark_todo_done 同款.
#[tauri::command]
pub async fn journal_delete_todo(
    line: u32,
    text_hint: String,
    origin: Option<String>,
) -> Result<String, String> {
    dispatch_todo_op(origin.as_deref(), |path| {
        try_delete_on_file(path, line, &text_hint)
    })
}

/// P3.4.7b (6/15 鸿波): mark_done / delete 共享路由逻辑.
///
/// origin 给 → 单文件操作 (失败直接返 Err, 不跨文件试 — 防误改另一文件)
/// origin=None → 双文件试, 先 current_todos.md 后 employee_journal.md, 谁先成功算谁.
///   两个都失败时合并两个 err 给 caller debug.
///
/// 副作用安全: try_* helper 失败时不写文件 (text_hint/regex 校验都在 fs::write 前),
/// 所以"先试 weekly 失败再试 journal" 不会误改 weekly.
fn dispatch_todo_op<F>(origin: Option<&str>, op: F) -> Result<String, String>
where
    F: Fn(&PathBuf) -> Result<String, String>,
{
    match origin {
        Some("weekly") => {
            let path = current_todos_path().ok_or_else(|| "HOME 没设".to_string())?;
            op(&path)
        }
        Some("journal") => {
            let path = journal_path().ok_or_else(|| "HOME 没设".to_string())?;
            op(&path)
        }
        Some(other) => Err(format!("origin 取值非法 (要 'weekly' / 'journal' / 不传): {other}")),
        None => {
            // 老 caller (TodosDetail 调没 origin 时) — 双文件试
            let cur = current_todos_path().ok_or_else(|| "HOME 没设".to_string())?;
            let jrn = journal_path().ok_or_else(|| "HOME 没设".to_string())?;
            match op(&cur) {
                Ok(msg) => Ok(msg),
                Err(e_cur) => match op(&jrn) {
                    Ok(msg) => Ok(msg),
                    Err(e_jrn) => Err(format!(
                        "双文件都没命中 (origin 没传, 老 caller 兼容路径). \
                         current_todos.md: {e_cur} | employee_journal.md: {e_jrn}"
                    )),
                },
            }
        }
    }
}

/// P3.4.7b (6/15 鸿波): 单文件标完成 — 原 journal_mark_todo_done body 抽出来.
fn try_mark_done_on_file(path: &PathBuf, line: u32, text_hint: &str) -> Result<String, String> {
    if !path.exists() {
        return Err(format!("{} 不存在", path.display()));
    }
    let text = fs::read_to_string(path).map_err(|e| format!("读 {} 失败: {e}", path.display()))?;
    let mut lines: Vec<String> = text.lines().map(String::from).collect();

    let idx = (line as usize).checked_sub(1).ok_or_else(|| "line 必须 >= 1".to_string())?;
    let line_str = lines.get(idx).ok_or_else(|| {
        format!("{} 只有 {} 行, line {} 越界", path.display(), lines.len(), line)
    })?;

    if !line_str.contains(text_hint) {
        return Err(format!(
            "text_hint 跟 {} line {} 不匹配 (文件可能被员工自己改了, 调 journal_todos_fetch 重拉). 该行内容: {:?}",
            path.display(),
            line,
            line_str.chars().take(60).collect::<String>()
        ));
    }

    let checkbox_pattern = regex::Regex::new(r"^(\s*[-*+]\s*)\[\s\](\s+.+)$").unwrap();
    let new_line = match checkbox_pattern.captures(line_str) {
        Some(cap) => format!("{}[x]{}", &cap[1], &cap[2]),
        None => {
            return Err(format!(
                "{} line {} 不是未完成 checkbox 行 (要求 '- [ ] xxx' 格式). 内容: {:?}",
                path.display(),
                line,
                line_str.chars().take(60).collect::<String>()
            ));
        }
    };

    lines[idx] = new_line;
    let new_content = lines.join("\n") + if text.ends_with('\n') { "\n" } else { "" };
    fs::write(path, &new_content).map_err(|e| format!("写 {} 失败: {e}", path.display()))?;
    Ok(format!(
        "✅ 标已完成: {} line {} \"{}\"",
        path.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
        line,
        text_hint
    ))
}

/// P3.4.7b (6/15 鸿波): 单文件删 — 原 journal_delete_todo body 抽出来.
fn try_delete_on_file(path: &PathBuf, line: u32, text_hint: &str) -> Result<String, String> {
    if !path.exists() {
        return Err(format!("{} 不存在", path.display()));
    }
    let text = fs::read_to_string(path).map_err(|e| format!("读 {} 失败: {e}", path.display()))?;
    let mut lines: Vec<String> = text.lines().map(String::from).collect();

    let idx = (line as usize).checked_sub(1).ok_or_else(|| "line 必须 >= 1".to_string())?;
    let line_str = lines.get(idx).ok_or_else(|| {
        format!("{} 只有 {} 行, line {} 越界", path.display(), lines.len(), line)
    })?;

    if !line_str.contains(text_hint) {
        return Err(format!(
            "text_hint 跟 {} line {} 不匹配 (文件可能被员工改过, 调 journal_todos_fetch 重拉). 该行内容: {:?}",
            path.display(),
            line,
            line_str.chars().take(60).collect::<String>()
        ));
    }

    let is_checkbox = regex::Regex::new(r"^\s*[-*+]\s*\[[ xX]\]\s+.+$").unwrap().is_match(line_str);
    let is_inline_todo = regex::Regex::new(r"(?i)(?:^|\s)(?:todo|待办)\s*[:：\-]").unwrap().is_match(line_str);
    if !is_checkbox && !is_inline_todo {
        return Err(format!(
            "{} line {} 不是 TODO 行 (不许删非 TODO 内容防误伤). 内容: {:?}",
            path.display(),
            line,
            line_str.chars().take(60).collect::<String>()
        ));
    }

    let deleted = lines.remove(idx);
    let new_content = lines.join("\n") + if text.ends_with('\n') { "\n" } else { "" };
    fs::write(path, &new_content).map_err(|e| format!("写 {} 失败: {e}", path.display()))?;
    Ok(format!(
        "🗑 已删: {} \"{}\"",
        path.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
        deleted.chars().take(60).collect::<String>()
    ))
}

/// 追加新 TODO. 默认写 current_todos.md (本周待办主源, P3.4.7b 鸿波拍).
///
/// 路由 (P3.4.7b 6/15 鸿波):
///   - 显式 origin="weekly" → ~/.catfish/current_todos.md
///   - 显式 origin="journal" → ~/.catfish/employee_journal.md
///   - 隐式 (老 caller, origin=None):
///       · section 非空 → journal (section 概念在流水帐 ## 日期段 才有意义)
///       · section 空/不传 → current_todos.md (默认本周待办)
///
/// 幂等性 (BL-CATFISH-TODO-SYNC v0.1.7, P3.4.7b 跨文件扩展):
///   跨 current_todos.md + employee_journal.md 双文件查重 — 任一文件有相同 text
///   的 - [ ] / - [x] checkbox 就跳过, 防 catfish-todo-sync 多次 sync 重复.
#[tauri::command]
pub async fn journal_add_todo(
    text: String,
    section: Option<String>,
    origin: Option<String>,
) -> Result<String, String> {
    let trimmed_text = text.trim().to_string();
    if trimmed_text.is_empty() {
        return Err("text 不能为空".to_string());
    }

    // 路由目标文件
    let target_path = resolve_target_path(origin.as_deref(), section.as_deref())?;

    // 没文件先建空文件
    if !target_path.exists() {
        if let Some(parent) = target_path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("建 ~/.catfish 失败: {e}"))?;
        }
        fs::write(&target_path, "").map_err(|e| format!("建文件 {} 失败: {e}", target_path.display()))?;
    }

    // 跨双文件查重 (P3.4.7b): 任一文件已有同 text checkbox 跳过
    let cur = current_todos_path().ok_or_else(|| "HOME 没设".to_string())?;
    let jrn = journal_path().ok_or_else(|| "HOME 没设".to_string())?;
    if todo_already_exists(&trimmed_text, &[&cur, &jrn]) {
        return Ok(format!(
            "⏭ 已存在跳过 (跨 current_todos.md + employee_journal.md): \"{}\"",
            trimmed_text.chars().take(60).collect::<String>()
        ));
    }

    let mut content = fs::read_to_string(&target_path)
        .map_err(|e| format!("读 {} 失败: {e}", target_path.display()))?;
    let new_todo = format!("- [ ] {}", trimmed_text);

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

    fs::write(&target_path, &final_content)
        .map_err(|e| format!("写 {} 失败: {e}", target_path.display()))?;
    Ok(format!(
        "📝 已加到 {}: \"{}\"",
        target_path.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
        trimmed_text.chars().take(60).collect::<String>()
    ))
}

/// P3.4.7b (6/15 鸿波): add_todo / mark_done / delete 路由 helper.
///
/// 优先级: 显式 origin > 隐式 (section 决定 journal vs weekly) > 默认 weekly.
fn resolve_target_path(origin: Option<&str>, section: Option<&str>) -> Result<PathBuf, String> {
    match origin {
        Some("journal") => journal_path().ok_or_else(|| "HOME 没设".to_string()),
        Some("weekly") => current_todos_path().ok_or_else(|| "HOME 没设".to_string()),
        Some(other) => Err(format!(
            "origin 取值非法 (要 'weekly' / 'journal' / 不传): {other}"
        )),
        None => {
            // 兼容老 caller: section 非空 → journal (section 在流水帐才有意义), 否则 weekly
            if section.map(|s| !s.trim().is_empty()).unwrap_or(false) {
                journal_path().ok_or_else(|| "HOME 没设".to_string())
            } else {
                current_todos_path().ok_or_else(|| "HOME 没设".to_string())
            }
        }
    }
}

/// P3.4.7b (6/15 鸿波): 跨多文件检查同 text 的 checkbox 已存在.
/// 跟老 add_todo 单文件查重算法相同, 但接受 path slice 扫多个文件.
fn todo_already_exists(trimmed_text: &str, paths: &[&PathBuf]) -> bool {
    let pattern = format!(
        r"(?m)^\s*[-*+]\s*\[[ xX]\]\s+{}\s*$",
        regex::escape(trimmed_text)
    );
    let re = match regex::Regex::new(&pattern) {
        Ok(r) => r,
        Err(_) => return false,
    };
    for p in paths {
        if let Ok(content) = fs::read_to_string(p) {
            if re.is_match(&content) {
                return true;
            }
        }
    }
    false
}

/// 抽本周未完成 TODO. 返 JSON array. 没文件返 [].
///
/// P3.5.137 (6/29 鸿波 catch "archive 后早安仍 23"): **砍 journal 源**.
///
/// 真因: P3.4.7a (6/15 鸿波 design intent) 真**写真清楚** — `employee_journal.md`
/// "退到向后兼容位置, 主 TODO 源换 current_todos.md". 但 journal_todos_fetch 真**仍在**
/// 双源扫 + 合并, 跟 6/15 design 矛盾, 早安累积 23 条 (current 7 + journal 16).
///
/// P3.5.133 (6/29 鸿波 ship) archive cron 真**只清** current_todos.md 完成行, 真**不动**
/// journal — archive 后早安仍 23, 真因就在这.
///
/// 修法: 砍 journal 源, 真**只扫** current_todos.md (P3.5.133 cron 真每周日 reset).
///   - journal 文件**仍正常 read/write** (journal_read_recent / journal_mark_todo_done /
///     journal_delete_todo / dispatch_todo_op 真**仍接受** origin="journal" 真**向后兼容**)
///   - advisor 真 surface TODO 真**source** 改成 current_todos.md 真**唯一**
///   - 真**砍**真**`merge_todos_weekly_first` 函数 + 6 个单测** (鸿波 6/29 拍 "彻底删除死代码")
///
/// 性能: 50KB regex 扫一遍 < 5ms.
#[tauri::command]
pub async fn journal_todos_fetch() -> Result<String, String> {
    let weekly_todos = read_and_tag(current_todos_path(), "weekly")?;
    serde_json::to_string(&weekly_todos).map_err(|e| format!("序列化 TODO 失败: {e}"))
}

/// P3.4.7a (6/15 鸿波): 读单文件 + 抽 todos + 打 origin tag. 文件不存在 → 空 vec
/// (跟老 journal_todos_fetch "没 journal 不算错" 同语义).
fn read_and_tag(path: Option<PathBuf>, origin: &str) -> Result<Vec<JournalTodo>, String> {
    let path = match path {
        Some(p) => p,
        None => return Ok(Vec::new()),
    };
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = fs::read_to_string(&path)
        .map_err(|e| format!("读 {} 失败: {}", path.display(), e))?;
    let mut todos = extract_todos(&text);
    for t in todos.iter_mut() {
        t.origin = origin.to_string();
    }
    Ok(todos)
}

// P3.5.137 (6/29 鸿波拍 "彻底删除死代码"): merge_todos_weekly_first 函数 + 6 个单测
// 真**完全砍**. 真因:
//   1. P3.4.7a (6/15) design intent 写清楚 "journal 退到向后兼容位置" — 永久退役 advisor 路径
//   2. journal_todos_fetch 砍 journal 源后, fn 唯一 prod caller 消失 → 永久 dead
//   3. git history 保留 design 演进轨迹, source code 不需保留 dead code 做考古
//   4. 未来业务变了真**重新 audit 重新实现** 比解封 dead fn 更合理
// 回滚: git show HEAD~1 -- src/commands/journal.rs

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
                    // P3.4.7a (6/15): extract_todos 不知道来源, 由 journal_todos_fetch
                    //   后置覆盖 ("weekly" / "journal").
                    origin: String::new(),
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
                    origin: String::new(),  // P3.4.7a: 见 checkbox 分支注释
                });
            }
        }
    }

    todos
}

// ─── P3.4.7c (6/15 鸿波): 每周日 00:00 自动 reset current_todos.md ──────────
//
// 鸿波 hermes MEMORY 写过的设计落地:
//   "current_todos.md 是本周待办文件, 每周日 reset. 过周但未完成的 TODO 自动带入
//   新一周, 已完成的清掉. 不保留过期 TODO 归档."
//
// 实现:
//   1. reset 算法 reset_current_todos_text — 纯函数, 单测 cover
//   2. current_todos_weekly_reset Tauri command — 手动触发 / autostart 调
//   3. weekly_reset_marker_path + should_run_weekly_reset — autostart 自动判断
//      (跨过周一就跑, 防多次启动重复 reset)
//   4. audit chain (P3.3.51) 记 reset 事件

// 注: Serialize / PathBuf / fs / regex::Regex 都在文件顶部已 import (line 17-21).
// chrono 在函数内局部 import (use chrono::{Local, NaiveDate};) 避免顶部污染.

/// P3.4.7c (6/15): reset 算法结果 — 跟 Tauri command 返一致.
#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct WeeklyResetReport {
    /// 删掉的 - [x] 已完成行数
    pub completed_removed: usize,
    /// 保留的 - [ ] 未完成行数 (带入新一周)
    pub pending_kept: usize,
    /// 重置后的 current_todos.md 文件大小 (bytes)
    pub new_size_bytes: usize,
    /// 文件不存在 / 跳过等 (UI 显 "本周无需重置")
    pub skipped: bool,
    /// skipped=true 时的原因 (e.g. "current_todos.md 不存在")
    #[serde(default)]
    pub skipped_reason: String,
}

/// P3.4.7c (6/15 鸿波): reset 算法 — 纯函数, 单测 cover.
///
/// 规则:
///   - 删 `- [x]` / `- [X]` (含 *, + bullet, 含缩进) 已完成行
///   - 保留 `- [ ]` 未完成行 + 所有非 checkbox 行 (section title / 空行 / 普通文本)
///   - 行序保持原样, 只是删了 done 行 (上下文 section title 等不动)
///
/// 边界:
///   - 空输入 → completed=0, kept=0, new 内容空
///   - 全是 done → 删全, 留 section title (员工归零看着干净)
///   - 全是 pending → 不动 (kept = 总数)
pub(crate) fn reset_current_todos_text(content: &str) -> (String, WeeklyResetReport) {
    let completed_re = regex::Regex::new(r"^\s*[-*+]\s*\[[xX]\]\s+").unwrap();
    let pending_re = regex::Regex::new(r"^\s*[-*+]\s*\[\s\]\s+").unwrap();

    let mut kept_lines: Vec<&str> = Vec::new();
    let mut completed_removed = 0usize;
    let mut pending_kept = 0usize;

    for line in content.lines() {
        if completed_re.is_match(line) {
            completed_removed += 1;
            continue;  // 删
        }
        if pending_re.is_match(line) {
            pending_kept += 1;
        }
        // 非 todo 行 (section title / 空行 / 普通文本) 也保留 — section context 不丢
        kept_lines.push(line);
    }

    // 保留尾部 newline (跟原文件 trailing 风格一致)
    let new_content = if content.ends_with('\n') {
        kept_lines.join("\n") + "\n"
    } else {
        kept_lines.join("\n")
    };

    let report = WeeklyResetReport {
        completed_removed,
        pending_kept,
        new_size_bytes: new_content.len(),
        skipped: false,
        skipped_reason: String::new(),
    };
    (new_content, report)
}

/// P3.4.7c (6/15 鸿波): 手动 / autostart 触发的本周 reset.
///
/// 副作用:
///   - 改写 ~/.catfish/current_todos.md (删 - [x] 行)
///   - 写 ~/.catfish/.weekly_reset_last (marker, 防重复 reset)
///   - audit chain append ~/.catfish/audit/weekly_reset.jsonl (跟 P3.3.51 同模式)
///
/// 文件不存在 → skipped=true, 不算错 (员工还没建 current_todos.md, 没东西可 reset).
#[tauri::command]
pub async fn current_todos_weekly_reset() -> Result<WeeklyResetReport, String> {
    let path = current_todos_path().ok_or_else(|| "HOME 没设".to_string())?;
    if !path.exists() {
        return Ok(WeeklyResetReport {
            completed_removed: 0,
            pending_kept: 0,
            new_size_bytes: 0,
            skipped: true,
            skipped_reason: format!("{} 不存在 (本周还没建待办)", path.display()),
        });
    }

    let content = fs::read_to_string(&path)
        .map_err(|e| format!("读 {} 失败: {e}", path.display()))?;
    let (new_content, report) = reset_current_todos_text(&content);

    fs::write(&path, &new_content)
        .map_err(|e| format!("写 {} 失败: {e}", path.display()))?;

    // marker 写今天 — 防 autostart 同一周内重复跑
    if let Some(marker) = weekly_reset_marker_path() {
        let today = chrono::Local::now().date_naive().to_string();
        let _ = fs::write(&marker, today);
    }

    // audit chain append (跟 P3.3.51 / political_scan persist_audit 同模式)
    if let Ok(home) = std::env::var("HOME") {
        let audit_path = format!("{home}/.catfish/audit/weekly_reset.jsonl");
        let payload = serde_json::json!({
            "event_type": "weekly_reset",
            "date": chrono::Local::now().to_rfc3339(),
            "completed_removed": report.completed_removed,
            "pending_kept": report.pending_kept,
            "new_size_bytes": report.new_size_bytes,
        });
        if let Err(e) = super::audit_chain::chain_append_impl(audit_path, payload).await {
            log::warn!("[weekly_reset] audit chain append 失败 (不阻塞): {e}");
        }
    }

    Ok(report)
}

/// P3.4.7c (6/15): marker 文件 path — 记上次 reset 的 YYYY-MM-DD.
/// autostart 钩子读它判断是否需要新一轮 reset.
pub(crate) fn weekly_reset_marker_path() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".catfish/.weekly_reset_last"))
}

/// P3.4.7c (6/15 鸿波): autostart 钩子判断 — 是否需要跑本周 reset.
///
/// 规则: 上次 reset 的"本周一" < 现在的"本周一" → 跑.
/// 等价于: 上次 reset 之后已经跨过一个周日 → 跑.
///
/// 从没 reset 过 (marker 不存在) → 跑 (首次启动).
///
/// 用 chrono::Local 本地时区 — 员工在哪儿就按哪儿的"周日 00:00".
pub(crate) fn should_run_weekly_reset() -> bool {
    // Datelike 不需要 import — monday_of() 内部自己 import 调 .weekday().
    use chrono::{Local, NaiveDate};

    let marker = match weekly_reset_marker_path() {
        Some(p) => p,
        None => return false,
    };

    let now = Local::now().date_naive();

    let last_reset: Option<NaiveDate> = std::fs::read_to_string(&marker)
        .ok()
        .and_then(|s| NaiveDate::parse_from_str(s.trim(), "%Y-%m-%d").ok());

    match last_reset {
        None => true,  // 首次启动 / 从没 reset 过 → 跑
        Some(last) => {
            let now_monday = monday_of(now);
            let last_monday = monday_of(last);
            now_monday > last_monday
        }
    }
}

/// P3.4.7c (6/15): 给定日期的本周一 (周一为本周第一天, ISO 8601).
fn monday_of(date: chrono::NaiveDate) -> chrono::NaiveDate {
    use chrono::Datelike;
    let days_since_monday = date.weekday().num_days_from_monday() as i64;
    date - chrono::Duration::days(days_since_monday)
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

    // ── P3.4.7c (6/15 鸿波): reset_current_todos_text 算法 ──

    #[test]
    fn test_reset_empty() {
        let (out, rep) = reset_current_todos_text("");
        assert_eq!(out, "");
        assert_eq!(rep.completed_removed, 0);
        assert_eq!(rep.pending_kept, 0);
    }

    #[test]
    fn test_reset_keeps_pending_removes_done() {
        let input = "- [ ] 未完成 A\n- [x] 已完成 B\n- [ ] 未完成 C\n";
        let (out, rep) = reset_current_todos_text(input);
        assert!(out.contains("未完成 A"));
        assert!(out.contains("未完成 C"));
        assert!(!out.contains("已完成 B"));
        assert_eq!(rep.completed_removed, 1);
        assert_eq!(rep.pending_kept, 2);
    }

    #[test]
    fn test_reset_keeps_section_titles() {
        // section title 必须留 — 员工看着新一周仍有结构
        let input = "## 2026-06-15 本周待办\n- [ ] 任务 A\n- [x] 任务 B\n## 备注\n详细说明文本\n";
        let (out, _) = reset_current_todos_text(input);
        assert!(out.contains("## 2026-06-15 本周待办"));
        assert!(out.contains("## 备注"));
        assert!(out.contains("详细说明文本"));
        assert!(out.contains("- [ ] 任务 A"));
        assert!(!out.contains("- [x] 任务 B"));
    }

    #[test]
    fn test_reset_all_completed_keeps_titles_only() {
        // 全部完成 → 只剩 section title, 周一员工看着"已归零"
        let input = "## 本周\n- [x] 完成 1\n- [x] 完成 2\n- [x] 完成 3\n";
        let (out, rep) = reset_current_todos_text(input);
        assert!(out.contains("## 本周"));
        assert!(!out.contains("[x]"));
        assert_eq!(rep.completed_removed, 3);
        assert_eq!(rep.pending_kept, 0);
    }

    #[test]
    fn test_reset_all_pending_unchanged() {
        // 全部未完成 → 内容不变 (除了 trailing 风格)
        let input = "## 本周\n- [ ] 任务 A\n- [ ] 任务 B\n";
        let (out, rep) = reset_current_todos_text(input);
        assert_eq!(out, input);
        assert_eq!(rep.completed_removed, 0);
        assert_eq!(rep.pending_kept, 2);
    }

    #[test]
    fn test_reset_supports_alt_bullets_and_indent() {
        // 兼容 * / + bullet + 缩进
        let input = "  - [x] 缩进已完成\n* [X] 星号大写已完成\n+ [ ] 加号未完成\n";
        let (out, rep) = reset_current_todos_text(input);
        assert!(!out.contains("缩进已完成"));
        assert!(!out.contains("星号大写已完成"));
        assert!(out.contains("加号未完成"));
        assert_eq!(rep.completed_removed, 2);
        assert_eq!(rep.pending_kept, 1);
    }

    #[test]
    fn test_monday_of_calculation() {
        use chrono::NaiveDate;
        // 2026-06-15 = 周一 → 本周一 = 自己
        let mon = NaiveDate::from_ymd_opt(2026, 6, 15).unwrap();
        assert_eq!(monday_of(mon), mon);

        // 2026-06-21 = 周日 → 本周一 = 6-15
        let sun = NaiveDate::from_ymd_opt(2026, 6, 21).unwrap();
        assert_eq!(monday_of(sun), NaiveDate::from_ymd_opt(2026, 6, 15).unwrap());

        // 2026-06-22 = 下周一 → 本周一 = 自己
        let next_mon = NaiveDate::from_ymd_opt(2026, 6, 22).unwrap();
        assert_eq!(monday_of(next_mon), next_mon);
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
