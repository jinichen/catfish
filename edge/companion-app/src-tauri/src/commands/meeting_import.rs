//! 导入录音 (10/11): 录音笔 / 手机录的会议文件 → 一场会议, 后面跟现场录音完全一样。
//!
//! 转写 (`meeting_transcribe`) 只认会议目录 `audio/seg-*.wav`, 不管它们是现场录的还是
//! 拷进来的。所以导入 = 建会议 → 解码成 `audio/seg-0000.wav` → 时长按 WAV 头算 →
//! 状态置「已录」。区分说话人 / 热词 / 改名 / 纪要表 / 待办全部复用, 数据只在本机。
//!
//! 解码复用 `meeting_asr::decode_to_wav16k`: macOS 走系统 afconvert, Windows 走组件包
//! 里的 PyAV。afconvert 不认 WMA (部分录音笔导出这个格式), 解码失败时给出明确提示,
//! 而不是一句"格式可能不支持"。

use std::path::{Path, PathBuf};

use crate::services::meeting_asr as asr;
use crate::services::meeting_store::{self as store, MeetingMeta, MeetingStatus};

/// 录音笔 / 手机 / 会议软件常见的导出格式 (文件选择器的过滤器)。
const AUDIO_EXTS: &[&str] = &["mp3", "m4a", "wav", "aac", "flac", "ogg", "opus", "wma", "amr", "aiff", "mp4", "mov"];

fn ext_of(path: &Path) -> String {
    path.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase()
}

/// 解码失败时给员工看的话: 平台上已知解不了的格式直接说清楚怎么办。
fn decode_error_hint(ext: &str, raw: &str) -> String {
    if cfg!(target_os = "macos") && ext == "wma" {
        return "Mac 上暂不支持 WMA 格式 —— 先用录音笔自带软件或格式工厂转成 mp3 / m4a 再导入".into();
    }
    format!("这个录音文件解不开 ({raw}); 换成 mp3 / m4a / wav 再试")
}

/// 原生文件选择器, 只列音频 / 视频。取消返回 None。
#[tauri::command]
pub fn meeting_import_pick_file() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("选择会议录音 (录音笔 / 手机录音)")
        .add_filter("录音文件", AUDIO_EXTS)
        .pick_file()
        .map(|p| p.to_string_lossy().into_owned())
}

/// 导入一个录音文件成一场会议。返回建好的会议 (状态「已录」, 可以直接点转写)。
#[tauri::command]
pub async fn meeting_import_audio(path: String, title: String, attendees: u32, hotwords: Vec<String>) -> Result<MeetingMeta, String> {
    let src = PathBuf::from(&path);
    if !src.is_file() {
        return Err(format!("找不到这个文件: {path}"));
    }
    let home = store::home()?;
    let inst = asr::installed(&home).ok_or("导入录音要先在「会议」页下载并安装会议组件包 (一次就好)")?;
    let script = super::file_parse_env::find_script("meeting_asr.py")?;
    let root = store::meetings_root(&home);
    let title = if title.trim().is_empty() {
        src.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default()
    } else {
        title
    };
    let meta = store::create(&root, &title, attendees, &hotwords)?;
    tauri::async_runtime::spawn_blocking(move || import_into(&root, meta, &src, |input, out| asr::decode_to_wav16k(&inst, &script, input, out)))
        .await
        .map_err(|e| format!("导入任务异常: {e}"))?
}

/// 解码进会议目录并收尾; 失败就把半成品会议删掉, 列表里不留一条空会议。
fn import_into(
    root: &Path,
    mut meta: MeetingMeta,
    src: &Path,
    decode: impl FnOnce(&Path, &Path) -> Result<(), String>,
) -> Result<MeetingMeta, String> {
    let fail = |root: &Path, id: &str, msg: String| -> Result<MeetingMeta, String> {
        if let Ok(dir) = store::meeting_dir(root, id) {
            let _ = std::fs::remove_dir_all(dir);
        }
        Err(msg)
    };
    let audio = match store::audio_dir(root, &meta.id) {
        Ok(a) => a,
        Err(e) => return fail(root, &meta.id, e),
    };
    let seg = audio.join("seg-0000.wav");
    if let Err(e) = decode(src, &seg) {
        return fail(root, &meta.id, decode_error_hint(&ext_of(src), &e));
    }
    let secs = hound::WavReader::open(&seg)
        .map(|r| r.duration() as f64 / r.spec().sample_rate.max(1) as f64)
        .unwrap_or(0.0);
    if secs < 1.0 {
        return fail(root, &meta.id, "录音解出来是空的 (不到 1 秒), 检查文件是不是完整".into());
    }
    meta.status = MeetingStatus::Recorded;
    meta.duration_secs = secs;
    meta.device = Some(format!("导入: {}", src.file_name().map(|n| n.to_string_lossy()).unwrap_or_default()));
    if let Err(e) = store::save(root, &meta) {
        return fail(root, &meta.id, e);
    }
    log::info!("[meeting] 导入录音 {} → {} ({:.0}s)", src.display(), meta.id, secs);
    Ok(meta)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_wav(path: &Path, secs: u32) {
        let spec = hound::WavSpec { channels: 1, sample_rate: 16000, bits_per_sample: 16, sample_format: hound::SampleFormat::Int };
        let mut w = hound::WavWriter::create(path, spec).unwrap();
        for _ in 0..(16000 * secs) {
            w.write_sample(0i16).unwrap();
        }
        w.finalize().unwrap();
    }

    #[test]
    fn import_puts_audio_where_transcribe_reads_it() {
        let d = tempfile::tempdir().unwrap();
        let meta = store::create(d.path(), "周例会", 3, &[]).unwrap();
        let src = d.path().join("REC001.m4a");
        std::fs::write(&src, b"x").unwrap();
        let out = import_into(d.path(), meta, &src, |_, out| {
            write_wav(out, 2);
            Ok(())
        })
        .unwrap();
        assert_eq!(out.status, MeetingStatus::Recorded);
        assert_eq!(out.duration_secs, 2.0);
        assert_eq!(out.device.as_deref(), Some("导入: REC001.m4a"));
        let audio = store::audio_dir(d.path(), &out.id).unwrap();
        assert_eq!(crate::services::meeting_audio::list_segments(&audio).len(), 1);
        assert_eq!(store::load(d.path(), &out.id).unwrap().status, MeetingStatus::Recorded);
    }

    #[test]
    fn failed_decode_leaves_no_empty_meeting() {
        let d = tempfile::tempdir().unwrap();
        let meta = store::create(d.path(), "x", 2, &[]).unwrap();
        let id = meta.id.clone();
        let src = d.path().join("a.wma");
        let err = import_into(d.path(), meta, &src, |_, _| Err("afconvert exit 1".into())).unwrap_err();
        assert!(!store::meeting_dir(d.path(), &id).unwrap().exists());
        if cfg!(target_os = "macos") {
            assert!(err.contains("WMA"), "{err}");
        }
    }

    #[test]
    fn empty_audio_is_rejected() {
        let d = tempfile::tempdir().unwrap();
        let meta = store::create(d.path(), "x", 2, &[]).unwrap();
        let id = meta.id.clone();
        let err = import_into(d.path(), meta, &d.path().join("a.mp3"), |_, out| {
            write_wav(out, 0);
            Ok(())
        })
        .unwrap_err();
        assert!(err.contains("空"), "{err}");
        assert!(!store::meeting_dir(d.path(), &id).unwrap().exists());
    }
}
