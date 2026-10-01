//! 会议录音的纯逻辑部分 (10/1, docs/MEETING-MINUTES-PLAN.md §3) —— 不依赖 cpal,
//! 所以 CI 的 ubuntu cargo test 照常能测。真正开麦克风的是 meeting_recorder.rs。
//!
//! - 分片写 WAV: 单声道 16-bit, **设备原生采样率** (FunASR 自己重采样, 实测 48k / 16k
//!   转出来一字不差); 每 `segment_secs` 换一个 seg-NNNN.wav。
//! - 防崩: 每秒 `flush()` 一次 —— hound 的 flush 会回写 WAV 头, 进程被杀也只丢最后
//!   不到一秒, 已写的分片都是合法 WAV。
//! - 上限: 写满 `max_secs` 自动停, 防止忘了关录一整夜。
//! - 电平: 每块数据算峰值, 界面拿它判断"麦克风到底有没有收到声音"(选错设备时
//!   第一眼就能看出来, 不用等会后转写出一片空白)。

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

pub const DEFAULT_SEGMENT_SECS: u32 = 300;
/// 4 小时。会议再长也该分场了; 主要是防"忘了停"。
pub const DEFAULT_MAX_SECS: u32 = 4 * 3600;
const FLUSH_EVERY: Duration = Duration::from_secs(1);

/// 交错多声道 f32 → 单声道 i16 (取平均)。
pub fn downmix_to_mono_i16(interleaved: &[f32], channels: usize) -> Vec<i16> {
    let ch = channels.max(1);
    interleaved
        .chunks_exact(ch)
        .map(|frame| {
            let avg = frame.iter().sum::<f32>() / ch as f32;
            (avg.clamp(-1.0, 1.0) * i16::MAX as f32) as i16
        })
        .collect()
}

/// 0.0–1.0 的峰值电平。
pub fn peak_level(samples: &[i16]) -> f32 {
    samples.iter().map(|s| (*s as i32).unsigned_abs()).max().unwrap_or(0) as f32 / i16::MAX as f32
}

pub fn segment_path(dir: &Path, index: u32) -> PathBuf {
    dir.join(format!("seg-{index:04}.wav"))
}

/// 录音目录里已有的分片, 按序号排好。
pub fn list_segments(dir: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("seg-") && n.ends_with(".wav"))
        })
        .collect();
    v.sort();
    v
}

/// 按名字挑设备: 指定了就必须精确找到 (找不到报错, 不悄悄换成别的麦克风 ——
/// 那正是 speech.rs 写死 `:0` 录到 iPhone 的问题); 没指定用系统默认。
pub fn pick_device_index(names: &[String], wanted: Option<&str>, default_index: Option<usize>) -> Result<usize, String> {
    match wanted {
        Some(w) => names
            .iter()
            .position(|n| n == w)
            .ok_or_else(|| format!("找不到录音设备「{w}」(可能已断开), 现有: {}", names.join(" / "))),
        None => default_index.ok_or_else(|| "系统没有默认录音设备".to_string()),
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum WriteOutcome {
    Continue,
    /// 到 max_secs 了, 调用方应该停止录音
    LimitReached,
}

pub struct SegmentWriter {
    dir: PathBuf,
    sample_rate: u32,
    segment_samples: u64,
    max_samples: u64,
    index: u32,
    in_segment: u64,
    total: u64,
    writer: Option<hound::WavWriter<std::io::BufWriter<std::fs::File>>>,
    last_flush: Instant,
}

impl SegmentWriter {
    pub fn new(dir: &Path, sample_rate: u32, segment_secs: u32, max_secs: u32) -> Result<Self, String> {
        std::fs::create_dir_all(dir).map_err(|e| format!("建录音目录失败: {e}"))?;
        // 续录 (同一场会停了又开): 接在已有分片后面编号, 不覆盖
        let index = list_segments(dir).len() as u32;
        Ok(Self {
            dir: dir.to_path_buf(),
            sample_rate,
            segment_samples: sample_rate as u64 * segment_secs.max(1) as u64,
            max_samples: sample_rate as u64 * max_secs.max(1) as u64,
            index,
            in_segment: 0,
            total: 0,
            writer: None,
            last_flush: Instant::now(),
        })
    }

    fn open_next(&mut self) -> Result<(), String> {
        self.index += 1;
        let spec = hound::WavSpec {
            channels: 1,
            sample_rate: self.sample_rate,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let path = segment_path(&self.dir, self.index);
        self.writer = Some(hound::WavWriter::create(&path, spec).map_err(|e| format!("建 {} 失败: {e}", path.display()))?);
        self.in_segment = 0;
        Ok(())
    }

    pub fn write(&mut self, samples: &[i16]) -> Result<WriteOutcome, String> {
        for &s in samples {
            if self.total >= self.max_samples {
                self.close_current()?;
                return Ok(WriteOutcome::LimitReached);
            }
            if self.writer.is_none() || self.in_segment >= self.segment_samples {
                self.close_current()?;
                self.open_next()?;
            }
            let w = self.writer.as_mut().expect("刚打开");
            w.write_sample(s).map_err(|e| format!("写录音失败: {e}"))?;
            self.in_segment += 1;
            self.total += 1;
        }
        if self.last_flush.elapsed() >= FLUSH_EVERY {
            self.last_flush = Instant::now();
            if let Some(w) = self.writer.as_mut() {
                w.flush().map_err(|e| format!("写录音失败: {e}"))?;
            }
        }
        Ok(if self.total >= self.max_samples { WriteOutcome::LimitReached } else { WriteOutcome::Continue })
    }

    fn close_current(&mut self) -> Result<(), String> {
        if let Some(w) = self.writer.take() {
            w.finalize().map_err(|e| format!("收尾录音分片失败: {e}"))?;
        }
        Ok(())
    }

    pub fn finish(mut self) -> Result<f64, String> {
        self.close_current()?;
        Ok(self.total as f64 / self.sample_rate as f64)
    }

    pub fn seconds(&self) -> f64 {
        self.total as f64 / self.sample_rate as f64
    }
}

#[cfg(test)]
#[path = "meeting_audio_tests.rs"]
mod tests;
