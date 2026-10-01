use super::*;

fn python3() -> Option<PathBuf> {
    ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        .iter()
        .map(PathBuf::from)
        .find(|p| p.is_file())
}

#[test]
fn base_python_comes_from_pyvenv_cfg_home() {
    let d = tempfile::tempdir().unwrap();
    let home = d.path().join("cpython/bin");
    std::fs::create_dir_all(&home).unwrap();
    let exe = if cfg!(windows) { "python.exe" } else { "python3.11" };
    std::fs::write(home.join(exe), "").unwrap();
    let venv = d.path().join("venv");
    std::fs::create_dir_all(&venv).unwrap();
    std::fs::write(
        venv.join("pyvenv.cfg"),
        format!("home = {}\ninclude-system-site-packages = false\nversion_info = 3.11\n", home.display()),
    )
    .unwrap();
    assert_eq!(base_python_of(&venv).unwrap(), home.join(exe));

    std::fs::write(venv.join("pyvenv.cfg"), "version = 3.11\n").unwrap();
    assert!(base_python_of(&venv).unwrap_err().contains("没有 home"));
}

#[test]
fn installed_requires_paths_to_still_exist() {
    let d = tempfile::tempdir().unwrap();
    assert!(installed(d.path()).is_none());
    let r = root(d.path());
    let models = r.join("models-1.0.0");
    std::fs::create_dir_all(models.join("asr")).unwrap();
    let py = r.join("venv-1.0.0/bin/python");
    std::fs::create_dir_all(py.parent().unwrap()).unwrap();
    std::fs::write(&py, "").unwrap();
    let inst = Installed { version: "1.0.0".into(), python: py.clone(), models: models.clone() };
    std::fs::write(r.join("current.json"), serde_json::to_string(&inst).unwrap()).unwrap();
    assert_eq!(installed(d.path()), Some(inst));

    std::fs::remove_dir_all(models).unwrap(); // 用户手动删了
    assert!(installed(d.path()).is_none());
}

#[test]
fn events_parse_and_non_events_are_ignored() {
    assert_eq!(
        parse_event(r#"{"event":"phase","phase":"recognizing","duration_secs":70.0}"#),
        Some(AsrEvent::Phase { phase: "recognizing".into(), duration_secs: Some(70.0) })
    );
    assert_eq!(parse_event(r#"{"event":"phase","phase":"loading"}"#),
               Some(AsrEvent::Phase { phase: "loading".into(), duration_secs: None }));
    assert!(matches!(parse_event(r#"{"event":"error","message":"x"}"#), Some(AsrEvent::Error { .. })));
    assert_eq!(parse_event("funasr version: 1.4.16."), None);
}

/// 用假脚本跑通 transcribe 的进程 / 管道 / 事件处理 (真模型的端到端在 #[ignore] 测试里)。
fn fake_run(body: &str) -> (Result<AsrEvent, String>, Vec<AsrEvent>) {
    let Some(py) = python3() else { panic!("测试机上没有 python3") };
    let d = tempfile::tempdir().unwrap();
    let script = d.path().join("fake_asr.py");
    std::fs::write(&script, body).unwrap();
    let inst = Installed { version: "t".into(), python: py, models: d.path().into() };
    let args = TranscribeArgs { script: &script, audio_dir: d.path(), out: &d.path().join("t.json"), speakers: 2, hotwords: &[] };
    let mut seen = vec![];
    let r = transcribe(&inst, &args, &d.path().join("empty"), |e| seen.push(e.clone()));
    (r, seen)
}

#[test]
fn transcribe_returns_done_and_forwards_phases() {
    let (r, seen) = fake_run(
        "import sys, json\n\
         print('library noise on stderr', file=sys.stderr)\n\
         for p in ['loading','recognizing']: print(json.dumps({'event':'phase','phase':p}), flush=True)\n\
         print(json.dumps({'event':'done','out':'x','segments':3,'speakers':2,'duration_secs':9.5}))\n",
    );
    assert!(matches!(r, Ok(AsrEvent::Done { segments: 3, speakers: 2, .. })), "{r:?}");
    assert_eq!(seen.len(), 3);
}

#[test]
fn transcribe_surfaces_script_error_message() {
    let (r, _) = fake_run(
        "import json, sys\nprint(json.dumps({'event':'error','message':'模型不全'}))\nsys.exit(1)\n",
    );
    assert_eq!(r.unwrap_err(), "模型不全");
}

#[test]
fn transcribe_crash_reports_stderr_tail() {
    let (r, _) = fake_run("import sys\nprint('Traceback: boom', file=sys.stderr)\nsys.exit(3)\n");
    let e = r.unwrap_err();
    assert!(e.contains("异常退出") && e.contains("boom"), "{e}");
}

/// 真包端到端: Rust install() 装进临时 HOME → 真脚本转写。默认跳过 (要 2.3GB 的包)。
///   CATFISH_MEETING_PACK=<包路径> CATFISH_MEETING_AUDIO=<放 seg-*.wav 的目录> \
///   CATFISH_BASE_PYTHON=<python3.11> cargo test --lib meeting_asr::tests::e2e -- --ignored --nocapture
#[test]
#[ignore]
fn e2e_install_real_pack_and_transcribe() {
    let pack = PathBuf::from(std::env::var("CATFISH_MEETING_PACK").expect("CATFISH_MEETING_PACK"));
    let audio = PathBuf::from(std::env::var("CATFISH_MEETING_AUDIO").expect("CATFISH_MEETING_AUDIO"));
    let base = PathBuf::from(std::env::var("CATFISH_BASE_PYTHON").expect("CATFISH_BASE_PYTHON"));
    let home = tempfile::tempdir().unwrap();
    let t = std::time::Instant::now();
    let mut steps = vec![];
    let inst = install(&pack, home.path(), &base, |s| steps.push(s)).unwrap();
    println!("install {:.0}s · steps {steps:?} · {inst:?}", t.elapsed().as_secs_f64());
    assert_eq!(installed(home.path()), Some(inst.clone()));

    let script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("scripts/meeting_asr.py");
    let out = home.path().join("transcript.json");
    let args = TranscribeArgs { script: &script, audio_dir: &audio, out: &out, speakers: 3, hotwords: &["游戏平台".to_string()] };
    let t = std::time::Instant::now();
    let done = transcribe(&inst, &args, &home.path().join("empty"), |e| println!("  event {e:?}")).unwrap();
    println!("transcribe {:.0}s · {done:?}", t.elapsed().as_secs_f64());
    assert!(matches!(done, AsrEvent::Done { segments, .. } if segments > 0));
    assert!(std::fs::read_dir(home.path().join("empty")).unwrap().next().is_none(), "不许联网下载");
}
