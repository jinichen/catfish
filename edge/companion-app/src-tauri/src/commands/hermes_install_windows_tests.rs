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

// ─── 9/18: .replaced-* 无限堆积 ──────────────────────────────
//
// 离线安装器换版本时把旧目录挪成 hermes-agent.replaced-<时间戳>。那是有意的
// 退路, 但从来没人清 —— 真机上攒了 22 个、18 GB。成功之后保留最近一份就够。

fn backup(paths: &BootstrapPaths, stamp: &str) -> PathBuf {
    let dir = paths.hermes_home.join(format!("hermes-agent.replaced-{stamp}"));
    std::fs::create_dir_all(dir.join("venv")).unwrap();
    std::fs::write(dir.join("venv/big"), "x".repeat(1024)).unwrap();
    dir
}

#[test]
fn prune_keeps_the_newest_backup_and_removes_the_rest() {
    let (_dir, paths) = fixture();
    let old = backup(&paths, "20260901-100000");
    let mid = backup(&paths, "20260910-100000");
    let new = backup(&paths, "20260917-100000");

    assert_eq!(prune_replaced_backups(&paths, 1), 2);
    assert!(!old.exists() && !mid.exists());
    assert!(new.exists(), "最近一份要留着 —— 新版本有问题时这是唯一能翻回去的东西");
    assert!(paths.install_dir.exists(), "绝不能碰安装目录本身");
}

#[test]
fn prune_is_a_noop_when_within_the_keep_budget() {
    let (_dir, paths) = fixture();
    let only = backup(&paths, "20260917-100000");
    assert_eq!(prune_replaced_backups(&paths, 1), 0);
    assert!(only.exists());
}

#[test]
fn prune_ignores_unrelated_siblings() {
    let (_dir, paths) = fixture();
    backup(&paths, "20260901-100000");
    backup(&paths, "20260910-100000");
    let unrelated = paths.hermes_home.join("hermes-agent-backup-manual");
    std::fs::create_dir_all(&unrelated).unwrap();
    let broken = paths.hermes_home.join("hermes-agent.broken-20260101-000000");
    std::fs::create_dir_all(&broken).unwrap();

    prune_replaced_backups(&paths, 1);
    assert!(unrelated.exists(), "名字不匹配的目录一律不碰");
    assert!(broken.exists(), ".broken- 是 git 路径留的, 不归这里管");
}
