//! 文件解析命令 — 五一 sprint Day 1 多模态文件上传.
//!
//! # 设计
//!
//! 前端 ChatInput 拖入 / 点 📎 选 PDF/Excel/Word/CSV/TXT/MD →
//!   File → ArrayBuffer → 写 /tmp/catfish-upload-<uuid>.<ext> →
//!   invoke('parse_file', { tmp_path }) →
//!   Rust 调 Python helper (用 hermes/gateway venv) →
//!   返 { filename, ext, char_count, truncated, text } →
//!   前端 push 到 attachments[] (kind="file"), 用户发送时跟图片一起带给 LLM.
//!
//! # 为什么走 Python 不直接 Rust
//!
//! - PDF/Excel/Word 解析库 Python 生态成熟 (pypdfium2/openpyxl/python-docx)
//! - Rust 等价库要么少 (calamine for xlsx OK, 但 docx 几乎没维护好的)
//! - 鲶鱼 hermes/gateway venv 已经装这几个库 (4-30 weekly-report skill 验过)
//! - 走 subprocess 简单稳定, 不用 PyO3 内嵌 Python (开发期复杂度高)

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct ParseFileResult {
    pub filename: String,
    pub ext: String,
    pub char_count: usize,
    pub truncated: bool,
    pub text: String,
}

#[derive(Debug, Deserialize)]
struct ParseError {
    error: String,
}

/// 探测 Python 解释器. 优先用鲶鱼自带 venv (依赖已装), 兜底系统 python3.
fn find_python() -> Option<PathBuf> {
    if let Ok(custom) = std::env::var("CATFISH_PYTHON") {
        let p = PathBuf::from(custom);
        if p.exists() {
            return Some(p);
        }
    }
    let home = std::env::var("HOME").ok()?;
    let candidates = [
        // hermes venv (Python 3.11, 一直有 pip install pypdfium2/openpyxl/docx)
        format!("{home}/.hermes/hermes-agent/venv/bin/python"),
        // catfish gateway venv (Python 3.12, 4-30 weekly-report skill 验过依赖)
        format!("{home}/person_task/catfish/central/llm-gateway/venv/bin/python"),
        // 系统 brew Python (M4)
        "/opt/homebrew/bin/python3".to_string(),
        // 系统 python3
        "/usr/bin/python3".to_string(),
    ];
    for c in &candidates {
        let p = PathBuf::from(c);
        if p.exists() {
            return Some(p);
        }
    }
    None
}

/// 找 parse_file.py 脚本. 跟 Tauri binary 同 bundle 里 (Resources 目录).
/// dev 模式从源码 src-tauri/scripts/parse_file.py 找.
fn find_parse_script() -> Option<PathBuf> {
    // 1. dev 模式 — 源码相对路径
    if let Ok(cwd) = std::env::current_dir() {
        // tauri dev 启动时 cwd = src-tauri/, 脚本在 scripts/parse_file.py
        let p = cwd.join("scripts").join("parse_file.py");
        if p.exists() {
            return Some(p);
        }
        // 或者 cwd = companion-app/, 脚本在 src-tauri/scripts/parse_file.py
        let p = cwd.join("src-tauri").join("scripts").join("parse_file.py");
        if p.exists() {
            return Some(p);
        }
    }
    // 2. release 模式 — .app/Contents/Resources/scripts/parse_file.py
    //    (需要 tauri.conf.json 把 scripts 目录打进 bundle, Phase 2 再做)
    //    暂时硬编码用户路径兜底
    if let Ok(home) = std::env::var("HOME") {
        let p = PathBuf::from(home)
            .join("person_task/catfish/edge/companion-app/src-tauri/scripts/parse_file.py");
        if p.exists() {
            return Some(p);
        }
    }
    None
}

/// 接收前端写的 /tmp 文件路径, 调 Python 解析, 返结构化结果.
#[tauri::command]
pub async fn parse_file(tmp_path: String) -> Result<ParseFileResult, String> {
    let path = PathBuf::from(&tmp_path);
    if !path.exists() {
        return Err(format!("文件不存在: {tmp_path}"));
    }

    let py = find_python().ok_or_else(|| {
        "Python 解释器找不到. 设 env CATFISH_PYTHON=/path/to/python".to_string()
    })?;
    let script = find_parse_script().ok_or_else(|| {
        "parse_file.py 脚本找不到 (dev 模式跑 npm run tauri dev 的目录得对)".to_string()
    })?;

    log::info!("parse_file: {} {} {}", py.display(), script.display(), tmp_path);

    let output = std::process::Command::new(&py)
        .arg(&script)
        .arg(&tmp_path)
        .output()
        .map_err(|e| format!("Python 调用失败: {e}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    // Python helper 不论成功失败都打 JSON. 但万一 import 出错或 segfault, stderr 有内容
    if stdout.trim().is_empty() {
        return Err(format!(
            "Python 解析没输出. stderr: {stderr}"
        ));
    }

    // 先试解析 error JSON
    if let Ok(err) = serde_json::from_str::<ParseError>(&stdout) {
        return Err(err.error);
    }

    // 再试解析正常 result
    let result: ParseFileResult = serde_json::from_str(&stdout).map_err(|e| {
        format!(
            "Python 输出 JSON 解析失败: {e}\nstdout: {stdout}\nstderr: {stderr}"
        )
    })?;

    log::info!(
        "parse_file: ✅ {} ({} chars, truncated={})",
        result.filename, result.char_count, result.truncated
    );
    Ok(result)
}

/// 接收 base64 文件内容 + 文件名, 写 tmp + 调 Python 解析 + 清理 tmp.
/// 给前端直接调用 (不需要前端先写文件).
#[tauri::command]
pub async fn parse_file_from_b64(
    file_b64: String,
    filename: String,
) -> Result<ParseFileResult, String> {
    use base64::Engine;

    let bytes = base64::engine::general_purpose::STANDARD
        .decode(file_b64.trim())
        .map_err(|e| format!("base64 解码失败: {e}"))?;

    if bytes.is_empty() {
        return Err("空文件".to_string());
    }

    // 文件名 sanitize: 只保留扩展名, 主名用 uuid 防路径注入
    let ext = std::path::Path::new(&filename)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("bin")
        .to_lowercase();
    let uuid = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let tmp = std::env::temp_dir().join(format!("catfish-upload-{uuid}.{ext}"));

    std::fs::write(&tmp, &bytes).map_err(|e| format!("写 tmp 失败: {e}"))?;
    log::info!("parse_file_from_b64: 写 {} ({} bytes)", tmp.display(), bytes.len());

    let result = parse_file(tmp.to_string_lossy().to_string()).await;
    let _ = std::fs::remove_file(&tmp); // 清理 tmp 不论成功失败

    // 用前端送的真名替换 sanitize 出的 uuid 名字
    result.map(|mut r| {
        r.filename = filename;
        r
    })
}
