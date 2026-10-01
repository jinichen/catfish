//! 进程内录音 (cpal: macOS CoreAudio / Windows WASAPI) —— 10/1,
//! docs/MEETING-MINUTES-PLAN.md §3。取代 speech.rs 里"起 brew 装的 ffmpeg、写死
//! avfoundation 设备 `:0`、最长 5 分钟"那套。
//!
//! 线程模型: 一个专用录音线程**拥有** cpal 的 Stream (macOS 上 Stream 不能跨线程
//! 搬), 音频回调把数据转成单声道 i16 塞进通道, 同一个线程从通道取出交给
//! SegmentWriter 写盘。外面只通过 stop 标志和共享计数跟它打交道。
//!
//! `start` 要等录音线程回报"流真的开起来了"才返回 —— 权限被拒、设备被占这类错误
//! 当场报给界面, 而不是界面显示"录音中"其实什么都没录。
//!
//! ⚠ 打包签名: 进程内录音受 hardened runtime 管, 主 App 必须带
//! `com.apple.security.device.audio-input` entitlement (build-sign-notarize-arm64.sh),
//! 否则签名后的正式包录到的是静音, 而 `tauri dev` 一切正常。

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::JoinHandle;
use std::time::Duration;

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::Serialize;

use super::meeting_audio::{downmix_to_mono_i16, list_segments, peak_level, pick_device_index, SegmentWriter, WriteOutcome};

#[derive(Debug, Clone, Serialize)]
pub struct InputDevice {
    pub name: String,
    pub is_default: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct RecorderStatus {
    pub recording: bool,
    pub seconds: f64,
    /// 最近一块数据的峰值 0–1; 长时间是 0 = 麦克风没收到声音 (设备选错 / 没授权)
    pub level: f32,
    pub device: String,
    pub sample_rate: u32,
    pub dir: Option<String>,
    /// 录音线程遇到的错误 (设备被拔掉等); 有值时 recording 可能已经是 false
    pub error: Option<String>,
    /// 写满上限自动停了
    pub limit_reached: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct RecordingSummary {
    pub dir: String,
    pub seconds: f64,
    pub segments: usize,
}

struct Shared {
    level_bits: AtomicU32,
    millis: AtomicU64,
    running: AtomicBool,
    limit_reached: AtomicBool,
    error: Mutex<Option<String>>,
}

struct Active {
    stop: Arc<AtomicBool>,
    shared: Arc<Shared>,
    join: JoinHandle<Result<f64, String>>,
    device: String,
    sample_rate: u32,
    dir: PathBuf,
}

fn slot() -> &'static Mutex<Option<Active>> {
    static SLOT: OnceLock<Mutex<Option<Active>>> = OnceLock::new();
    SLOT.get_or_init(|| Mutex::new(None))
}

fn device_name(d: &cpal::Device) -> String {
    d.description().map(|x| x.name().to_string()).unwrap_or_else(|_| "未知设备".into())
}

pub fn list_input_devices() -> Result<Vec<InputDevice>, String> {
    let host = cpal::default_host();
    let default = host.default_input_device().map(|d| device_name(&d));
    let devices = host.input_devices().map_err(|e| format!("列录音设备失败: {e}"))?;
    Ok(devices
        .map(|d| {
            let name = device_name(&d);
            InputDevice { is_default: default.as_deref() == Some(name.as_str()), name }
        })
        .collect())
}

pub fn start(dir: &Path, device: Option<&str>, segment_secs: u32, max_secs: u32) -> Result<RecorderStatus, String> {
    let mut guard = slot().lock().map_err(|e| e.to_string())?;
    if let Some(a) = guard.as_ref() {
        if a.shared.running.load(Ordering::Relaxed) {
            return Err("已经在录音中, 先停止".into());
        }
    }
    // 上一次录音自己停了 (到上限 / 出错) 但还没被 stop 收走: 先收尾
    if let Some(old) = guard.take() {
        let _ = old.join.join();
    }

    let stop = Arc::new(AtomicBool::new(false));
    let shared = Arc::new(Shared {
        level_bits: AtomicU32::new(0),
        millis: AtomicU64::new(0),
        running: AtomicBool::new(true),
        limit_reached: AtomicBool::new(false),
        error: Mutex::new(None),
    });
    let (ready_tx, ready_rx) = mpsc::channel::<Result<(String, u32), String>>();
    let dir_owned = dir.to_path_buf();
    let wanted = device.map(str::to_string);
    let (stop_t, shared_t) = (stop.clone(), shared.clone());

    let join = std::thread::Builder::new()
        .name("meeting-recorder".into())
        .spawn(move || {
            let r = record_thread(&dir_owned, wanted.as_deref(), segment_secs, max_secs, &stop_t, &shared_t, &ready_tx);
            shared_t.running.store(false, Ordering::Relaxed);
            if let Err(e) = &r {
                *shared_t.error.lock().unwrap() = Some(e.clone());
                let _ = ready_tx.send(Err(e.clone())); // 还没回报过的话, 让 start 拿到错误
            }
            r
        })
        .map_err(|e| format!("起录音线程失败: {e}"))?;

    match ready_rx.recv_timeout(Duration::from_secs(10)) {
        Ok(Ok((device, sample_rate))) => {
            log::info!("[meeting] 开始录音 · {device} · {sample_rate}Hz → {}", dir.display());
            *guard = Some(Active { stop, shared, join, device, sample_rate, dir: dir.to_path_buf() });
            drop(guard);
            Ok(status())
        }
        Ok(Err(e)) => {
            let _ = join.join();
            Err(e)
        }
        Err(_) => {
            stop.store(true, Ordering::Relaxed);
            Err("打开麦克风超时 (10 秒没响应)".into())
        }
    }
}

fn record_thread(
    dir: &Path,
    wanted: Option<&str>,
    segment_secs: u32,
    max_secs: u32,
    stop: &AtomicBool,
    shared: &Arc<Shared>,
    ready: &mpsc::Sender<Result<(String, u32), String>>,
) -> Result<f64, String> {
    let host = cpal::default_host();
    let devices: Vec<cpal::Device> = host.input_devices().map_err(|e| format!("列录音设备失败: {e}"))?.collect();
    let names: Vec<String> = devices.iter().map(device_name).collect();
    let default_name = host.default_input_device().map(|d| device_name(&d));
    let default_index = default_name.and_then(|n| names.iter().position(|x| *x == n));
    let idx = pick_device_index(&names, wanted, default_index)?;
    let device = &devices[idx];
    let config = device.default_input_config().map_err(|e| format!("读「{}」的录音参数失败: {e}", names[idx]))?;
    let sample_rate = config.sample_rate();
    let channels = config.channels() as usize;

    let mut writer = SegmentWriter::new(dir, sample_rate, segment_secs, max_secs)?;
    let (tx, rx) = mpsc::sync_channel::<Vec<i16>>(256);
    let err_shared = shared.clone();
    let on_error = move |e: cpal::Error| {
        log::warn!("[meeting] 录音流出错: {e}");
        *err_shared.error.lock().unwrap() = Some(format!("录音设备出错: {e}"));
    };

    macro_rules! build {
        ($t:ty) => {{
            let tx = tx.clone();
            device.build_input_stream(
                config.clone().into(),
                move |data: &[$t], _: &cpal::InputCallbackInfo| {
                    let f: Vec<f32> = data.iter().map(|s| <f32 as cpal::FromSample<$t>>::from_sample_(*s)).collect();
                    // 写盘跟不上时丢块而不是阻塞音频回调 (阻塞会让整个流卡死)
                    let _ = tx.try_send(downmix_to_mono_i16(&f, channels));
                },
                on_error,
                None,
            )
        }};
    }
    let stream = match config.sample_format() {
        cpal::SampleFormat::F32 => build!(f32),
        cpal::SampleFormat::I16 => build!(i16),
        cpal::SampleFormat::I32 => build!(i32),
        cpal::SampleFormat::U16 => build!(u16),
        other => return Err(format!("不支持的录音格式 {other:?}")),
    }
    .map_err(|e| format!("打开「{}」失败 (检查系统设置 → 隐私 → 麦克风): {e}", names[idx]))?;
    stream.play().map_err(|e| format!("开始录音失败: {e}"))?;
    let _ = ready.send(Ok((names[idx].clone(), sample_rate)));
    drop(tx);

    while !stop.load(Ordering::Relaxed) {
        match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(block) => {
                shared.level_bits.store(peak_level(&block).to_bits(), Ordering::Relaxed);
                let outcome = writer.write(&block)?;
                shared.millis.store((writer.seconds() * 1000.0) as u64, Ordering::Relaxed);
                if outcome == WriteOutcome::LimitReached {
                    shared.limit_reached.store(true, Ordering::Relaxed);
                    log::info!("[meeting] 到录音上限 {max_secs}s, 自动停止");
                    break;
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    drop(stream);
    // 通道里剩下的也写掉
    while let Ok(block) = rx.try_recv() {
        if writer.write(&block)? == WriteOutcome::LimitReached {
            break;
        }
    }
    writer.finish()
}

pub fn status() -> RecorderStatus {
    let guard = slot().lock().ok();
    match guard.as_ref().and_then(|g| g.as_ref()) {
        Some(a) => RecorderStatus {
            recording: a.shared.running.load(Ordering::Relaxed),
            seconds: a.shared.millis.load(Ordering::Relaxed) as f64 / 1000.0,
            level: f32::from_bits(a.shared.level_bits.load(Ordering::Relaxed)),
            device: a.device.clone(),
            sample_rate: a.sample_rate,
            dir: Some(a.dir.display().to_string()),
            error: a.shared.error.lock().ok().and_then(|e| e.clone()),
            limit_reached: a.shared.limit_reached.load(Ordering::Relaxed),
        },
        None => RecorderStatus {
            recording: false, seconds: 0.0, level: 0.0, device: String::new(),
            sample_rate: 0, dir: None, error: None, limit_reached: false,
        },
    }
}

/// 停止并收尾。没在录也返回 Ok (幂等), 只是 seconds 为 0。
pub fn stop() -> Result<Option<RecordingSummary>, String> {
    let active = slot().lock().map_err(|e| e.to_string())?.take();
    let Some(a) = active else { return Ok(None) };
    a.stop.store(true, Ordering::Relaxed);
    let seconds = a.join.join().map_err(|_| "录音线程异常退出".to_string())??;
    let segments = list_segments(&a.dir).len();
    log::info!("[meeting] 停止录音 · {seconds:.1}s · {segments} 片");
    Ok(Some(RecordingSummary { dir: a.dir.display().to_string(), seconds, segments }))
}
