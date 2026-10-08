"""Guard the desktop Windows install path (runtime coverage is in Windows CI)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ROOT / "edge/companion-app/src-tauri/src/commands"
RESOURCES = ROOT / "edge/companion-app/src-tauri/resources/windows"


def test_core_skips_optional_and_interactive_setup():
    source = (COMMANDS / "hermes_install_windows.rs").read_text()
    for flag in ("-SkipComputerUse", "-SkipSetup", "-NonInteractive", "-Json"):
        assert f'OsString::from("{flag}")' in source


def test_verified_core_is_reused_before_reinstall():
    source = (COMMANDS / "hermes_install.rs").read_text()
    windows = source.split('fn bootstrap_locked(', 1)[1].split('#[cfg(not(target_os', 1)[0]
    assert windows.index("reuse_verified_core(paths)?") < windows.index("::bootstrap(resource_dir")


def test_reader_requires_bundle_fingerprint_not_just_exe():
    # 9/25 (705006a) 附加组件安装从 hermes_install_windows.rs 整段搬进了
    # hermes_install_windows_addons.rs, 逻辑原样。主文件只剩 `mod addons` 引用。
    main = (COMMANDS / "hermes_install_windows.rs").read_text()
    assert '#[path = "hermes_install_windows_addons.rs"]' in main
    source = (COMMANDS / "hermes_install_windows_addons.rs").read_text()
    reader = source.split('"catfish-wechat-reader")', 1)[1].split("failures\n}", 1)[0]
    assert ".catfish-wechat-reader-installed.sha256" in reader
    # 跳过安装必须过指纹比对 (marker 内容 == 本次资源哈希), exe 在只是前提之一。
    assert "addon_fingerprint::matches(&marker, hash, reader_exe.is_file())" in reader
    assert "if !reader_current {" in reader
    assert "if !reader_exe.is_file()" not in source


def test_reader_install_preserves_strict_safety_diagnostics():
    source = (RESOURCES / "install-wechat-reader.ps1").read_text()
    assert "--offline" in source
    assert "--reinstall" in source
    assert "protocol_version" in source
    assert "-isnot [bool]" in source
    assert "reader safety contract failed:" in source


def test_reader_resource_build_runs_windows_installer_test():
    source = (ROOT / "edge/companion-app/scripts/build-wechat-reader-resource.ps1").read_text()
    assert "test-windows-reader-install.ps1" in source


def test_windows_reader_harness_captures_exit_code_after_process_exit():
    source = (ROOT / "edge/companion-app/scripts/test-windows-reader-install.ps1").read_text()
    assert "while (-not $process.HasExited" in source
    assert "CATFISH_TEST_STATUS" in source
    assert "TryParse($statusText" in source
    assert "$process.ExitCode" not in source
