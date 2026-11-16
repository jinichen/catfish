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
//! `com.apple.security.device.audio-input` entitlement (build-sign-notarize-mac.sh),
//! 否则签名后的正式包录到的是静音, 而 `tauri dev` 一切正常。
//!
//! 11/11 线上会议: 选了「同时录电脑声音」时, 再开一路 (meeting_sysaudio), 写到 audio/system/。
//! 两条以同一个起点对齐: 没有声音在放时系统不送数据, 按墙上时间补静音 (silence_needed)。
//! 电脑声音开不起来 (没授权 / 系统太老) 就整个不开始 —— 员工要的是录线上会议, 悄悄只录麦克风
//! 会到会后才发现对方的话一句没有。

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::Serialize;

use super::meeting_audio::{
    downmix_to_mono_i16, list_segments, pad_track_to, peak_level, pick_device_index, silence_needed, system_track_dir,
    track_seconds, SegmentWriter, WriteOutcome,
};
use super::meeting_sysaudio::{self as sysaudio, SysCapture, SysPick};

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
    /// 11/06: 丢了几小段声音 (cpal Xrun: 电脑太忙, 音频没及时取走, 每次几十毫秒)。录音照常继续, 不算错误
    pub glitches: u32,
    /// 11/06: 不影响录音的提示 (系统换了麦克风, 录音自动跟过去了)
    pub notice: Option<String>,
    /// 11/11: 同时在录的电脑声音 (线上会议对方的声音); 只录麦克风时 None
    pub system: Option<SystemTrackStatus>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SystemTrackStatus {
    /// 录的是谁的声音: 「腾讯会议」/「全部电脑声音」
    pub source: String,
    pub level: f32,
    pub seconds: f64,
    /// "tap" | "sck" | "loopback"
    pub mode: String,
    pub error: Option<String>,
}

/// 录音流报上来的问题分三类 (11/06 鸿波: 打包时开会录音, 冒出红字「录音设备出错: A buffer underrun or overrun
/// occurred」, 其实录音一直在继续, 只是某一瞬间丢了几十毫秒 —— 红字还一直挂到录完, 看着像坏了)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StreamIssue {
    /// 瞬间丢了一小段 (Xrun), 计数
    Glitch,
    /// 系统换了录音设备, 流自动跟过去了, 提示一句
    Rerouted,
    /// 真出错了 (设备被拔掉 / 后端报错 ...), 红字
    Fatal,
}

fn classify(kind: cpal::ErrorKind) -> StreamIssue {
    match kind {
        cpal::ErrorKind::Xrun => StreamIssue::Glitch,
        cpal::ErrorKind::DeviceChanged => StreamIssue::Rerouted,
        _ => StreamIssue::Fatal,
    }
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
    glitches: AtomicU32,
    notice: Mutex<Option<String>>,
    sys_level_bits: AtomicU32,
    sys_millis: AtomicU64,
    /// 11/16: 现在录的是哪个麦克风 / 采样率 (中途断开会自动换成系统默认的那个)
    device: Mutex<String>,
    sample_rate: AtomicU32,
}

struct Active {
    stop: Arc<AtomicBool>,
    shared: Arc<Shared>,
    join: JoinHandle<Result<f64, String>>,
    device: String,
    sample_rate: u32,
    dir: PathBuf,
    system: Option<ActiveSystem>,
}

struct ActiveSystem {
    source: String,
    mode: String,
    error: Arc<Mutex<Option<String>>>,
    join: JoinHandle<Result<(), String>>,
}

/// 电脑声音那条的写盘线程: 收一块写一块, 落后墙上时间就先补静音; 麦克风那边停了它也停。
fn system_thread(
    mut cap: SysCapture,
    dir: PathBuf,
    t0: std::time::Instant,
    segment_secs: u32,
    max_secs: u32,
    stop: Arc<AtomicBool>,
    shared: Arc<Shared>,
) -> Result<(), String> {
    const TOLERANCE: Duration = Duration::from_millis(250);
    let rate = cap.rate;
    let mut writer = SegmentWriter::new(&dir, rate, segment_secs, max_secs)?;
    let mut written: u64 = 0;
    let silence = vec![0i16; rate as usize];
    let result = (|| -> Result<(), String> {
        loop {
            if stop.load(Ordering::Relaxed) || !shared.running.load(Ordering::Relaxed) {
                return Ok(());
            }
            let block = match cap.blocks.recv_timeout(Duration::from_millis(200)) {
                Ok(b) => Some(b),
                Err(mpsc::RecvTimeoutError::Timeout) => None,
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    // 小程序退了 (出错 / 被杀): 记一句, 麦克风照录
                    let mut e = cap.error.lock().unwrap();
                    if e.is_none() {
                        *e = Some("电脑声音中断了 (会议软件退出? ), 麦克风照常在录".into());
                    }
                    return Ok(());
                }
            };
            let mut gap = silence_needed(written, rate, t0.elapsed(), TOLERANCE);
            while gap > 0 {
                let n = gap.min(silence.len() as u64) as usize;
                if writer.write(&silence[..n])? == WriteOutcome::LimitReached {
                    return Ok(());
                }
                written += n as u64;
                gap -= n as u64;
            }
            match block {
                Some(b) => {
                    shared.sys_level_bits.store(peak_level(&b).to_bits(), Ordering::Relaxed);
                    written += b.len() as u64;
                    if writer.write(&b)? == WriteOutcome::LimitReached {
                        return Ok(());
                    }
                }
                None => shared.sys_level_bits.store(0f32.to_bits(), Ordering::Relaxed),
            }
            shared.sys_millis.store((writer.seconds() * 1000.0) as u64, Ordering::Relaxed);
        }
    })();
    cap.stop();
    writer.finish()?;
    result
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

pub fn start(
    dir: &Path,
    device: Option<&str>,
    segment_secs: u32,
    max_secs: u32,
    system: Option<&SysPick>,
) -> Result<RecorderStatus, String> {
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
        glitches: AtomicU32::new(0),
        notice: Mutex::new(None),
        sys_level_bits: AtomicU32::new(0),
        sys_millis: AtomicU64::new(0),
        device: Mutex::new(String::new()),
        sample_rate: AtomicU32::new(0),
    });
    // 同一场会停了又录: 麦克风那条已经有多长 (电脑声音那条要先补齐到这里再接着录)
    let mic_before = track_seconds(dir);
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
            let t0 = std::time::Instant::now();
            let system = match system {
                None => None,
                Some(pick) => match start_system(dir, pick, mic_before, t0, segment_secs, max_secs, &stop, &shared) {
                    Ok(s) => Some(s),
                    Err(e) => {
                        stop.store(true, Ordering::Relaxed);
                        let _ = join.join();
                        return Err(e);
                    }
                },
            };
            log::info!("[meeting] 开始录音 · {device} · {sample_rate}Hz → {}", dir.display());
            *guard = Some(Active { stop, shared, join, device, sample_rate, dir: dir.to_path_buf(), system });
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

#[allow(clippy::too_many_arguments)]
fn start_system(
    dir: &Path,
    pick: &SysPick,
    mic_before: f64,
    t0: std::time::Instant,
    segment_secs: u32,
    max_secs: u32,
    stop: &Arc<AtomicBool>,
    shared: &Arc<Shared>,
) -> Result<ActiveSystem, String> {
    let cap = sysaudio::start(pick)?;
    let sys_dir = system_track_dir(dir);
    // 前面几段只录了麦克风: 电脑声音这条先补静音到同样长, 两条才对得上时间
    pad_track_to(&sys_dir, mic_before, cap.rate)?;
    let (mode, error) = (cap.mode.clone(), cap.error.clone());
    let (stop_t, shared_t) = (stop.clone(), shared.clone());
    let join = std::thread::Builder::new()
        .name("meeting-system-audio".into())
        .spawn(move || system_thread(cap, sys_dir, t0, segment_secs, max_secs, stop_t, shared_t))
        .map_err(|e| format!("起电脑声音线程失败: {e}"))?;
    Ok(ActiveSystem { source: pick.label(), mode, error, join })
}

/// 连续这么久收不到一块数据 = 设备其实已经没了 (有的蓝牙耳机断开时不报错, 流就这么停着)。输入流平时安静时也照样送静音块。
const STALL: Duration = Duration::from_secs(6);
/// 断开后找不到可用麦克风时, 隔多久再试一次
const RETRY: Duration = Duration::from_secs(1);

struct Opened {
    stream: cpal::Stream,
    name: String,
    rate: u32,
}

/// 打开一个麦克风 (wanted = None 就用系统默认的), 开始往 tx 送块。设备报「断开」等致命错误时把 lost 置上, 由录音线程换设备。
fn open_input(
    host: &cpal::Host,
    wanted: Option<&str>,
    tx: &mpsc::SyncSender<Vec<i16>>,
    shared: &Arc<Shared>,
    lost: &Arc<AtomicBool>,
) -> Result<Opened, String> {
    let devices: Vec<cpal::Device> = host.input_devices().map_err(|e| format!("列录音设备失败: {e}"))?.collect();
    let names: Vec<String> = devices.iter().map(device_name).collect();
    let default_name = host.default_input_device().map(|d| device_name(&d));
    let default_index = default_name.and_then(|n| names.iter().position(|x| *x == n));
    let idx = pick_device_index(&names, wanted, default_index)?;
    let device = &devices[idx];
    let config = device.default_input_config().map_err(|e| format!("读「{}」的录音参数失败: {e}", names[idx]))?;
    let sample_rate = config.sample_rate();
    let channels = config.channels() as usize;

    let (err_shared, err_lost) = (shared.clone(), lost.clone());
    let on_error = move |e: cpal::Error| match classify(e.kind()) {
        StreamIssue::Glitch => {
            let n = err_shared.glitches.fetch_add(1, Ordering::Relaxed) + 1;
            if n == 1 || n % 50 == 0 {
                log::warn!("[meeting] 录音丢了一小段 (第 {n} 次, 电脑太忙): {e}");
            }
        }
        StreamIssue::Rerouted => {
            log::info!("[meeting] 系统换了录音设备, 录音自动跟过去了: {e}");
            *err_shared.notice.lock().unwrap() = Some("系统换了录音设备 (比如插上 / 拔掉了耳机), 录音已自动跟过去, 照常继续".into());
        }
        StreamIssue::Fatal => {
            // 11/16 以前: 只记一句红字, 录音线程接着空等 —— 计时停住、电平停在最后那一下 (「没收到声音」也不提示),
            // 看着还在录, 其实什么都没录上 (AirPods 断开后 17 分钟没录到)。现在交给录音线程换麦克风接着录。
            log::warn!("[meeting] 录音流出错, 换麦克风接着录: {e}");
            err_lost.store(true, Ordering::Relaxed);
        }
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
    Ok(Opened { stream, name: names[idx].clone(), rate: sample_rate })
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
    let (tx, rx) = mpsc::sync_channel::<Vec<i16>>(256);
    let lost = Arc::new(AtomicBool::new(false));
    let opened = open_input(&host, wanted, &tx, shared, &lost)?;
    let _ = ready.send(Ok((opened.name.clone(), opened.rate)));
    *shared.device.lock().unwrap() = opened.name.clone();
    shared.sample_rate.store(opened.rate, Ordering::Relaxed);

    // 换过麦克风的话, 前面那几段已经写完的秒数 (换设备 = 新开一片, 采样率可能不同: AirPods 24k / MacBook 48k)
    let mut done_secs = 0.0;
    let mut writer = Some(SegmentWriter::new(dir, opened.rate, segment_secs, max_secs)?);
    let mut current = Some(opened);
    let mut last_data = Instant::now();
    let mut limit = false;

    while !stop.load(Ordering::Relaxed) && !limit {
        match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(block) => {
                last_data = Instant::now();
                let w = writer.as_mut().expect("录音中总有 writer");
                shared.level_bits.store(peak_level(&block).to_bits(), Ordering::Relaxed);
                let outcome = w.write(&block)?;
                shared.millis.store(((done_secs + w.seconds()) * 1000.0) as u64, Ordering::Relaxed);
                if outcome == WriteOutcome::LimitReached {
                    shared.limit_reached.store(true, Ordering::Relaxed);
                    log::info!("[meeting] 到录音上限 {max_secs}s, 自动停止");
                    limit = true;
                }
                continue;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
        if !lost.load(Ordering::Relaxed) && last_data.elapsed() < STALL {
            continue;
        }

        // ── 麦克风没了 (报了断开, 或 6 秒没送一块): 收掉这一片, 换系统默认的麦克风接着录 ──
        let old = current.take().map(|o| { drop(o.stream); o.name }).unwrap_or_default();
        while let Ok(block) = rx.try_recv() {
            let _ = writer.as_mut().expect("录音中总有 writer").write(&block)?;
        }
        done_secs += writer.take().expect("录音中总有 writer").finish()?;
        shared.level_bits.store(0f32.to_bits(), Ordering::Relaxed);
        log::warn!("[meeting] 「{old}」断开了 (已录 {done_secs:.0}s), 换系统默认麦克风接着录");
        let reopened = loop {
            if stop.load(Ordering::Relaxed) {
                break None;
            }
            lost.store(false, Ordering::Relaxed);
            match open_input(&host, None, &tx, shared, &lost) {
                Ok(o) => break Some(o),
                Err(e) => {
                    *shared.error.lock().unwrap() =
                        Some(format!("「{old}」断开了, 在等可用的麦克风 (连上 / 插上就自动接着录): {e}"));
                    std::thread::sleep(RETRY);
                }
            }
        };
        let Some(o) = reopened else { break };
        let remaining = (max_secs as f64 - done_secs).max(0.0) as u32;
        if remaining == 0 {
            break;
        }
        let mut w = SegmentWriter::new(dir, o.rate, segment_secs, remaining)?;
        // 断开到接上这段没录到: 补同样长的静音, 后面的话在时间轴上才对得上 (跟电脑声音那条、会后转写的时间)
        let gap = last_data.elapsed().as_secs_f64();
        let zeros = vec![0i16; o.rate as usize];
        let mut left = (gap * o.rate as f64) as u64;
        while left > 0 {
            let n = left.min(zeros.len() as u64) as usize;
            if w.write(&zeros[..n])? == WriteOutcome::LimitReached {
                limit = true;
                break;
            }
            left -= n as u64;
        }
        let note = if o.name == old {
            format!("「{old}」中断了一下, 已重新接上 (中间约 {gap:.0} 秒没录到)")
        } else {
            format!("「{old}」断开了, 已自动换成「{}」接着录 (中间约 {gap:.0} 秒没录到)", o.name)
        };
        log::info!("[meeting] {note}");
        *shared.notice.lock().unwrap() = Some(note);
        *shared.error.lock().unwrap() = None;
        *shared.device.lock().unwrap() = o.name.clone();
        shared.sample_rate.store(o.rate, Ordering::Relaxed);
        writer = Some(w);
        current = Some(o);
        last_data = Instant::now();
    }
    drop(current);
    // 通道里剩下的也写掉
    let Some(mut w) = writer else { return Ok(done_secs) };
    while let Ok(block) = rx.try_recv() {
        if w.write(&block)? == WriteOutcome::LimitReached {
            break;
        }
    }
    Ok(done_secs + w.finish()?)
}

pub fn status() -> RecorderStatus {
    let guard = slot().lock().ok();
    match guard.as_ref().and_then(|g| g.as_ref()) {
        Some(a) => RecorderStatus {
            recording: a.shared.running.load(Ordering::Relaxed),
            seconds: a.shared.millis.load(Ordering::Relaxed) as f64 / 1000.0,
            level: f32::from_bits(a.shared.level_bits.load(Ordering::Relaxed)),
            device: a.shared.device.lock().ok().map(|d| d.clone()).filter(|d| !d.is_empty()).unwrap_or_else(|| a.device.clone()),
            sample_rate: match a.shared.sample_rate.load(Ordering::Relaxed) { 0 => a.sample_rate, r => r },
            dir: Some(a.dir.display().to_string()),
            error: a.shared.error.lock().ok().and_then(|e| e.clone()),
            limit_reached: a.shared.limit_reached.load(Ordering::Relaxed),
            glitches: a.shared.glitches.load(Ordering::Relaxed),
            notice: a.shared.notice.lock().ok().and_then(|n| n.clone()),
            system: a.system.as_ref().map(|s| SystemTrackStatus {
                source: s.source.clone(),
                level: f32::from_bits(a.shared.sys_level_bits.load(Ordering::Relaxed)),
                seconds: a.shared.sys_millis.load(Ordering::Relaxed) as f64 / 1000.0,
                mode: s.mode.clone(),
                error: s.error.lock().ok().and_then(|e| e.clone()),
            }),
        },
        None => RecorderStatus {
            recording: false, seconds: 0.0, level: 0.0, device: String::new(),
            sample_rate: 0, dir: None, error: None, limit_reached: false, glitches: 0, notice: None, system: None,
        },
    }
}

/// 停止并收尾。没在录也返回 Ok (幂等), 只是 seconds 为 0。
pub fn stop() -> Result<Option<RecordingSummary>, String> {
    let active = slot().lock().map_err(|e| e.to_string())?.take();
    let Some(a) = active else { return Ok(None) };
    a.stop.store(true, Ordering::Relaxed);
    let seconds = a.join.join().map_err(|_| "录音线程异常退出".to_string())??;
    let had_system = a.system.is_some();
    if let Some(s) = a.system {
        if let Err(e) = s.join.join().map_err(|_| "电脑声音线程异常退出".to_string()).and_then(|r| r) {
            log::warn!("[meeting] 电脑声音收尾出错: {e}");
        }
    }
    // 两条收成一样长 (尾巴上没声音在放的那段, 以及这次没录电脑声音的那段)
    let sys_dir = system_track_dir(&a.dir);
    if had_system || !list_segments(&sys_dir).is_empty() {
        if let Err(e) = pad_track_to(&sys_dir, track_seconds(&a.dir), 16_000) {
            log::warn!("[meeting] 电脑声音那条补齐失败: {e}");
        }
    }
    let segments = list_segments(&a.dir).len();
    log::info!("[meeting] 停止录音 · {seconds:.1}s · {segments} 片");
    Ok(Some(RecordingSummary { dir: a.dir.display().to_string(), seconds, segments }))
}

#[cfg(test)]
mod stream_issue_tests {
    use super::*;

    #[test]
    fn 丢一小段和换设备不算出错_其余才红字() {
        assert_eq!(classify(cpal::ErrorKind::Xrun), StreamIssue::Glitch);
        assert_eq!(classify(cpal::ErrorKind::DeviceChanged), StreamIssue::Rerouted);
        assert_eq!(classify(cpal::ErrorKind::DeviceNotAvailable), StreamIssue::Fatal);
        assert_eq!(classify(cpal::ErrorKind::BackendError), StreamIssue::Fatal);
    }
}
