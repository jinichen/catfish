//! 会中实时转写进程 (11/15, docs/MEETING-MINUTES-PLAN.md §8 会中小鲶)。
//!
//! 开始录音时起 `meeting_asr_live.py` (会议组件包的 venv, 同一套模型, 不分说话人), 它每 10 秒把新录到的
//! 识别一遍, 追加到会议目录的 `live_transcript.jsonl`, 状态写 `live_state.json`。结束录音时关掉它的 stdin
//! = 让它把剩下的认完再退; 等不到 (机器太慢) 就在后台线程里再等一会儿, 最后杀掉 —— 不卡结束录音。
//!
//! 实时转写失败不影响录音: 起不来只记日志, 界面上看 live_state.json 的状态。会后照常整场转写 (分说话人)。
//!
//! Linux 没有录音 (跟 meeting_audio 一样只有测试在用), 不报「没用到」。
#![cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::services::meeting_asr::Installed;

/// 结束录音后等它收尾多久 (最后一口最多 90 秒音频, 慢机器上十几秒)。
const FINISH_WAIT: Duration = Duration::from_secs(90);

static LIVE: Mutex<Option<(Child, PathBuf)>> = Mutex::new(None);

/// 起实时转写。已经有一个在跑 (上一场没收完) 就先让它收尾。
pub fn start(inst: &Installed, script: &Path, audio: &Path, meeting_dir: &Path, hotwords: &[String], empty_cache: &Path) -> Result<(), String> {
    stop();
    std::fs::create_dir_all(empty_cache).map_err(|e| e.to_string())?;
    let mut cmd = if cfg!(target_os = "macos") {
        let mut c = crate::services::process::python_command("/usr/bin/nice");
        c.args(["-n", "10"]).arg(&inst.python);
        c
    } else {
        crate::services::process::python_command(&inst.python)
    };
    cmd.arg(script)
        .arg("--audio-dir").arg(audio)
        .arg("--models").arg(&inst.models)
        .arg("--out-dir").arg(meeting_dir)
        .arg("--hotwords").arg(hotwords.join(" "))
        .env("PYTHONUTF8", "1")
        // 全程离线, 跟会后转写一样
        .env("HTTPS_PROXY", "http://127.0.0.1:9")
        .env("HTTP_PROXY", "http://127.0.0.1:9")
        .env("NO_PROXY", "")
        .env("MODELSCOPE_CACHE", empty_cache)
        .env("HF_HUB_OFFLINE", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| format!("起实时转写失败: {e}"))?;
    let id = meeting_dir.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    // stdout 是事件 (记日志), stderr 是 FunASR 的输出 (只留最后几行) —— 都得读走, 不然管道写满会卡住它
    if let Some(out) = child.stdout.take() {
        let id = id.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(out).lines().map_while(Result::ok) {
                log::info!("[meeting-live] {id} {line}");
            }
        });
    }
    if let Some(err) = child.stderr.take() {
        std::thread::spawn(move || {
            let mut tail: Vec<String> = Vec::new();
            for line in BufReader::new(err).lines().map_while(Result::ok) {
                tail.push(line);
                if tail.len() > 20 {
                    tail.remove(0);
                }
            }
            if tail.iter().any(|l| l.contains("Traceback") || l.contains("Error")) {
                log::warn!("[meeting-live] {id} stderr 末尾:\n{}", tail.join("\n"));
            }
        });
    }
    log::info!("[meeting-live] {} 起了实时转写 (pid {})", meeting_dir.display(), child.id());
    *LIVE.lock().unwrap_or_else(|e| e.into_inner()) = Some((child, meeting_dir.to_path_buf()));
    Ok(())
}

/// 结束: 关 stdin 让它收尾, 后台等它退; 超时杀掉。没在跑就什么都不做。
pub fn stop() {
    let taken = LIVE.lock().unwrap_or_else(|e| e.into_inner()).take();
    let Some((mut child, dir)) = taken else { return };
    drop(child.stdin.take());
    std::thread::spawn(move || {
        let started = Instant::now();
        loop {
            match child.try_wait() {
                Ok(Some(st)) => {
                    log::info!("[meeting-live] {} 收尾完成 ({st}, {:.0}s)", dir.display(), started.elapsed().as_secs_f64());
                    return;
                }
                Ok(None) if started.elapsed() < FINISH_WAIT => std::thread::sleep(Duration::from_millis(500)),
                _ => {
                    log::warn!("[meeting-live] {} 收尾超时, 结束进程", dir.display());
                    let _ = child.kill();
                    let _ = child.wait();
                    mark_stopped(&dir);
                    return;
                }
            }
        }
    });
}

/// 被杀掉的: 状态文件还停在 running, 改成 done (界面不再显示「转写中」)。
fn mark_stopped(dir: &Path) {
    let p = dir.join("live_state.json");
    let Ok(text) = std::fs::read_to_string(&p) else { return };
    if let Ok(mut v) = serde_json::from_str::<serde_json::Value>(&text) {
        v["status"] = "done".into();
        v["partial"] = "".into();
        let _ = std::fs::write(&p, v.to_string());
    }
}

/// 读会中文字: 第 `since` 行起 (前端增量取)。最后一行可能正写到一半, 认不出就不算。
pub fn read_lines(dir: &Path, since: usize) -> (Vec<serde_json::Value>, usize) {
    let Ok(text) = std::fs::read_to_string(dir.join("live_transcript.jsonl")) else { return (Vec::new(), 0) };
    let all: Vec<serde_json::Value> = text.lines().filter_map(|l| serde_json::from_str(l).ok()).collect();
    let total = all.len();
    (all.into_iter().skip(since).collect(), total)
}

pub fn read_state(dir: &Path) -> serde_json::Value {
    std::fs::read_to_string(dir.join("live_state.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(serde_json::Value::Null)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_lines_skips_half_written_and_pages() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(
            d.path().join("live_transcript.jsonl"),
            "{\"start\":0,\"end\":1,\"text\":\"一\",\"track\":\"mic\"}\n{\"start\":1,\"end\":2,\"text\":\"二\",\"track\":\"mic\"}\n{\"start\":2,\"en",
        )
        .unwrap();
        let (rows, total) = read_lines(d.path(), 0);
        assert_eq!((rows.len(), total), (2, 2));
        let (rows, total) = read_lines(d.path(), 1);
        assert_eq!((rows[0]["text"].as_str(), total), (Some("二"), 2));
        assert_eq!(read_lines(&d.path().join("nope"), 0).1, 0);
        assert!(read_state(d.path()).is_null());
    }

    #[test]
    fn mark_stopped_clears_running_state() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("live_state.json"), r#"{"status":"running","partial":"半句"}"#).unwrap();
        mark_stopped(d.path());
        let v = read_state(d.path());
        assert_eq!((v["status"].as_str(), v["partial"].as_str()), (Some("done"), Some("")));
    }
}
