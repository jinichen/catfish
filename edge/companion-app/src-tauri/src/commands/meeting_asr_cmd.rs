//! 会议组件包安装 + 转写 —— 前端入口 (10/1, docs/MEETING-MINUTES-PLAN.md §3)。
//! 逻辑在 services::meeting_asr; 组件包下载走 commands::components (P0)。
//!
//! 事件:
//!   meeting_asr_progress        {step, error?, done}     安装进度
//!   meeting_transcribe_progress {id, phase, duration_secs?, error?, done}

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use serde::Serialize;
use serde_json::json;
use tauri::Emitter;

use crate::services::components as comp;
use crate::services::meeting_asr::{self as asr, AsrEvent, Installed, TranscribeArgs};
use crate::services::meeting_store::{self as store, MeetingStatus};

static INSTALLING: AtomicBool = AtomicBool::new(false);
static TRANSCRIBING: AtomicBool = AtomicBool::new(false);

#[derive(Debug, Serialize)]
pub struct AsrStatus {
    pub installed: Option<Installed>,
    /// ~/.catfish/runtime/ 里已下载并校验过、还没装 (或比已装新) 的包
    pub pack_ready: Option<String>,
    pub installing: bool,
    pub transcribing: bool,
}

fn version_key(v: &str) -> Vec<u32> {
    v.split('.').map(|x| x.parse().unwrap_or(0)).collect()
}

/// runtime/ 里校验过的最新会议组件包 → (文件, 版本)。
pub fn ready_pack(runtime: &Path) -> Option<(PathBuf, String)> {
    let prefix = format!("{}-", asr::COMPONENT_NAME);
    let suffix = format!("-{}.tar.gz", comp::current_platform());
    std::fs::read_dir(runtime)
        .ok()?
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            let version = name.strip_prefix(&prefix)?.strip_suffix(&suffix)?.to_string();
            let stamp = std::fs::read_to_string(runtime.join(format!("{name}.verified"))).ok()?;
            (stamp.trim().len() == 64).then(|| (e.path(), version))
        })
        .max_by_key(|(_, v)| version_key(v))
}

fn hermes_venv() -> Result<PathBuf, String> {
    crate::services::catfish_paths::hermes_home()
        .map(|h| h.join("hermes-agent").join("venv"))
        .ok_or_else(|| "找不到 hermes 目录".to_string())
}

#[tauri::command]
pub fn meeting_asr_status() -> Result<AsrStatus, String> {
    let home = store::home()?;
    let installed = asr::installed(&home);
    let pack_ready = ready_pack(&comp::runtime_dir()?)
        .filter(|(_, v)| installed.as_ref().is_none_or(|i| version_key(v) > version_key(&i.version)))
        .map(|(p, _)| p.file_name().unwrap_or_default().to_string_lossy().to_string());
    Ok(AsrStatus {
        installed,
        pack_ready,
        installing: INSTALLING.load(Ordering::Relaxed),
        transcribing: TRANSCRIBING.load(Ordering::Relaxed),
    })
}

#[tauri::command]
pub fn meeting_asr_install(app: tauri::AppHandle) -> Result<(), String> {
    let home = store::home()?;
    let (pack, version) = ready_pack(&comp::runtime_dir()?)
        .ok_or("会议组件包还没下载 (或没校验), 先下载")?;
    let base = asr::base_python_of(&hermes_venv()?)?;
    if INSTALLING.swap(true, Ordering::SeqCst) {
        return Err("正在安装中".into());
    }
    log::info!("[meeting-asr] 安装 {version} · 基础解释器 {}", base.display());
    tauri::async_runtime::spawn_blocking(move || {
        let emit_app = app.clone();
        let r = asr::install(&pack, &home, &base, |step| {
            let _ = emit_app.emit("meeting_asr_progress", json!({ "step": step, "done": false }));
        });
        match &r {
            Ok(i) => log::info!("[meeting-asr] 装好 {} → {}", i.version, i.python.display()),
            Err(e) => log::warn!("[meeting-asr] 安装失败: {e}"),
        }
        let _ = app.emit(
            "meeting_asr_progress",
            json!({ "step": "finished", "done": true, "error": r.err() }),
        );
        INSTALLING.store(false, Ordering::SeqCst);
    });
    Ok(())
}

#[tauri::command]
pub fn meeting_transcribe(app: tauri::AppHandle, id: String) -> Result<(), String> {
    let home = store::home()?;
    let inst = asr::installed(&home).ok_or("会议组件包还没装")?;
    let root = store::meetings_root(&home);
    let mut meta = store::load(&root, &id)?;
    if !matches!(meta.status, MeetingStatus::Recorded | MeetingStatus::Failed | MeetingStatus::Transcribed) {
        return Err(format!("这场会议现在不能转写 (状态: {:?})", meta.status));
    }
    let script = super::file_parse_env::find_script("meeting_asr.py")?;
    if TRANSCRIBING.swap(true, Ordering::SeqCst) {
        return Err("已经有一场会议在转写, 等它完成".into());
    }
    meta.status = MeetingStatus::Transcribing;
    meta.error = None;
    store::save(&root, &meta)?;

    tauri::async_runtime::spawn_blocking(move || {
        let r = run_transcribe(&app, &home, &root, &inst, &script, &meta);
        let mut meta = store::load(&root, &meta.id).unwrap_or(meta);
        match &r {
            Ok(()) => meta.status = MeetingStatus::Transcribed,
            Err(e) => {
                log::warn!("[meeting] {} 转写失败: {e}", meta.id);
                meta.status = MeetingStatus::Failed;
                meta.error = Some(e.clone());
            }
        }
        let _ = store::save(&root, &meta);
        let _ = app.emit(
            "meeting_transcribe_progress",
            json!({ "id": meta.id, "phase": "finished", "done": true, "error": r.err() }),
        );
        TRANSCRIBING.store(false, Ordering::SeqCst);
    });
    Ok(())
}

fn run_transcribe(
    app: &tauri::AppHandle,
    home: &Path,
    root: &Path,
    inst: &Installed,
    script: &Path,
    meta: &store::MeetingMeta,
) -> Result<(), String> {
    let audio = store::audio_dir(root, &meta.id)?;
    let out = store::transcript_path(root, &meta.id)?;
    let args = TranscribeArgs { script, audio_dir: &audio, out: &out, speakers: meta.attendees, hotwords: &meta.hotwords };
    let started = std::time::Instant::now();
    let done = asr::transcribe(inst, &args, &asr::root(home).join(".empty-cache"), |ev| {
        if let AsrEvent::Phase { phase, duration_secs } = ev {
            let _ = app.emit(
                "meeting_transcribe_progress",
                json!({ "id": meta.id, "phase": phase, "duration_secs": duration_secs, "done": false }),
            );
        }
    })?;
    log::info!("[meeting] {} 转写完成 {done:?} · 用时 {:.0}s", meta.id, started.elapsed().as_secs_f64());
    Ok(())
}

/// transcript.json 原样返回 (前端渲染)。
#[tauri::command]
pub fn meeting_transcript(id: String) -> Result<serde_json::Value, String> {
    let root = store::meetings_root(&store::home()?);
    let p = store::transcript_path(&root, &id)?;
    let text = std::fs::read_to_string(&p).map_err(|_| "这场会议还没有转写结果".to_string())?;
    serde_json::from_str(&text).map_err(|e| format!("transcript.json 格式不对: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ready_pack_picks_highest_verified_version_for_this_platform() {
        let d = tempfile::tempdir().unwrap();
        let plat = comp::current_platform();
        let put = |ver: &str, verified: bool| {
            let f = format!("meeting-asr-{ver}-{plat}.tar.gz");
            std::fs::write(d.path().join(&f), b"x").unwrap();
            if verified {
                std::fs::write(d.path().join(format!("{f}.verified")), "a".repeat(64)).unwrap();
            }
        };
        assert!(ready_pack(d.path()).is_none());
        put("1.2.0", true);
        put("1.10.0", true); // 1.10 > 1.2, 不是字符串比较
        put("2.0.0", false); // 没校验 (下了一半 / IT 刚放进来) 不算
        std::fs::write(d.path().join("meeting-asr-9.0.0-other-platform.tar.gz"), b"x").unwrap();
        let (p, v) = ready_pack(d.path()).unwrap();
        assert_eq!(v, "1.10.0");
        assert!(p.ends_with(format!("meeting-asr-1.10.0-{plat}.tar.gz")));
    }
}
