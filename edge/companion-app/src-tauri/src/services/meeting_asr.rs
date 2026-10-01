//! 会议组件包的安装 + 调转写脚本 (10/1, docs/MEETING-MINUTES-PLAN.md §3)。
//!
//! ```text
//! ~/.catfish/meeting-asr/
//!     current.json          {version, python, models} —— 唯一的"装好了"判据
//!     venv-<ver>/           独立 venv (不进 hermes venv, 见计划文档)
//!     models-<ver>/{vad,asr,punc,spk}
//! ```
//!
//! 安装 = 解包 → 用 **hermes venv 的同一个基础解释器** 建 venv → pip 离线装
//! (`--no-index`, 只认包里的 wheel) → import 自检 → 模型挪到位 → 原子写 current.json
//! → 删旧版本。任何一步失败都不碰 current.json, 旧版本照常能用。
//!
//! 为什么用 pip 不用 uv: uv 在 App 资源目录里, 要拿 AppHandle 才找得到; pip 离线装
//! 这 88 个 wheel 实测 22 秒, 一次性的事, 不值得多一条依赖链。
//!
//! 转写 = 用这个 venv 跑 edge-runtime 里的 meeting_asr.py, 逐行读它 stdout 的 JSON
//! 事件。全程离线: 代理指到不存在的端口 + ModelScope 缓存指到空目录, 模型只从
//! models-<ver>/ 读 —— 真去联网会立刻失败而不是悄悄下几个 G。

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::Stdio;

use serde::{Deserialize, Serialize};

pub const COMPONENT_NAME: &str = "meeting-asr";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Installed {
    pub version: String,
    pub python: PathBuf,
    pub models: PathBuf,
}

#[derive(Debug, Deserialize)]
struct PackJson {
    version: String,
    requirements: Vec<String>,
}

pub fn root(home: &Path) -> PathBuf {
    home.join(".catfish").join("meeting-asr")
}

fn venv_python(venv: &Path) -> PathBuf {
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

/// current.json 指的东西都还在才算装好 (用户手动删了目录就当没装)。
pub fn installed(home: &Path) -> Option<Installed> {
    let text = std::fs::read_to_string(root(home).join("current.json")).ok()?;
    let i: Installed = serde_json::from_str(&text).ok()?;
    (i.python.is_file() && i.models.join("asr").is_dir()).then_some(i)
}

/// 从 venv 的 pyvenv.cfg 读出它的基础解释器 (`home = ...` 那一行)。
pub fn base_python_of(venv: &Path) -> Result<PathBuf, String> {
    let cfg = venv.join("pyvenv.cfg");
    let text = std::fs::read_to_string(&cfg).map_err(|e| format!("读 {} 失败: {e}", cfg.display()))?;
    let home = text
        .lines()
        .find_map(|l| {
            let (k, v) = l.split_once('=')?;
            (k.trim() == "home").then(|| PathBuf::from(v.trim()))
        })
        .ok_or_else(|| format!("{} 里没有 home", cfg.display()))?;
    let names: &[&str] = if cfg!(windows) { &["python.exe"] } else { &["python3.11", "python3", "python"] };
    names
        .iter()
        .map(|n| home.join(n))
        .find(|p| p.is_file())
        .ok_or_else(|| format!("{} 下找不到 python", home.display()))
}

fn run(cmd: &mut std::process::Command, what: &str) -> Result<(), String> {
    let out = cmd.output().map_err(|e| format!("{what} 起不来: {e}"))?;
    if out.status.success() {
        return Ok(());
    }
    let err = String::from_utf8_lossy(&out.stderr);
    let tail: String = err.lines().rev().take(8).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join("\n");
    Err(format!("{what} 失败 ({}):\n{tail}", out.status))
}

/// 装一个已校验过的组件包。`on_step` 报给界面看 ("解包" / "建环境" / ...)。
pub fn install(pack: &Path, home: &Path, base_python: &Path, mut on_step: impl FnMut(&'static str)) -> Result<Installed, String> {
    let root = root(home);
    std::fs::create_dir_all(&root).map_err(|e| format!("建 {} 失败: {e}", root.display()))?;
    let staging = root.join(format!(".staging-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&staging);
    std::fs::create_dir_all(&staging).map_err(|e| format!("建临时目录失败: {e}"))?;
    let result = install_inner(pack, &root, &staging, base_python, &mut on_step);
    let _ = std::fs::remove_dir_all(&staging);
    result
}

fn install_inner(
    pack: &Path,
    root: &Path,
    staging: &Path,
    base_python: &Path,
    on_step: &mut impl FnMut(&'static str),
) -> Result<Installed, String> {
    on_step("unpacking");
    run(
        crate::services::process::background_command("tar").arg("-xzf").arg(pack).arg("-C").arg(staging),
        "解包",
    )?;
    let pj: PackJson = serde_json::from_str(
        &std::fs::read_to_string(staging.join("pack.json")).map_err(|e| format!("包里没有 pack.json: {e}"))?,
    )
    .map_err(|e| format!("pack.json 格式不对: {e}"))?;
    if !pj.version.chars().all(|c| c.is_ascii_digit() || c == '.') {
        return Err(format!("包版本号不合法: {}", pj.version));
    }

    on_step("creating_venv");
    let venv = root.join(format!("venv-{}", pj.version));
    let _ = std::fs::remove_dir_all(&venv);
    run(crate::services::process::python_command(base_python).arg("-m").arg("venv").arg(&venv), "建 Python 环境")?;
    let py = venv_python(&venv);

    on_step("installing");
    run(
        crate::services::process::python_command(&py)
            .args(["-m", "pip", "install", "--no-index", "--disable-pip-version-check", "-q", "--find-links"])
            .arg(staging.join("wheels"))
            .args(&pj.requirements)
            .env("PIP_NO_INPUT", "1"),
        "离线安装依赖",
    )?;

    on_step("checking");
    run(
        crate::services::process::python_command(&py).args(["-c", "import funasr, torch, torchaudio, soundfile"]),
        "依赖自检",
    )?;

    on_step("finishing");
    let models = root.join(format!("models-{}", pj.version));
    let _ = std::fs::remove_dir_all(&models);
    std::fs::rename(staging.join("models"), &models).map_err(|e| format!("放模型失败: {e}"))?;
    let inst = Installed { version: pj.version.clone(), python: py, models };
    let tmp = root.join(".current.json.tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(&inst).map_err(|e| e.to_string())?)
        .map_err(|e| format!("写 current.json 失败: {e}"))?;
    std::fs::rename(&tmp, root.join("current.json")).map_err(|e| format!("写 current.json 失败: {e}"))?;

    // 旧版本 (venv-x / models-x) 清掉; 失败不要紧, 下次再清
    for e in std::fs::read_dir(root).into_iter().flatten().flatten() {
        let name = e.file_name().to_string_lossy().to_string();
        let keep = name == format!("venv-{}", pj.version) || name == format!("models-{}", pj.version);
        if (name.starts_with("venv-") || name.starts_with("models-")) && !keep {
            let _ = std::fs::remove_dir_all(e.path());
        }
    }
    Ok(inst)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum AsrEvent {
    Phase {
        phase: String,
        #[serde(default)]
        duration_secs: Option<f64>,
    },
    Done {
        out: String,
        segments: usize,
        speakers: usize,
        duration_secs: f64,
    },
    Error {
        message: String,
    },
}

/// 脚本 stdout 的一行 → 事件; 不是事件的行 (理论上没有, 脚本已经把库的 print 挪到 stderr) 忽略。
pub fn parse_event(line: &str) -> Option<AsrEvent> {
    serde_json::from_str(line.trim()).ok()
}

pub enum AudioInput<'a> {
    /// 会议录音目录 (seg-*.wav, 原生采样率)
    Dir(&'a Path),
    /// 单个 16k wav (语音输入 / 上传的音频文件, 先 decode_to_wav16k)。
    /// 目前只有 macOS 的语音输入用 (speech.rs), Windows 上没人构造。
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    File(&'a Path),
}

pub struct TranscribeArgs<'a> {
    pub script: &'a Path,
    pub audio: AudioInput<'a>,
    pub out: &'a Path,
    /// 0 = 不分说话人 (语音输入 / 上传音频)
    pub speakers: u32,
    pub hotwords: &'a [String],
}

/// 任意音频 / 视频文件 → 16k 单声道 wav。macOS 用系统自带的 afconvert (CoreAudio):
/// mp3 / m4a / aac / flac / opus / wav / aiff 以及 mp4 / mov 的音轨都实测能解 ——
/// 不再要员工 brew 装 ffmpeg (那个从来不在安装包里, 客户机上没有)。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn decode_to_wav16k(input: &Path, out: &Path) -> Result<(), String> {
    if !cfg!(target_os = "macos") {
        return Err("音频解码目前只支持 macOS".into());
    }
    run(
        crate::services::process::background_command("/usr/bin/afconvert")
            .args(["-f", "WAVE", "-d", "LEI16@16000", "-c", "1"])
            .arg(input)
            .arg(out),
        "音频解码 (这个格式可能不支持, 换成 mp3 / m4a / wav 再试)",
    )
}

/// 转写结果里的整段文字。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn read_text(out: &Path) -> Result<String, String> {
    let v: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(out).map_err(|e| format!("读转写结果失败: {e}"))?,
    )
    .map_err(|e| format!("转写结果格式不对: {e}"))?;
    Ok(v.get("text").and_then(|t| t.as_str()).unwrap_or("").trim().to_string())
}

/// 阻塞跑完一次转写 (在 spawn_blocking 里调)。返回 Done 事件。
pub fn transcribe(inst: &Installed, a: &TranscribeArgs, empty_cache: &Path, mut on_event: impl FnMut(&AsrEvent)) -> Result<AsrEvent, String> {
    std::fs::create_dir_all(empty_cache).map_err(|e| e.to_string())?;
    // 降低优先级: 会后转写占满 CPU 好几分钟, 不该让员工手上的活卡顿
    let mut cmd = if cfg!(target_os = "macos") {
        let mut c = crate::services::process::python_command("/usr/bin/nice");
        c.args(["-n", "10"]).arg(&inst.python);
        c
    } else {
        crate::services::process::python_command(&inst.python)
    };
    cmd.arg(a.script);
    match a.audio {
        AudioInput::Dir(d) => cmd.arg("--audio-dir").arg(d),
        AudioInput::File(f) => cmd.arg("--audio-file").arg(f),
    };
    cmd.arg("--models").arg(&inst.models)
        .arg("--speakers").arg(a.speakers.to_string())
        .arg("--hotwords").arg(a.hotwords.join(" "))
        .arg("--out").arg(a.out)
        // 全程离线: 真要联网会立刻失败, 不会悄悄下载几个 G
        .env("HTTPS_PROXY", "http://127.0.0.1:9")
        .env("HTTP_PROXY", "http://127.0.0.1:9")
        .env("NO_PROXY", "")
        .env("MODELSCOPE_CACHE", empty_cache)
        .env("HF_HUB_OFFLINE", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| format!("起转写进程失败: {e}"))?;
    let stderr = child.stderr.take();
    // stderr 另起线程读, 防止管道写满把子进程卡住; 留最后几十行做报错上下文
    let err_thread = std::thread::spawn(move || {
        let mut tail: Vec<String> = Vec::new();
        if let Some(s) = stderr {
            for line in BufReader::new(s).lines().map_while(Result::ok) {
                tail.push(line);
                if tail.len() > 40 {
                    tail.remove(0);
                }
            }
        }
        tail
    });
    let mut last: Option<AsrEvent> = None;
    if let Some(out) = child.stdout.take() {
        for line in BufReader::new(out).lines().map_while(Result::ok) {
            if let Some(ev) = parse_event(&line) {
                on_event(&ev);
                last = Some(ev);
            }
        }
    }
    let status = child.wait().map_err(|e| format!("等转写进程失败: {e}"))?;
    let stderr_tail = err_thread.join().unwrap_or_default();
    match last {
        Some(ev @ AsrEvent::Done { .. }) if status.success() => Ok(ev),
        Some(AsrEvent::Error { message }) => Err(message),
        _ => Err(format!(
            "转写进程异常退出 ({status}):\n{}",
            stderr_tail.iter().rev().take(8).rev().cloned().collect::<Vec<_>>().join("\n")
        )),
    }
}

#[cfg(test)]
#[path = "meeting_asr_tests.rs"]
mod tests;
