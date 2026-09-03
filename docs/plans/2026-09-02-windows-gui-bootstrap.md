# Windows GUI Bootstrap Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move long Windows Hermes and add-on installation out of the MSI transaction into a hidden, observable first-launch bootstrap owned by Companion.

**Architecture:** The MSI will only deploy the application and offline resources. On the first packaged Windows launch, the existing Hermes bootstrap progress channel will run the bundled PowerShell installers with `CREATE_NO_WINDOW`, capture output to a local log, and emit stage progress to the existing React status surface. Completion and version markers will make the process idempotent, while optional email/WeChat failures remain local feature failures.

**Tech Stack:** Rust/Tauri, Windows PowerShell process APIs, existing Hermes bootstrap state/progress files, React/TypeScript, WiX/Tauri configuration.

---

### Task 1: Add the Windows hidden bootstrap executor

**Files:**
- Create: `edge/companion-app/src-tauri/src/commands/hermes_install_windows.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/mod.rs`
- Test: `edge/companion-app/src-tauri/src/commands/hermes_install_windows.rs` (unit tests for argument construction and resource validation)

**Steps:**
1. Add Windows-only helpers that resolve `resources/windows`, build the core/email/WeChat PowerShell commands, set `CREATE_NO_WINDOW`, and append stdout/stderr to `%LOCALAPPDATA%\\hermes\\logs\\catfish-bootstrap.log`.
2. Add a Windows bootstrap function that reports check/core/deps/email/WeChat/complete phases and treats email/WeChat/dependency failures as non-fatal optional feature failures.
3. Add focused tests that do not execute Windows processes on macOS, covering required resource paths and command argument shape.

### Task 2: Route Windows first launch through the GUI bootstrap

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install.rs`
- Modify: `edge/companion-app/src-tauri/src/lib.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install_artifacts.rs`

**Steps:**
1. Use the Windows executor when the target is Windows, while retaining the existing atomic Unix bootstrap path unchanged.
2. Start the Windows bootstrap from Tauri setup in release builds and leave debug builds on the existing local-development behavior.
3. Make Windows resolve `uv.exe` correctly for optional dependency installation.
4. Preserve the existing completion marker, lock, failure record, progress event, and retry command.

### Task 3: Remove long-running MSI CustomActions

**Files:**
- Modify: `edge/companion-app/src-tauri/tauri.windows.conf.json`
- Modify: `edge/companion-app/src-tauri/wix/catfish-postinstall.wxs`
- Modify: `edge/companion-app/scripts/build-msi-local.ps1`
- Modify: `.github/workflows/build-windows-msi.yml`

**Steps:**
1. Stop including the WiX post-install fragment in Windows MSI builds so MSI only installs files.
2. Keep the offline resources bundled for first launch.
3. Remove instructions that imply MSI executes Hermes installation; replace them with first-launch bootstrap/log instructions.
4. Ensure no build script restores a PowerShell CustomAction as a hidden workaround.

### Task 4: Verify the end-to-end change

**Steps:**
1. Run Rust unit tests and `cargo check`.
2. Run frontend build and the Hermes bootstrap tests.
3. Run `bash scripts/check_file_sizes.sh --strict` and `git diff --check`.
4. Verify the Windows config/WiX XML parses and review the diff for unrelated dirty-worktree changes.

