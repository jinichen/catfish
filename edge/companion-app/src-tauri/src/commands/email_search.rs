//! 邮件页搜索框搜「更早的邮件」(11/19)。
//!
//! 鸿波「搜索框也改成能搜到更早的邮件」: 邮件页列表最多 500 封 (EMAIL_LIST_MAX), 搜索框原来只在
//! 这 500 封里筛。更早的信还在「邮件」App / 邮箱服务器里 —— 交给 `catfish-email search`
//! (Apple Mail: AppleScript `whose subject/sender contains`; IMAP: 服务端 SEARCH), 只取元数据, 不读正文。

use super::email::{email_command, email_component_missing_error};
use crate::services::catfish_paths;

/// 一次最多拿多少封。搜索结果是给人一眼扫的, 50 封够了; 太多 AppleScript 也慢。
const SEARCH_MAX: u32 = 50;

/// 搜某个文件夹 (Inbox / Sent / Drafts) 里主题 / 发件人含 `query` 的信, 新的在前。返回 list 同格式的 JSON 数组。
#[tauri::command]
pub async fn email_search(query: String, folder: Option<String>, limit: Option<u32>) -> Result<String, String> {
    let q = query.trim().to_string();
    if q.chars().count() < 2 {
        return Ok("[]".to_string());
    }
    let bin = catfish_paths::catfish_email_bin().ok_or_else(email_component_missing_error)?;
    let n = limit.unwrap_or(SEARCH_MAX).clamp(1, SEARCH_MAX);
    let folder = folder.filter(|f| !f.trim().is_empty()).unwrap_or_else(|| "Inbox".to_string());
    let args = search_args(&q, &folder, n);
    let out = tauri::async_runtime::spawn_blocking(move || email_command(&bin).args(&args).output())
        .await
        .map_err(|e| format!("搜索没跑起来: {e}"))?
        .map_err(|e| format!("catfish-email 调用失败: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if stderr.is_empty() { format!("搜索失败, 退出码 {:?}", out.status.code()) } else { stderr });
    }
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    Ok(if stdout.trim().is_empty() { "[]".to_string() } else { stdout })
}

fn search_args(query: &str, folder: &str, limit: u32) -> Vec<String> {
    // query 作为单独一个参数传 (不拼字符串), 以 "--" 开头的关键词也不会被当成选项
    vec![
        "search".into(), "--json".into(),
        "--folder".into(), folder.into(),
        "--limit".into(), limit.to_string(),
        "--".into(), query.into(),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn args_keep_query_as_one_positional_after_double_dash() {
        let a = search_args("--盘点 通知", "Inbox", 50);
        assert_eq!(a, ["search", "--json", "--folder", "Inbox", "--limit", "50", "--", "--盘点 通知"]);
    }

    #[tokio::test]
    async fn too_short_query_returns_empty_without_running_anything() {
        assert_eq!(email_search(" 盘 ".into(), None, None).await.unwrap(), "[]");
    }
}
