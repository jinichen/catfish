# Windows Hidden Background Processes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Preserve automatic Outlook/Foxmail discovery and background mail polling without opening Windows console windows.

**Architecture:** Add one shared `background_command` constructor in the process service that applies `CREATE_NO_WINDOW` on Windows and is a no-op elsewhere. Route recurring process probes, email CLI calls, dependency checks, and Windows bootstrap commands through it; make the scheduler reuse the same email command builder as the UI so `foxmail_root` is inherited consistently.

**Tech Stack:** Rust, Tauri 2, `std::process::Command`, Cargo tests.

---

### Task 1: Shared Windows background command

**Files:**
- Modify: `edge/companion-app/src-tauri/src/services/process.rs`
- Test: `edge/companion-app/src-tauri/src/services/process.rs`

**Steps:**
1. Add a regression test that checks recurring Windows process paths use the shared constructor.
2. Run the focused Cargo test and confirm it fails before implementation.
3. Add `background_command`, applying `CREATE_NO_WINDOW` only on Windows.
4. Route `taskkill`, `tasklist`, and `wmic` through the constructor.
5. Run the focused tests and confirm they pass.

### Task 2: Email UI and scheduler share one process path

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/email.rs`
- Modify: `edge/companion-app/src-tauri/src/services/email_scheduler.rs`

**Steps:**
1. Make `email_command` crate-visible and construct the command through `background_command`.
2. Replace the scheduler's direct `Command::new` call with `email_command`.
3. Verify the scheduler now receives `foxmail_root` and `CATFISH_EMAIL_CLIENT` exactly like UI refreshes.
4. Run email and process regression tests.

### Task 3: Startup checks and bootstrap

**Files:**
- Modify: `edge/companion-app/src-tauri/src/services/autostart_deps.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install_windows.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/dream.rs`
- Modify: `edge/companion-app/src-tauri/src/services/distill_scheduler.rs`

**Steps:**
1. Route Python, Playwright, agent-browser, PowerShell, installer health checks, and timed memory distillation through the shared background constructors.
2. Remove duplicated Windows process flag constants/imports.
3. Run `cargo fmt`, Cargo tests, and frontend tests/build.
4. Run `bash scripts/check_file_sizes.sh --strict` and `git diff --check`.
