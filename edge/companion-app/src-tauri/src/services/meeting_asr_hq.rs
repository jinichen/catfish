//! 高精度转写组件 meeting-asr-hq (11/17, docs/MEETING-MINUTES-PLAN.md §12)。
//!
//! 鸿波 11/17「现在的转写准确性不够」→ 盲测 Qwen3-ASR 23 : Paraformer 11。但它重 (模型 4.4GB、运行占 ~4GB 内存、
//! 两小时的会要几十分钟), 所以不进默认的会议组件 (meeting-asr), 做成员工自己选的组件:
//!   · 包: pack.json + wheels/ + models/asr-qwen3/ (scripts/build-meeting-asr-hq-pack.sh), 不带 Python ——
//!     用已装的默认组件那个 Python 3.11 另建一个 venv (~/.catfish/meeting-asr-hq/venv-<版本>), 两边依赖互不影响
//!     (Qwen3-ASR 要 transformers 4.57.6, 默认组件里是 5.x);
//!   · 跑: 已经转写过的会议点「高精度重新转写」→ meeting_asr_qwen.py 读那场的 transcript.json (句子 / 说话人照用),
//!     只换文字。单独一个进程, 默认组件那个早就退了, 内存不叠加。

use std::path::{Path, PathBuf};

use crate::services::meeting_asr::{self as asr, AsrEvent, Installed, PackJson};

pub const COMPONENT_NAME: &str = "meeting-asr-hq";

pub fn root(home: &Path) -> PathBuf {
    home.join(".catfish").join(COMPONENT_NAME)
}

/// current.json 指的东西都还在才算装好。
pub fn installed(home: &Path) -> Option<Installed> {
    let text = std::fs::read_to_string(root(home).join("current.json")).ok()?;
    let i: Installed = serde_json::from_str(&text).ok()?;
    (i.python.is_file() && i.models.is_dir()).then_some(i)
}

/// 装完的 import 自检
fn import_check(pj: &PackJson) -> String {
    let mut mods = vec!["torch", "torchaudio", "soundfile", "numpy"];
    if pj.requirements_nodeps.iter().any(|r| r.starts_with("qwen-asr")) {
        mods.push("qwen_asr");
    }
    format!("import {}", mods.join(", "))
}

/// 装高精度组件。`base` = 已装的默认会议组件 (要借它的 Python 3.11 建 venv)。
pub fn install(pack: &Path, home: &Path, base: &Installed, mut on_step: impl FnMut(&'static str)) -> Result<Installed, String> {
    let root = root(home);
    std::fs::create_dir_all(&root).map_err(|e| format!("建目录失败: {e}"))?;
    let staging = root.join(".staging");
    let _ = std::fs::remove_dir_all(&staging);
    std::fs::create_dir_all(&staging).map_err(|e| format!("建目录失败: {e}"))?;
    let r = install_inner(pack, &root, &staging, base, &mut on_step);
    let _ = std::fs::remove_dir_all(&staging);
    r
}

fn install_inner(
    pack: &Path,
    root: &Path,
    staging: &Path,
    base: &Installed,
    on_step: &mut impl FnMut(&'static str),
) -> Result<Installed, String> {
    on_step("unpacking");
    asr::run(
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
    let base_venv = base.python.parent().and_then(Path::parent).ok_or("默认会议组件的 Python 路径不对")?;
    let base_python = asr::base_python_of(base_venv)?;
    let venv = root.join(format!("venv-{}", pj.version));
    let _ = std::fs::remove_dir_all(&venv);
    asr::run(crate::services::process::python_command(&base_python).arg("-m").arg("venv").arg(&venv), "建 Python 环境")?;
    let py = asr::venv_python(&venv);

    on_step("installing");
    asr::run(
        crate::services::process::python_command(&py)
            .args(["-m", "pip", "install", "--no-index", "--disable-pip-version-check", "-q", "--find-links"])
            .arg(staging.join("wheels"))
            .args(&pj.requirements)
            .env("PIP_NO_INPUT", "1"),
        "离线安装依赖",
    )?;
    if !pj.requirements_nodeps.is_empty() {
        asr::run(
            crate::services::process::python_command(&py)
                .args(["-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check", "-q", "--find-links"])
                .arg(staging.join("wheels"))
                .args(&pj.requirements_nodeps)
                .env("PIP_NO_INPUT", "1"),
            "离线安装识别模型的代码",
        )?;
    }

    on_step("checking");
    asr::run(crate::services::process::python_command(&py).args(["-c", &import_check(&pj)]), "依赖自检")?;

    on_step("finishing");
    let models = root.join(format!("models-{}", pj.version));
    let _ = std::fs::remove_dir_all(&models);
    std::fs::rename(staging.join("models"), &models).map_err(|e| format!("放模型失败: {e}"))?;
    let inst = Installed { version: pj.version.clone(), python: py, models };
    let tmp = root.join(".current.json.tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(&inst).map_err(|e| e.to_string())?)
        .map_err(|e| format!("写 current.json 失败: {e}"))?;
    std::fs::rename(&tmp, root.join("current.json")).map_err(|e| format!("写 current.json 失败: {e}"))?;
    for e in std::fs::read_dir(root).into_iter().flatten().flatten() {
        let name = e.file_name().to_string_lossy().to_string();
        let keep = name == format!("venv-{}", pj.version) || name == format!("models-{}", pj.version);
        if (name.starts_with("venv-") || name.starts_with("models-")) && !keep {
            let _ = std::fs::remove_dir_all(e.path());
        }
    }
    Ok(inst)
}

pub struct RetranscribeArgs<'a> {
    pub script: &'a Path,
    pub transcript: &'a Path,
    pub audio_dir: &'a Path,
    pub out: &'a Path,
    pub hotwords: &'a [String],
}

/// 阻塞跑完一次高精度重新转写 (在 spawn_blocking 里调)。返回 Done 事件。
pub fn retranscribe(hq: &Installed, a: &RetranscribeArgs, empty_cache: &Path, on_event: impl FnMut(&AsrEvent)) -> Result<AsrEvent, String> {
    let mut cmd = asr::offline_command(&hq.python, empty_cache)?;
    cmd.arg(a.script)
        .arg("--transcript").arg(a.transcript)
        .arg("--audio-dir").arg(a.audio_dir)
        .arg("--models").arg(&hq.models)
        .arg("--hotwords").arg(a.hotwords.join(" "))
        .arg("--out").arg(a.out);
    asr::run_events(cmd, on_event)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn import_check_includes_qwen_only_when_packed() {
        let pj: PackJson = serde_json::from_str(
            r#"{"version":"1.0.0","requirements":["torch==2.11.0"],"requirements_nodeps":["qwen-asr==0.0.6"]}"#,
        )
        .unwrap();
        assert_eq!(import_check(&pj), "import torch, torchaudio, soundfile, numpy, qwen_asr");
        let bare: PackJson = serde_json::from_str(r#"{"version":"1.0.0","requirements":[]}"#).unwrap();
        assert!(bare.requirements_nodeps.is_empty());
        assert_eq!(import_check(&bare), "import torch, torchaudio, soundfile, numpy");
    }

    #[test]
    fn installed_requires_python_and_models_present() {
        let d = tempfile::tempdir().unwrap();
        let r = root(d.path());
        std::fs::create_dir_all(&r).unwrap();
        let py = r.join("venv-1.0.0/bin/python");
        let models = r.join("models-1.0.0");
        let inst = Installed { version: "1.0.0".into(), python: py.clone(), models: models.clone() };
        std::fs::write(r.join("current.json"), serde_json::to_string(&inst).unwrap()).unwrap();
        assert!(installed(d.path()).is_none(), "目录被删了就算没装");
        std::fs::create_dir_all(py.parent().unwrap()).unwrap();
        std::fs::write(&py, "").unwrap();
        std::fs::create_dir_all(&models).unwrap();
        assert_eq!(installed(d.path()), Some(inst));
    }
}
