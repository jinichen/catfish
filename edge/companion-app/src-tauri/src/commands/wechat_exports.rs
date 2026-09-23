//! 微信「合并转发」导出 ZIP 的导入 (9/23)。
//!
//! 入口是聊天框: 员工拖入 ZIP → 前端把字节 (base64) 交给 `wechat_export_stage`
//! → 这里落到导入库的 `.staging/` 下, 让读取器 `inspect` 一遍 (只看不存) →
//! 前端弹确认框 (群名 / 我是谁 / 首次授权) → `wechat_export_import` 真正导入,
//! 顺手 `render` 出整理好的文本放进这条聊天消息 → 删掉暂存文件。
//!
//! 解析、认群、去重全在 catfish-wechat-reader (Python) 里, 两个平台同一份代码;
//! 这里只管搬文件、调读取器、写授权 —— 不在 Rust 里再写一遍规则。
//!
//! 导入库固定在 `~/.catfish/wechat-exports/` (Windows 是 `%USERPROFILE%\.catfish\...`),
//! 跟 Tool Bridge 读的是同一个目录 (授权配置里的 source_path)。

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use serde_json::{json, Value};

use super::wechat_archive::{
    catfish_home, default_export_helper_path, helper_installed, load_config, reader_command,
    supported_platform, verify_helper, write_config, WeChatArchiveConfig, CONFIG_VERSION,
    SOURCE_LIBRARY,
};

/// 聊天框走 base64 过 IPC; 带几十张图的导出也就几十 MB, 200 MB 足够且不至于卡死。
const MAX_ZIP_BYTES: usize = 200 * 1024 * 1024;
/// 放进聊天消息的整理文本上限 (字符)。超过的部分模型用 catfish_wechat_history 按需查。
const RENDER_MAX_CHARS: u32 = 30_000;
/// 落进 uploads 的全文上限 (读取器 render 自己也封顶 20 万)。
const FULL_RENDER_MAX_CHARS: u32 = 200_000;
const READER_TIMEOUT: Duration = Duration::from_secs(90);
/// 员工拖进来却没点「导入」也没点「取消」(比如直接关了窗口) —— 暂存文件留一小时就清。
const STAGE_TTL: Duration = Duration::from_secs(3600);

pub(crate) fn library_dir() -> Result<PathBuf, String> {
    Ok(catfish_home()?.join("wechat-exports"))
}

fn staging_dir() -> Result<PathBuf, String> {
    Ok(library_dir()?.join(".staging"))
}

/// stage id 是我们自己生成的 32 位小写十六进制; 前端传回来的一律按这个格式校验,
/// 防止拿它拼出库外路径。
fn valid_stage_id(id: &str) -> bool {
    id.len() == 32 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn valid_group_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.bytes().all(|b| b.is_ascii_hexdigit())
}

fn staged_path(stage_id: &str) -> Result<PathBuf, String> {
    if !valid_stage_id(stage_id) {
        return Err("导入编号无效".to_string());
    }
    Ok(staging_dir()?.join(format!("{stage_id}.zip")))
}

fn new_stage_id() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut bytes);
    hex::encode(bytes)
}

fn prune_stale_stages(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    let now = SystemTime::now();
    for entry in entries.flatten() {
        let stale = entry
            .metadata()
            .and_then(|meta| meta.modified())
            .ok()
            .and_then(|modified| now.duration_since(modified).ok())
            .is_some_and(|age| age > STAGE_TTL);
        if stale {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

fn resolve_helper() -> Result<PathBuf, String> {
    let configured = load_config()
        .map(|config| config.helper_path)
        .filter(|path| !path.trim().is_empty())
        .map(PathBuf::from);
    let path = match configured {
        Some(path) => path,
        None => default_export_helper_path()?,
    };
    let helper = path
        .canonicalize()
        .map_err(|_| format!("微信聊天读取器未安装: {}", path.display()))?;
    if !helper_installed(&helper) {
        return Err(format!("微信聊天读取器不可执行: {}", helper.display()));
    }
    Ok(helper)
}

/// 调读取器, 返回它输出的 JSON。读取器失败时 stdout 仍是 `{"ok": false, "error"}`,
/// 把那句原因原样交给员工 (比如「不是认识的微信聊天记录格式」), 不笼统报「失败」。
async fn run_reader(helper: &Path, args: Vec<OsString>) -> Result<Value, String> {
    let mut command = reader_command(helper);
    command.args(args);
    let output = tokio::time::timeout(READER_TIMEOUT, command.output())
        .await
        .map_err(|_| "微信聊天读取器超时".to_string())?
        .map_err(|e| format!("微信聊天读取器无法启动: {e}"))?;
    let parsed: Option<Value> = serde_json::from_slice(&output.stdout).ok();
    match parsed {
        Some(value) if value.get("ok").and_then(Value::as_bool) == Some(true) => Ok(value),
        Some(value) => Err(value
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("微信聊天读取器返回失败")
            .chars()
            .take(300)
            .collect()),
        None => {
            // 没有 JSON 多半是命令行本身没认出来 (比如老版本读取器没有这个子命令),
            // argparse 的报错在 stderr, 只含命令名, 不含聊天内容 —— 带最后一行出来好排查。
            let stderr = String::from_utf8_lossy(&output.stderr);
            let hint: String = stderr
                .lines()
                .rev()
                .find(|line| !line.trim().is_empty())
                .unwrap_or("")
                .chars()
                .take(200)
                .collect();
            Err(format!(
                "微信聊天读取器没有返回有效结果 (退出码 {}){}",
                output.status.code().map_or("?".to_string(), |code| code.to_string()),
                if hint.is_empty() { String::new() } else { format!(": {hint}") },
            ))
        }
    }
}

/// 9/23 实测: 开发机上装的还是 0.1.0 读取器 (没有 inspect / import), 拖进 ZIP 只得到
/// 「退出码 2」。先问读取器自己支不支持微信 ZIP, 不支持就直接说清楚要升级。
async fn ensure_supports_wechat_zip(helper: &Path) -> Result<(), String> {
    // doctor 的输出没有 ok 字段, 不走 run_reader
    let mut command = reader_command(helper);
    command.arg("doctor").arg("--json");
    let output = tokio::time::timeout(READER_TIMEOUT, command.output())
        .await
        .map_err(|_| "微信聊天读取器自检超时".to_string())?
        .map_err(|e| format!("微信聊天读取器无法启动: {e}"))?;
    let report: Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "微信聊天读取器自检没有返回有效结果".to_string())?;
    let supported = report
        .get("formats")
        .and_then(Value::as_array)
        .is_some_and(|formats| formats.iter().any(|f| f.as_str() == Some("wechat_zip")));
    if supported {
        Ok(())
    } else {
        Err("本机的微信聊天读取器版本太旧，还不支持微信导出的 ZIP。重启 Companion 会自动升级；\
             开发环境请手动重装 edge/wechat-reader。"
            .to_string())
    }
}

fn arg(value: impl Into<OsString>) -> OsString {
    value.into()
}

/// 当前授权是否已经覆盖导入库 + 当前 Picker 模型。不覆盖就要在确认框里让员工同意。
fn needs_consent(current_model: Option<&str>) -> bool {
    let Some(model) = current_model else { return true };
    !load_config().is_some_and(|config| {
        config.version == CONFIG_VERSION
            && config.enabled
            && config.source_type == SOURCE_LIBRARY
            && config.consented_picker_model == model
    })
}

#[tauri::command]
pub async fn wechat_export_stage(file_b64: String, filename: String) -> Result<Value, String> {
    use base64::Engine;
    if !supported_platform() {
        return Err("当前系统不支持微信聊天记录导入".to_string());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(file_b64.trim())
        .map_err(|e| format!("文件读取失败: {e}"))?;
    if bytes.len() > MAX_ZIP_BYTES {
        return Err("微信导出文件超过 200 MB".to_string());
    }
    let helper = resolve_helper()?;
    ensure_supports_wechat_zip(&helper).await?;
    let dir = staging_dir()?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建暂存目录失败: {e}"))?;
    prune_stale_stages(&dir);
    let stage_id = new_stage_id();
    let path = dir.join(format!("{stage_id}.zip"));
    std::fs::write(&path, &bytes).map_err(|e| format!("暂存文件写入失败: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    let inspected = run_reader(&helper, vec![
        arg("inspect"), arg("--json"),
        arg("--source"), path.clone().into_os_string(),
        arg("--library"), library_dir()?.into_os_string(),
    ])
    .await;
    let inspect = match inspected {
        Ok(value) => value,
        Err(error) => {
            let _ = std::fs::remove_file(&path);
            return Err(error);
        }
    };
    let current = crate::services::picker_config::current_model();
    Ok(json!({
        "stageId": stage_id,
        "filename": filename,
        "sizeBytes": bytes.len(),
        "inspect": inspect,
        "needsConsent": needs_consent(current.as_deref()),
        "pickerModel": current,
    }))
}

#[tauri::command]
pub async fn wechat_export_import(
    stage_id: String,
    group_id: Option<String>,
    group_name: Option<String>,
    self_name: Option<String>,
    consent: bool,
    max_documents: Option<u32>,
) -> Result<Value, String> {
    let path = staged_path(&stage_id)?;
    if !path.is_file() {
        return Err("暂存的导出文件已过期，请重新拖入".to_string());
    }
    let model = crate::services::picker_config::current_model()
        .ok_or_else(|| "请先在聊天 Picker 中选择模型".to_string())?;
    let consent_required = needs_consent(Some(&model));
    if consent_required && !consent {
        return Err("需要先确认：导入的聊天内容会交给当前模型分析".to_string());
    }
    let helper = resolve_helper()?;
    let library = library_dir()?;

    let mut args = vec![
        arg("import"), arg("--json"),
        arg("--source"), path.clone().into_os_string(),
        arg("--library"), library.clone().into_os_string(),
    ];
    match (group_id.as_deref(), group_name.as_deref().map(str::trim)) {
        (Some(id), _) if valid_group_id(id) => args.extend([arg("--group-id"), arg(id)]),
        (Some(_), _) => return Err("群编号无效".to_string()),
        (None, Some(name)) if !name.is_empty() => args.extend([arg("--group-name"), arg(name)]),
        _ => {}
    }
    if let Some(name) = self_name.as_deref() {
        // 空字符串 = 「我不在这些发送人里」, 读取器那边就是这个约定
        args.extend([arg("--self-name"), arg(name)]);
    }
    let imported = run_reader(&helper, args).await?;

    let effective_self = imported.get("self_name").and_then(Value::as_str).unwrap_or("");
    let group_label = imported
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("微信聊天")
        .to_string();
    let render = |max_chars: u32| {
        let mut render_args = vec![
            arg("render"), arg("--json"),
            arg("--source"), path.clone().into_os_string(),
            arg("--max-chars"), arg(max_chars.to_string()),
        ];
        if !effective_self.is_empty() {
            render_args.extend([arg("--self-name"), arg(effective_self)]);
        }
        render_args
    };
    // 全文 (读取器上限 20 万字) 落一份 .txt 进 uploads, 作为这个附件的 keptPath ——
    // 员工说「存进知识库」时, 现成的 catfish_wiki_ingest 就读它, 不用另写入库逻辑。
    let full = run_reader(&helper, render(FULL_RENDER_MAX_CHARS)).await?;
    let full_text = full.get("text").and_then(Value::as_str).unwrap_or("");
    let rendered = if full_text.chars().count() <= RENDER_MAX_CHARS as usize {
        full.clone()
    } else {
        run_reader(&helper, render(RENDER_MAX_CHARS)).await?
    };
    let transcript_path = write_transcript_upload(&group_label, &full)?;
    let (documents, skipped_documents) =
        read_documents(&helper, &path, &stage_id, max_documents.unwrap_or(0)).await;

    if consent_required {
        // 首次 (或换了模型后) 的授权: 读取器过一遍安全自检才写, 跟原来卡片上的
        // 「确认授权」是同一个判据, 只是挪到了导入确认框里。
        verify_helper(&helper).await?;
        write_config(&WeChatArchiveConfig {
            version: CONFIG_VERSION,
            enabled: true,
            helper_path: helper.to_string_lossy().to_string(),
            source_type: SOURCE_LIBRARY.to_string(),
            source_path: library.to_string_lossy().to_string(),
            source_size: 0,
            source_modified_ns: 0,
            consented_picker_model: model,
            consented_at: chrono::Utc::now().to_rfc3339(),
        })?;
    }
    let _ = std::fs::remove_file(&path);
    Ok(json!({
        "groupId": imported.get("group_id"),
        "name": imported.get("name"),
        "selfName": imported.get("self_name"),
        "alreadyImported": imported.get("already_imported"),
        "transcript": rendered.get("text"),
        "truncated": rendered.get("truncated"),
        "renderedCount": rendered.get("rendered_count"),
        "messageCount": rendered.get("message_count"),
        "transcriptPath": transcript_path,
        "documents": documents,
        "skippedDocuments": skipped_documents,
    }))
}

/// 整理好的全文写成 `~/.catfish/uploads/<ts>-<群名>-微信聊天记录.txt`。
/// 跟聊天框上传的文件放在一起、同一种命名, 隐私清单 / 清理逻辑不用为它另开一类。
fn write_transcript_upload(group: &str, full: &Value) -> Result<String, String> {
    let home = crate::util::paths::home_env().map_err(|_| "找不到用户目录".to_string())?;
    let dir = PathBuf::from(home).join(".catfish").join("uploads");
    std::fs::create_dir_all(&dir).map_err(|e| format!("uploads 目录创建失败: {e}"))?;
    let safe: String = group
        .chars()
        .map(|c| if matches!(c, '/' | '\\' | '\0' | ':' | '\n' | '\r') { '_' } else { c })
        .take(60)
        .collect();
    let ts = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let target = dir.join(format!("{ts}-{safe}-微信聊天记录.txt"));
    let count = full.get("message_count").and_then(Value::as_u64).unwrap_or(0);
    let shown = full.get("rendered_count").and_then(Value::as_u64).unwrap_or(0);
    let mut body = format!("# 微信聊天记录: {group} ({count} 条)\n");
    if shown < count {
        body.push_str(&format!("# 只包含前 {shown} 条 (超出单文件上限)\n"));
    }
    body.push('\n');
    body.push_str(full.get("text").and_then(Value::as_str).unwrap_or(""));
    body.push('\n');
    std::fs::write(&target, body).map_err(|e| format!("写聊天记录全文失败: {e}"))?;
    Ok(target.to_string_lossy().to_string())
}

/// 二期: 包里的 pdf/docx/xlsx… 抽出来, 逐个走聊天框上传同一条解析路
/// (`file_parse::keep_parsed_upload`)。单个失败不影响导入, 原因带回给员工和模型。
async fn read_documents(
    helper: &Path,
    source: &Path,
    stage_id: &str,
    limit: u32,
) -> (Vec<Value>, Vec<Value>) {
    let mut documents = Vec::new();
    let mut skipped = Vec::new();
    if limit == 0 {
        return (documents, skipped);
    }
    let Ok(dir) = staging_dir().map(|d| d.join(format!("{stage_id}-docs"))) else {
        return (documents, skipped);
    };
    let _ = std::fs::remove_dir_all(&dir);
    if let Err(error) = std::fs::create_dir_all(&dir) {
        skipped.push(json!({ "name": "*", "reason": format!("无法创建临时目录: {error}") }));
        return (documents, skipped);
    }
    let extracted = run_reader(helper, vec![
        arg("extract-documents"), arg("--json"),
        arg("--source"), source.to_path_buf().into_os_string(),
        arg("--dest"), dir.clone().into_os_string(),
        arg("--limit"), arg(limit.min(20).to_string()),
    ])
    .await;
    match extracted {
        Err(error) => skipped.push(json!({ "name": "*", "reason": error })),
        Ok(report) => {
            if let Some(items) = report.get("skipped").and_then(Value::as_array) {
                skipped.extend(items.iter().cloned());
            }
            for item in report.get("extracted").and_then(Value::as_array).into_iter().flatten() {
                let (Some(name), Some(file)) = (
                    item.get("name").and_then(Value::as_str),
                    item.get("path").and_then(Value::as_str),
                ) else {
                    continue;
                };
                let size = item.get("size").cloned().unwrap_or(Value::Null);
                match super::file_parse::keep_parsed_upload(PathBuf::from(file), name.to_string()).await {
                    Ok(parsed) => {
                        let mut value = serde_json::to_value(parsed).unwrap_or(Value::Null);
                        if let Value::Object(map) = &mut value {
                            map.insert("size_bytes".to_string(), size);
                        }
                        documents.push(value);
                    }
                    Err(error) => skipped.push(json!({
                        "name": name,
                        "reason": error.chars().take(120).collect::<String>(),
                    })),
                }
            }
        }
    }
    let _ = std::fs::remove_dir_all(&dir);
    (documents, skipped)
}

#[tauri::command]
pub fn wechat_export_discard(stage_id: String) -> Result<(), String> {
    let path = staged_path(&stage_id)?;
    match std::fs::remove_file(&path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("删除暂存文件失败: {error}")),
    }
}

#[tauri::command]
pub async fn wechat_export_groups() -> Result<Value, String> {
    let library = library_dir()?;
    if !library.is_dir() {
        return Ok(json!({ "ok": true, "items": [], "count": 0 }));
    }
    let helper = resolve_helper()?;
    run_reader(&helper, vec![
        arg("groups"), arg("--json"), arg("--library"), library.into_os_string(),
    ])
    .await
}

#[tauri::command]
pub async fn wechat_export_update_group(
    group_id: String,
    name: Option<String>,
    self_name: Option<String>,
) -> Result<Value, String> {
    if !valid_group_id(&group_id) {
        return Err("群编号无效".to_string());
    }
    let helper = resolve_helper()?;
    let mut args = vec![
        arg("update-group"), arg("--json"),
        arg("--library"), library_dir()?.into_os_string(),
        arg("--group-id"), arg(group_id),
    ];
    if let Some(name) = name {
        args.extend([arg("--name"), arg(name)]);
    }
    if let Some(self_name) = self_name {
        args.extend([arg("--self-name"), arg(self_name)]);
    }
    run_reader(&helper, args).await
}

#[tauri::command]
pub async fn wechat_export_remove_group(group_id: String) -> Result<Value, String> {
    if !valid_group_id(&group_id) {
        return Err("群编号无效".to_string());
    }
    let helper = resolve_helper()?;
    run_reader(&helper, vec![
        arg("remove-group"), arg("--json"),
        arg("--library"), library_dir()?.into_os_string(),
        arg("--group-id"), arg(group_id),
    ])
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stage_ids_cannot_escape_the_staging_directory() {
        assert!(valid_stage_id(&new_stage_id()));
        let too_long = "a".repeat(33);
        for bad in ["../../etc/passwd", "", "ABCDEF0123456789abcdef0123456789", too_long.as_str()] {
            assert!(!valid_stage_id(bad), "{bad} 不该通过");
        }
        assert!(staged_path("../x").is_err());
    }

    #[test]
    fn group_ids_are_plain_hex() {
        assert!(valid_group_id("7767781d2216"));
        assert!(!valid_group_id("7767781d2216/../x"));
        assert!(!valid_group_id(""));
    }

    #[test]
    fn stale_stages_are_pruned_fresh_ones_kept() {
        let dir = tempfile::tempdir().unwrap();
        let fresh = dir.path().join("fresh.zip");
        std::fs::write(&fresh, b"x").unwrap();
        prune_stale_stages(dir.path());
        assert!(fresh.exists(), "刚暂存的文件不能被当成过期清掉");
    }
}
