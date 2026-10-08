//! 早安卡片「已关闭」台账 (10/8) —— `~/.catfish/advisor_closed.jsonl`, 只追加。
//!
//! # 为什么单独一份
//!
//! 10/8「期中固定资产盘点整改」员工说了两次已填完, 卡片还是第三次冒出来。根因:
//! 关闭状态存在 `advisor_cache.json` 的 `taskChatSummaries[uid].manualStatus`, 而每次
//! 写缓存只保留**本轮结果里还在的** uid —— 被判已完成的卡正是本轮被拿掉的那张,
//! 它的状态当场就被一起删了。下一轮没人记得它关过, 来源邮件还在窗口里就又生成。
//!
//! 台账跟缓存分开, 不随缓存重写被清理: 按钮 (Companion) 和小鲶的
//! `catfish_advisor_close_task` (tool-bridge, Python) 都往这里追加一行, 生成卡片时
//! 按 uid / 标题 / 来源邮件主题匹配过滤 (逻辑在 `src/lib/advisor_closed.ts`)。
//!
//! # 为什么是 JSONL 追加而不是 JSON 改写
//!
//! 两个进程 (Rust / Python) 会写同一个文件。单行追加 (O_APPEND) 不需要跨语言的锁,
//! 也不会因为一方读改写把另一方刚写的覆盖掉。折叠 (同一条的最新一行算数) 在读侧做。

use std::io::Write;
use std::path::PathBuf;

/// 读侧只看最后这么多行 —— 台账一天几条, 3000 行够两三年, 防止文件意外膨胀拖慢早安。
const MAX_LINES: usize = 3000;
/// 单行上限: 一条台账 (标题 + 几条来源引用) 远小于此, 超了说明调用方传错东西。
const MAX_LINE_BYTES: usize = 8 * 1024;

fn ledger_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("advisor_closed.jsonl"))
}

fn read_tail(path: &PathBuf) -> Vec<serde_json::Value> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    let start = lines.len().saturating_sub(MAX_LINES);
    lines[start..]
        .iter()
        // 坏行 (半截写入 / 手改坏) 跳过, 不让一行拖垮整个早安
        .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
        .filter(|v| v.is_object())
        .collect()
}

fn append_line(path: &PathBuf, entry: &serde_json::Value) -> Result<(), String> {
    if !entry.is_object() {
        return Err("台账条目必须是 JSON 对象".to_string());
    }
    let line = serde_json::to_string(entry).map_err(|e| format!("序列化失败: {e}"))?;
    if line.len() > MAX_LINE_BYTES {
        return Err(format!("台账条目过大 ({} 字节)", line.len()));
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("建目录失败: {e}"))?;
    }
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("打开 {} 失败: {e}", path.display()))?;
    // 一次 write_all 一整行 (含换行): O_APPEND 下同一行不会和另一进程的写交错
    f.write_all(format!("{line}\n").as_bytes())
        .map_err(|e| format!("写 {} 失败: {e}", path.display()))
}

#[tauri::command]
pub async fn advisor_closed_read() -> Result<Vec<serde_json::Value>, String> {
    Ok(read_tail(&ledger_path()?))
}

#[tauri::command]
pub async fn advisor_closed_append(entry: serde_json::Value) -> Result<(), String> {
    append_line(&ledger_path()?, &entry)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn tmp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "catfish-advisor-closed-{}-{name}.jsonl",
            std::process::id()
        ));
        let _ = std::fs::remove_file(&p);
        p
    }

    #[test]
    fn append_then_read_roundtrip_skips_bad_lines() {
        let p = tmp("roundtrip");
        append_line(&p, &json!({"op": "close", "title": "甲"})).unwrap();
        std::fs::OpenOptions::new()
            .append(true)
            .open(&p)
            .unwrap()
            .write_all(b"{half-written\n")
            .unwrap();
        append_line(&p, &json!({"op": "reopen", "title": "甲"})).unwrap();
        let rows = read_tail(&p);
        assert_eq!(rows.len(), 2, "坏行跳过, 好行保留: {rows:?}");
        assert_eq!(rows[1]["op"], "reopen");
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn rejects_non_object_and_oversized() {
        let p = tmp("reject");
        assert!(append_line(&p, &json!("str")).is_err());
        let big = "x".repeat(MAX_LINE_BYTES + 1);
        assert!(append_line(&p, &json!({"title": big})).is_err());
        assert!(read_tail(&p).is_empty());
    }

    #[test]
    fn missing_file_reads_empty() {
        assert!(read_tail(&tmp("missing")).is_empty());
    }
}
