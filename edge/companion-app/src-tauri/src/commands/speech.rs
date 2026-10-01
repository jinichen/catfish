//! 语音输入 (聊天 🎤 / 录屏学习的旁白) + 上传音频转文字 —— 10/1 改版。
//!
//! 原来是"起 brew 装的 ffmpeg 录音 (avfoundation 写死设备 `:0`, 最长 5 分钟) +
//! whisper.cpp small/medium 转文字"。ffmpeg / whisper-cli / 模型都不在安装包里, 客户机上
//! 根本跑不起来; `:0` 在开发机上是 iPhone 连续互通麦克风; whisper small 中文会议集
//! 字错率 ~25% (FunASR ~7%)。鸿波 10/1 拍板: 并入会议功能, 删掉 whisper。
//!
//! 现在:
//!   录音 = services::meeting_recorder (进程内 cpal, 系统默认麦克风, 10 分钟上限)
//!   转写 = 会议组件包 (FunASR) 的 meeting_asr.py, `--speakers 0` 不分说话人
//!   解码 = macOS 自带 afconvert (上传的 mp3 / m4a / 视频音轨 → 16k wav)
//!
//! 命令名和返回形状不变, 聊天输入框 / 录屏学习 (RecordingOverlay) 一行不用改。
//!
//! 冷启动耗时: 实测 6 秒语音约 18 秒出字 (其中加载模型 ~17 秒) —— 跟原来 whisper
//! medium 差不多 (9/29 日志: 停止到出字 16 秒)。常驻进程能降到 1 秒内, 另记。
//!
//! ⚠ 录音器全局只有一个, 会议录音也用它: 停 / 取消前先确认在录的是**语音输入自己的
//! 目录**, 绝不能把一场正在录的会议停掉。

use tauri::Window;

#[cfg(target_os = "macos")]
mod imp {
    use std::path::{Path, PathBuf};

    use crate::services::meeting_asr::{self as asr, AudioInput, TranscribeArgs};
    use crate::services::meeting_recorder as rec;
    use crate::services::meeting_store;

    /// 语音输入最长 10 分钟 (说一段话, 不是开会)。
    const DICTATE_MAX_SECS: u32 = 600;

    pub(super) fn dictate_dir() -> PathBuf {
        std::env::temp_dir().join("catfish-dictate")
    }

    fn is_dictating() -> bool {
        rec::status().dir.as_deref() == Some(dictate_dir().display().to_string().as_str())
    }

    pub(super) fn need_asr() -> Result<asr::Installed, String> {
        asr::installed(&meeting_store::home()?)
            .ok_or_else(|| "语音转文字要先在「会议」页下载并安装会议组件包 (一次就好)".to_string())
    }

    pub(super) fn start() -> Result<(), String> {
        need_asr()?;
        let st = rec::status();
        if st.recording {
            return Err(if is_dictating() { "已经在录音中, 先停止".into() } else { "会议正在录音, 结束会议录音后再用语音输入".into() });
        }
        let dir = dictate_dir();
        let _ = std::fs::remove_dir_all(&dir);
        rec::start(&dir, None, DICTATE_MAX_SECS, DICTATE_MAX_SECS)?;
        Ok(())
    }

    /// 停录音并转写; 阻塞 (调用方放 spawn_blocking)。
    pub(super) fn stop_and_transcribe() -> Result<String, String> {
        if !is_dictating() {
            return Err("没在语音输入".into());
        }
        let sum = rec::stop()?.ok_or("没在录音")?;
        let dir = dictate_dir();
        let result = (|| {
            if sum.seconds < 0.5 {
                return Err("录音太短".to_string());
            }
            transcribe(AudioInput::Dir(&dir))
        })();
        let _ = std::fs::remove_dir_all(&dir);
        result
    }

    pub(super) fn cancel() -> Result<(), String> {
        if is_dictating() {
            rec::stop()?;
        }
        let _ = std::fs::remove_dir_all(dictate_dir());
        Ok(())
    }

    /// 不分说话人的整段转写 → 文字。
    pub(super) fn transcribe(audio: AudioInput) -> Result<String, String> {
        let inst = need_asr()?;
        let home = meeting_store::home()?;
        let script = super::super::file_parse_env::find_script("meeting_asr.py")?;
        let out = std::env::temp_dir().join(format!("catfish-stt-{}.json", std::process::id()));
        let args = TranscribeArgs { script: &script, audio, out: &out, speakers: 0, hotwords: &[] };
        let r = asr::transcribe(&inst, &args, &asr::root(&home).join(".empty-cache"), |_| {})
            .and_then(|_| asr::read_text(&out));
        let _ = std::fs::remove_file(&out);
        let text = r?;
        if text.is_empty() {
            return Err("没识别出文字 (音频太短 / 没有人声?)".into());
        }
        Ok(text)
    }

    /// 上传的音频: base64 → 原文件 → afconvert 16k wav → 转写。返回 (文字, 时长秒)。
    pub(super) fn transcribe_bytes(bytes: &[u8], filename: &str) -> Result<(String, Option<f64>), String> {
        need_asr()?;
        let ext = Path::new(filename).extension().and_then(|e| e.to_str()).unwrap_or("bin").to_lowercase();
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let raw = std::env::temp_dir().join(format!("catfish-audio-raw-{stamp}.{ext}"));
        let wav = std::env::temp_dir().join(format!("catfish-audio-{stamp}.wav"));
        std::fs::write(&raw, bytes).map_err(|e| format!("写临时文件失败: {e}"))?;
        let r = asr::decode_to_wav16k(&raw, &wav).and_then(|_| {
            let secs = hound::WavReader::open(&wav)
                .ok()
                .map(|r| r.duration() as f64 / r.spec().sample_rate.max(1) as f64);
            transcribe(AudioInput::File(&wav)).map(|t| (t, secs))
        });
        let _ = std::fs::remove_file(&raw);
        let _ = std::fs::remove_file(&wav);
        r
    }
}

#[derive(Debug, serde::Serialize)]
pub struct TranscribeResult {
    pub text: String,
    pub duration_sec: Option<f64>,
    pub original_filename: String,
}

#[cfg(not(target_os = "macos"))]
const UNSUPPORTED: &str = "语音转文字目前只支持 macOS";

#[tauri::command]
pub fn speech_start_recording(_window: Window) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        imp::start()
    }
    #[cfg(not(target_os = "macos"))]
    {
        Err(UNSUPPORTED.into())
    }
}

#[tauri::command]
pub async fn speech_stop_and_transcribe(_window: Window) -> Result<String, String> {
    #[cfg(target_os = "macos")]
    {
        let text = tauri::async_runtime::spawn_blocking(imp::stop_and_transcribe)
            .await
            .map_err(|e| format!("转写任务异常: {e}"))??;
        log::info!("speech_stop_and_transcribe: ✅ 识别 {} 字", text.chars().count());
        Ok(text)
    }
    #[cfg(not(target_os = "macos"))]
    {
        Err(UNSUPPORTED.into())
    }
}

#[tauri::command]
pub fn speech_cancel_recording(_window: Window) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        imp::cancel()
    }
    #[cfg(not(target_os = "macos"))]
    {
        Ok(())
    }
}

/// 拖进聊天框的音频文件转文字。mp3 / m4a / aac / flac / opus / wav / aiff, 以及
/// mp4 / mov 的音轨 (afconvert 实测都能解)。
#[tauri::command]
pub async fn transcribe_audio_from_b64(
    _window: Window,
    file_b64: String,
    filename: String,
) -> Result<TranscribeResult, String> {
    #[cfg(target_os = "macos")]
    {
        use base64::Engine;
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(file_b64.trim())
            .map_err(|e| format!("base64 解码失败: {e}"))?;
        if bytes.len() < 1024 {
            return Err(format!("音频文件过小 ({} bytes), 不像有效音频", bytes.len()));
        }
        let name = filename.clone();
        log::info!("transcribe_audio_from_b64: {name} ({} KB)", bytes.len() / 1024);
        let (text, duration_sec) = tauri::async_runtime::spawn_blocking(move || imp::transcribe_bytes(&bytes, &name))
            .await
            .map_err(|e| format!("转写任务异常: {e}"))??;
        Ok(TranscribeResult { text, duration_sec, original_filename: filename })
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (file_b64, filename);
        Err(UNSUPPORTED.into())
    }
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::imp;
    use std::path::PathBuf;

    /// 真包: 装进临时 HOME → 上传一个 m4a (afconvert 解码) → 不分说话人转写。默认跳过。
    ///   CATFISH_MEETING_PACK=<包> CATFISH_TEST_M4A=<m4a> CATFISH_BASE_PYTHON=<python3.11> \
    ///   cargo test --lib commands::speech::tests -- --ignored --nocapture --test-threads=1
    #[test]
    #[ignore]
    fn e2e_upload_m4a_transcribes_without_whisper() {
        let pack = PathBuf::from(std::env::var("CATFISH_MEETING_PACK").unwrap());
        let m4a = std::fs::read(std::env::var("CATFISH_TEST_M4A").unwrap()).unwrap();
        let base = PathBuf::from(std::env::var("CATFISH_BASE_PYTHON").unwrap());
        let home = tempfile::tempdir().unwrap();
        crate::services::meeting_asr::install(&pack, home.path(), &base, |_| {}).unwrap();
        std::env::set_var("HOME", home.path());

        let t = std::time::Instant::now();
        let (text, secs) = imp::transcribe_bytes(&m4a, "memo.m4a").unwrap();
        println!("m4a {secs:?}s → {:.1}s · {text}", t.elapsed().as_secs_f64());
        assert!(text.chars().count() > 5);
        assert!(secs.unwrap() > 1.0);

        // 不认识的格式要给人话, 不是一串 afconvert 报错
        let err = imp::transcribe_bytes(&vec![7u8; 4096], "x.m4a").unwrap_err();
        assert!(err.contains("音频解码"), "{err}");
    }

    /// 会议在录时, 语音输入不能开、更不能把会议停掉。默认跳过 (要真麦克风 + 装好的包)。
    #[test]
    #[ignore]
    fn dictation_never_stops_a_meeting_recording() {
        let meeting = tempfile::tempdir().unwrap();
        crate::services::meeting_recorder::start(meeting.path(), None, 300, 3600).unwrap();
        // 没装包的机器 start 会先报"要安装组件包", 装了的报"会议正在录音"; 两种都不能开录
        assert!(imp::start().is_err());
        assert!(imp::stop_and_transcribe().unwrap_err().contains("没在语音输入"));
        imp::cancel().unwrap();
        assert!(crate::services::meeting_recorder::status().recording, "会议录音被语音输入停掉了");
        crate::services::meeting_recorder::stop().unwrap();
    }
}
