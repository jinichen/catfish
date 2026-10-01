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
    let st = rec::start(dir.path(), None, 300, 3600).unwrap();
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
