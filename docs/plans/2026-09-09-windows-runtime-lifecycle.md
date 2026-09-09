# Windows runtime lifecycle Implementation Plan

> Execute in this workspace; preserve the unrelated briefing changes. No automatic commit.

**Goal:** Prevent legacy Windows console loops, upgrade the bundled email runtime, and correctly discover Foxmail data without manual YAML edits.

**Architecture:** A small GUI-subsystem MSI helper performs narrowly scoped current-user process/legacy-entry cleanup before file replacement/removal. Companion prevents duplicate instances and uses console-free child processes. Email readiness includes the distribution fingerprint, not only import success. Mail data, task data, and knowledge files are never cleanup targets.

**Tech Stack:** Rust/std/fs2/SHA256, WiX 3, PowerShell, Python/winreg, pytest.

## Tasks

1. Add failing registry tests in `edge/email-agent/tests/test_foxmail_discovery.py`, including unequal subkey/value counts. Fix enumeration and installation-path variants in `adapters/foxmail_discovery.py`. Run the email suite.
2. In `hermes_install_windows.rs`, compare archive+installer fingerprints against a marker, run offline reinstall on changes, and record only verified success. Pass the actual Hermes home to the installer. Add hash/marker regression tests.
3. Remove conflicting detached flags in `services/process.rs`; add current-user instance locking before Tauri services start.
4. Create `wix/maintenance.rs` and `wix/windows-maintenance.ps1`. Stop exact owned processes and legacy `CatfishSearchWatcher` registrations; backup registrations and leave all user data intact. Embed helper in MSI using `build.rs` and `wix/main.wxs`, so uninstall never depends on Python or installed files.
5. Retire the console BAT watcher installer in `edge/local-search/src/catfish_search/daemon_windows.py`; retain safe removal and guide managed startup to Companion. Never recreate the deprecated loop.
6. Run Python suites, Rust checks/tests, PowerShell contract tests on Windows CI, `git diff --check`, and `bash scripts/check_file_sizes.sh --strict`. Windows acceptance: legacy upgrade, uninstall/reinstall, login after uninstall, custom Foxmail disk, unavailable Outlook, healthy unchanged addon (no reinstall).

## Acceptance boundary

Local macOS tests cannot prove Windows MSI execution or absence of windows. Windows CI must compile the helper and validate cleanup using disposable fixtures. A real Windows install/uninstall and Foxmail account test remains required before claiming field acceptance. Unknown services/startup entries must be reported, not removed by wildcard name.
