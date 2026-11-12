//! 会议纪要 / 转写的修正和分享 (11/12, 鸿波「是不是应该有个分享或者邮件; 内容是不是还应该可以修正」)。
//!
//! - 纪要: 改了存回去、存成 Word、放进群聊暂存区 —— 都在 tool-bridge (meeting_minutes_edit.py), 这里注入会议根目录。
//! - 转写: 点一句改错字。原话留在 `orig_text` (改回去时对照); 改过的转写下次「重新生成」纪要就按改后的写。
//! - 常用热词: 改错字时顺手把人名 / 术语记下, 新建会议的热词框自动带上 (~/.catfish/meetings/hotwords.json)。
//! - 听一句 (11/12 鸿波「不能播放录音, 怎么来修正识别内容」): 按转写里那句的起止时间, 从会议录音 (5 分钟一个
//!   audio/seg-NNNN.wav; 线上会议对方的声音在 audio/system/, 跟麦克风那条时间对齐) 截一小段, 在内存里拼成 WAV
//!   以 base64 交给界面, 界面转成 blob 播 —— 不落临时文件、不走 asset 协议 (Mac / Windows 临时目录的放行规则不同,
//!   WebView 还可能吃旧缓存)。一句几秒几百 KB; 最长截 90 秒。录音不出本机。
//!
//! 发邮件 / 发到群都是员工在界面上点了才发, 这里没有直接发送的口子。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::meeting_audio;
use crate::services::meeting_store as store;
use crate::services::tool_bridge_rpc;

const RPC_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_SEGMENT_CHARS: usize = 2000;

async fn rpc(method: &str, id: &str, extra: Value) -> Result<Value, String> {
    let home = store::home()?;
    let mut params = json!({ "meeting_id": id, "catfish_home": home.join(".catfish").display().to_string() });
    if let (Some(p), Some(e)) = (params.as_object_mut(), extra.as_object()) {
        for (k, v) in e {
            p.insert(k.clone(), v.clone());
        }
    }
    tool_bridge_rpc::call_with_timeout(method, params, RPC_TIMEOUT)
        .await
        .map_err(|e| e.split_once("]: ").map(|(_, m)| m.to_string()).unwrap_or(e))
}

/// 改过的纪要存回去: `fields` = 逐项 (摘要 / 决议 / 待办 / 待定问题 / 上次遗留), `markdown` = 按文字模版写的全文。
#[tauri::command]
pub async fn meeting_minutes_save(id: String, fields: Option<Value>, markdown: Option<String>) -> Result<Value, String> {
    rpc("meeting/minutes_save", &id, json!({ "fields": fields, "markdown": markdown })).await
}

/// 存成 Word (会议目录里), 返回 {path, name, size} —— 导出、邮件附件都用它。
#[tauri::command]
pub async fn meeting_minutes_docx(id: String) -> Result<Value, String> {
    rpc("meeting/minutes_docx", &id, json!({})).await
}

/// 纪要文件放进群聊暂存区, 返回 {stage_id, name}; 界面再走群聊的「发送」。
#[tauri::command]
pub async fn meeting_minutes_stage(id: String) -> Result<Value, String> {
    rpc("meeting/minutes_stage", &id, json!({})).await
}

/// 改转写里的一句 (第 index 段)。空字符串 = 改回原话。
#[tauri::command]
pub fn meeting_transcript_edit(id: String, index: usize, text: String) -> Result<Value, String> {
    let root = store::meetings_root(&store::home()?);
    let p = store::transcript_path(&root, &id)?;
    edit_segment(&p, index, &text)
}

fn edit_segment(p: &std::path::Path, index: usize, text: &str) -> Result<Value, String> {
    let raw = std::fs::read_to_string(p).map_err(|_| "这场会议还没有转写结果".to_string())?;
    let mut doc: Value = serde_json::from_str(&raw).map_err(|e| format!("transcript.json 格式不对: {e}"))?;
    let seg = doc
        .get_mut("segments")
        .and_then(|s| s.as_array_mut())
        .and_then(|a| a.get_mut(index))
        .and_then(|s| s.as_object_mut())
        .ok_or("找不到这一句 (转写可能刚重新做过, 刷新一下)")?;
    let current = seg.get("text").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    let orig = seg.get("orig_text").and_then(|v| v.as_str()).map(str::to_string).unwrap_or_else(|| current.clone());
    let new: String = text.trim().chars().take(MAX_SEGMENT_CHARS).collect();
    if new.is_empty() || new == orig {
        seg.insert("text".into(), json!(orig));
        seg.remove("orig_text");
    } else {
        seg.insert("text".into(), json!(new));
        seg.insert("orig_text".into(), json!(orig));
    }
    let out = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?;
    let tmp = p.with_extension("json.tmp");
    std::fs::write(&tmp, out).map_err(|e| format!("保存转写失败: {e}"))?;
    std::fs::rename(&tmp, p).map_err(|e| format!("保存转写失败: {e}"))?;
    Ok(doc["segments"][index].clone())
}

fn hotwords_file() -> Result<std::path::PathBuf, String> {
    Ok(store::meetings_root(&store::home()?).join("hotwords.json"))
}

fn read_hotwords(p: &std::path::Path) -> Vec<String> {
    std::fs::read_to_string(p)
        .ok()
        .and_then(|t| serde_json::from_str::<Vec<String>>(&t).ok())
        .unwrap_or_default()
}

/// 常用热词 (改转写时记下的人名 / 术语), 新建会议时带进热词框。
#[tauri::command]
pub fn meeting_hotwords_saved() -> Result<Vec<String>, String> {
    Ok(read_hotwords(&hotwords_file()?))
}

/// 记下几个常用热词; `remove` 给了就是去掉。返回最新的整份。
#[tauri::command]
pub fn meeting_hotwords_remember(words: Vec<String>, remove: Option<bool>) -> Result<Vec<String>, String> {
    let p = hotwords_file()?;
    update_hotwords(&p, &words, remove.unwrap_or(false))
}

fn update_hotwords(p: &std::path::Path, words: &[String], remove: bool) -> Result<Vec<String>, String> {
    let mut cur = read_hotwords(p);
    let add = store::normalize_hotwords(words);
    if remove {
        cur.retain(|w| !add.contains(w));
    } else {
        // 新记的放前面: 超过上限时先挤掉最早记的
        let mut merged = add.clone();
        merged.extend(cur.into_iter().filter(|w| !add.contains(w)));
        cur = store::normalize_hotwords(&merged);
    }
    if let Some(dir) = p.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("保存热词失败: {e}"))?;
    }
    std::fs::write(p, serde_json::to_string_pretty(&cur).map_err(|e| e.to_string())?)
        .map_err(|e| format!("保存热词失败: {e}"))?;
    Ok(cur)
}

const CLIP_BEFORE: f64 = 0.3;
const CLIP_AFTER: f64 = 0.6;
const CLIP_MAX_SECS: f64 = 90.0;

/// 截一句话的录音: `track` = "system" 截电脑声音那条 (线上会议对方说的), 其余截麦克风那条。返回 WAV 的 base64。
#[tauri::command]
pub fn meeting_audio_clip(id: String, start: f64, end: f64, track: Option<String>) -> Result<String, String> {
    use base64::Engine as _;
    let root = store::meetings_root(&store::home()?);
    let audio = store::audio_dir(&root, &id)?;
    let sys = meeting_audio::system_track_dir(&audio);
    let dir = if track.as_deref() == Some("system") && !meeting_audio::list_segments(&sys).is_empty() { sys } else { audio };
    let mut buf = std::io::Cursor::new(Vec::new());
    cut_clip(&dir, start, end, &mut buf)?;
    Ok(base64::engine::general_purpose::STANDARD.encode(buf.into_inner()))
}

/// 从一条音轨 (按序号排好的若干 WAV 分片) 截 [start - 0.3s, end + 0.6s] 写成单声道 16-bit WAV。
fn cut_clip<W: std::io::Write + std::io::Seek>(dir: &std::path::Path, start: f64, end: f64, out: &mut W) -> Result<(), String> {
    if !(start.is_finite() && end.is_finite()) || end < start {
        return Err("这一句的时间不对".into());
    }
    let from = (start - CLIP_BEFORE).max(0.0);
    let to = (end + CLIP_AFTER).min(from + CLIP_MAX_SECS);
    let segs = meeting_audio::list_segments(dir);
    if segs.is_empty() {
        return Err("这场会议的录音不在本机了 (可能被删了), 听不了".into());
    }
    let mut samples: Vec<i16> = Vec::new();
    let mut out_rate: Option<u32> = None;
    let mut offset = 0.0_f64; // 这个分片开头在整场会议里是第几秒
    for p in segs {
        let mut r = hound::WavReader::open(&p).map_err(|e| format!("读录音失败: {e}"))?;
        let spec = r.spec();
        let rate = spec.sample_rate.max(1) as f64;
        let ch = spec.channels.max(1) as usize;
        let secs = r.duration() as f64 / rate;
        let (seg_from, seg_to) = ((from - offset).max(0.0), (to - offset).min(secs));
        if seg_to > seg_from {
            let rate_now = *out_rate.get_or_insert(spec.sample_rate);
            if rate_now != spec.sample_rate {
                return Err("这场会议的录音分片采样率不一致, 截不了".into());
            }
            let first = (seg_from * rate).round() as u32;
            let frames = ((seg_to - seg_from) * rate).round() as usize;
            r.seek(first).map_err(|e| format!("读录音失败: {e}"))?;
            let mono: Vec<i16> = match spec.sample_format {
                hound::SampleFormat::Int if spec.bits_per_sample <= 16 => {
                    let raw: Vec<i16> = r.samples::<i16>().take(frames * ch).filter_map(Result::ok).collect();
                    raw.chunks(ch).map(|c| (c.iter().map(|&x| x as i32).sum::<i32>() / c.len() as i32) as i16).collect()
                }
                hound::SampleFormat::Int => {
                    let shift = spec.bits_per_sample.saturating_sub(16);
                    let raw: Vec<i32> = r.samples::<i32>().take(frames * ch).filter_map(Result::ok).collect();
                    raw.chunks(ch).map(|c| ((c.iter().map(|&x| x as i64).sum::<i64>() / c.len() as i64) >> shift) as i16).collect()
                }
                hound::SampleFormat::Float => {
                    let raw: Vec<f32> = r.samples::<f32>().take(frames * ch).filter_map(Result::ok).collect();
                    raw.chunks(ch).map(|c| ((c.iter().sum::<f32>() / c.len() as f32).clamp(-1.0, 1.0) * i16::MAX as f32) as i16).collect()
                }
            };
            samples.extend(mono);
        }
        offset += secs;
        if offset >= to {
            break;
        }
    }
    let Some(rate) = out_rate else { return Err("这一句的时间超出了录音长度".into()) };
    let mut w = hound::WavWriter::new(out, hound::WavSpec { channels: 1, sample_rate: rate, bits_per_sample: 16, sample_format: hound::SampleFormat::Int })
        .map_err(|e| format!("截录音失败: {e}"))?;
    for v in samples {
        w.write_sample(v).map_err(|e| format!("截录音失败: {e}"))?;
    }
    w.finalize().map_err(|e| format!("截录音失败: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 改一句留原话_改回去去掉原话() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("transcript.json");
        std::fs::write(&p, r#"{"segments":[{"spk":0,"start":0,"end":1,"text":"西西RC末次"},{"spk":1,"start":1,"end":2,"text":"好"}]}"#).unwrap();
        let s = edit_segment(&p, 0, " CC RC末次 ").unwrap();
        assert_eq!(s["text"], "CC RC末次");
        assert_eq!(s["orig_text"], "西西RC末次");
        // 再改一次: 原话还是最初那句
        let s = edit_segment(&p, 0, "CCRC 末次").unwrap();
        assert_eq!(s["orig_text"], "西西RC末次");
        // 清空 = 改回原话
        let s = edit_segment(&p, 0, "").unwrap();
        assert_eq!(s["text"], "西西RC末次");
        assert!(s.get("orig_text").is_none());
        assert!(edit_segment(&p, 9, "x").is_err());
        let doc: Value = serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(doc["segments"][1]["text"], "好");
    }

    fn tone(path: &std::path::Path, secs: u32, rate: u32, value: i16) {
        let mut w = hound::WavWriter::create(path, hound::WavSpec { channels: 1, sample_rate: rate, bits_per_sample: 16, sample_format: hound::SampleFormat::Int }).unwrap();
        for _ in 0..secs * rate {
            w.write_sample(value).unwrap();
        }
        w.finalize().unwrap();
    }

    #[test]
    fn 截一句_跨分片_前后多留一点() {
        let d = tempfile::tempdir().unwrap();
        tone(&d.path().join("seg-0001.wav"), 5, 1000, 100);
        tone(&d.path().join("seg-0002.wav"), 5, 1000, 200);
        let mut out = std::io::Cursor::new(Vec::new());
        cut_clip(d.path(), 4.0, 6.0, &mut out).unwrap();   // 跨两片: 3.7 .. 6.6 秒
        let mut r = hound::WavReader::new(std::io::Cursor::new(out.into_inner())).unwrap();
        let v: Vec<i16> = r.samples::<i16>().map(|x| x.unwrap()).collect();
        assert_eq!(v.len(), 2900);
        assert_eq!(v[0], 100);
        assert_eq!(*v.last().unwrap(), 200);
        let mut sink = std::io::Cursor::new(Vec::new());
        assert!(cut_clip(d.path(), 20.0, 21.0, &mut sink).is_err());          // 超出录音长度
        assert!(cut_clip(d.path(), 3.0, 1.0, &mut sink).is_err());            // 时间倒了
        assert!(cut_clip(&d.path().join("没有"), 0.0, 1.0, &mut sink).is_err()); // 录音不在了
    }

    #[test]
    fn 常用热词_新的在前_去重_能删() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("meetings").join("hotwords.json");
        assert_eq!(update_hotwords(&p, &["CCRC".into(), "栾总".into()], false).unwrap(), vec!["CCRC", "栾总"]);
        assert_eq!(update_hotwords(&p, &["福富，CCRC".into()], false).unwrap(), vec!["福富", "CCRC", "栾总"]);
        assert_eq!(update_hotwords(&p, &["CCRC".into()], true).unwrap(), vec!["福富", "栾总"]);
        assert_eq!(read_hotwords(&p), vec!["福富", "栾总"]);
    }
}
