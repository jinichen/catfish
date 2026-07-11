//! BL-CATFISH-WIKI-MODE P1.2.2 (6/4) — chat 真 💾 button 触发 wiki/queries/ 写盘.
//!
//! 员工对助手回答点 💾 存 wiki, 写到:
//!   ~/.catfish/wiki/queries/<YYYY-MM-DD>-<topic-slug>.md
//!
//! frontmatter (Obsidian 兼容):
//!   ---
//!   type: query
//!   sources: [chat]
//!   date: 2026-06-04
//!   time: "13:45"
//!   session_id: 20260604_130758_64dab7
//!   message_id: client-side-uuid
//!   topic: <preview 前 30 字 slugify>
//!   ---
//!
//!   # <user_message 真 前 60 字>
//!
//!   ## 问 (鸿波 @ 13:45)
//!   <user_message 真 full text>
//!
//!   ## 答 (小鲶)
//!   <assistant_response 真 full text>
//!
//! 写完待 P1.2.3 plugin watch ~/.catfish/wiki/queries/ 真 mtime 变化 真自动触发
//! Analysis + Generation pipeline 抽 entity/concept.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct WikiQueryWriteResult {
    pub path: String,
    pub bytes: u64,
}

fn home_dir() -> Result<PathBuf, String> {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME 环境变量".to_string())
}

fn queries_dir() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("wiki").join("queries"))
}

/// slugify — 替换 path-unsafe char 真 `-`, 真接受 unicode (含中文).
/// `max_chars` chars 计算 (not bytes — 真避中文 byte slice 真 panic).
fn slugify(s: &str, max_chars: usize) -> String {
    let bad: &[char] = &[
        '/', '\\', ':', '*', '?', '"', '<', '>', '|', '\n', '\r', '\t', ' ', '\u{3000}',
    ];
    let cleaned: String = s
        .chars()
        .take(max_chars)
        .map(|c| if bad.contains(&c) { '-' } else { c })
        .collect();
    let trimmed = cleaned.trim_matches('-').trim_matches('.');
    if trimmed.is_empty() {
        "untitled".to_string()
    } else {
        trimmed.to_string()
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_save_chat_message(
    date: String,           // 前端 JS Date 真 "YYYY-MM-DD"
    time: String,           // 前端 JS Date 真 "HH:MM"
    session_id: String,
    message_id: String,
    user_message: String,
    assistant_response: String,
) -> Result<WikiQueryWriteResult, String> {
    // 1. 校验 date 真 YYYY-MM-DD format (简单 check)
    if date.len() != 10 || !date.chars().nth(4).is_some_and(|c| c == '-') {
        return Err(format!("date format 不合法 (期望 YYYY-MM-DD): {date}"));
    }

    // 2. topic-slug 真 user_message 前 30 字
    let topic_slug = slugify(&user_message, 30);

    // 3. file path: ~/.catfish/wiki/queries/<date>-<slug>.md
    let dir = queries_dir()?;
    fs::create_dir_all(&dir).map_err(|e| format!("建目录 {dir:?} 失败: {e}"))?;
    let filename = format!("{date}-{topic_slug}.md");
    let path = dir.join(&filename);

    // 4. 防同名覆盖 — 加 `-<message_id 前 6 字>` suffix
    let final_path = if path.exists() {
        let msg_short: String = message_id.chars().take(6).collect();
        dir.join(format!("{date}-{topic_slug}-{msg_short}.md"))
    } else {
        path
    };

    // 5. 组装 markdown content
    let topic_preview: String = user_message
        .chars()
        .take(60)
        .collect::<String>()
        .replace('\n', " ");
    let content = format!(
        "---\n\
         type: query\n\
         sources: [chat]\n\
         date: {date}\n\
         time: \"{time}\"\n\
         session_id: {session_id}\n\
         message_id: {message_id}\n\
         topic: {topic_preview}\n\
         ---\n\
         \n\
         # {topic_preview}\n\
         \n\
         ## 问 (鸿波 @ {time})\n\
         \n\
         {user_message}\n\
         \n\
         ## 答 (小鲶)\n\
         \n\
         {assistant_response}\n"
    );

    // 6. 写
    fs::write(&final_path, &content)
        .map_err(|e| format!("写 {final_path:?} 失败: {e}"))?;
    let bytes = content.len() as u64;

    Ok(WikiQueryWriteResult {
        path: final_path.to_string_lossy().to_string(),
        bytes,
    })
}
