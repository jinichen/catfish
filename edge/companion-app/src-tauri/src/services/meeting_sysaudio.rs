//! 线上会议录「电脑放出来的声音」(11/11) —— 腾讯会议 / AONE 这类会议, 对方的声音从会议软件放出来,
//! 员工戴耳机时麦克风收不到。这里取那一路声音, 交给 meeting_recorder 写成第二条音轨 (audio/system/)。
//!
//! - macOS: 起 catfish-audiotap 小程序 (swift/catfish-audiotap.swift)。14.2+ 用系统的进程抓取,
//!   可以只录选中的会议软件; 13–14.1 用 ScreenCaptureKit; 更老的系统录不了。
//!   小程序 stdout 写 32 位浮点交错 PCM, stderr 写一行行 JSON 事件; 关它的 stdin = 停。
//! - Windows: cpal 在默认输出设备上开录音流 = WASAPI 回环, 录全部电脑声音 (不分程序)。
//!
//! 声音数据转成单声道 i16 一块块送进通道; 写盘、补静音对齐在 meeting_recorder。

use std::sync::mpsc;
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SysApp {
    pub name: String,
    pub bundle_id: String,
    pub pids: Vec<i32>,
    /// 这会儿正在出声
    pub playing: bool,
    /// 认得的会议软件 (排前面、默认选它)
    pub meeting: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct SysSources {
    /// "tap" (macOS 14.2+, 可选程序) | "sck" (macOS 13–14.1, 可选程序) | "loopback" (Windows, 全部声音) | "none"
    pub mode: String,
    pub apps: Vec<SysApp>,
    /// 给员工看的一句说明 (权限在哪开 / 为什么录不了)
    pub hint: String,
}

/// 员工选的声音来源。`bundle_id` 和 `pids` 都空 = 全部电脑声音 (不含鲶鱼自己)。
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct SysPick {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub bundle_id: Option<String>,
    #[serde(default)]
    pub pids: Vec<i32>,
}

impl SysPick {
    pub fn label(&self) -> String {
        if self.name.trim().is_empty() { "全部电脑声音".into() } else { self.name.trim().to_string() }
    }
}

/// 开起来的一路电脑声音。`blocks` 是单声道 i16; 出错写进 `error`。drop / stop 停掉。
pub struct SysCapture {
    pub rate: u32,
    pub mode: String,
    pub blocks: mpsc::Receiver<Vec<i16>>,
    pub error: Arc<Mutex<Option<String>>>,
    stopper: Option<Box<dyn FnOnce() + Send>>,
}

impl SysCapture {
    pub fn stop(&mut self) {
        if let Some(f) = self.stopper.take() {
            f();
        }
    }
}

impl Drop for SysCapture {
    fn drop(&mut self) {
        self.stop();
    }
}

/// 小程序 stderr 的一行事件 (只有 Mac 有小程序)
#[cfg(any(target_os = "macos", test))]
#[derive(Debug, Deserialize, PartialEq)]
pub struct HelperEvent {
    pub event: String,
    #[serde(default)]
    pub rate: u32,
    #[serde(default)]
    pub channels: usize,
    #[serde(default)]
    pub mode: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub message: String,
}

#[cfg(any(target_os = "macos", test))]
pub fn parse_event(line: &str) -> Option<HelperEvent> {
    serde_json::from_str(line.trim()).ok()
}

/// 权限类错误给一句员工能照着做的话
pub fn permission_hint(mode: &str) -> &'static str {
    match mode {
        "tap" => "第一次录会弹「录制系统音频」的授权, 点允许。之前点了拒绝的话: 系统设置 → 隐私与安全性 → 屏幕与系统录音 → 「仅系统录音」里打开 Catfish Companion。",
        "sck" => "要「屏幕录制」权限 (只取声音, 画面不录): 系统设置 → 隐私与安全性 → 屏幕录制 里打开 Catfish Companion, 然后重开鲶鱼。",
        "loopback" => "Windows 录的是这台电脑放出来的全部声音 (会议之外的提示音也会录进去), 开会时把音乐之类关掉。",
        _ => "这台电脑的系统版本录不了电脑声音 (Mac 要 macOS 13 以上), 只能录麦克风。戴耳机开会的话请改用外放, 或会后用「导入录音文件」导入会议软件自己的录音。",
    }
}

// ───────────────────────── macOS: catfish-audiotap ─────────────────────────

#[cfg(target_os = "macos")]
mod imp {
    use super::*;
    use crate::services::meeting_audio::{downmix_to_mono_i16, F32FrameDecoder};
    use std::io::{BufRead, BufReader, Read};
    use std::path::PathBuf;
    use std::process::{Child, Stdio};
    use std::time::Duration;

    pub fn helper_path() -> Option<PathBuf> {
        if let Ok(exe) = std::env::current_exe() {
            if let Some(contents) = exe.parent().and_then(|p| p.parent()) {
                let c = contents.join("Resources").join("catfish-audiotap");
                if c.exists() {
                    return Some(c);
                }
            }
        }
        let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("swift/catfish-audiotap");
        dev.exists().then_some(dev)
    }

    fn run_json(args: &[&str]) -> Result<serde_json::Value, String> {
        let bin = helper_path().ok_or("找不到 catfish-audiotap (安装包不完整?)")?;
        let out = crate::services::process::background_command(&bin).args(args).output().map_err(|e| format!("起 catfish-audiotap 失败: {e}"))?;
        if !out.status.success() {
            return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
        }
        serde_json::from_slice(&out.stdout).map_err(|e| format!("catfish-audiotap 输出看不懂: {e}"))
    }

    pub fn sources() -> SysSources {
        let mode = run_json(&["probe"])
            .ok()
            .and_then(|v| v.get("mode").and_then(|m| m.as_str()).map(str::to_string))
            .unwrap_or_else(|| "none".into());
        let apps: Vec<SysApp> = if mode == "none" {
            Vec::new()
        } else {
            run_json(&["list"]).ok().and_then(|v| serde_json::from_value(v).ok()).unwrap_or_default()
        };
        SysSources { hint: permission_hint(&mode).into(), mode, apps }
    }

    pub fn start(pick: &SysPick) -> Result<SysCapture, String> {
        let bin = helper_path().ok_or("找不到 catfish-audiotap (安装包不完整?)")?;
        let mut cmd = crate::services::process::background_command(&bin);
        cmd.arg("capture");
        if !pick.pids.is_empty() {
            cmd.arg("--pids").arg(pick.pids.iter().map(|p| p.to_string()).collect::<Vec<_>>().join(","));
        }
        if let Some(b) = pick.bundle_id.as_deref().filter(|b| !b.is_empty()) {
            cmd.arg("--bundle").arg(b);
        }
        let mut child: Child = cmd
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("起 catfish-audiotap 失败: {e}"))?;
        let stderr = child.stderr.take().ok_or("拿不到 catfish-audiotap 的输出")?;
        let mut stdout = child.stdout.take().ok_or("拿不到 catfish-audiotap 的输出")?;
        let stdin = child.stdin.take();

        // 第一行事件: started 或 error。弹授权框时用户可能要点一会儿, 给 60 秒
        let (first_tx, first_rx) = mpsc::channel::<Option<HelperEvent>>();
        let error = Arc::new(Mutex::new(None::<String>));
        let err_slot = error.clone();
        std::thread::Builder::new()
            .name("audiotap-events".into())
            .spawn(move || {
                let mut first = Some(first_tx);
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    let ev = parse_event(&line);
                    if let Some(tx) = first.take() {
                        let _ = tx.send(ev);
                        continue;
                    }
                    match ev {
                        Some(e) if e.event == "error" => {
                            log::warn!("[meeting] 电脑声音出错: {}", e.message);
                            *err_slot.lock().unwrap() = Some(format!("电脑声音: {}", e.message));
                        }
                        _ => log::debug!("[meeting] audiotap: {line}"),
                    }
                }
            })
            .map_err(|e| e.to_string())?;
        let started = match first_rx.recv_timeout(Duration::from_secs(60)) {
            Ok(Some(e)) if e.event == "started" && e.rate > 0 && e.channels > 0 => e,
            Ok(Some(e)) if e.event == "error" => {
                let _ = child.wait();
                let hint = if e.kind == "permission" { format!(" —— {}", permission_hint("tap")) } else { String::new() };
                return Err(format!("录电脑声音失败: {}{hint}", e.message));
            }
            Ok(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("录电脑声音失败: catfish-audiotap 没正常启动".into());
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("录电脑声音失败: 等了 60 秒没开起来 (授权框没点?)".into());
            }
        };

        let (tx, rx) = mpsc::sync_channel::<Vec<i16>>(256);
        let channels = started.channels;
        std::thread::Builder::new()
            .name("audiotap-pcm".into())
            .spawn(move || {
                let mut dec = F32FrameDecoder::new(channels);
                let mut buf = vec![0u8; 16 * 1024];
                loop {
                    match stdout.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => {
                            let f = dec.push(&buf[..n]);
                            if !f.is_empty() {
                                // 写盘跟不上就丢块, 不卡小程序 (卡住它会让系统丢更多)
                                let _ = tx.try_send(downmix_to_mono_i16(&f, channels));
                            }
                        }
                    }
                }
            })
            .map_err(|e| e.to_string())?;

        let child = Arc::new(Mutex::new(Some((child, stdin))));
        let stopper = move || {
            if let Some((mut c, stdin)) = child.lock().unwrap().take() {
                drop(stdin); // 小程序看到 stdin 关了就收尾退出
                for _ in 0..30 {
                    if matches!(c.try_wait(), Ok(Some(_))) {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                let _ = c.kill();
                let _ = c.wait();
            }
        };
        log::info!("[meeting] 电脑声音: {} · {}Hz × {} 声道 · {}", started.mode, started.rate, channels, pick.label());
        Ok(SysCapture { rate: started.rate, mode: started.mode, blocks: rx, error, stopper: Some(Box::new(stopper)) })
    }
}

// ───────────────────────── Windows: WASAPI 回环 ─────────────────────────

#[cfg(target_os = "windows")]
mod imp {
    use super::*;
    use crate::services::meeting_audio::downmix_to_mono_i16;
    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::Duration;

    pub fn sources() -> SysSources {
        let host = cpal::default_host();
        let mode = if host.default_output_device().is_some() { "loopback" } else { "none" };
        SysSources { mode: mode.into(), apps: Vec::new(), hint: permission_hint(mode).into() }
    }

    pub fn start(_pick: &SysPick) -> Result<SysCapture, String> {
        let stop = Arc::new(AtomicBool::new(false));
        let error = Arc::new(Mutex::new(None::<String>));
        let (tx, rx) = mpsc::sync_channel::<Vec<i16>>(256);
        let (ready_tx, ready_rx) = mpsc::channel::<Result<u32, String>>();
        let (stop_t, err_t) = (stop.clone(), error.clone());
        // cpal 的 Stream 不跨线程搬: 一个线程开流、拿着、等停
        let join = std::thread::Builder::new()
            .name("meeting-loopback".into())
            .spawn(move || {
                let r = (|| -> Result<cpal::Stream, String> {
                    let host = cpal::default_host();
                    let dev = host.default_output_device().ok_or("这台电脑没有放声音的设备")?;
                    let cfg = dev.default_output_config().map_err(|e| format!("读放音设备参数失败: {e}"))?;
                    let channels = cfg.channels() as usize;
                    let rate = cfg.sample_rate();
                    macro_rules! build {
                        ($t:ty) => {{
                            let tx = tx.clone();
                            let err_t = err_t.clone();
                            dev.build_input_stream(
                                cfg.clone().into(),
                                move |data: &[$t], _: &cpal::InputCallbackInfo| {
                                    let f: Vec<f32> =
                                        data.iter().map(|s| <f32 as cpal::FromSample<$t>>::from_sample_(*s)).collect();
                                    let _ = tx.try_send(downmix_to_mono_i16(&f, channels));
                                },
                                move |e: cpal::Error| {
                                    if !matches!(e.kind(), cpal::ErrorKind::Xrun | cpal::ErrorKind::DeviceChanged) {
                                        *err_t.lock().unwrap() = Some(format!("电脑声音: {e}"));
                                    }
                                },
                                None,
                            )
                        }};
                    }
                    let stream = match cfg.sample_format() {
                        cpal::SampleFormat::F32 => build!(f32),
                        cpal::SampleFormat::I16 => build!(i16),
                        cpal::SampleFormat::I32 => build!(i32),
                        cpal::SampleFormat::U16 => build!(u16),
                        other => return Err(format!("不支持的声音格式 {other:?}")),
                    }
                    .map_err(|e| format!("开电脑声音回环失败: {e}"))?;
                    stream.play().map_err(|e| format!("开始录电脑声音失败: {e}"))?;
                    let _ = ready_tx.send(Ok(rate));
                    Ok(stream)
                })();
                match r {
                    Ok(stream) => {
                        while !stop_t.load(Ordering::Relaxed) {
                            std::thread::sleep(Duration::from_millis(100));
                        }
                        drop(stream);
                    }
                    Err(e) => {
                        let _ = ready_tx.send(Err(e));
                    }
                }
            })
            .map_err(|e| e.to_string())?;
        let rate = match ready_rx.recv_timeout(Duration::from_secs(10)) {
            Ok(Ok(r)) => r,
            Ok(Err(e)) => {
                let _ = join.join();
                return Err(e);
            }
            Err(_) => {
                stop.store(true, Ordering::Relaxed);
                return Err("开电脑声音回环超时".into());
            }
        };
        let join = Mutex::new(Some(join));
        let stopper = move || {
            stop.store(true, Ordering::Relaxed);
            if let Some(j) = join.lock().unwrap().take() {
                let _ = j.join();
            }
        };
        log::info!("[meeting] 电脑声音: WASAPI 回环 · {rate}Hz");
        Ok(SysCapture { rate, mode: "loopback".into(), blocks: rx, error, stopper: Some(Box::new(stopper)) })
    }
}

pub use imp::{sources, start};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helper_events_parse() {
        let e = parse_event(r#"{"event":"started","rate":48000,"channels":2,"mode":"tap","processes":1}"#).unwrap();
        assert_eq!((e.event.as_str(), e.rate, e.channels, e.mode.as_str()), ("started", 48000, 2, "tap"));
        let e = parse_event(r#"{"event":"error","kind":"permission","message":"没权限"}"#).unwrap();
        assert_eq!((e.kind.as_str(), e.message.as_str()), ("permission", "没权限"));
        assert!(parse_event("not json").is_none());
    }

    #[test]
    fn pick_label_defaults_to_all_sound() {
        assert_eq!(SysPick::default().label(), "全部电脑声音");
        assert_eq!(SysPick { name: " 腾讯会议 ".into(), ..Default::default() }.label(), "腾讯会议");
    }
}
