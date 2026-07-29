//! BL-FILE-SESSION-INDEX-V1 Phase 1 — 附件 metadata 持久化
//!
//! 解决问题: 之前附件 (PDF / Excel / 图片 / 音频) 在 useChat.ts 拖入后
//! 只在 React in-memory state 里, 切会话 / 重启 Companion 就没了. state.db
//! 里只留占位字符串 ("📄 1 份文档 (xxx.pdf) — in-memory, 切会话不保留").
//!
//! 设计原则 (跟 BL-FILE-SESSION-INDEX-V1.md 一致):
//!   - 中央 0 红线: 附件 metadata 在边缘员工本机, **不上中央**
//!   - 不存内容: 只存路径 / 文件名 / kind / 大小. 内容 (preview + sidecar)
//!     仍在 ~/.catfish/uploads/<ts>-<name> 物理文件
//!   - 独立 db: ~/.catfish/attachments.db (跟 hermes state.db 完全分开,
//!     升级 hermes schema 不冲突)
//!   - 跟 hermes 共用 session_id / message_id (这俩是 hermes state.db 主键,
//!     attachments 表里只引用 string id, 不做 FK 约束 — 因为两 db 物理分离)
//!
//! Tauri commands 暴露给前端:
//!   - attachment_record: 发消息时调, INSERT metadata
//!   - attachment_list_by_session: 切会话回该 session 时调, 恢复附件 chip
//!   - attachment_search_local: 按 name 模糊搜 (Phase 2 用 tool-bridge 实现真 BM25)
//!   - attachment_delete: 员工 / admin 主动删某附件 metadata + 物理文件
//!   - attachment_delete_by_user: 离职清理整 user 所有附件 (X-Catfish-User 限定)
//!
//! Phase 2 (tool-bridge 那边) 会读这个 db 做跨会话 BM25 搜.
//!
//! P3.5.8 (6/16 鸿波 BL-FILE-SESSION-INDEX-V1 Phase 2 真补): 加 attachment_load_base64
//! 命令读 keptPath 转 base64. 让 sessionMessages.ts resume 时把图片还原到
//! message.attachments, chatWire.toWire 看到 attachments 才走 multipart 分支带
//! image_url 给上游 LLM 看. Phase 1 (5/30) 只持久化 metadata 不还原 base64, 切走
//! session / 重启 Companion 后历史图片在 wire 里全失踪, gateway 看到 user_multipart=0,
//! 小鲶真没看到图编借口糊弄 ("X-Catfish-User 认证头" 类). 私有 Qwen3.5 122B /
//! Qwen3-VL 30B 都 supports_vision=true, 锅在客户端 wire 链路.

use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::Engine;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

const BUSY_TIMEOUT_MS: u32 = 5000;

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn attachment_db_path() -> Result<PathBuf, String> {
    let home = home_dir().ok_or_else(|| "找不到 home 目录".to_string())?;
    let catfish_dir = home.join(".catfish");
    std::fs::create_dir_all(&catfish_dir)
        .map_err(|e| format!("建 ~/.catfish/ 失败: {e}"))?;
    Ok(catfish_dir.join("attachments.db"))
}

fn open_db() -> Result<Connection, String> {
    let path = attachment_db_path()?;
    let conn = Connection::open(&path)
        .map_err(|e| format!("打开 attachments.db 失败: {e}"))?;
    conn.busy_timeout(std::time::Duration::from_millis(BUSY_TIMEOUT_MS as u64))
        .map_err(|e| format!("设 busy_timeout 失败: {e}"))?;
    init_schema(&conn)?;
    Ok(conn)
}

/// 幂等建表 + index. 跟 hermes state.db 完全独立, schema 演化我们自己控.
fn init_schema(conn: &Connection) -> Result<(), String> {
    conn.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS attachments (
            id              TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            session_id      TEXT NOT NULL,
            message_id      TEXT NOT NULL,
            kind            TEXT NOT NULL,
            file_kind       TEXT,
            name            TEXT NOT NULL,
            mime_type       TEXT,
            size_bytes      INTEGER,
            kept_path       TEXT,
            parsed_text_path TEXT,
            meta            TEXT,
            created_at      REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attachments_user_session
            ON attachments(user_id, session_id);
        CREATE INDEX IF NOT EXISTS idx_attachments_created
            ON attachments(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_attachments_name
            ON attachments(name);
        CREATE INDEX IF NOT EXISTS idx_attachments_user
            ON attachments(user_id);
        "#,
    )
    .map_err(|e| format!("建 attachments 表失败: {e}"))?;
    Ok(())
}

fn now_unix() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn random_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    let pid = std::process::id();
    let mut state: u64 = (nanos as u64).wrapping_mul(2654435761) ^ (pid as u64);
    let mut out = String::with_capacity(16);
    for _ in 0..16 {
        state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        let nibble = (state >> 60) & 0xF;
        out.push(std::char::from_digit(nibble as u32, 16).unwrap_or('0'));
    }
    out
}

// ============================================================
// Tauri commands
// ============================================================

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentRecordInput {
    pub user_id: String,
    pub session_id: String,
    /// 来自 useChat persistMessage 写 messages 表后的 rowid (作 string).
    /// 没有时传空串, 我们仍 INSERT (作 floating attachment).
    pub message_id: String,
    /// "image" | "file" | "audio"
    pub kind: String,
    /// "pdf" | "xlsx" | "docx" | ...
    pub file_kind: Option<String>,
    pub name: String,
    pub mime_type: Option<String>,
    pub size_bytes: Option<i64>,
    /// ~/.catfish/uploads/<ts>-<name>
    pub kept_path: Option<String>,
    /// ~/.catfish/uploads/<ts>-<name>.parsed.txt (大文件 BM25 sidecar)
    pub parsed_text_path: Option<String>,
    /// 自由 JSON 字符串 (transcript_chars / duration / ext / ...)
    pub meta: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentRecordOutput {
    pub id: String,
    pub created_at: f64,
}

#[tauri::command(rename_all = "camelCase")]
pub async fn attachment_record(
    input: AttachmentRecordInput,
) -> Result<AttachmentRecordOutput, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db()?;
        let id = random_id();
        let created_at = now_unix();
        conn.execute(
            r#"
            INSERT INTO attachments (
                id, user_id, session_id, message_id,
                kind, file_kind, name, mime_type, size_bytes,
                kept_path, parsed_text_path, meta, created_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)
            "#,
            params![
                id,
                input.user_id,
                input.session_id,
                input.message_id,
                input.kind,
                input.file_kind,
                input.name,
                input.mime_type,
                input.size_bytes,
                input.kept_path,
                input.parsed_text_path,
                input.meta,
                created_at,
            ],
        )
        .map_err(|e| format!("insert attachment 失败: {e}"))?;
        Ok::<AttachmentRecordOutput, String>(AttachmentRecordOutput { id, created_at })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentRow {
    pub id: String,
    pub user_id: String,
    pub session_id: String,
    pub message_id: String,
    pub kind: String,
    pub file_kind: Option<String>,
    pub name: String,
    pub mime_type: Option<String>,
    pub size_bytes: Option<i64>,
    pub kept_path: Option<String>,
    pub parsed_text_path: Option<String>,
    pub meta: Option<String>,
    pub created_at: f64,
}

fn row_to_attachment(row: &rusqlite::Row<'_>) -> rusqlite::Result<AttachmentRow> {
    Ok(AttachmentRow {
        id: row.get(0)?,
        user_id: row.get(1)?,
        session_id: row.get(2)?,
        message_id: row.get(3)?,
        kind: row.get(4)?,
        file_kind: row.get(5)?,
        name: row.get(6)?,
        mime_type: row.get(7)?,
        size_bytes: row.get(8)?,
        kept_path: row.get(9)?,
        parsed_text_path: row.get(10)?,
        meta: row.get(11)?,
        created_at: row.get(12)?,
    })
}

const SELECT_COLS: &str = "id, user_id, session_id, message_id, kind, file_kind, \
    name, mime_type, size_bytes, kept_path, parsed_text_path, meta, created_at";

/// 列某 session 的所有附件 (按 created_at 升序, 跟消息时间顺序对齐).
#[tauri::command(rename_all = "camelCase")]
pub async fn attachment_list_by_session(
    session_id: String,
) -> Result<Vec<AttachmentRow>, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db()?;
        let sql = format!(
            "SELECT {SELECT_COLS} FROM attachments WHERE session_id = ?1 \
             ORDER BY created_at ASC"
        );
        let mut stmt = conn.prepare(&sql).map_err(|e| format!("prepare: {e}"))?;
        let rows = stmt
            .query_map(params![session_id], row_to_attachment)
            .map_err(|e| format!("query: {e}"))?
            .filter_map(|r| r.ok())
            .collect();
        Ok::<Vec<AttachmentRow>, String>(rows)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 列某员工的所有附件 (按 created_at 倒序, 最近的在前).
/// 可选 limit (默认 50), file_kind filter.
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentListByUserInput {
    pub user_id: String,
    pub limit: Option<u32>,
    pub file_kind: Option<String>,
    pub days_back: Option<u32>,
}

#[tauri::command(rename_all = "camelCase")]
pub async fn attachment_list_by_user(
    input: AttachmentListByUserInput,
) -> Result<Vec<AttachmentRow>, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db()?;
        let limit = input.limit.unwrap_or(50).clamp(1, 500);
        let days_back = input.days_back.unwrap_or(90);
        let cutoff = now_unix() - (days_back as f64 * 86400.0);
        // borrow checker: 把 query_map 结果绑 local var, 让 stmt 在 collect 之后再 drop.
        // 之前写法 stmt 在 block end drop, 但 .map_err(...)? 留下的临时 ControlFlow
        // 还借着 stmt → "stmt dropped while still borrowed".
        let rows: Vec<AttachmentRow> = if let Some(fk) = input.file_kind.as_deref() {
            let sql = format!(
                "SELECT {SELECT_COLS} FROM attachments \
                 WHERE user_id = ?1 AND file_kind = ?2 AND created_at >= ?3 \
                 ORDER BY created_at DESC LIMIT ?4"
            );
            let mut stmt = conn.prepare(&sql).map_err(|e| format!("prepare: {e}"))?;
            let mapped = stmt
                .query_map(params![input.user_id, fk, cutoff, limit as i64], row_to_attachment)
                .map_err(|e| format!("query: {e}"))?;
            let v: Vec<AttachmentRow> = mapped.filter_map(|r| r.ok()).collect();
            v
        } else {
            let sql = format!(
                "SELECT {SELECT_COLS} FROM attachments \
                 WHERE user_id = ?1 AND created_at >= ?2 \
                 ORDER BY created_at DESC LIMIT ?3"
            );
            let mut stmt = conn.prepare(&sql).map_err(|e| format!("prepare: {e}"))?;
            let mapped = stmt
                .query_map(params![input.user_id, cutoff, limit as i64], row_to_attachment)
                .map_err(|e| format!("query: {e}"))?;
            let v: Vec<AttachmentRow> = mapped.filter_map(|r| r.ok()).collect();
            v
        };
        Ok::<Vec<AttachmentRow>, String>(rows)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 按文件名模糊搜 (LIKE), 限定 user_id. 跨 session.
/// 真 BM25 内容搜在 tool-bridge attachments_search.py.
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentSearchInput {
    pub user_id: String,
    pub query: String,
    pub limit: Option<u32>,
}

#[tauri::command(rename_all = "camelCase")]
pub async fn attachment_search_local(
    input: AttachmentSearchInput,
) -> Result<Vec<AttachmentRow>, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db()?;
        let limit = input.limit.unwrap_or(20).clamp(1, 200);
        let pattern = format!(
            "%{}%",
            input
                .query
                .replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_")
        );
        let sql = format!(
            "SELECT {SELECT_COLS} FROM attachments \
             WHERE user_id = ?1 AND name LIKE ?2 ESCAPE '\\' \
             ORDER BY created_at DESC LIMIT ?3"
        );
        let mut stmt = conn.prepare(&sql).map_err(|e| format!("prepare: {e}"))?;
        let rows = stmt
            .query_map(params![input.user_id, pattern, limit as i64], row_to_attachment)
            .map_err(|e| format!("query: {e}"))?
            .filter_map(|r| r.ok())
            .collect();
        Ok::<Vec<AttachmentRow>, String>(rows)
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 按 id 删除单条附件 metadata. 物理文件 (kept_path / parsed_text_path) 也一并删.
/// 调用方传 user_id 做权限校验 (防别处 id 串到别员工的附件被删).
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentDeleteInput {
    pub user_id: String,
    pub id: String,
    /// 默认 true. 设 false 只删 metadata 不删物理文件 (调试用).
    pub delete_files: Option<bool>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentDeleteOutput {
    pub deleted: bool,
    pub files_removed: u32,
}

#[tauri::command(rename_all = "camelCase")]
pub async fn attachment_delete(
    input: AttachmentDeleteInput,
) -> Result<AttachmentDeleteOutput, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db()?;
        // 取出物理路径, 同时验证 user_id 匹配
        let row: Option<(Option<String>, Option<String>)> = conn
            .query_row(
                "SELECT kept_path, parsed_text_path FROM attachments \
                 WHERE id = ?1 AND user_id = ?2",
                params![input.id, input.user_id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()
            .map_err(|e| format!("query: {e}"))?;
        let (kept, sidecar) = match row {
            Some(t) => t,
            None => {
                return Ok(AttachmentDeleteOutput { deleted: false, files_removed: 0 });
            }
        };
        let n = conn
            .execute(
                "DELETE FROM attachments WHERE id = ?1 AND user_id = ?2",
                params![input.id, input.user_id],
            )
            .map_err(|e| format!("delete: {e}"))?;

        let mut files_removed = 0u32;
        if input.delete_files.unwrap_or(true) {
            for p in [kept, sidecar].into_iter().flatten() {
                if std::path::Path::new(&p).exists() && std::fs::remove_file(&p).is_ok() {
                    files_removed += 1;
                }
            }
        }
        Ok::<AttachmentDeleteOutput, String>(AttachmentDeleteOutput {
            deleted: n > 0,
            files_removed,
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

/// 删某员工所有附件 (用于离职清理). 同样物理文件一起删.
/// 返删除条数 + 删除物理文件数.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentDeleteByUserOutput {
    pub rows_deleted: u32,
    pub files_removed: u32,
}

#[tauri::command(rename_all = "camelCase")]
pub async fn attachment_delete_by_user(
    user_id: String,
) -> Result<AttachmentDeleteByUserOutput, String> {
    tokio::task::spawn_blocking(move || {
        let conn = open_db()?;
        // 先收集所有物理路径
        let mut stmt = conn
            .prepare("SELECT kept_path, parsed_text_path FROM attachments WHERE user_id = ?1")
            .map_err(|e| format!("prepare: {e}"))?;
        let paths: Vec<(Option<String>, Option<String>)> = stmt
            .query_map(params![user_id], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(|e| format!("query: {e}"))?
            .filter_map(|r| r.ok())
            .collect();
        drop(stmt);

        let rows_deleted = conn
            .execute("DELETE FROM attachments WHERE user_id = ?1", params![user_id])
            .map_err(|e| format!("delete: {e}"))? as u32;

        let mut files_removed = 0u32;
        for (kept, sidecar) in paths {
            for p in [kept, sidecar].into_iter().flatten() {
                if std::path::Path::new(&p).exists() && std::fs::remove_file(&p).is_ok() {
                    files_removed += 1;
                }
            }
        }
        Ok::<AttachmentDeleteByUserOutput, String>(AttachmentDeleteByUserOutput {
            rows_deleted,
            files_removed,
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

// ============================================================
// P3.5.8 Phase 2: image 落盘 + 从 keptPath 读 base64
// ============================================================

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentSaveImageOutput {
    /// 持久化到 ~/.catfish/uploads/<ts>-<safe-name> 的绝对路径.
    /// 写入 attachments.db keptPath 字段, resume 时 attachment_load_base64 读这个路径还原 base64.
    pub kept_path: String,
    pub size_bytes: u64,
}

/// 把 base64 image 写到 ~/.catfish/uploads/, 返 keptPath.
///
/// attachmentHelpers.ts image 分支调一次. 之前 image 只 in-memory base64 → 切走 session
/// 后 wire 里失踪. 现在跟 file attachment 同款 — 落盘 + 写 attachments.db keptPath,
/// resume 时 sessionMessages.loadSessionMessagesAsChatAsync → attachment_load_base64
/// 读回 base64 填回 message.attachments, wire 重新带 image_url 给 vision LLM.
///
/// 文件名: `<unix-ts>-<safe-name>`. ts 防同名覆盖, safe-name 砍 / \ \0 防注入.
///
/// size 上限 20MB (跟 attachment_load_base64 一致).
#[tauri::command(rename_all = "camelCase")]
pub async fn attachment_save_image(
    base64_data: String,
    filename: String,
) -> Result<AttachmentSaveImageOutput, String> {
    tokio::task::spawn_blocking(move || {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(base64_data.trim())
            .map_err(|e| format!("base64 解码失败: {e}"))?;
        let size_bytes = bytes.len() as u64;
        const MAX_BYTES: u64 = 20 * 1024 * 1024;
        if size_bytes > MAX_BYTES {
            return Err(format!(
                "图片 {} bytes 超过 20MB 上限, 拒收",
                size_bytes
            ));
        }
        if bytes.is_empty() {
            return Err("空图片".to_string());
        }

        let home = home_dir().ok_or_else(|| "找不到 home 目录".to_string())?;
        let uploads_dir = home.join(".catfish").join("uploads");
        std::fs::create_dir_all(&uploads_dir)
            .map_err(|e| format!("建 uploads dir 失败: {e}"))?;

        let safe = filename
            .replace(['/', '\\', '\0'], "_")
            .chars()
            .take(120)  // 防超长 filename 撞 ext4 / apfs 上限
            .collect::<String>();
        let safe = if safe.is_empty() { "pasted-image.png".to_string() } else { safe };
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        // 加 nanos 防同秒多次粘贴撞名
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let kept = uploads_dir.join(format!("{ts}-{nanos}-{safe}"));

        std::fs::write(&kept, &bytes).map_err(|e| format!("写 image 失败: {e}"))?;

        Ok::<AttachmentSaveImageOutput, String>(AttachmentSaveImageOutput {
            kept_path: kept.to_string_lossy().to_string(),
            size_bytes,
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AttachmentLoadBase64Output {
    /// 文件 base64 编码 (不带 data: 前缀, 前端自己拼 `data:${mimeType};base64,${base64}`)
    pub base64: String,
    /// 文件实际字节数 (sanity check: 跟 attachments.db 里 sizeBytes 对得上)
    pub size_bytes: u64,
}

/// 从 keptPath 文件读字节 → base64 encode → 返前端拼 data URL.
///
/// 用途: BL-FILE-SESSION-INDEX-V1 Phase 2. session resume 时 sessionMessages.ts
/// 对每条 image attachment 调一次, 把 base64 填回 ChatMessage.attachments,
/// 让后续 toWire 走 multipart 分支带 image_url 给上游 LLM.
///
/// 安全:
///   - 路径必须在 ~/.catfish/uploads/ 下 (防员工传任意路径让 Companion 读敏感文件)
///   - 文件 size 上限 20MB (image 一般 < 5MB, 给宽裕; 防误读巨大 file 内存爆)
#[tauri::command(rename_all = "camelCase")]
pub async fn attachment_load_base64(
    kept_path: String,
) -> Result<AttachmentLoadBase64Output, String> {
    tokio::task::spawn_blocking(move || {
        // 路径白名单: 必须在 ~/.catfish/uploads/ 下 (Companion 自己存的)
        let home = home_dir().ok_or_else(|| "找不到 home 目录".to_string())?;
        let uploads_root = home.join(".catfish").join("uploads");
        let path = PathBuf::from(&kept_path);
        let canonical = path
            .canonicalize()
            .map_err(|e| format!("路径不存在或无法访问: {kept_path} ({e})"))?;
        let uploads_canonical = uploads_root
            .canonicalize()
            .map_err(|e| format!("~/.catfish/uploads/ 不存在: {e}"))?;
        if !canonical.starts_with(&uploads_canonical) {
            return Err(format!(
                "拒载 — 路径不在 ~/.catfish/uploads/ 白名单内: {}",
                canonical.display()
            ));
        }

        // size 上限 20MB
        let meta = std::fs::metadata(&canonical)
            .map_err(|e| format!("读 metadata 失败: {e}"))?;
        let size_bytes = meta.len();
        const MAX_BYTES: u64 = 20 * 1024 * 1024;
        if size_bytes > MAX_BYTES {
            return Err(format!(
                "文件 {} bytes 超过 20MB 上限, 拒载",
                size_bytes
            ));
        }

        // 读 + base64
        let bytes = std::fs::read(&canonical)
            .map_err(|e| format!("读文件失败: {e}"))?;
        let b64 = base64::engine::general_purpose::STANDARD.encode(&bytes);

        Ok::<AttachmentLoadBase64Output, String>(AttachmentLoadBase64Output {
            base64: b64,
            size_bytes,
        })
    })
    .await
    .map_err(|e| format!("内部错误: {e}"))?
}

// ============================================================
// 单测
// ============================================================
#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::test_env::ENV_LOCK;
    use tempfile::TempDir;

    fn setup_test_env() -> (TempDir, std::sync::MutexGuard<'static, ()>) {
        let guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let tmp = TempDir::new().expect("tempdir");
        std::env::set_var("HOME", tmp.path());
        (tmp, guard)
    }

    fn make_input(user: &str, session: &str, name: &str) -> AttachmentRecordInput {
        AttachmentRecordInput {
            user_id: user.into(),
            session_id: session.into(),
            message_id: "msg-1".into(),
            kind: "file".into(),
            file_kind: Some("pdf".into()),
            name: name.into(),
            mime_type: Some("application/pdf".into()),
            size_bytes: Some(12345),
            kept_path: None,
            parsed_text_path: None,
            meta: None,
        }
    }

    #[tokio::test]
    async fn record_and_list_by_session() {
        let (_t, _g) = setup_test_env();
        let r = attachment_record(make_input("u@x.com", "sess-1", "a.pdf"))
            .await
            .unwrap();
        assert!(!r.id.is_empty());

        let rows = attachment_list_by_session("sess-1".into()).await.unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].name, "a.pdf");
        assert_eq!(rows[0].user_id, "u@x.com");
    }

    #[tokio::test]
    async fn list_by_user_filters_user_and_kind() {
        let (_t, _g) = setup_test_env();
        attachment_record(make_input("a@x.com", "s1", "x.pdf")).await.unwrap();
        attachment_record(make_input("a@x.com", "s1", "y.xlsx").tap(|i| i.file_kind = Some("xlsx".into()))).await.unwrap();
        attachment_record(make_input("b@x.com", "s2", "z.pdf")).await.unwrap();

        let all_a = attachment_list_by_user(AttachmentListByUserInput {
            user_id: "a@x.com".into(),
            limit: None,
            file_kind: None,
            days_back: None,
        })
        .await
        .unwrap();
        assert_eq!(all_a.len(), 2);

        let only_xlsx = attachment_list_by_user(AttachmentListByUserInput {
            user_id: "a@x.com".into(),
            limit: None,
            file_kind: Some("xlsx".into()),
            days_back: None,
        })
        .await
        .unwrap();
        assert_eq!(only_xlsx.len(), 1);
        assert_eq!(only_xlsx[0].name, "y.xlsx");
    }

    // 简易 helper 让 chain modify (类似 Kotlin .apply)
    trait Tap: Sized {
        fn tap(mut self, f: impl FnOnce(&mut Self)) -> Self {
            f(&mut self);
            self
        }
    }
    impl Tap for AttachmentRecordInput {}

    #[tokio::test]
    async fn search_local_by_name() {
        let (_t, _g) = setup_test_env();
        attachment_record(make_input("u@x.com", "s", "客户合同_v3.pdf")).await.unwrap();
        attachment_record(make_input("u@x.com", "s", "周报-W23.xlsx").tap(|i| i.file_kind = Some("xlsx".into()))).await.unwrap();
        attachment_record(make_input("other@x.com", "s", "客户合同_v4.pdf")).await.unwrap();

        let hits = attachment_search_local(AttachmentSearchInput {
            user_id: "u@x.com".into(),
            query: "客户合同".into(),
            limit: None,
        })
        .await
        .unwrap();
        assert_eq!(hits.len(), 1, "应只命中 u@x.com 自己的, 不串到 other@x.com");
        assert_eq!(hits[0].name, "客户合同_v3.pdf");
    }

    #[tokio::test]
    async fn delete_removes_row_and_user_iso() {
        let (_t, _g) = setup_test_env();
        let r = attachment_record(make_input("u@x.com", "s", "a.pdf")).await.unwrap();

        // 别人不能删我的
        let bad = attachment_delete(AttachmentDeleteInput {
            user_id: "other@x.com".into(),
            id: r.id.clone(),
            delete_files: Some(false),
        })
        .await
        .unwrap();
        assert!(!bad.deleted);

        // 我自己可以删
        let good = attachment_delete(AttachmentDeleteInput {
            user_id: "u@x.com".into(),
            id: r.id.clone(),
            delete_files: Some(false),
        })
        .await
        .unwrap();
        assert!(good.deleted);

        let still = attachment_list_by_session("s".into()).await.unwrap();
        assert!(still.is_empty());
    }

    #[tokio::test]
    async fn delete_by_user_clears_all() {
        let (_t, _g) = setup_test_env();
        for n in &["a.pdf", "b.xlsx", "c.docx"] {
            attachment_record(make_input("leaver@x.com", "s", n)).await.unwrap();
        }
        attachment_record(make_input("keeper@x.com", "s", "z.pdf")).await.unwrap();

        let out = attachment_delete_by_user("leaver@x.com".into()).await.unwrap();
        assert_eq!(out.rows_deleted, 3);

        let still = attachment_list_by_user(AttachmentListByUserInput {
            user_id: "keeper@x.com".into(),
            limit: None,
            file_kind: None,
            days_back: None,
        })
        .await
        .unwrap();
        assert_eq!(still.len(), 1, "keeper 不受影响");
    }
}
