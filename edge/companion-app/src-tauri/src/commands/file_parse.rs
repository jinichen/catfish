//! 文件解析命令 — 五一 sprint Day 1 多模态文件上传.
//!
//! # 设计
//!
//! 前端 ChatInput 拖入 / 点 📎 选 PDF/Excel/Word/CSV/TXT/MD →
//!   File → FileReader.readAsDataURL → base64 →
//!   invoke('parse_file_from_b64', { fileB64, filename }) →
//!   Rust 写 /tmp + 调 Python helper (用 hermes/gateway venv) →
//!   返 { filename, ext, kind, preview_text, preview_chars, meta, kept_path } →
//!   Rust **永远** mv 原文件到 ~/.catfish/uploads/<ts>-<filename> (preview-only mode) →
//!   前端 push 到 attachments[] (kind="file"), 发送时拼 preview + path + execute_code 提示.
//!
//! # 为什么走 Python 不直接 Rust
//!
//! - PDF/Excel/Word 解析库 Python 生态成熟 (pypdfium2/openpyxl/python-docx)
//! - Rust 等价库要么少 (calamine for xlsx OK, 但 docx 几乎没维护好的)
//! - 鲶鱼 hermes/gateway venv 已经装这几个库 (4-30 weekly-report skill 验过)
//! - 走 subprocess 简单稳定, 不用 PyO3 内嵌 Python (开发期复杂度高)

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct ParseFileResult {
    pub filename: String,
    pub ext: String,
    /// kind: "excel" | "pdf" | "word" | "csv" | "text"
    pub kind: String,
    /// preview 内容 (~5K 字, 给 LLM 看个梗概), 不放完整数据
    pub preview_text: String,
    /// preview 字符数 (UI chip 显示用)
    pub preview_chars: usize,
    /// 结构化元信息 (sheet 名 / 行数 / 页数 / 等), 给 LLM 拼 execute_code 用.
    /// 透明 JSON, parse_file.py 各 parser 自己定义字段.
    pub meta: serde_json::Value,
    /// 原文件保留路径 (永远有, ~/.catfish/uploads/<ts>-<name>).
    /// LLM 100% 用 execute_code 读完整数据 — 不再有"截断"概念.
    pub kept_path: String,
    /// BL-L26 (5/7): 大文件 (≥50KB 全文) 的 BM25 sidecar 路径 (含全文纯文本).
    /// 前端发消息时调 attachment_bm25_search(parsed_text_path, query) 取相关段落
    /// 替换 preview 注入 user message. 小文件没这字段.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parsed_text_path: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ParseError {
    error: String,
}

/// 探测 Python 解释器.
///
/// 5/6 鸿波报"在 gateway 装了 pypdfium2 还报错": Root cause = 之前 hermes venv
/// 优先, 但 hermes venv 不一定装 catfish 解析依赖 (pypdfium2/openpyxl/python-docx).
///
/// 修: catfish gateway venv 优先 (我们文档明确要求装这里); 同时**检测每个候选
/// 是否真有依赖** — pypdfium2 / openpyxl / docx 都齐, 才用; 缺任意一个就跳下一个.
/// 这样不管员工装哪个 venv, 哪个真齐就用哪个.
fn find_python() -> Option<PathBuf> {
    if let Ok(custom) = std::env::var("CATFISH_PYTHON") {
        let p = PathBuf::from(custom);
        if p.exists() {
            return Some(p);
        }
    }
    let home = std::env::var("HOME").ok()?;
    let candidates = [
        // catfish gateway venv (Python 3.12) - parse_file.py 依赖文档里明确装这里
        format!("{home}/person_task/catfish/central/llm-gateway/venv/bin/python"),
        // hermes venv (Python 3.11) - 兜底
        format!("{home}/.hermes/hermes-agent/venv/bin/python"),
        // 系统 brew Python (M4)
        "/opt/homebrew/bin/python3".to_string(),
        // 系统 python3
        "/usr/bin/python3".to_string(),
    ];

    // 先找依赖齐的 (pypdfium2 + openpyxl + docx 全装)
    for c in &candidates {
        let p = PathBuf::from(c);
        if p.exists() && _has_parse_deps(&p) {
            log::info!("find_python: 依赖齐, 用 {}", p.display());
            return Some(p);
        }
    }
    // 都不齐 — 退而求其次, 用第一个存在的 (parse_file.py 跑时报具体缺啥)
    for c in &candidates {
        let p = PathBuf::from(c);
        if p.exists() {
            log::warn!(
                "find_python: 没找到依赖齐的 venv, 退而用 {} (员工 PDF/Excel 上传可能报缺依赖)",
                p.display(),
            );
            return Some(p);
        }
    }
    None
}

/// 检测 Python 候选是否装了 parse_file.py 三大依赖 (pypdfium2 / openpyxl / docx).
///
/// 一次 subprocess 调用, ~100ms, 只在启动找 Python 时跑一次. 不影响每次 parse 性能.
fn _has_parse_deps(py: &PathBuf) -> bool {
    let out = std::process::Command::new(py)
        .arg("-c")
        .arg("import pypdfium2, openpyxl, docx")
        .output();
    matches!(out, Ok(o) if o.status.success())
}

/// 找 parse_file.py 脚本. 跟 Tauri binary 同 bundle 里 (Resources 目录).
/// dev 模式从源码 src-tauri/scripts/parse_file.py 找.
fn find_parse_script() -> Option<PathBuf> {
    find_script("parse_file.py")
}

/// 找 Python 脚本 (跟 parse_file 同目录).
/// BL-L26 (5/7): 抽出共享 attachment_bm25.py 复用同一查找逻辑.
fn find_script(name: &str) -> Option<PathBuf> {
    if let Ok(cwd) = std::env::current_dir() {
        let p = cwd.join("scripts").join(name);
        if p.exists() {
            return Some(p);
        }
        let p = cwd.join("src-tauri").join("scripts").join(name);
        if p.exists() {
            return Some(p);
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        let p = PathBuf::from(home)
            .join("person_task/catfish/edge/companion-app/src-tauri/scripts")
            .join(name);
        if p.exists() {
            return Some(p);
        }
    }
    None
}

/// Python 输出的 JSON (无 kept_path, 由 Rust 端填).
#[derive(Debug, Deserialize)]
struct ParseFileFromPython {
    filename: String,
    ext: String,
    kind: String,
    preview_text: String,
    preview_chars: usize,
    meta: serde_json::Value,
    /// BL-L26 (5/7): Python 端写的 sidecar 路径 (相对 /tmp/<uuid>.<ext>.parsed.txt).
    /// Rust 端会跟着 kept_path mv 一起搬, 改成 ~/.catfish/uploads/<ts>-<name>.parsed.txt.
    #[serde(default)]
    parsed_text_path: Option<String>,
}

/// 接收前端写的 /tmp 文件路径, 调 Python 解析, 返 (preview, meta).
/// 不返回 kept_path — 由 caller (parse_file_from_b64) 决定文件去向.
async fn parse_file_inner(tmp_path: &str) -> Result<ParseFileFromPython, String> {
    let path = PathBuf::from(tmp_path);
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
        .arg(tmp_path)
        .output()
        .map_err(|e| format!("Python 调用失败: {e}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if stdout.trim().is_empty() {
        return Err(format!("Python 解析没输出. stderr: {stderr}"));
    }

    if let Ok(err) = serde_json::from_str::<ParseError>(&stdout) {
        return Err(err.error);
    }

    let result: ParseFileFromPython = serde_json::from_str(&stdout).map_err(|e| {
        format!(
            "Python 输出 JSON 解析失败: {e}\nstdout: {stdout}\nstderr: {stderr}"
        )
    })?;

    log::info!(
        "parse_file: ✅ {} ({}, preview {} chars)",
        result.filename, result.kind, result.preview_chars,
    );
    Ok(result)
}

/// 老接口保留 (前端不直接调, 留作 admin/test 用).
/// 5/5 重构后行为: 返 preview-only result, kept_path 是空 (caller 决定保留).
#[tauri::command]
pub async fn parse_file(tmp_path: String) -> Result<ParseFileResult, String> {
    let inner = parse_file_inner(&tmp_path).await?;
    Ok(ParseFileResult {
        filename: inner.filename,
        ext: inner.ext,
        kind: inner.kind,
        preview_text: inner.preview_text,
        preview_chars: inner.preview_chars,
        meta: inner.meta,
        kept_path: tmp_path,  // 用 tmp_path 占位, caller 自己决定保留
        parsed_text_path: inner.parsed_text_path,
    })
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

    // parse Python (preview-only)
    let inner = match parse_file_inner(&tmp.to_string_lossy()).await {
        Ok(r) => r,
        Err(e) => {
            // 解析失败也删 tmp
            let _ = std::fs::remove_file(&tmp);
            return Err(e);
        }
    };

    // 5/5 重构: 永远把原文件 mv 到 ~/.catfish/uploads/. 不再有"截断"概念,
    // LLM 100% 调 execute_code 用 pandas/openpyxl/pypdfium2 读完整数据.
    // 文件命名: <unix-ts>-<原 filename>, 多次同名上传不冲突.
    let home = std::env::var("HOME").unwrap_or_default();
    let uploads_dir = std::path::PathBuf::from(&home).join(".catfish").join("uploads");
    std::fs::create_dir_all(&uploads_dir)
        .map_err(|e| format!("uploads dir 建失败: {e}"))?;

    let safe = filename.replace(['/', '\\', '\0'], "_");
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let kept = uploads_dir.join(format!("{ts}-{safe}"));

    // mv 优先 (同分区秒级), 跨分区 fallback cp + rm
    let kept_str = match std::fs::rename(&tmp, &kept) {
        Ok(_) => kept.to_string_lossy().to_string(),
        Err(e_rename) => match std::fs::copy(&tmp, &kept) {
            Ok(_) => {
                let _ = std::fs::remove_file(&tmp);
                kept.to_string_lossy().to_string()
            }
            Err(e_copy) => {
                let _ = std::fs::remove_file(&tmp);
                return Err(format!(
                    "保留原文件失败 (rename: {e_rename}, copy: {e_copy})"
                ));
            }
        },
    };

    log::info!(
        "parse_file_from_b64: ✅ 保留 {} (LLM 用 execute_code 读完整数据)",
        kept_str,
    );

    // BL-L26 (5/7): 如果 Python 写了 BM25 sidecar (大文件 ≥50KB), 跟着 kept_path 搬.
    // sidecar 命名: <kept_path>.parsed.txt (跟 kept 同目录, 同 ts 前缀)
    let parsed_text_path = if let Some(py_sidecar) = inner.parsed_text_path.as_deref() {
        let target = uploads_dir.join(format!("{ts}-{safe}.parsed.txt"));
        match std::fs::rename(py_sidecar, &target) {
            Ok(_) => Some(target.to_string_lossy().to_string()),
            Err(e) => {
                log::warn!(
                    "BL-L26: 搬 BM25 sidecar 失败 ({}), 大文件 fallback 走 preview: {}",
                    py_sidecar, e,
                );
                // 不阻塞主流程, 大文件就走 preview (跟没 sidecar 一样, 体验略降但 chat 仍工作)
                None
            }
        }
    } else {
        None
    };
    if let Some(ref p) = parsed_text_path {
        log::info!("BL-L26: ✅ BM25 sidecar 就绪 → {p}");
    }

    Ok(ParseFileResult {
        filename,
        ext: inner.ext,
        kind: inner.kind,
        preview_text: inner.preview_text,
        preview_chars: inner.preview_chars,
        meta: inner.meta,
        kept_path: kept_str,
        parsed_text_path,
    })
}


// ============================================================
// BL-L26 (5/7): 大文件 BM25 段落检索
// ============================================================
//
// 前端发消息时, 如果 message 含 attachment 且 attachment.parsedTextPath != null
// (parse_file_from_b64 写出来的 sidecar), 调这个 command 取 top-K 跟员工问题相关
// 的段落, 拼到 user message 里取代 preview.
//
// 设计:
//   - Python helper attachment_bm25.py 是单进程 stateless (不缓存, 每次都重切+打分)
//   - 100KB 文件实测 ~30-80ms, 一次 chat 消息开销可接受
//   - sidecar 不存在 / Python 失败 → 返空 passages 数组 + reason; 前端 fallback 走 preview

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttachmentPassage {
    pub text: String,
    pub score: f64,
    pub ord: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttachmentBm25Result {
    pub passages: Vec<AttachmentPassage>,
    pub total_passages: u32,
    pub query_strategy: String,
    /// 失败原因 (sidecar 不存在 / Python 出错). 成功时空字符串.
    pub error: String,
}

#[tauri::command]
pub async fn attachment_bm25_search(
    parsed_text_path: String,
    query: String,
    top_k: Option<u32>,
) -> Result<AttachmentBm25Result, String> {
    let k = top_k.unwrap_or(5).clamp(1, 20);

    // sidecar 不存在 → 直接返空, 不报错 (前端 fallback)
    if !Path::new(&parsed_text_path).exists() {
        return Ok(AttachmentBm25Result {
            passages: vec![],
            total_passages: 0,
            query_strategy: "no_sidecar".to_string(),
            error: format!("sidecar 不存在: {parsed_text_path}"),
        });
    }

    let py = find_python().ok_or_else(|| {
        "Python 解释器找不到. 设 env CATFISH_PYTHON=/path/to/python".to_string()
    })?;
    let script = find_script("attachment_bm25.py").ok_or_else(|| {
        "attachment_bm25.py 脚本找不到".to_string()
    })?;

    log::info!(
        "BL-L26 bm25_search: kept={}, query={:?}, top_k={}",
        parsed_text_path, query.chars().take(30).collect::<String>(), k,
    );

    let output = std::process::Command::new(&py)
        .arg(&script)
        .arg("--text-path").arg(&parsed_text_path)
        .arg("--query").arg(&query)
        .arg("--top-k").arg(k.to_string())
        .output()
        .map_err(|e| format!("attachment_bm25 调用失败: {e}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if stdout.trim().is_empty() {
        // Python 没输出 / 崩了 — 返空, 前端 fallback
        log::warn!("BL-L26: bm25 helper 没输出. stderr: {stderr}");
        return Ok(AttachmentBm25Result {
            passages: vec![],
            total_passages: 0,
            query_strategy: "helper_crashed".to_string(),
            error: format!("python helper 没输出: {stderr}"),
        });
    }

    // helper 报错也返结构化错误, 不让 chat 挂
    if let Ok(err) = serde_json::from_str::<ParseError>(&stdout) {
        return Ok(AttachmentBm25Result {
            passages: vec![],
            total_passages: 0,
            query_strategy: "helper_error".to_string(),
            error: err.error,
        });
    }

    #[derive(Deserialize)]
    struct PyOut {
        passages: Vec<PyPassage>,
        total_passages: u32,
        query_strategy: String,
    }
    #[derive(Deserialize)]
    struct PyPassage {
        text: String,
        score: f64,
        ord: u32,
    }

    let parsed: PyOut = serde_json::from_str(&stdout).map_err(|e| {
        format!("BL-L26: bm25 helper 输出 JSON 解析失败: {e}\nstdout: {stdout}")
    })?;

    Ok(AttachmentBm25Result {
        passages: parsed.passages.into_iter().map(|p| AttachmentPassage {
            text: p.text,
            score: p.score,
            ord: p.ord,
        }).collect(),
        total_passages: parsed.total_passages,
        query_strategy: parsed.query_strategy,
        error: String::new(),
    })
}
