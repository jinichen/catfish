//! 会议的本机存储 (10/1, docs/MEETING-MINUTES-PLAN.md §3)。
//!
//! ```text
//! ~/.catfish/meetings/<id>/
//!     meta.json        标题 / 参会人数 / 热词 / 状态 / 时长
//!     audio/seg-NNNN.wav
//!     transcript.json  转写结果 (meeting_asr.py 写)
//!     (后续: minutes.md)
//! ```
//!
//! 数据只在本机 (CENTRAL-EDGE-DATA-BOUNDARY): 音频和转写不出员工电脑, 只有转写
//! 文字在生成纪要时经网关去大模型。
//!
//! 参会人数必填 (鸿波 10/1 拍板): 不给人数时, 30 分钟的录音说话人聚类会分出几十个
//! "说话人" (实测 4 人分成 42 人), 给了就稳定 (同一人跨时段标签一致 ~92%)。

use std::path::{Path, PathBuf};

use chrono::Local;
use serde::{Deserialize, Serialize};

pub const MAX_ATTENDEES: u32 = 50;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MeetingStatus {
    Created,
    Recording,
    Recorded,
    Transcribing,
    Transcribed,
    /// 转写失败, 原因在 meta.error; 可以重试
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MeetingMeta {
    pub id: String,
    pub title: String,
    pub created_at: String,
    pub attendees: u32,
    #[serde(default)]
    pub hotwords: Vec<String>,
    pub status: MeetingStatus,
    #[serde(default)]
    pub duration_secs: f64,
    #[serde(default)]
    pub device: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
}

pub fn meetings_root(home: &Path) -> PathBuf {
    home.join(".catfish").join("meetings")
}

pub fn home() -> Result<PathBuf, String> {
    crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME".to_string())
}

/// id 进路径, 只认自己生成的格式。
pub fn valid_id(id: &str) -> bool {
    id.starts_with("mtg_")
        && id.len() <= 40
        && id[4..].chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

pub fn meeting_dir(root: &Path, id: &str) -> Result<PathBuf, String> {
    if !valid_id(id) {
        return Err(format!("会议 id 不合法: {id:?}"));
    }
    Ok(root.join(id))
}

pub fn audio_dir(root: &Path, id: &str) -> Result<PathBuf, String> {
    Ok(meeting_dir(root, id)?.join("audio"))
}

pub fn transcript_path(root: &Path, id: &str) -> Result<PathBuf, String> {
    Ok(meeting_dir(root, id)?.join("transcript.json"))
}

fn new_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    format!("mtg_{}_{:04x}", Local::now().format("%Y%m%d-%H%M%S"), nanos & 0xffff)
}

/// 热词: 去空白、去重、限长 (太多热词反而干扰识别)。
pub fn normalize_hotwords(raw: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for w in raw.iter().flat_map(|s| s.split([',', '，', '、', '\n'])) {
        let w = w.trim();
        if !w.is_empty() && w.chars().count() <= 20 && !out.iter().any(|x| x == w) {
            out.push(w.to_string());
        }
    }
    out.truncate(100);
    out
}

pub fn create(root: &Path, title: &str, attendees: u32, hotwords: &[String]) -> Result<MeetingMeta, String> {
    if attendees == 0 || attendees > MAX_ATTENDEES {
        return Err(format!("参会人数要填 1–{MAX_ATTENDEES} (用来区分说话人, 不填长会议会把几个人拆成几十个)"));
    }
    let title = title.trim();
    let meta = MeetingMeta {
        id: new_id(),
        title: if title.is_empty() { format!("会议 {}", Local::now().format("%m-%d %H:%M")) } else { title.to_string() },
        created_at: Local::now().to_rfc3339(),
        attendees,
        hotwords: normalize_hotwords(hotwords),
        status: MeetingStatus::Created,
        duration_secs: 0.0,
        device: None,
        error: None,
    };
    std::fs::create_dir_all(audio_dir(root, &meta.id)?).map_err(|e| format!("建会议目录失败: {e}"))?;
    save(root, &meta)?;
    Ok(meta)
}

pub fn load(root: &Path, id: &str) -> Result<MeetingMeta, String> {
    let p = meeting_dir(root, id)?.join("meta.json");
    let text = std::fs::read_to_string(&p).map_err(|e| format!("读 {} 失败: {e}", p.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{} 格式不对: {e}", p.display()))
}

pub fn save(root: &Path, meta: &MeetingMeta) -> Result<(), String> {
    let dir = meeting_dir(root, &meta.id)?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("建会议目录失败: {e}"))?;
    let tmp = dir.join(".meta.json.tmp");
    let body = serde_json::to_string_pretty(meta).map_err(|e| e.to_string())?;
    std::fs::write(&tmp, body).map_err(|e| format!("写 meta 失败: {e}"))?;
    std::fs::rename(&tmp, dir.join("meta.json")).map_err(|e| format!("写 meta 失败: {e}"))
}

/// 新的在前。坏掉的 meta 跳过 (记日志), 不让一个坏目录拖垮整个列表。
pub fn list(root: &Path) -> Vec<MeetingMeta> {
    let mut v: Vec<MeetingMeta> = std::fs::read_dir(root)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| {
            let id = e.file_name().to_string_lossy().to_string();
            if !valid_id(&id) {
                return None;
            }
            load(root, &id).map_err(|err| log::warn!("[meeting] 跳过 {id}: {err}")).ok()
        })
        .collect();
    v.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    v
}

#[cfg(test)]
#[path = "meeting_store_tests.rs"]
mod tests;
