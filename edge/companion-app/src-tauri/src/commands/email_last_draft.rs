//! 最近一次存进草稿箱的那封 (9/29) —— 对话结束后据此跳到邮件页草稿箱。
//!
//! 文件由邮件组件在 `catfish-email draft` 成功时写 (catfish_email/last_draft.py),
//! 不管是谁调的: 对话里的工具、小鲶在终端里直接跑的命令、邮件页。
//!
//! 9/29 鸿波: "Windows 版落草稿箱后依旧没有跳转到草稿箱, 还需要人工选择
//! 邮件 → 草稿箱"。原来前端靠认工具名 + 回合结束再列一次草稿箱按 Date 头猜,
//! 链上任何一环断了都只是不跳、不报错。见 src/lib/draftJump.ts。

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

const FILENAME: &str = "email-last-draft.json";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LastDraft {
    /// 跟邮件列表里的 id 同一个格式 (带来源前缀), 可以直接拿去选中
    pub id: String,
    /// Unix 秒 (带小数) —— 跟前端 Date.now() 同一台机器的钟
    pub created_at: f64,
}

/// `~/.catfish/email-last-draft.json`, CATFISH_HOME 优先 —— 跟 Python 那边同一个规则。
fn marker_path() -> Option<PathBuf> {
    if let Some(dir) = std::env::var_os("CATFISH_HOME").filter(|v| !v.is_empty()) {
        return Some(PathBuf::from(dir).join(FILENAME));
    }
    let home = crate::util::paths::home_env().ok()?;
    Some(PathBuf::from(home).join(".catfish").join(FILENAME))
}

fn read_marker(path: &std::path::Path) -> Option<LastDraft> {
    let text = std::fs::read_to_string(path).ok()?;
    let draft: LastDraft = serde_json::from_str(&text).ok()?;
    (!draft.id.is_empty()).then_some(draft)
}

/// 没存过草稿 / 文件坏了 → None (前端就不跳, 不报错)。
#[tauri::command]
pub fn email_last_draft() -> Option<LastDraft> {
    read_marker(&marker_path()?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 读邮件组件写的那份() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join(FILENAME);
        // 形状照抄 last_draft.record: 多出来的字段 (adapter) 不影响
        std::fs::write(
            &path,
            r#"{"id": "imap|INBOX.Drafts|1|42", "adapter": "imap", "created_at": 1790000000.5}"#,
        )
        .unwrap();
        assert_eq!(
            read_marker(&path),
            Some(LastDraft { id: "imap|INBOX.Drafts|1|42".into(), created_at: 1790000000.5 })
        );
    }

    #[test]
    fn 没有_或坏了_或没有id_都当没存过() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().join(FILENAME);
        assert_eq!(read_marker(&path), None);
        std::fs::write(&path, "{not json").unwrap();
        assert_eq!(read_marker(&path), None);
        std::fs::write(&path, r#"{"id": "", "created_at": 1.0}"#).unwrap();
        assert_eq!(read_marker(&path), None);
    }

    #[test]
    fn 路径跟_python_同一个规则() {
        let _guard = crate::util::test_env::env_lock();
        let tmp = tempfile::TempDir::new().unwrap();
        let old = std::env::var_os("CATFISH_HOME");
        std::env::set_var("CATFISH_HOME", tmp.path());
        let got = marker_path();
        match old {
            Some(v) => std::env::set_var("CATFISH_HOME", v),
            None => std::env::remove_var("CATFISH_HOME"),
        }
        assert_eq!(got, Some(tmp.path().join("email-last-draft.json")));
    }
}
