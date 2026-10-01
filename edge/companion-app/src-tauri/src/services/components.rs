//! 可选组件下载 (10/1, docs/MEETING-MINUTES-PLAN.md §4).
//!
//! 大的可选组件 (会议纪要组件包 ~2.7GB 等) 不进安装包, 由中央 catfish-web 的
//! `/components/` 静态托管 (central/web/catfish-locations.conf), 这里按需下载到
//! `~/.catfish/runtime/` —— 跟 IT 手动放离线包是同一个目录, 两条路落点一致。
//!
//! 流程: 读 `/components/manifest.json` → 断点续传到 `<file>.part` → 校验 sha256
//! → 原子改名成 `<file>` → 写 `<file>.verified` (内容 = sha256)。
//!
//! `.verified` 的作用: 判断"已就绪"不用每次把 2.7GB 重新算一遍哈希, 只比对
//! sidecar 里的哈希跟 manifest 是否一致 + 文件大小。IT 手动放进来的包没有
//! sidecar, 走 [`verify_existing`] 补算一次。
//!
//! HTTP client 只有连接超时和读空闲超时, **没有总时长上限** —— reqwest 的
//! `.timeout()` 包含读 body, 2.7GB 在慢网上要几十分钟, 设了总时长等于必断
//! (9/30 聊天流式第 600 秒被切断就是这个坑, 见 commands/http_proxy.rs)。

use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const MANIFEST_PATH: &str = "/components/manifest.json";
pub const SUPPORTED_SCHEMA: u32 = 1;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
/// 多久一个字节都没收到算断。不是总时长。
const READ_IDLE_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ComponentEntry {
    pub name: String,
    pub version: String,
    pub platform: String,
    pub file: String,
    pub size: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Manifest {
    pub schema: u32,
    #[serde(default)]
    pub components: Vec<ComponentEntry>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum LocalStatus {
    /// 已下载且校验过 (或 IT 放的包补算过)
    Ready,
    /// 有 `.part`, 下了一部分
    Partial { downloaded: u64 },
    /// 文件在但没校验过 (IT 手动放的), 需要 verify_existing
    Unverified,
    Missing,
}

/// 本机对应 manifest 里的哪个 platform。
pub fn current_platform() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows-x64"
    } else if cfg!(target_arch = "aarch64") {
        "mac-arm64"
    } else {
        "mac-x64"
    }
}

pub fn runtime_dir() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| "找不到 HOME".to_string())?;
    Ok(PathBuf::from(home).join(".catfish").join("runtime"))
}

pub fn parse_manifest(body: &str) -> Result<Manifest, String> {
    let m: Manifest =
        serde_json::from_str(body).map_err(|e| format!("manifest.json 解析失败: {e}"))?;
    if m.schema != SUPPORTED_SCHEMA {
        return Err(format!(
            "manifest schema={} 不认识 (本版本 Companion 只认 {SUPPORTED_SCHEMA}), 请升级 Companion",
            m.schema
        ));
    }
    for c in &m.components {
        // 文件名会被拼进本机路径, 不许带目录
        if c.file.contains('/') || c.file.contains('\\') || c.file.starts_with('.') {
            return Err(format!("manifest 里的文件名不合法: {:?}", c.file));
        }
        if c.sha256.len() != 64 || !c.sha256.chars().all(|ch| ch.is_ascii_hexdigit()) {
            return Err(format!("{} 的 sha256 格式不对", c.file));
        }
    }
    Ok(m)
}

/// manifest 里本平台的那一条。
pub fn find_entry<'a>(m: &'a Manifest, name: &str, platform: &str) -> Option<&'a ComponentEntry> {
    m.components.iter().find(|c| c.name == name && c.platform == platform)
}

fn verified_path(dir: &Path, file: &str) -> PathBuf {
    dir.join(format!("{file}.verified"))
}

fn part_path(dir: &Path, file: &str) -> PathBuf {
    dir.join(format!("{file}.part"))
}

pub fn local_status(dir: &Path, entry: &ComponentEntry) -> LocalStatus {
    let full = dir.join(&entry.file);
    if let Ok(meta) = std::fs::metadata(&full) {
        if meta.len() == entry.size {
            let stamp = std::fs::read_to_string(verified_path(dir, &entry.file)).unwrap_or_default();
            if stamp.trim().eq_ignore_ascii_case(&entry.sha256) {
                return LocalStatus::Ready;
            }
            return LocalStatus::Unverified;
        }
    }
    match std::fs::metadata(part_path(dir, &entry.file)) {
        Ok(meta) => LocalStatus::Partial { downloaded: meta.len() },
        Err(_) => LocalStatus::Missing,
    }
}

pub fn sha256_file(path: &Path, cancel: &AtomicBool) -> Result<String, String> {
    let mut f = std::fs::File::open(path).map_err(|e| format!("打开 {} 失败: {e}", path.display()))?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        if cancel.load(Ordering::Relaxed) {
            return Err("已取消".into());
        }
        let n = f.read(&mut buf).map_err(|e| format!("读 {} 失败: {e}", path.display()))?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(hex::encode(h.finalize()))
}

/// IT 手动放进 runtime/ 的包: 补算一次哈希, 对得上就写 `.verified`。
pub fn verify_existing(dir: &Path, entry: &ComponentEntry, cancel: &AtomicBool) -> Result<(), String> {
    let full = dir.join(&entry.file);
    let got = sha256_file(&full, cancel)?;
    if !got.eq_ignore_ascii_case(&entry.sha256) {
        return Err(format!(
            "{} 的 sha256 跟中央 manifest 对不上 (本机 {}…, 应为 {}…), 文件可能损坏或版本不对",
            entry.file,
            &got[..12],
            &entry.sha256[..12]
        ));
    }
    std::fs::write(verified_path(dir, &entry.file), &entry.sha256)
        .map_err(|e| format!("写校验标记失败: {e}"))
}

pub fn download_client() -> Result<reqwest::Client, String> {
    crate::util::http_client::trust_central(
        reqwest::Client::builder()
            .connect_timeout(CONNECT_TIMEOUT)
            .read_timeout(READ_IDLE_TIMEOUT),
    )
    .build()
    .map_err(|e| format!("建 http client 失败: {e}"))
}

pub async fn fetch_manifest(client: &reqwest::Client, web_base: &str) -> Result<Manifest, String> {
    let url = format!("{}{MANIFEST_PATH}", web_base.trim_end_matches('/'));
    let resp = tokio::time::timeout(Duration::from_secs(30), client.get(&url).send())
        .await
        .map_err(|_| format!("连中央超时: {url}"))?
        .map_err(|e| format!("连中央失败 ({url}): {e}"))?;
    if resp.status() == reqwest::StatusCode::NOT_FOUND {
        return Err("中央还没有发布任何组件 (manifest.json 不存在), 请联系 IT".into());
    }
    if !resp.status().is_success() {
        return Err(format!("读 manifest 失败: HTTP {}", resp.status()));
    }
    let body = resp.text().await.map_err(|e| format!("读 manifest 失败: {e}"))?;
    parse_manifest(&body)
}

#[derive(Debug, Clone, Serialize)]
pub struct Progress {
    pub name: String,
    /// downloading / verifying / done / error / cancelled
    pub phase: &'static str,
    pub downloaded: u64,
    pub total: u64,
    pub error: Option<String>,
}

/// 下载 + 校验 + 落位。`on_progress` 至多每 ~1MB 调一次。
pub async fn download(
    client: &reqwest::Client,
    web_base: &str,
    dir: &Path,
    entry: &ComponentEntry,
    cancel: Arc<AtomicBool>,
    mut on_progress: impl FnMut(&'static str, u64),
) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("建目录 {} 失败: {e}", dir.display()))?;
    let part = part_path(dir, &entry.file);
    let mut have = std::fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
    if have > entry.size {
        // 上一次下的是别的版本或者坏了
        let _ = std::fs::remove_file(&part);
        have = 0;
    }

    if have < entry.size {
        let url = format!("{}/components/{}", web_base.trim_end_matches('/'), entry.file);
        let mut req = client.get(&url);
        if have > 0 {
            req = req.header(reqwest::header::RANGE, format!("bytes={have}-"));
        }
        let mut resp = req.send().await.map_err(|e| format!("下载 {url} 失败: {e}"))?;
        let status = resp.status();
        let append = if status == reqwest::StatusCode::PARTIAL_CONTENT {
            true
        } else if status.is_success() {
            // 服务器不认 Range, 从头来
            have = 0;
            false
        } else {
            return Err(format!("下载 {} 失败: HTTP {status}", entry.file));
        };
        let mut out = std::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .append(append)
            .truncate(!append)
            .open(&part)
            .map_err(|e| format!("写 {} 失败: {e}", part.display()))?;
        let mut since_report = 0u64;
        on_progress("downloading", have);
        loop {
            if cancel.load(Ordering::Relaxed) {
                return Err("已取消".into());
            }
            match resp.chunk().await {
                Ok(Some(bytes)) => {
                    out.write_all(&bytes).map_err(|e| format!("写盘失败: {e}"))?;
                    have += bytes.len() as u64;
                    since_report += bytes.len() as u64;
                    if since_report >= 1 << 20 {
                        since_report = 0;
                        on_progress("downloading", have);
                    }
                }
                Ok(None) => break,
                Err(e) => return Err(format!("下载中断 (已下 {have} 字节, 再点一次会接着下): {e}")),
            }
        }
        out.flush().map_err(|e| format!("写盘失败: {e}"))?;
        on_progress("downloading", have);
    }

    if have != entry.size {
        return Err(format!("大小不对: 下到 {have} 字节, manifest 说 {} 字节", entry.size));
    }
    on_progress("verifying", have);
    let got = sha256_file(&part, &cancel)?;
    if !got.eq_ignore_ascii_case(&entry.sha256) {
        // 坏的 .part 留着只会让下次续传接着坏
        let _ = std::fs::remove_file(&part);
        return Err(format!("{} 校验失败 (sha256 不符), 已删除, 请重新下载", entry.file));
    }
    let full = dir.join(&entry.file);
    std::fs::rename(&part, &full).map_err(|e| format!("落位失败: {e}"))?;
    std::fs::write(verified_path(dir, &entry.file), &entry.sha256)
        .map_err(|e| format!("写校验标记失败: {e}"))?;
    on_progress("done", have);
    Ok(())
}

/// 进行中的下载 (name → 取消标志)。同一个组件同时只允许一个下载。
pub fn active() -> &'static Mutex<HashMap<String, Arc<AtomicBool>>> {
    static ACTIVE: OnceLock<Mutex<HashMap<String, Arc<AtomicBool>>>> = OnceLock::new();
    ACTIVE.get_or_init(|| Mutex::new(HashMap::new()))
}

#[cfg(test)]
#[path = "components_tests.rs"]
mod tests;
