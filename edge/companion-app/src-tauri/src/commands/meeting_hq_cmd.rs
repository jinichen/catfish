//! 高精度转写 (11/17, docs/MEETING-MINUTES-PLAN.md §12) —— 前端入口。逻辑在 services::meeting_asr_hq。
//!
//! 事件跟默认转写同一套: 安装进度 `meeting_hq_progress {step, error?, done}`; 转写进度照旧
//! `meeting_transcribe_progress {id, phase, done}` (阶段 refining 带 done/total 段数), 也能 meeting_transcribe_phase 查。
//! 占同一把「一次只转一场」的锁 (meeting_asr_cmd::TRANSCRIBING)。

use std::sync::atomic::{AtomicBool, Ordering};

use serde::Serialize;
use serde_json::json;
use tauri::Emitter;

use super::meeting_asr_cmd::{backup_before_redo, clear_progress, ready_pack_named, set_progress, version_key, TRANSCRIBING};
use crate::services::components as comp;
use crate::services::meeting_asr::{self as asr, AsrEvent, Installed};
use crate::services::meeting_asr_hq::{self as hq, RetranscribeArgs};
use crate::services::meeting_store::{self as store, MeetingStatus};

static INSTALLING: AtomicBool = AtomicBool::new(false);

#[derive(Debug, Serialize)]
pub struct HqStatus {
    pub installed: Option<Installed>,
    /// 已下载并校验、还没装 (或比已装新) 的包
    pub pack_ready: Option<String>,
    pub installing: bool,
    /// 默认会议组件装了没 (高精度组件要借它的 Python)
    pub base_installed: bool,
}

#[tauri::command]
pub fn meeting_hq_status() -> Result<HqStatus, String> {
    let home = store::home()?;
    let installed = hq::installed(&home);
    let pack_ready = ready_pack_named(&comp::runtime_dir()?, hq::COMPONENT_NAME)
        .filter(|(_, v)| installed.as_ref().is_none_or(|i| version_key(v) > version_key(&i.version)))
        .map(|(p, _)| p.file_name().unwrap_or_default().to_string_lossy().to_string());
    Ok(HqStatus {
        installed,
        pack_ready,
        installing: INSTALLING.load(Ordering::Relaxed),
        base_installed: asr::installed(&home).is_some(),
    })
}

#[tauri::command]
pub fn meeting_hq_install(app: tauri::AppHandle) -> Result<(), String> {
    let home = store::home()?;
    let base = asr::installed(&home).ok_or("先装会议组件包, 高精度组件要借它的 Python")?;
    let (pack, version) = ready_pack_named(&comp::runtime_dir()?, hq::COMPONENT_NAME)
        .ok_or("高精度组件还没下载 (或没校验), 先下载")?;
    if INSTALLING.swap(true, Ordering::SeqCst) {
        return Err("正在安装中".into());
    }
    log::info!("[meeting-hq] 安装 {version}");
    tauri::async_runtime::spawn_blocking(move || {
        let emit_app = app.clone();
        let r = hq::install(&pack, &home, &base, |step| {
            let _ = emit_app.emit("meeting_hq_progress", json!({ "step": step, "done": false }));
        });
        match &r {
            Ok(i) => {
                log::info!("[meeting-hq] 装好 {} → {}", i.version, i.python.display());
                // 11/19: 装好就清安装包 (含旧版本) —— 高精度组件一个包就 4 GB
                if let Ok(rt) = comp::runtime_dir() {
                    let freed = comp::prune_installed_packs(&rt, hq::COMPONENT_NAME, &i.version);
                    log::info!("[meeting-hq] 清掉安装包 {} MB", freed / 1_048_576);
                }
            }
            Err(e) => log::warn!("[meeting-hq] 安装失败: {e}"),
        }
        let _ = app.emit("meeting_hq_progress", json!({ "step": "finished", "done": true, "error": r.err() }));
        INSTALLING.store(false, Ordering::SeqCst);
    });
    Ok(())
}

/// 已转写的会议用 Qwen3-ASR 重出文字。句子 / 说话人照用原来的, 起好的名字不动; 原转写另存 transcript.before-<时间>.json。
#[tauri::command]
pub fn meeting_transcribe_hq(app: tauri::AppHandle, id: String) -> Result<(), String> {
    let home = store::home()?;
    let inst = hq::installed(&home).ok_or("高精度组件还没装")?;
    let root = store::meetings_root(&home);
    let mut meta = store::load(&root, &id)?;
    if meta.status != MeetingStatus::Transcribed {
        return Err("先完成普通转写, 再做高精度重新转写".into());
    }
    let script = super::file_parse_env::find_script("meeting_asr_qwen.py")?;
    if TRANSCRIBING.swap(true, Ordering::SeqCst) {
        return Err("已经有一场会议在转写, 等它完成".into());
    }
    meta.status = MeetingStatus::Transcribing;
    meta.error = None;
    store::save(&root, &meta)?;
    clear_progress();
    set_progress(&meta.id, "starting", None, None);

    tauri::async_runtime::spawn_blocking(move || {
        let r = run(&app, &home, &root, &inst, &script, &meta);
        let mut meta = store::load(&root, &meta.id).unwrap_or(meta);
        // 失败也还是「已转写」: 原来的转写没动 (脚本只在成功时整份替换), 只是报错
        meta.status = MeetingStatus::Transcribed;
        if let Err(e) = &r {
            log::warn!("[meeting-hq] {} 高精度重新转写失败: {e}", meta.id);
        }
        let _ = store::save(&root, &meta);
        let _ = app.emit(
            "meeting_transcribe_progress",
            json!({ "id": meta.id, "phase": "finished", "done": true, "error": r.err() }),
        );
        clear_progress();
        TRANSCRIBING.store(false, Ordering::SeqCst);
    });
    Ok(())
}

fn run(
    app: &tauri::AppHandle,
    home: &std::path::Path,
    root: &std::path::Path,
    inst: &Installed,
    script: &std::path::Path,
    meta: &store::MeetingMeta,
) -> Result<(), String> {
    let dir = store::meeting_dir(root, &meta.id)?;
    let transcript = store::transcript_path(root, &meta.id)?;
    let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S").to_string();
    backup_before_redo(&dir, &transcript, &stamp);
    let audio = store::audio_dir(root, &meta.id)?;
    let args = RetranscribeArgs { script, transcript: &transcript, audio_dir: &audio, out: &transcript, hotwords: &meta.hotwords };
    let started = std::time::Instant::now();
    let done = hq::retranscribe(inst, &args, &asr::root(home).join(".empty-cache"), |ev| {
        if let AsrEvent::Phase { phase, duration_secs, done, total } = ev {
            let counts = done.zip(*total);
            set_progress(&meta.id, phase, *duration_secs, counts);
            let _ = app.emit(
                "meeting_transcribe_progress",
                json!({ "id": meta.id, "phase": phase, "duration_secs": duration_secs, "done": false }),
            );
        }
    })?;
    log::info!("[meeting-hq] {} 高精度重新转写完成 {done:?} · 用时 {:.0}s", meta.id, started.elapsed().as_secs_f64());
    Ok(())
}
