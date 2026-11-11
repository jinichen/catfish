use super::*;

fn read_wav(path: &Path) -> (hound::WavSpec, Vec<i16>) {
    let mut r = hound::WavReader::open(path).unwrap();
    let spec = r.spec();
    let s = r.samples::<i16>().map(|x| x.unwrap()).collect();
    (spec, s)
}

#[test]
fn downmix_averages_channels_and_clamps() {
    let st = [0.5_f32, -0.5, 1.0, 1.0, 2.0, 2.0];
    assert_eq!(downmix_to_mono_i16(&st, 2), vec![0, i16::MAX, i16::MAX]);
    assert_eq!(downmix_to_mono_i16(&[0.25, -1.0], 1).len(), 2);
}

#[test]
fn peak_level_is_normalized() {
    assert_eq!(peak_level(&[]), 0.0);
    assert!((peak_level(&[0, -16384, 100]) - 0.5).abs() < 0.001);
}

#[test]
fn device_pick_is_exact_and_never_silently_substitutes() {
    let names = vec!["“Chenhb”的麦克风".to_string(), "MacBook Air麦克风".to_string()];
    assert_eq!(pick_device_index(&names, Some("MacBook Air麦克风"), Some(0)).unwrap(), 1);
    // 没指定 → 系统默认, 不是序号 0
    assert_eq!(pick_device_index(&names, None, Some(1)).unwrap(), 1);
    let err = pick_device_index(&names, Some("Jabra Speak"), Some(1)).unwrap_err();
    assert!(err.contains("Jabra Speak") && err.contains("MacBook Air麦克风"), "{err}");
    assert!(pick_device_index(&names, None, None).is_err());
}

#[test]
fn rotates_segments_and_keeps_every_sample() {
    let dir = tempfile::tempdir().unwrap();
    // 1000 Hz, 2 秒一片 → 每片 2000 个样本
    let mut w = SegmentWriter::new(dir.path(), 1000, 2, 3600).unwrap();
    let data: Vec<i16> = (0..5500).map(|i| (i % 1000) as i16).collect();
    assert_eq!(w.write(&data).unwrap(), WriteOutcome::Continue);
    assert_eq!(w.finish().unwrap(), 5.5);

    let segs = list_segments(dir.path());
    assert_eq!(segs.iter().map(|p| p.file_name().unwrap().to_str().unwrap()).collect::<Vec<_>>(),
               vec!["seg-0001.wav", "seg-0002.wav", "seg-0003.wav"]);
    let mut all = vec![];
    for (i, p) in segs.iter().enumerate() {
        let (spec, s) = read_wav(p);
        assert_eq!((spec.channels, spec.sample_rate, spec.bits_per_sample), (1, 1000, 16));
        assert_eq!(s.len(), [2000, 2000, 1500][i]);
        all.extend(s);
    }
    assert_eq!(all, data);
}

#[test]
fn stops_at_max_duration() {
    let dir = tempfile::tempdir().unwrap();
    let mut w = SegmentWriter::new(dir.path(), 1000, 300, 3).unwrap();
    assert_eq!(w.write(&vec![1; 2000]).unwrap(), WriteOutcome::Continue);
    assert_eq!(w.write(&vec![1; 2000]).unwrap(), WriteOutcome::LimitReached);
    assert_eq!(w.finish().unwrap(), 3.0);
    let (_, s) = read_wav(&list_segments(dir.path())[0]);
    assert_eq!(s.len(), 3000);
}

#[test]
fn resuming_continues_numbering_instead_of_overwriting() {
    let dir = tempfile::tempdir().unwrap();
    let mut a = SegmentWriter::new(dir.path(), 1000, 300, 3600).unwrap();
    a.write(&[7; 100]).unwrap();
    a.finish().unwrap();
    let mut b = SegmentWriter::new(dir.path(), 1000, 300, 3600).unwrap();
    b.write(&[9; 50]).unwrap();
    b.finish().unwrap();
    let segs = list_segments(dir.path());
    assert_eq!(segs.len(), 2);
    assert_eq!(read_wav(&segs[0]).1, vec![7; 100]);
    assert_eq!(read_wav(&segs[1]).1, vec![9; 50]);
}

#[test]
fn killed_process_still_leaves_a_readable_wav() {
    let dir = tempfile::tempdir().unwrap();
    let mut w = SegmentWriter::new(dir.path(), 1000, 300, 3600).unwrap();
    w.write(&[3; 400]).unwrap();
    std::thread::sleep(FLUSH_EVERY + Duration::from_millis(50));
    w.write(&[4; 100]).unwrap(); // 触发 flush (回写 WAV 头)
    // 模拟进程被杀: 不 finish、不 drop (drop 会自动 finalize, 那就测不到了)
    std::mem::forget(w);

    let (_, s) = read_wav(&list_segments(dir.path())[0]);
    assert_eq!(s.len(), 500, "flush 之前写的都应该能读出来");
}

/// 真机冒烟: 列设备 + 用系统默认麦克风录 3 秒。默认跳过 (CI 没有麦克风)。
///   cargo test --lib meeting_audio::tests::real_mic -- --ignored --nocapture
#[cfg(any(target_os = "macos", target_os = "windows"))]
#[test]
#[ignore]
fn real_mic_smoke() {
    use crate::services::meeting_recorder as rec;
    let devs = rec::list_input_devices().unwrap();
    println!("devices: {devs:?}");
    assert!(!devs.is_empty());
    let dir = tempfile::tempdir().unwrap();
    let st = rec::start(dir.path(), None, 300, 3600, None).unwrap();
    println!("start: {st:?}");
    let mut peak = 0f32;
    for _ in 0..30 {
        std::thread::sleep(Duration::from_millis(100));
        peak = peak.max(rec::status().level);
    }
    let sum = rec::stop().unwrap().unwrap();
    println!("summary: {sum:?} · 期间最大电平 {peak:.3}");
    assert!(sum.seconds > 2.0, "{sum:?}");
    let (spec, s) = read_wav(&list_segments(dir.path())[0]);
    println!("wav: {spec:?} · {} samples", s.len());
    assert_eq!(spec.channels, 1);
}

// 11/11 线上会议: 电脑声音那条
#[test]
fn silence_fills_only_when_behind_wall_clock_beyond_tolerance() {
    let tol = Duration::from_millis(250);
    // 2 秒没收到任何数据 (没声音在放) → 补到 1.75 秒
    assert_eq!(silence_needed(0, 1000, Duration::from_secs(2), tol), 1750);
    // 数据正常在来, 只晚到一点 → 不补
    assert_eq!(silence_needed(1900, 1000, Duration::from_secs(2), tol), 0);
    // 刚开始, 还在容差里
    assert_eq!(silence_needed(0, 1000, Duration::from_millis(200), tol), 0);
    // 写得比墙上时间还多 (声卡时钟略快) → 不补, 也不出负数
    assert_eq!(silence_needed(5000, 1000, Duration::from_secs(2), tol), 0);
}

#[test]
fn pad_track_appends_silence_up_to_target() {
    let d = tempfile::tempdir().unwrap();
    let mut w = SegmentWriter::new(d.path(), 1000, 300, 3600).unwrap();
    w.write(&[7; 1500]).unwrap();
    w.finish().unwrap();
    pad_track_to(d.path(), 4.0, 1000).unwrap();
    assert!((track_seconds(d.path()) - 4.0).abs() < 0.01);
    let segs = list_segments(d.path());
    assert_eq!(segs.len(), 2, "补的静音另起一片, 不改已有的");
    let (_, s) = read_wav(&segs[1]);
    assert!(s.iter().all(|x| *x == 0));
    // 已经够长 → 不动
    pad_track_to(d.path(), 3.0, 1000).unwrap();
    assert_eq!(list_segments(d.path()).len(), 2);
}

#[test]
fn f32_decoder_keeps_partial_frames_for_next_read() {
    let mut dec = F32FrameDecoder::new(2);
    let bytes: Vec<u8> = [0.5f32, -0.5, 0.25, 1.0].iter().flat_map(|f| f.to_le_bytes()).collect();
    // 一帧 8 字节; 先来 11 字节 → 一帧, 零头 3 字节留着
    assert_eq!(dec.push(&bytes[..11]), vec![0.5, -0.5]);
    assert_eq!(dec.push(&bytes[11..]), vec![0.25, 1.0]);
    assert!(dec.push(&[]).is_empty());
}

/// 真录一段线上会议: 麦克风 + 电脑声音两条。默认跳过 (要声卡、要授权)。
///   CATFISH_TEST_CLIP=<一段有声音的音频> cargo test --lib real_online_meeting -- --ignored --nocapture
/// 先停 1.5 秒什么都不放 (系统这时不送数据, 要靠补静音对齐), 再放音频, 停了再录 1.5 秒。
#[cfg(target_os = "macos")]
#[test]
#[ignore]
fn real_online_meeting_records_two_aligned_tracks() {
    use crate::services::meeting_recorder as rec;
    use crate::services::meeting_sysaudio::SysPick;
    let clip = std::env::var("CATFISH_TEST_CLIP").expect("CATFISH_TEST_CLIP");
    let dir = tempfile::tempdir().unwrap();
    let st = rec::start(dir.path(), None, 300, 3600, Some(&SysPick::default())).unwrap();
    assert!(st.system.is_some(), "{st:?}");
    std::thread::sleep(Duration::from_millis(1500));
    let status = std::process::Command::new("afplay").arg(&clip).status().unwrap();
    assert!(status.success());
    let mut peak = 0f32;
    for _ in 0..15 {
        std::thread::sleep(Duration::from_millis(100));
        peak = peak.max(rec::status().system.map(|s| s.level).unwrap_or(0.0));
    }
    let sum = rec::stop().unwrap().unwrap();
    let sys = system_track_dir(dir.path());
    let (mic_s, sys_s) = (track_seconds(dir.path()), track_seconds(&sys));
    println!("summary {sum:?} · mic {mic_s:.2}s · system {sys_s:.2}s · 录音中电脑声音电平 {peak:.3}");
    assert!((mic_s - sys_s).abs() < 0.3, "两条差太多: mic {mic_s} / system {sys_s}");
    let samples: Vec<i16> = list_segments(&sys).iter().flat_map(|p| read_wav(p).1).collect();
    let rate = read_wav(&list_segments(&sys)[0]).0.sample_rate as usize;
    let first_loud = samples.iter().position(|s| s.unsigned_abs() > 1000).expect("电脑声音那条全是静音");
    println!("电脑声音第一次出声在 {:.2}s", first_loud as f64 / rate as f64);
    // 1.5 秒之后才开始放: 前面那段是补的静音, 不能被吃掉 (吃掉了两条就对不上)
    assert!(first_loud as f64 / rate as f64 > 1.2, "前面没补静音");
}
