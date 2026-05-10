//! 本地 Piper TTS — BL-VOICE2 (5/10 鸿波 "这么好玩的东西没理由不现在做").
//!
//! # 设计 (跟 STT speech.rs 对称)
//!
//!   STT: 员工 🎤 → ffmpeg 录音 → whisper.cpp 转文字 → 100% 本地
//!   TTS: 鲶鱼回复 → piper 合成 .wav → 前端 <audio> 播放 → 100% 本地
//!
//! 数据流:
//!   Companion 收到鲶鱼 reply →
//!     ChatMessage 显示文字 + 小喇叭按钮 →
//!     员工点击 → invoke('tts_synthesize', { text }) →
//!     Rust 启 piper subprocess: echo text | piper -m zh_CN-huayan-medium.onnx --output_file /tmp/<uuid>.wav →
//!     返 wav 路径 → 前端 convertFileSrc → <audio src=...>.play()
//!
//! # 为啥 Piper (鸿波 5/10 提议)
//!
//!   - 100% 本地 CPU 跑, 数据不出员工电脑 (跟央企"敏感对话不上云"对齐)
//!   - 无 API key, 无授权费 (采购友好)
//!   - 中文 zh_CN 多档 (huayan 女声 / bizhao 男声), low/medium/high 三 quality
//!   - 模型 ~30MB (medium), ~80MB (high), 比 whisper-large-v3 (3GB) 小一个量级
//!   - 跟 whisper.cpp 同模板: subprocess + ~/.catfish/<...>-models/ 缓存
//!
//! # 依赖 (员工首次部署)
//!
//!   - mac: brew install piper-tts  或 download piper.tar.gz from GitHub release
//!   - win: 内置 piper.exe 到 .exe bundle (BL-WIN9 同款 binary 探测)
//!   - 模型: ~/.catfish/piper-voices/zh_CN-huayan-medium.onnx + .json
//!     首次启动检测到没装 → log warn + 返 Err 引导员工下载 (跟 whisper 同款)
//!
//! # 配置 (~/.catfish/companion.yaml)
//!
//!   tts:
//!     enabled: true
//!     voice: zh_CN-huayan-medium     # 文件名 (不含 .onnx 扩展)
//!     volume: 1.0                    # 0.0-1.0, 留扩展
//!
//! # 未发声 fallback
//!
//!   piper 二进制找不到 → 返清楚错误 + 装载指引 (前端按钮显灰 + tooltip)
//!   模型文件没下 → 返清楚错误 + 下载命令
//!   text 太长 (>5000 字) → 截到 5000 字, log warn (避免合成几分钟卡死)

#[cfg(target_os = "macos")]
use std::path::PathBuf;
use tauri::Window;

const MAX_TTS_TEXT_LEN: usize = 5000;
// 默认中文女声 medium — 鸿波 5/10 踩坑确认: HuggingFace zh_CN 系列 quality
// 上限就是 medium, 没有 high (英文 voice 才有完整四档). 不要改 high.
const DEFAULT_VOICE: &str = "zh_CN-huayan-medium";

/// 探测 piper 二进制. 跟 speech.rs find_executable 同模板.
/// macOS GUI app 启动时 PATH 不含 brew / pipx user bin, 必须显式探.
///
/// piper 装法 (5/10 鸿波 "brew install piper-tts" formula 不存在踩坑后确认):
///   1. brew install python3 + pip install piper-tts
///      → /opt/homebrew/bin/piper (Apple Silicon) 或 /usr/local/bin/piper (Intel)
///   2. pipx install piper-tts (推荐, 隔离 Python deps)
///      → ~/.local/bin/piper
///   3. GitHub release tarball
///      → 解压到任意位置, 设 CATFISH_PIPER env 指
#[cfg(target_os = "macos")]
fn find_piper_executable() -> Option<PathBuf> {
    // 1. env override (员工自己装别处)
    if let Ok(custom) = std::env::var("CATFISH_PIPER") {
        let p = PathBuf::from(custom);
        if p.exists() {
            return Some(p);
        }
    }

    // 2. 常见路径 (按优先级 — catfish 约定路径放最前, 跟 whisper-models 同目录)
    let mut candidates: Vec<PathBuf> = vec![];
    if let Ok(home) = std::env::var("HOME") {
        // ★ BL-VOICE2 fix2 (5/10): 推荐路径 ~/.catfish/piper-venv/bin/piper
        // 解决 PEP 668 (Python 3.12+ + homebrew 拦 pip 直装) + 代理问题 (走
        // venv 不需要 brew install 任何东西). 鸿波 mac py3.14 + 7890 代理没起来
        // 时, brew install pipx 失败 + pip3 install 被 PEP 668 拦, venv 是
        // 唯一可行路径.
        candidates.push(
            PathBuf::from(&home)
                .join(".catfish").join("piper-venv").join("bin").join("piper"),
        );
        // pipx / pip --user 默认路径 (代理恢复后可用)
        candidates.push(PathBuf::from(&home).join(".local").join("bin").join("piper"));
        candidates.push(PathBuf::from(&home).join(".local").join("bin").join("piper-tts"));
        // pip3 --user 在 mac py3.14 下装到这 (系统 Python framework 路径)
        candidates.push(
            PathBuf::from(&home)
                .join("Library").join("Python").join("3.14").join("bin").join("piper"),
        );
        candidates.push(
            PathBuf::from(&home)
                .join("Library").join("Python").join("3.13").join("bin").join("piper"),
        );
        candidates.push(
            PathBuf::from(&home)
                .join("Library").join("Python").join("3.12").join("bin").join("piper"),
        );
    }
    candidates.extend([
        PathBuf::from("/opt/homebrew/bin/piper"),       // brew Python pip → Apple Silicon
        PathBuf::from("/usr/local/bin/piper"),          // brew Python pip → Intel Mac
        PathBuf::from("/opt/local/bin/piper"),          // MacPorts
        PathBuf::from("/opt/homebrew/bin/piper-tts"),   // 别名
        PathBuf::from("/usr/local/bin/piper-tts"),
    ]);
    for p in &candidates {
        if p.exists() {
            return Some(p.clone());
        }
    }

    // 3. PATH 兜底
    if let Ok(path_env) = std::env::var("PATH") {
        for dir in path_env.split(':') {
            for name in ["piper", "piper-tts"] {
                let p = PathBuf::from(dir).join(name);
                if p.exists() {
                    return Some(p);
                }
            }
        }
    }

    None
}

#[cfg(target_os = "macos")]
fn voice_dir() -> Result<PathBuf, String> {
    let home = std::env::var("HOME").map_err(|_| "HOME env 未设".to_string())?;
    Ok(PathBuf::from(home).join(".catfish").join("piper-voices"))
}

/// BL-VOICE2 fix4 (5/10): 从 ~/.catfish/companion.yaml 读 tts.voice 字段.
/// 跟 endpoints.rs 同模板, 但独立 yaml 段不耦合.
///
/// 优先级 voice_id (高→低):
///   1. invoke 参数 voice (前端显式传, 给"试听别的 voice" 留)
///   2. env CATFISH_PIPER_VOICE (dev / 临时测试)
///   3. yaml tts.voice (持久配置, 客户改完重启 Companion 即生效)
///   4. DEFAULT_VOICE (zh_CN-huayan-medium)
///
/// 失败 silent — yaml 不存在 / 解析错都返 None, 走下层 fallback.
#[cfg(target_os = "macos")]
fn read_yaml_voice() -> Option<String> {
    use serde::Deserialize;

    #[derive(Deserialize)]
    struct CompanionYaml {
        tts: Option<TtsSection>,
    }
    #[derive(Deserialize)]
    struct TtsSection {
        voice: Option<String>,
    }

    let home = std::env::var("HOME").ok()?;
    let path = PathBuf::from(home).join(".catfish").join("companion.yaml");
    if !path.exists() {
        return None;
    }
    let content = std::fs::read_to_string(&path).ok()?;
    let parsed: CompanionYaml = serde_yaml::from_str(&content).ok()?;
    let voice = parsed.tts?.voice?;
    if voice.trim().is_empty() {
        return None;
    }
    log::info!("BL-VOICE2 fix4: tts.voice 走 yaml = {}", voice);
    Some(voice)
}

/// 找指定 voice 的 .onnx 模型文件. 不存在 / size 异常返详细引导.
///
/// BL-VOICE2 fix5 (5/10): 加 size 校验防 HuggingFace LFS pointer 坑. 鸿波下
/// high voice 时 curl 拿到 15 字节 pointer text (`version https://git-lfs.../`),
/// piper 打开 .onnx.json 报 JSONDecodeError. 真文件 .onnx ~30-80MB / .onnx.json ~5KB,
/// 任一 < 1KB 直接判定为 LFS pointer 假文件, 让员工重下而不是看 piper 一脸懵.
#[cfg(target_os = "macos")]
fn voice_model_path(voice: &str) -> Result<PathBuf, String> {
    // env override 直接给完整路径
    if let Ok(custom) = std::env::var("CATFISH_PIPER_MODEL") {
        return Ok(PathBuf::from(custom));
    }

    let dir = voice_dir()?;
    let onnx = dir.join(format!("{voice}.onnx"));
    let json = dir.join(format!("{voice}.onnx.json"));

    if onnx.exists() && json.exists() {
        // BL-VOICE2 fix5: size 健全性检查
        let onnx_size = std::fs::metadata(&onnx).map(|m| m.len()).unwrap_or(0);
        let json_size = std::fs::metadata(&json).map(|m| m.len()).unwrap_or(0);
        // .onnx 真模型最少 5MB (x_low ~5MB), pointer 一般 < 200 字节
        // .onnx.json 真配置 1-10KB, pointer 一般 < 200 字节
        if onnx_size < 1_000_000 || json_size < 500 {
            return Err(format!(
                "voice 模型文件 size 异常 (像 HuggingFace LFS pointer 不是真模型):\n  \
                 {} = {} 字节 (期望 >= 5MB)\n  \
                 {} = {} 字节 (期望 >= 500)\n\n\
                 删了重下:\n  \
                 cd {}\n  \
                 rm {voice}.onnx {voice}.onnx.json\n  \
                 curl -fL -O https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/{voice}.onnx\n  \
                 curl -fL -O https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/{voice}.onnx.json\n\n\
                 注: -f flag 让 curl HTTP 错误时 fail 而不是把错误页存成文件.\n\
                 注: 不是所有 voice 都有 high quality 档, huayan 可能只到 medium.\n\
                 看 https://huggingface.co/rhasspy/piper-voices/tree/main/zh/zh_CN 确认.",
                onnx.display(), onnx_size,
                json.display(), json_size,
                dir.display(),
            ));
        }
        return Ok(onnx);
    }

    Err(format!(
        "piper voice 模型未下载: {}/\n\
         缺 {}.onnx 或 {}.onnx.json\n\n\
         首次部署下载 (中文女声 medium, ~30MB):\n  \
         mkdir -p {}\n  \
         curl -L -o {}.onnx \\\n    \
         https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/{}.onnx\n  \
         curl -L -o {}.onnx.json \\\n    \
         https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/{}.onnx.json\n\n\
         其他 voice 选项: zh_CN-huayan-{{x_low,low,medium}} / zh_CN-bizhao-medium (男声)",
        dir.display(),
        voice, voice,
        dir.display(),
        onnx.display(), voice,
        onnx.display(), voice,
    ))
}

/// 合成 TTS 音频. 返 wav 文件绝对路径 (临时文件, 前端播放完不需主动删 — 系统重启会清).
///
/// 参数:
///   text: 待合成文本 (>5000 字会截断)
///   voice: 可选, 默认 yaml.tts.voice 或 "zh_CN-huayan-medium"
#[cfg(target_os = "macos")]
#[tauri::command]
pub async fn tts_synthesize(
    _window: Window,
    text: String,
    voice: Option<String>,
) -> Result<String, String> {
    use std::io::Write;

    if text.trim().is_empty() {
        return Err("text 为空".to_string());
    }

    // 截断: piper 合成长度跟字数线性, 5000 字 ~ 几分钟音频, 防卡死
    let text = if text.chars().count() > MAX_TTS_TEXT_LEN {
        log::warn!(
            "tts_synthesize: text {} 字超过 {} 截断",
            text.chars().count(),
            MAX_TTS_TEXT_LEN
        );
        text.chars().take(MAX_TTS_TEXT_LEN).collect::<String>()
    } else {
        text
    };

    // BL-VOICE2 fix4 (5/10): voice 优先级 invoke 参数 > env > yaml > 默认
    let voice_id = voice
        .or_else(|| std::env::var("CATFISH_PIPER_VOICE").ok())
        .or_else(read_yaml_voice)
        .unwrap_or_else(|| DEFAULT_VOICE.to_string());

    let piper_bin = find_piper_executable().ok_or_else(|| {
        "piper 二进制找不到. 一键装 (推荐, 走 venv 不依赖代理 / 不动系统 Python):\n  \
         bash edge/companion-app/scripts/install-piper-tts.sh\n\n\
         或手动:\n  \
         python3 -m venv ~/.catfish/piper-venv\n  \
         ~/.catfish/piper-venv/bin/pip install piper-tts\n\n\
         安装别处时, 设 env CATFISH_PIPER=/path/to/piper 重启 Companion.\n\
         注: brew install piper-tts formula 不存在 (5/10 鸿波踩过), 别试.".to_string()
    })?;

    let model = voice_model_path(&voice_id)?;

    // 输出到临时 wav. 用纳秒 + pid 保证多次合成不撞名 (异步并发也安全).
    let uuid = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let pid = std::process::id();
    let wav_path = std::env::temp_dir().join(format!("catfish-tts-{pid}-{uuid}.wav"));

    // text 字数提前算好, 避免 move 进闭包后外面没法再 borrow (BL-VOICE2 fix3).
    let text_len = text.chars().count();

    log::info!(
        "tts_synthesize: piper={} model={} → {}",
        piper_bin.display(),
        model.display(),
        wav_path.display()
    );

    // piper 从 stdin 读 text. spawn + 写 stdin + wait_with_output.
    // 在 spawn_blocking 里跑避免阻塞 tokio runtime (subprocess 是 sync API).
    let wav_path_for_thread = wav_path.clone();
    let result = tokio::task::spawn_blocking(move || -> Result<(), String> {
        // 用短选项 -m / -f, piper 1.x (rhasspy) 和 piper1-gpl (OHF-Voice 2025+) 都兼容.
        // 长选项 --output_file (下划线) vs --output-file (横杠) 在不同版本不一致, 短选项稳.
        //
        // BL-VOICE2 fix3 (5/10): 加 --sentence-silence + --length-scale 改善断句听感.
        // 鸿波反馈"断句不太合理" → 默认 sentence-silence=0.2 中文听起来偏快, 加到 0.4
        // (句间换气更明显). length-scale=1.05 整体语速放慢 5% (原速 1.0 偏快).
        // (CLI 参数都是长选项, piper 1.x + piper1-gpl 都接受这俩)
        let mut child = std::process::Command::new(&piper_bin)
            .args([
                "-m",
                model.to_str().unwrap(),
                "-f",
                wav_path_for_thread.to_str().unwrap(),
                "--sentence-silence",
                "0.4",
                "--length-scale",
                "1.05",
            ])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| format!("piper spawn 失败: {e}"))?;

        // 喂 text 到 stdin
        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(text.as_bytes())
                .map_err(|e| format!("piper stdin 写入失败: {e}"))?;
            // drop stdin → piper 看到 EOF 开始合成
        }

        let output = child
            .wait_with_output()
            .map_err(|e| format!("piper wait 失败: {e}"))?;

        if !output.status.success() {
            return Err(format!(
                "piper 合成失败 (exit={:?}): {}",
                output.status.code(),
                String::from_utf8_lossy(&output.stderr)
            ));
        }

        Ok(())
    })
    .await
    .map_err(|e| format!("tokio spawn_blocking join 失败: {e}"))?;

    result?;

    // 检查 wav 真生成了
    let size = std::fs::metadata(&wav_path)
        .map(|m| m.len())
        .map_err(|e| format!("wav 读 metadata 失败: {e}"))?;
    if size < 100 {
        let _ = std::fs::remove_file(&wav_path);
        return Err(format!("piper 生成的 wav 太小 ({} bytes), 可能合成失败", size));
    }

    log::info!(
        "tts_synthesize: ✅ {} 字 → {} ({} KB)",
        text_len,
        wav_path.display(),
        size / 1024
    );
    Ok(wav_path.to_string_lossy().to_string())
}

/// 检测 piper / 默认 voice 模型是否就绪. 给前端 onboarding 显状态用.
/// BL-VOICE2 fix4 (5/10): 跟 tts_synthesize 的 voice 解析逻辑一致, 走 env > yaml > 默认.
#[cfg(target_os = "macos")]
#[tauri::command]
pub fn tts_status() -> Result<TtsStatus, String> {
    let piper_bin = find_piper_executable();
    let voice_id = std::env::var("CATFISH_PIPER_VOICE")
        .ok()
        .or_else(read_yaml_voice)
        .unwrap_or_else(|| DEFAULT_VOICE.to_string());
    let model_ok = voice_model_path(&voice_id).is_ok();

    Ok(TtsStatus {
        piper_installed: piper_bin.is_some(),
        piper_path: piper_bin.map(|p| p.to_string_lossy().to_string()),
        default_voice: voice_id,
        default_voice_ready: model_ok,
        voice_dir: voice_dir().map(|p| p.to_string_lossy().to_string()).ok(),
    })
}

#[derive(Debug, serde::Serialize)]
pub struct TtsStatus {
    pub piper_installed: bool,
    pub piper_path: Option<String>,
    pub default_voice: String,
    pub default_voice_ready: bool,
    pub voice_dir: Option<String>,
}

// ===== 非 macOS stub =====
//
// BL-WIN1 后续: Win 端打包 piper.exe 到 bundle resource, find_executable 改读
// resource path. Linux 同模板. 现在先 mac, 跟 STT 节奏一致.

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub async fn tts_synthesize(
    _window: Window,
    _text: String,
    _voice: Option<String>,
) -> Result<String, String> {
    Err("Piper TTS Phase 2 加 Win/Linux, 现在 macOS only.".to_string())
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub fn tts_status() -> Result<TtsStatus, String> {
    Ok(TtsStatus {
        piper_installed: false,
        piper_path: None,
        default_voice: DEFAULT_VOICE.to_string(),
        default_voice_ready: false,
        voice_dir: None,
    })
}

#[cfg(not(target_os = "macos"))]
#[derive(Debug, serde::Serialize)]
pub struct TtsStatus {
    pub piper_installed: bool,
    pub piper_path: Option<String>,
    pub default_voice: String,
    pub default_voice_ready: bool,
    pub voice_dir: Option<String>,
}
