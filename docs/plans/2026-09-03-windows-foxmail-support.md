# Windows Foxmail Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the Windows Companion package automatically install its mail CLI and allow `catfish-email` to discover and read Foxmail mailboxes on Windows.

**Architecture:** Keep the existing offline MSI resource/bootstrap flow, but make its completion observable and retryable. Add a Windows Foxmail adapter that reuses the existing Foxmail mailbox parser, discovers per-user Foxmail profiles without hard-coded usernames or drive letters, and exposes read-only operations first; keep send/delete/draft operations explicitly unsupported until a safe write path is verified.

**Tech Stack:** Rust/Tauri bootstrap, PowerShell, Python, pathlib, SQLite/`.box` parser, pytest, Vitest.

---

### Task 1: Establish current behavior and fixture coverage

**Files:**
- Inspect: `edge/email-agent/src/catfish_email/inbox.py`
- Inspect: `edge/email-agent/src/catfish_email/box_parser.py`
- Inspect: `edge/email-agent/src/catfish_email/adapters/foxmail_mac.py`
- Test: `edge/email-agent/tests/test_adapter_foxmail_win.py`

**Steps:**
1. Record Windows Foxmail profile locations and the existing parser's accepted `.box` formats.
2. Add a failing test for Windows adapter discovery against a temporary profile tree.
3. Add a failing test that a parsed Windows message carries a stable `foxmail_win|...` id.
4. Run the focused pytest module and confirm failure before implementation.

### Task 2: Implement Windows Foxmail read-only adapter

**Files:**
- Create: `edge/email-agent/src/catfish_email/adapters/foxmail_win.py`
- Modify: `edge/email-agent/src/catfish_email/inbox.py`
- Modify: `edge/email-agent/src/catfish_email/adapters/__init__.py`
- Test: `edge/email-agent/tests/test_adapter_foxmail_win.py`

**Steps:**
1. Discover profiles from `%LOCALAPPDATA%` and `%APPDATA%`, with optional `CATFISH_FOXMAIL_ROOT` override for testing and enterprise packaging.
2. Detect Foxmail 7/8/9 profile layouts without assuming a fixed username or drive.
3. Reuse `box_parser.py` and `foxmail_db.py` for account, folder, list, read, and search operations.
4. Return clear `DataNotFoundError`/`ClientNotRunningError` messages when Foxmail is absent or no mailbox is configured.
5. Mark write operations unsupported rather than mutating Foxmail files.
6. Register `foxmail-win` and make Windows adapter selection prefer Outlook when available, then Foxmail.
7. Run the focused adapter and contract tests.

### Task 3: Make Windows bootstrap install email reliably

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install_windows.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install_state.rs`
- Modify: `edge/companion-app/src-tauri/src/lib.rs`
- Test: existing Rust command tests and Windows packaging checks

**Steps:**
1. Ensure the Windows release bootstrap always reaches the optional-components phase after core Hermes is already healthy.
2. Persist a clear last-error/phase marker when `install-catfish-email.ps1` is skipped or fails.
3. Make retry re-run missing optional components without reinstalling the whole core runtime.
4. Verify resource paths resolve from the packaged `resources/windows` directory.
5. Add a packaging assertion that `chromium-embed.tar.gz` cannot be zero bytes.
6. Run Rust tests, file-size checks, and the Windows resource validation script.

### Task 4: Improve platform-correct user diagnostics

**Files:**
- Modify: `edge/companion-app/src/lib/emailPlatformHints.ts`
- Modify: `edge/companion-app/src/tabs/Email/EmailTab.tsx`
- Modify: `edge/companion-app/src-tauri/src/commands/email.rs`
- Test: `edge/companion-app/src/lib/emailPlatformHints.test.ts`

**Steps:**
1. Tell Windows users to open/configure Outlook or Foxmail, never show Mail.app instructions.
2. Show the Windows bootstrap log path and distinguish “CLI missing” from “no supported mailbox found”.
3. Add tests for macOS and Windows wording.
4. Run TypeScript/Vitest checks.

### Task 5: Verify with a clean Windows package

**Files:**
- Inspect: `edge/companion-app/scripts/build-msi-local.ps1`
- Inspect: `edge/companion-app/src-tauri/tauri.windows.conf.json`

**Steps:**
1. Build from the committed tree, not an MSI made from uncommitted files.
2. Install on a clean Windows profile with Foxmail configured.
3. Confirm `catfish-email.exe`, the bootstrap log, `catfish-email accounts --human`, and `catfish-email list --json`.
4. Test Companion email list/read and verify no macOS path appears.
5. Commit source and tests, then push only after all checks pass.
