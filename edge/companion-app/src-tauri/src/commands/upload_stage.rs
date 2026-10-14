//! 界面传给本机后台的文件: 分块写进临时文件, 再交给各个用途 (10/14 上限统一 200MB)。
//!
//! 之前每个上传口子都是"整个文件 → 一个 base64 字符串 (或数字数组) → 一次 invoke"。
//! 20MB 时还撑得住; 放到 200MB, base64 串就有 270MB, 前端、IPC、Rust 各拿一份,
//! 内存一次冲到七八百 MB, 配置低的 Windows 上 webview 直接崩。现在前端按 4MB 一块
//! (base64 后约 5.4MB, 低于 startupDiagnostics 的 8MB 超大载荷告警) 依次写进
//! `<临时目录>/catfish-stage/<id>.part`, 用的时候按 id 取路径 / 字节。
//!
//! 用的地方: 聊天附件 (文档 / 音频 / 图片)、会议纪要模版、技能 zip。
use std::io::Write;
use std::path::PathBuf;

/// 所有上传口子统一的上限 (10/14 鸿波「就是把现在的上限都调整到200M」)。
pub const MAX_UPLOAD_BYTES: u64 = 200 * 1024 * 1024;

fn stage_dir() -> PathBuf {
    std::env::temp_dir().join("catfish-stage")
}

/// id 由前端 crypto.randomUUID() 生成; 只认字母数字和 "-", 防路径穿越。
fn part_path(upload_id: &str) -> Result<PathBuf, String> {
    let ok = (8..=64).contains(&upload_id.len())
        && upload_id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-');
    if !ok {
        return Err(format!("上传编号不合法: {upload_id:?}"));
    }
    Ok(stage_dir().join(format!("{upload_id}.part")))
}

pub(crate) fn too_large_message(size: u64) -> String {
    format!(
        "文件太大 ({:.1}MB > {}MB)",
        size as f64 / 1024.0 / 1024.0,
        MAX_UPLOAD_BYTES / 1024 / 1024
    )
}

/// 写一块。offset 必须等于已经写进去的字节数 (第一块 0) —— 对不上说明中间断过,
/// 不拼半截文件。返回写完后的总字节数。
pub(crate) fn append_chunk(upload_id: &str, offset: u64, chunk: &[u8]) -> Result<u64, String> {
    let path = part_path(upload_id)?;
    if offset + chunk.len() as u64 > MAX_UPLOAD_BYTES {
        let _ = std::fs::remove_file(&path);
        return Err(too_large_message(offset + chunk.len() as u64));
    }
    let mut file = if offset == 0 {
        std::fs::create_dir_all(stage_dir()).map_err(|e| format!("建临时目录失败: {e}"))?;
        sweep_stale();
        std::fs::File::create(&path).map_err(|e| format!("建临时文件失败: {e}"))?
    } else {
        let have = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(u64::MAX);
        if have != offset {
            let _ = std::fs::remove_file(&path);
            return Err("上传中断了, 请重新选一次文件".to_string());
        }
        std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .map_err(|e| format!("打开临时文件失败: {e}"))?
    };
    file.write_all(chunk).map_err(|e| format!("写临时文件失败: {e}"))?;
    Ok(offset + chunk.len() as u64)
}

/// 上传到一半 Companion 被关掉留下的半截文件: 超过一天的顺手删掉。
fn sweep_stale() {
    let Ok(entries) = std::fs::read_dir(stage_dir()) else { return };
    for entry in entries.flatten() {
        let old = entry
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.elapsed().ok())
            .is_some_and(|age| age > std::time::Duration::from_secs(24 * 3600));
        if old {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// 取走整份文件: 改名成 `<临时目录>/catfish-upload-<id>.<ext>` 交给调用方 (调用方负责删 / 搬)。
pub(crate) fn take_path(upload_id: &str, filename: &str) -> Result<PathBuf, String> {
    let part = part_path(upload_id)?;
    let size = std::fs::metadata(&part).map(|m| m.len()).map_err(|_| "空文件".to_string())?;
    if size == 0 {
        let _ = std::fs::remove_file(&part);
        return Err("空文件".to_string());
    }
    let ext = std::path::Path::new(filename)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("bin")
        .to_lowercase();
    let ext: String = ext.chars().filter(|c| c.is_ascii_alphanumeric()).take(10).collect();
    let target = std::env::temp_dir().join(format!("catfish-upload-{upload_id}.{ext}"));
    std::fs::rename(&part, &target).map_err(|e| format!("临时文件改名失败: {e}"))?;
    Ok(target)
}

/// 取走整份字节 (给只收字节的老接口: 模版 / 技能 zip / 图片)。临时文件随即删掉。
pub(crate) fn take_bytes(upload_id: &str) -> Result<Vec<u8>, String> {
    let part = part_path(upload_id)?;
    let bytes = std::fs::read(&part).map_err(|_| "空文件".to_string());
    let _ = std::fs::remove_file(&part);
    let bytes = bytes?;
    if bytes.is_empty() {
        return Err("空文件".to_string());
    }
    Ok(bytes)
}

#[tauri::command(rename_all = "camelCase")]
pub async fn upload_stage_chunk(upload_id: String, offset: u64, chunk_b64: String) -> Result<u64, String> {
    tokio::task::spawn_blocking(move || {
        use base64::Engine;
        let chunk = base64::engine::general_purpose::STANDARD
            .decode(chunk_b64.trim())
            .map_err(|e| format!("base64 解码失败: {e}"))?;
        append_chunk(&upload_id, offset, &chunk)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 前端中途出错 / 员工取消时清掉半截文件。
#[tauri::command(rename_all = "camelCase")]
pub fn upload_stage_discard(upload_id: String) -> Result<(), String> {
    let _ = std::fs::remove_file(part_path(&upload_id)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id() -> String {
        format!("test-{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos())
    }

    #[test]
    fn chunks_join_into_one_file() {
        let id = id();
        assert_eq!(append_chunk(&id, 0, b"hello ").unwrap(), 6);
        assert_eq!(append_chunk(&id, 6, b"world").unwrap(), 11);
        assert_eq!(take_bytes(&id).unwrap(), b"hello world");
        assert!(take_bytes(&id).is_err(), "取走后就没了");
    }

    #[test]
    fn out_of_order_chunk_is_refused_and_cleaned() {
        let id = id();
        append_chunk(&id, 0, b"abc").unwrap();
        let err = append_chunk(&id, 10, b"x").unwrap_err();
        assert!(err.contains("中断"), "{err}");
        assert!(!part_path(&id).unwrap().exists());
    }

    #[test]
    fn over_200mb_is_refused() {
        let id = id();
        let err = append_chunk(&id, MAX_UPLOAD_BYTES, b"x").unwrap_err();
        assert!(err.contains("200MB"), "{err}");
    }

    #[test]
    fn bad_ids_and_empty_files() {
        assert!(part_path("../../etc/passwd").is_err());
        assert!(part_path("short").is_err());
        assert_eq!(take_path(&id(), "a.pdf").unwrap_err(), "空文件");
    }

    #[test]
    fn take_path_keeps_a_clean_extension() {
        let id = id();
        append_chunk(&id, 0, b"%PDF").unwrap();
        let p = take_path(&id, "报价 单.PDF").unwrap();
        assert!(p.to_string_lossy().ends_with(".pdf"));
        assert_eq!(std::fs::read(&p).unwrap(), b"%PDF");
        std::fs::remove_file(p).unwrap();
    }
}
