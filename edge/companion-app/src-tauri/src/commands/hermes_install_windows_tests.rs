use super::*;
use std::os::unix::fs::PermissionsExt;

fn fixture() -> (tempfile::TempDir, BootstrapPaths) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("hermes");
    let paths = BootstrapPaths {
        home: dir.path().to_path_buf(),
        install_dir: root.join("hermes-agent"),
        lock_file: root.join("lock"),
        transaction_file: root.join("transaction"),
        last_error_file: root.join("error"),
        hermes_home: root,
    };
    std::fs::create_dir_all(&paths.install_dir).unwrap();
    std::fs::write(paths.install_dir.join("pyproject.toml"), "[project]\n").unwrap();
    std::fs::write(paths.install_dir.join(".catfish-hermes-version"), hermes_pinned_commit()).unwrap();
    (dir, paths)
}

fn executable(path: &Path, status: u8) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, format!("#!/bin/sh\nexit {status}\n")).unwrap();
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
}

#[test]
fn successful_core_without_markers_is_adopted_without_rebuilding_venv() {
    let (_dir, paths) = fixture();
    executable(&hermes_venv_python(&paths.install_dir), 0);
    executable(&hermes_venv_tool(&paths.install_dir, "hermes"), 0);
    let user_file = paths.install_dir.join("venv/retained-addon");
    std::fs::write(&user_file, "keep").unwrap();
    assert!(reuse_verified_core(&paths).unwrap());
    assert!(core_health_problems(&paths, true).is_empty());
    assert_eq!(std::fs::read_to_string(user_file).unwrap(), "keep");
    assert!(reuse_verified_core(&paths).unwrap());
}

#[test]
fn failed_runtime_probe_or_wrong_pin_never_creates_completion_marker() {
    let (_dir, paths) = fixture();
    executable(&hermes_venv_python(&paths.install_dir), 1);
    executable(&hermes_venv_tool(&paths.install_dir, "hermes"), 0);
    assert!(!reuse_verified_core(&paths).unwrap());
    executable(&hermes_venv_python(&paths.install_dir), 0);
    std::fs::write(paths.install_dir.join(".catfish-hermes-version"), "0".repeat(40)).unwrap();
    assert!(!reuse_verified_core(&paths).unwrap());
    assert!(!paths.install_dir.join(super::super::hermes_install_base::COMPLETION_MARKER).exists());
}

#[test]
fn latest_known_stage_is_reported_without_arbitrary_log_content() {
    let (_dir, paths) = fixture();
    let mut log = open_bootstrap_log(&paths).unwrap();
    writeln!(log, "-> Installing dependencies...\nDownloading pillow\nprivate arbitrary output").unwrap();
    assert_eq!(current_stage(&paths), "正在下载 Python 依赖");
    writeln!(log, "-> TUI dependencies...").unwrap();
    assert_eq!(current_stage(&paths), "正在检查终端界面组件");
}
