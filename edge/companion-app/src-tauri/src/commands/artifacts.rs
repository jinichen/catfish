//! 对话右侧「成果区」(11/07 鸿波「先做成果区」): 小鲶生成的网页 / 文档 / 表格 / 图片在聊天右边直接看。
//!
//! 三个命令, 前端 (src/lib/tauri_artifacts.ts) 调:
//!   - `artifact_stat`  看文件在不在、是哪一类、多大、什么时候改的 (右栏每 2 秒问一次, 改了就刷新)。
//!     网页 / PDF / 图片要靠 asset 协议给 iframe / img 直接读, 这里**只放行这一个文件**
//!     (网页再加同目录, 方便它引用旁边的图片) —— tauri.conf.json 的静态 scope 不放宽,
//!     不然整个家目录都能被页面里的任何脚本按路径读到。
//!   - `artifact_read_text`  文本 / Markdown / CSV 的原始字节 (base64), 编码由前端判 (UTF-8 不行再按 GB18030,
//!     Windows 上 Excel 另存的 CSV 多是 GBK)。最多读 2MB。
//!   - `artifact_office_preview`  Word / Excel / PPT 调 scripts/parse_file_preview.py 转成 JSON, 前端用 React 画。
//!
//! 路径检查复用 file.rs 的 resolve_path (展开 ~、必须绝对路径、拒 ~/.ssh 这类敏感目录)。

use std::path::Path;
use std::time::{Duration, UNIX_EPOCH};

use serde::Serialize;
use tauri::{AppHandle, Manager};

use super::file::resolve_path;
use super::file_parse_env::{find_python, find_script};

const TEXT_LIMIT: u64 = 2 * 1024 * 1024;
const OFFICE_LIMIT: u64 = 100 * 1024 * 1024;
const OFFICE_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Debug, Serialize, PartialEq)]
pub struct ArtifactInfo {
    /// 展开后的绝对路径 (前端拿它拼 asset URL)
    pub path: String,
    pub name: String,
    pub ext: String,
    /// html | pdf | image | markdown | table | text | office
    pub kind: &'static str,
    pub size: u64,
    pub mtime_ms: u64,
}

#[derive(Debug, Serialize)]
pub struct ArtifactText {
    pub b64: String,
    pub truncated: bool,
    pub size: u64,
}

/// 扩展名 → 右栏用哪种方式显示。不认识的返回 None (右栏不接, 还是「用默认程序打开」)。
/// 跟前端 src/lib/artifactKinds.ts 的表一致 —— 改一边要改另一边 (有单测各自钉住)。
pub(crate) fn kind_of(ext: &str) -> Option<&'static str> {
    Some(match ext {
        "html" | "htm" => "html",
        "pdf" => "pdf",
        "png" | "jpg" | "jpeg" | "gif" | "webp" | "svg" | "bmp" => "image",
        "md" | "markdown" => "markdown",
        "csv" | "tsv" => "table",
        "txt" | "log" | "json" | "yaml" | "yml" | "xml" | "toml" | "ini" | "sql" | "sh" | "py" | "js" | "ts"
        | "tsx" | "jsx" | "css" | "rs" | "go" => "text",
        "docx" | "xlsx" | "xlsm" | "xls" | "pptx" => "office",
        _ => return None,
    })
}

fn ext_of(p: &Path) -> String {
    p.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase()
}

fn info_of(p: &Path) -> Result<ArtifactInfo, String> {
    let meta = std::fs::metadata(p).map_err(|e| format!("读不到文件信息: {e}"))?;
    if !meta.is_file() {
        return Err("这是个文件夹, 不是文件".into());
    }
    let ext = ext_of(p);
    let kind = kind_of(&ext).ok_or_else(|| format!("这种文件 (.{ext}) 右栏显示不了, 用默认程序打开看"))?;
    let mtime_ms = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    Ok(ArtifactInfo {
        path: p.to_string_lossy().into_owned(),
        name: p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
        ext,
        kind,
        size: meta.len(),
        mtime_ms,
    })
}

#[tauri::command]
pub async fn artifact_stat(app: AppHandle, path: String) -> Result<ArtifactInfo, String> {
    let p = resolve_path(&path)?;
    let info = info_of(&p)?;
    if matches!(info.kind, "html" | "pdf" | "image") {
        let scope = app.asset_protocol_scope();
        scope.allow_file(&p).map_err(|e| format!("放行预览失败: {e}"))?;
        if info.kind == "html" {
            // 页面常引用旁边的图片 / css (同目录或 assets/ 子目录)。放行所在目录; 只有目录够深
            // (例 ~/.catfish/outputs/2026-11-07/) 才连子目录一起放 —— 文件在家目录 / 桌面 / 下载根下时
            // 不至于把整个家目录 / 桌面放开。放行的文件页面只能显示, 读不到内容 (asset 协议只认 Companion 的来源)。
            if let Some(dir) = p.parent() {
                let home = crate::util::paths::home_env().ok().map(std::path::PathBuf::from);
                let recursive = subdirs_ok(dir, home.as_deref());
                scope.allow_directory(dir, recursive).map_err(|e| format!("放行预览失败: {e}"))?;
            }
        }
    }
    Ok(info)
}

/// 网页所在目录要不要连子目录一起放行: 家目录里至少往下两层 (不含桌面 / 下载 / 文稿这一层),
/// 家目录外至少三层 (/tmp 不行, /tmp/a/b 可以)。
fn subdirs_ok(dir: &Path, home: Option<&Path>) -> bool {
    if let Some(rest) = home.and_then(|h| dir.strip_prefix(h).ok()) {
        return rest.components().count() >= 2;
    }
    if home.is_some_and(|h| h.starts_with(dir)) {
        return false; // 家目录的上级 (例 /Users)
    }
    dir.components().filter(|c| matches!(c, std::path::Component::Normal(_))).count() >= 3
}

fn read_head(p: &Path, limit: u64) -> Result<ArtifactText, String> {
    use std::io::Read;
    let size = std::fs::metadata(p).map_err(|e| format!("读不到文件信息: {e}"))?.len();
    let mut buf = Vec::with_capacity(size.min(limit) as usize);
    std::fs::File::open(p)
        .and_then(|f| f.take(limit).read_to_end(&mut buf))
        .map_err(|e| format!("读文件失败: {e}"))?;
    use base64::Engine;
    Ok(ArtifactText { b64: base64::engine::general_purpose::STANDARD.encode(&buf), truncated: size > limit, size })
}

#[tauri::command]
pub async fn artifact_read_text(path: String) -> Result<ArtifactText, String> {
    let p = resolve_path(&path)?;
    let info = info_of(&p)?;
    if !matches!(info.kind, "markdown" | "table" | "text") {
        return Err(format!(".{} 不是文本文件", info.ext));
    }
    read_head(&p, TEXT_LIMIT)
}

#[tauri::command]
pub async fn artifact_office_preview(path: String) -> Result<serde_json::Value, String> {
    let p = resolve_path(&path)?;
    let info = info_of(&p)?;
    if info.kind != "office" {
        return Err(format!(".{} 不是 Word / Excel / PPT", info.ext));
    }
    if info.size > OFFICE_LIMIT {
        return Err("文件超过 100MB, 右栏不预览, 用默认程序打开看".into());
    }
    let py = find_python()?;
    let script = find_script("parse_file_preview.py")?;
    let child = crate::services::process::python_tokio_command(&py)
        .arg(&script)
        .arg(&p)
        .kill_on_drop(true)
        .output();
    let output = tokio::time::timeout(OFFICE_TIMEOUT, child)
        .await
        .map_err(|_| "转换超过 60 秒没完成, 文件可能太大, 用默认程序打开看".to_string())?
        .map_err(|e| format!("Python 调用失败: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let value: serde_json::Value = serde_json::from_str(stdout.trim()).map_err(|_| {
        let stderr = String::from_utf8_lossy(&output.stderr);
        format!("预览转换没给出结果: {}", stderr.lines().last().unwrap_or("(无输出)"))
    })?;
    if let Some(err) = value.get("error").and_then(|e| e.as_str()) {
        return Err(err.to_string());
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 扩展名分类() {
        assert_eq!(kind_of("html"), Some("html"));
        assert_eq!(kind_of("htm"), Some("html"));
        assert_eq!(kind_of("pdf"), Some("pdf"));
        assert_eq!(kind_of("svg"), Some("image"));
        assert_eq!(kind_of("md"), Some("markdown"));
        assert_eq!(kind_of("csv"), Some("table"));
        assert_eq!(kind_of("json"), Some("text"));
        assert_eq!(kind_of("xlsx"), Some("office"));
        assert_eq!(kind_of("pptx"), Some("office"));
        assert_eq!(kind_of("zip"), None);
        assert_eq!(kind_of("doc"), None); // 老格式 python-docx 读不了
        assert_eq!(kind_of(""), None);
    }

    #[test]
    fn 文件信息_扩展名不分大小写_文件夹和不认识的拒() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("报告.HTML");
        std::fs::write(&f, "<p>hi</p>").unwrap();
        let info = info_of(&f).unwrap();
        assert_eq!((info.kind, info.ext.as_str(), info.name.as_str(), info.size), ("html", "html", "报告.HTML", 9));
        assert!(info.mtime_ms > 0);
        assert!(info_of(dir.path()).unwrap_err().contains("文件夹"));
        let z = dir.path().join("a.zip");
        std::fs::write(&z, "x").unwrap();
        assert!(info_of(&z).unwrap_err().contains("显示不了"));
    }

    #[test]
    fn 子目录只在够深时放行() {
        let home = Path::new("/Users/hb");
        assert!(!subdirs_ok(Path::new("/Users/hb"), Some(home)));
        assert!(!subdirs_ok(Path::new("/Users/hb/Desktop"), Some(home)));
        assert!(subdirs_ok(Path::new("/Users/hb/Desktop/方案"), Some(home)));
        assert!(subdirs_ok(Path::new("/Users/hb/.catfish/outputs/2026-11-07"), Some(home)));
        assert!(!subdirs_ok(Path::new("/Users"), Some(home)));
        assert!(!subdirs_ok(Path::new("/tmp"), Some(home)));
        assert!(!subdirs_ok(Path::new("/private/tmp"), Some(home)));
        assert!(subdirs_ok(Path::new("/private/tmp/report"), Some(home)));
        assert!(!subdirs_ok(Path::new("/"), None));
    }

    #[test]
    fn 文本只读前面一段_并标出截断() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("a.txt");
        std::fs::write(&f, "abcdef").unwrap();
        let t = read_head(&f, 4).unwrap();
        use base64::Engine;
        assert_eq!(base64::engine::general_purpose::STANDARD.decode(&t.b64).unwrap(), b"abcd");
        assert!(t.truncated);
        assert_eq!(t.size, 6);
        let t = read_head(&f, 100).unwrap();
        assert!(!t.truncated);
    }
}
