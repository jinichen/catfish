//! 本地 Whisper.cpp 语音识别 — 五一 sprint Day 1 (方案 C+: ffmpeg subprocess 录音).
//!
//! # 设计 (5/1 鸿波拍板, 多次踩坑后定稿)
//!
//! 之前两次走偏:
//!   方案 A (SFSpeechRecognizer + objc): dev binary 不是 .app bundle, NSException 崩
//!   方案 B (osascript 触发系统 dictation): WKWebView 内 textarea 不是 firstResponder, 文字进不来
//!   方案 C (MediaRecorder + getUserMedia): WKWebView 默认禁用 navigator.mediaDevices, undefined
//!
//! 最终方案 C+:
//!   - **录音在 Rust 端做** (ffmpeg avfoundation subprocess), 绕过 WKWebView 限制
//!   - 转文字 whisper-cli 本地, 数据 100% 不出员工电脑
//!
//! 数据流:
//!   Companion 🎤 按下 → invoke('speech_start_recording')
//!     → Rust 启 ffmpeg -f avfoundation -i ":0" → /tmp/catfish-rec-<uuid>.wav
//!     → 全局 RECORDING 状态保存子进程 + wav 路径
//!   Companion 🎤 再按 → invoke('speech_stop_and_transcribe')
//!     → Rust kill ffmpeg (SIGINT, ffmpeg finalize wav 头)
//!     → whisper-cli -m ggml-small.bin -l zh -f .wav -otxt
//!     → 读 .wav.txt, 清理临时文件, 返文本
//!
//! # 依赖 (员工首次部署)
//!
//! - `brew install whisper-cpp ffmpeg`
//! - 下载 ggml-small.bin (~466MB) 到 ~/.catfish/whisper-models/
//! - macOS 麦克风权限 (首次会弹系统对话框)

use std::path::PathBuf;
use std::process::Child;
use std::sync::{Mutex, OnceLock};
use tauri::Window;

/// 全局录音状态. 一次只能录一段.
/// BL-WIN1 (5/8): cfg(macos) — Windows 走 stub, 不用这个 state.
#[cfg(target_os = "macos")]
struct RecordingState {
    /// ffmpeg 子进程 (kill 用)
    child: Child,
    /// 输出 wav 路径
    wav_path: PathBuf,
}

#[cfg(target_os = "macos")]
fn recording_slot() -> &'static Mutex<Option<RecordingState>> {
    static SLOT: OnceLock<Mutex<Option<RecordingState>>> = OnceLock::new();
    SLOT.get_or_init(|| Mutex::new(None))
}

/// macOS GUI app 启动时的 PATH 不含 brew 路径. 探测可执行文件位置.
/// 按顺序试: env override / Apple Silicon brew / Intel brew / 系统 / PATH.
#[cfg(target_os = "macos")]
fn find_executable(name: &str) -> Option<PathBuf> {
    // 1. env override (例 CATFISH_FFMPEG=/path/to/ffmpeg)
    let env_key = format!("CATFISH_{}", name.to_uppercase().replace('-', "_"));
    if let Ok(custom) = std::env::var(&env_key) {
        let p = PathBuf::from(custom);
        if p.exists() {
            return Some(p);
        }
    }

    // 2. 几个常见路径
    let candidates = [
        format!("/opt/homebrew/bin/{name}"),  // Apple Silicon brew
        format!("/usr/local/bin/{name}"),     // Intel Mac brew
        format!("/usr/bin/{name}"),           // 系统
        format!("/opt/local/bin/{name}"),     // MacPorts
    ];
    for c in &candidates {
        let p = PathBuf::from(c);
        if p.exists() {
            return Some(p);
        }
    }

    // 3. PATH 兜底 (如果 app 启动时被 LaunchServices 注入了完整 PATH)
    if let Ok(path_env) = std::env::var("PATH") {
        for dir in path_env.split(':') {
            let p = PathBuf::from(dir).join(name);
            if p.exists() {
                return Some(p);
            }
        }
    }

    None
}

/// 开始录音. 启 ffmpeg avfoundation 子进程录默认麦克风到 /tmp/catfish-rec-<uuid>.wav.
///
/// 限制录音时长 ≤ 5 分钟 (-t 300), 避免员工忘记停止录爆磁盘.
#[cfg(target_os = "macos")]
#[tauri::command]
pub fn speech_start_recording(_window: Window) -> Result<(), String> {
    let mut slot = recording_slot().lock().map_err(|e| format!("锁失败: {e}"))?;
    if slot.is_some() {
        return Err("已经在录音中, 先停止".to_string());
    }

    let uuid = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let wav_path = std::env::temp_dir().join(format!("catfish-rec-{uuid}.wav"));

    let ffmpeg_bin = find_executable("ffmpeg").ok_or_else(|| {
        "ffmpeg 找不到. 请装: brew install ffmpeg\n\
        或设 env CATFISH_FFMPEG=/path/to/ffmpeg".to_string()
    })?;
    log::info!("speech_start_recording: 启 {} → {}", ffmpeg_bin.display(), wav_path.display());

    let child = std::process::Command::new(&ffmpeg_bin)
        .args([
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "avfoundation",  // macOS 音频输入框架
            "-i", ":0",            // :0 = 默认音频设备 (麦克风); ":1" 是第二个等
            "-ar", "16000",        // whisper 要 16 kHz
            "-ac", "1",            // 单声道
            "-c:a", "pcm_s16le",   // 16-bit PCM
            "-t", "300",           // 最多 5 分钟, 防爆
        ])
        .arg(&wav_path)
        .stdin(std::process::Stdio::piped())  // 留 stdin 让 SIGINT 能优雅退
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("ffmpeg 启动失败: {e}"))?;

    *slot = Some(RecordingState { child, wav_path });
    Ok(())
}

/// 停止录音 + 跑 whisper. 返回识别出的文本.
#[cfg(target_os = "macos")]
#[tauri::command]
pub async fn speech_stop_and_transcribe(_window: Window) -> Result<String, String> {
    // 先取出 state (尽快释放锁)
    let mut state = {
        let mut slot = recording_slot().lock().map_err(|e| format!("锁失败: {e}"))?;
        slot.take().ok_or_else(|| "没有正在录音".to_string())?
    };

    log::info!("speech_stop_and_transcribe: 停 ffmpeg pid={}", state.child.id());

    // 给 ffmpeg 发 SIGINT — 它会 finalize wav 头然后退出.
    // (直接 kill SIGKILL 会让 wav 文件 header 残缺, whisper 读不了)
    let pid = state.child.id();
    let _ = std::process::Command::new("kill")
        .args(["-INT", &pid.to_string()])
        .output();

    // 等 ffmpeg 优雅退 (最多 2s)
    let _ = state.child.wait();

    let wav_path = state.wav_path.clone();
    if !wav_path.exists() || std::fs::metadata(&wav_path).map(|m| m.len()).unwrap_or(0) < 1000 {
        let _ = std::fs::remove_file(&wav_path);
        return Err("录音文件太小或不存在 (录音可能太短)".to_string());
    }

    let txt_out = wav_path.with_extension("wav.txt");

    // 走 whisper.cpp + 本地 ggml 模型. 大模型 (large-v3) 中文准确率 OK, prompt 提升公文术语.
    // (5/1 砍掉 macOS 原生 SFSpeechRecognizer 路径, 因 macOS 没装 on-device 模型, 不可控)
    let model_path = whisper_model_path()?;
    let text = run_whisper_cpp(&wav_path, &txt_out, &model_path)?;

    // 清理临时文件
    let _ = std::fs::remove_file(&wav_path);
    let _ = std::fs::remove_file(&txt_out);

    if text.is_empty() {
        return Err("没识别出文字 (录音太短或没声?)".to_string());
    }

    log::info!("speech_stop_and_transcribe: ✅ 识别 {} 字", text.chars().count());
    Ok(text)
}

/// 紧急取消录音 (不转写, 直接清理).
#[cfg(target_os = "macos")]
#[tauri::command]
pub fn speech_cancel_recording(_window: Window) -> Result<(), String> {
    let mut state = {
        let mut slot = recording_slot().lock().map_err(|e| format!("锁失败: {e}"))?;
        match slot.take() {
            Some(s) => s,
            None => return Ok(()),
        }
    };
    let pid = state.child.id();
    let _ = std::process::Command::new("kill")
        .args(["-9", &pid.to_string()])
        .output();
    let _ = state.child.wait();
    let _ = std::fs::remove_file(&state.wav_path);
    log::info!("speech_cancel_recording: 取消并清理");
    Ok(())
}

/// 走 whisper.cpp + ggml 模型. 跨平台 (Win/Linux 也装 whisper-cpp 即可).
/// 中文准确率取决于模型: small (差) / medium (中) / large-v3 (较好).
#[cfg(target_os = "macos")]
fn run_whisper_cpp(
    wav_path: &PathBuf,
    txt_out: &PathBuf,
    model_path: &PathBuf,
) -> Result<String, String> {
    let whisper_bin = find_executable("whisper-cli")
        .or_else(|| find_executable("main"))
        .ok_or_else(|| {
            "未装 catfish-transcribe (macOS 原生) 也未装 whisper-cli.\n\
             推荐: 编译 native/transcribe-helper.swift 装 catfish-transcribe (准确率最佳)\n\
             或: brew install whisper-cpp".to_string()
        })?;

    let context_prompt = "以下是中文工作对话, 涉及鲶鱼平台、Companion、catfish、\
        公文汇报、月度总结、周报、资质管理、项目部署、团队、合规、安全、\
        ISO27001、27000、客户、PoC、demo、SSO、skill、agent 等场景.";

    let whisper_result = std::process::Command::new(&whisper_bin)
        .args([
            "-m", model_path.to_str().unwrap(),
            "-l", "zh",
            "-f", wav_path.to_str().unwrap(),
            "-otxt",
            "--no-prints",
            "--prompt", context_prompt,
        ])
        .output();

    match whisper_result {
        Ok(output) if output.status.success() => {
            let text = std::fs::read_to_string(txt_out)
                .or_else(|_| Ok::<String, std::io::Error>(
                    String::from_utf8_lossy(&output.stdout).to_string()
                ))
                .unwrap_or_default()
                .trim()
                .to_string();
            Ok(text)
        }
        Ok(output) => Err(format!(
            "whisper-cli 失败: {}",
            String::from_utf8_lossy(&output.stderr)
        )),
        Err(e) => Err(format!("whisper-cli 调用失败: {e}")),
    }
}

#[cfg(target_os = "macos")]
fn whisper_model_path() -> Result<PathBuf, String> {
    if let Ok(custom) = std::env::var("CATFISH_WHISPER_MODEL") {
        return Ok(PathBuf::from(custom));
    }
    let home = std::env::var("HOME").map_err(|_| "HOME env 未设")?;
    let dir = PathBuf::from(home).join(".catfish").join("whisper-models");

    // 按质量优先序: large-v3 > medium > small.
    // 中文公文场景 large-v3 准确率最佳 (M4 上勉强 1x realtime), small 速度快但准确率差.
    for name in &["ggml-large-v3.bin", "ggml-medium.bin", "ggml-small.bin"] {
        let p = dir.join(name);
        if p.exists() {
            log::info!("whisper_model_path: 使用 {}", p.display());
            return Ok(p);
        }
    }

    Err(format!(
        "whisper 模型不存在 ({}/ggml-*.bin)\n推荐下载 (中文公文 large-v3 准确率最佳):\n  \
         curl -L -o {}/ggml-large-v3.bin \\\n    \
         https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin\n\
         (M4 8GB 内存改用 medium: 替换 large-v3 为 medium, 文件 1.5GB)",
        dir.display(),
        dir.display(),
    ))
}

// ===== 非 macOS stub =====

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub fn speech_start_recording(_window: Window) -> Result<(), String> {
    Err("Whisper.cpp Phase 2 加 Win/Linux. 现在 macOS only.".to_string())
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub async fn speech_stop_and_transcribe(_window: Window) -> Result<String, String> {
    Err("Whisper.cpp Phase 2 加 Win/Linux".to_string())
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub fn speech_cancel_recording(_window: Window) -> Result<(), String> {
    Ok(())
}
