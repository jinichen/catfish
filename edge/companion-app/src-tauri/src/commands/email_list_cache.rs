//! 邮件列表的落盘缓存 (10/31): Companion 重启后进邮件页先显示上次的列表, 后台再刷新。
//!
//! 8/21 store/email.ts 的列表缓存故意只放内存, 理由是「冷启动本来就该拉一次, 后端
//! 热路径现在是毫秒级」—— 那时 Apple Mail 走 emlx 索引。9/27 索引撤掉、改回 AppleScript,
//! 前提没了: 鸿波 10/31 「每次重启进邮件都要等很久」, 实测 439 封要 19 秒 (修成批取之前
//! 70 多秒)。所以列表跟评级 / 分诊一样落到 ~/.catfish, 冷启动先拿这份。
//!
//! 内容是前端 setListCache 那份 (收件箱 + 已发送 + 账号, 都是 catfish-email list 的元数据,
//! 不含正文), 只在本机。前端整份交过来, 这里不解析, 只管读写。

use std::path::PathBuf;

const LIST_CACHE_FILE: &str = "email_list_cache.json";
/// 500 封收件 + 200 封已发送的元数据大概几百 KB, 给足余量; 超了说明前端传错了东西, 不写。
const MAX_BYTES: usize = 8 * 1024 * 1024;

fn cache_path() -> Option<PathBuf> {
    let home = crate::util::paths::home_env().ok()?;
    Some(PathBuf::from(home).join(".catfish").join(LIST_CACHE_FILE))
}

/// 上次存的列表 (JSON 文本)。没有 / 读不了 → None, 前端照常等实时拉取。
#[tauri::command]
pub fn email_list_cache_load() -> Option<String> {
    std::fs::read_to_string(cache_path()?).ok()
}

/// 前端每次拉列表成功后存一份。原子写 (先写 tmp 再改名), 失败只记日志。
#[tauri::command]
pub fn email_list_cache_save(payload: String) {
    if payload.len() > MAX_BYTES {
        log::warn!("[email-list-cache] {} 字节超上限, 不存", payload.len());
        return;
    }
    let Some(path) = cache_path() else { return };
    if let Err(e) = write_atomic(&path, &payload) {
        log::debug!("[email-list-cache] 写 {} 失败: {e}", path.display());
    }
}

fn write_atomic(path: &std::path::Path, payload: &str) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, payload)?;
    std::fs::rename(&tmp, path)
}

#[cfg(test)]
mod tests {
    use super::write_atomic;

    #[test]
    fn 原子写_覆盖旧的() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("sub").join("email_list_cache.json");
        write_atomic(&p, "{\"items\":[1]}").unwrap();
        write_atomic(&p, "{\"items\":[2]}").unwrap();
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "{\"items\":[2]}");
        assert!(!p.with_extension("json.tmp").exists());
    }
}
