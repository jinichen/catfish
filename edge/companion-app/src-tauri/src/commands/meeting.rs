//! 会议录音 —— 前端入口 (10/1, docs/MEETING-MINUTES-PLAN.md §3)。
//! 存储在 services::meeting_store, 录音在 services::meeting_recorder (mac / Windows)。

use crate::services::meeting_audio::track_seconds;
use crate::services::meeting_store::{self as store, MeetingMeta, MeetingStatus};

fn root() -> Result<std::path::PathBuf, String> {
    Ok(store::meetings_root(&store::home()?))
}

/// 分片里实际录到的秒数 (读 WAV 头, 不信 meta) —— 进程被杀后 meta 里的时长不准。
/// 麦克风那条为准 (线上会议的电脑声音那条录音时按它对齐)。
fn recorded_seconds(audio_dir: &std::path::Path) -> f64 {
    track_seconds(audio_dir)
}

/// meta 说"录音中"但录音线程并不在录 (App 被强退 / 崩溃): 改成已录, 时长按分片算。
fn heal_if_stale(root: &std::path::Path, mut m: MeetingMeta, active_dir: Option<&str>) -> MeetingMeta {
    if m.status != MeetingStatus::Recording {
        return m;
    }
    let Ok(audio) = store::audio_dir(root, &m.id) else { return m };
    if active_dir == Some(audio.display().to_string().as_str()) {
        return m;
    }
    m.duration_secs = recorded_seconds(&audio);
    m.status = MeetingStatus::Recorded;
    log::info!("[meeting] {} 上次录音没正常结束, 按已录分片收尾 ({:.0}s)", m.id, m.duration_secs);
    let _ = store::save(root, &m);
    m
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn active_dir() -> Option<String> {
    crate::services::meeting_recorder::status().dir
}
#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn active_dir() -> Option<String> {
    None
}

#[tauri::command]
pub fn meeting_list() -> Result<Vec<MeetingMeta>, String> {
    let root = root()?;
    let active = active_dir();
    Ok(store::list(&root).into_iter().map(|m| heal_if_stale(&root, m, active.as_deref())).collect())
}

/// 删除一场会议 (10/15)。正在录的、正在转写的不让删 —— 删了录音线程 / 转写进程还在往这个目录写。
/// meta 写着"录音中 / 转写中"但实际没在跑 (App 被强退留下的) 照常删。
#[tauri::command]
pub fn meeting_delete(id: String) -> Result<(), String> {
    let root = root()?;
    let meta = store::load(&root, &id)?;
    if meta.status == MeetingStatus::Recording {
        let audio = store::audio_dir(&root, &id)?;
        if active_dir().as_deref() == Some(audio.display().to_string().as_str()) {
            return Err("这场会议正在录音, 先结束录音再删".into());
        }
    }
    if meta.status == MeetingStatus::Transcribing && super::meeting_asr_cmd::transcribing() {
        return Err("这场会议正在转写, 等转写完再删".into());
    }
    store::remove(&root, &id)?;
    log::info!("[meeting] 已删除 {id}");
    Ok(())
}

#[tauri::command]
pub fn meeting_create(title: String, attendees: u32, hotwords: Vec<String>) -> Result<MeetingMeta, String> {
    store::create(&root()?, &title, attendees, &hotwords)
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
mod recording {
    use super::*;
    use crate::services::meeting_audio::{DEFAULT_MAX_SECS, DEFAULT_SEGMENT_SECS};
    use crate::services::meeting_recorder::{self as rec, InputDevice, RecorderStatus};
    use crate::services::meeting_sysaudio::{self as sysaudio, SysPick, SysSources};

    #[tauri::command]
    pub fn meeting_audio_devices() -> Result<Vec<InputDevice>, String> {
        rec::list_input_devices()
    }

    /// 11/11: 能录哪些程序的声音 (线上会议)。Mac 上要问一下系统, 放后台线程。
    #[tauri::command]
    pub async fn meeting_system_audio_sources() -> Result<SysSources, String> {
        tauri::async_runtime::spawn_blocking(sysaudio::sources).await.map_err(|e| e.to_string())
    }

    /// `system`: 线上会议同时录电脑声音 (None = 只录麦克风); `local_speakers`: 这台电脑前说话的人数。
    #[tauri::command]
    pub async fn meeting_record_start(
        id: String,
        device: Option<String>,
        system: Option<SysPick>,
        local_speakers: Option<u32>,
    ) -> Result<RecorderStatus, String> {
        // 第一次录电脑声音时系统会弹授权框, 等用户点的这段不能卡住界面线程
        tauri::async_runtime::spawn_blocking(move || {
            let root = root()?;
            let mut meta = store::load(&root, &id)?;
            let audio = store::audio_dir(&root, &id)?;
            let device = device.filter(|d| !d.trim().is_empty());
            let st = rec::start(&audio, device.as_deref(), DEFAULT_SEGMENT_SECS, DEFAULT_MAX_SECS, system.as_ref())?;
            meta.status = MeetingStatus::Recording;
            meta.device = Some(st.device.clone());
            if let Some(s) = &st.system {
                meta.system_audio = Some(s.source.clone());
                meta.local_speakers = Some(local_speakers.unwrap_or(1).clamp(1, meta.attendees.max(1)));
            }
            store::save(&root, &meta)?;
            Ok(st)
        })
        .await
        .map_err(|e| e.to_string())?
    }

    #[tauri::command]
    pub fn meeting_record_status() -> RecorderStatus {
        rec::status()
    }

    /// 停止录音并更新 meta。返回更新后的 meta (没在录就返回 None)。
    #[tauri::command]
    pub fn meeting_record_stop() -> Result<Option<MeetingMeta>, String> {
        let Some(sum) = rec::stop()? else { return Ok(None) };
        let root = root()?;
        let id = std::path::Path::new(&sum.dir)
            .parent()
            .and_then(|p| p.file_name())
            .map(|s| s.to_string_lossy().to_string())
            .ok_or("录音目录结构不对")?;
        let mut meta = store::load(&root, &id)?;
        meta.status = MeetingStatus::Recorded;
        meta.duration_secs = recorded_seconds(&store::audio_dir(&root, &id)?);
        store::save(&root, &meta)?;
        Ok(Some(meta))
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
mod recording {
    #[tauri::command]
    pub fn meeting_audio_devices() -> Result<Vec<serde_json::Value>, String> {
        Err("会议录音目前只支持 macOS / Windows".into())
    }
    #[tauri::command]
    pub fn meeting_system_audio_sources() -> Result<serde_json::Value, String> {
        Ok(serde_json::json!({ "mode": "none", "apps": [], "hint": "会议录音目前只支持 macOS / Windows" }))
    }
    #[tauri::command]
    pub fn meeting_record_start(
        _id: String,
        _device: Option<String>,
        _system: Option<serde_json::Value>,
        _local_speakers: Option<u32>,
    ) -> Result<serde_json::Value, String> {
        Err("会议录音目前只支持 macOS / Windows".into())
    }
    #[tauri::command]
    pub fn meeting_record_status() -> serde_json::Value {
        serde_json::json!({ "recording": false })
    }
    #[tauri::command]
    pub fn meeting_record_stop() -> Result<Option<super::MeetingMeta>, String> {
        Ok(None)
    }
}

pub use recording::*;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stale_recording_meta_is_healed_from_segments() {
        let d = tempfile::tempdir().unwrap();
        let mut m = store::create(d.path(), "x", 2, &[]).unwrap();
        m.status = MeetingStatus::Recording;
        store::save(d.path(), &m).unwrap();
        // 模拟录了 2.5 秒后进程被杀
        let audio = store::audio_dir(d.path(), &m.id).unwrap();
        let mut w = crate::services::meeting_audio::SegmentWriter::new(&audio, 1000, 300, 3600).unwrap();
        w.write(&[1; 2500]).unwrap();
        w.finish().unwrap();

        // 正在录的那一场不能被"修"掉
        let still = heal_if_stale(d.path(), m.clone(), Some(audio.display().to_string().as_str()));
        assert_eq!(still.status, MeetingStatus::Recording);

        let healed = heal_if_stale(d.path(), m, None);
        assert_eq!(healed.status, MeetingStatus::Recorded);
        assert_eq!(healed.duration_secs, 2.5);
        assert_eq!(store::load(d.path(), &healed.id).unwrap().status, MeetingStatus::Recorded);
    }
}
