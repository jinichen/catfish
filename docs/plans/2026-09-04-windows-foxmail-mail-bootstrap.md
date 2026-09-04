# Windows Foxmail 邮件与 Bootstrap 修复 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Windows Companion 正确读取用户自定义位置的 Foxmail 邮件，并把适配器失败显示为明确错误，同时避免启动时重复触发安装流程。

**Architecture:** 由 Companion 保存并向 `catfish-email` 传递 Foxmail Storage 路径；Windows 默认只选 Foxmail，避免无关的 Outlook COM 探测。邮件 CLI 在没有任何可用适配器时返回非零错误，前端不再把失败伪装成空收件箱。Bootstrap 继续使用隐藏子进程，但增加基于实际组件可用性的幂等判断，避免每次启动重复安装。

**Tech Stack:** Rust/Tauri、React/TypeScript、Python、pytest、Vitest。

---

### Task 1: 核对现有实现与测试边界

**Files:**
- Inspect: `edge/email-agent/src/catfish_email/__main__.py`
- Inspect: `edge/email-agent/src/catfish_email/inbox.py`
- Inspect: `edge/email-agent/src/catfish_email/adapters/foxmail_win.py`
- Inspect: `edge/companion-app/src-tauri/src/commands/email.rs`
- Inspect: `edge/companion-app/src/tabs/Email/EmailTab.tsx`
- Inspect: `edge/companion-app/src-tauri/src/commands/hermes_install.rs`

**Step 1:** Record line counts and locate existing test fixtures.

**Step 2:** Run the focused email and Companion tests before editing.

**Expected:** Baseline results are recorded; no source file crosses the 800-line limit.

### Task 2: Lock down Foxmail custom-root behavior

**Files:**
- Modify: `edge/email-agent/src/catfish_email/adapters/foxmail_win.py`
- Test: `edge/email-agent/tests/`

**Step 1:** Add tests for an explicit `CATFISH_FOXMAIL_ROOT` containing a flat account directory and a Storage directory containing account subdirectories.

**Step 2:** Run only the new tests and verify the current implementation exposes the path/layout gap.

**Step 3:** Implement deterministic root normalization and diagnostics without hardcoding the user's drive letter.

**Step 4:** Run the Foxmail adapter tests and verify both supported layouts pass.

### Task 3: Stop swallowing all adapter failures

**Files:**
- Modify: `edge/email-agent/src/catfish_email/__main__.py`
- Modify: `edge/email-agent/src/catfish_email/inbox.py`
- Test: `edge/email-agent/tests/`

**Step 1:** Add tests proving that when every adapter fails, `accounts` and `list` return a nonzero exit status and an actionable error; partial adapter success still returns data.

**Step 2:** Implement the smallest change that preserves successful partial results but distinguishes “empty mailbox” from “no adapter/data source available”.

**Step 3:** Add explicit Windows Foxmail selection when configured, so Outlook COM is not probed for a Foxmail-only installation.

**Step 4:** Run all email-agent tests.

### Task 4: Pass Foxmail root from Companion

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/email.rs`
- Modify: `edge/companion-app/src/tabs/Email/EmailTab.tsx`
- Modify: `edge/companion-app/src/lib/emailPlatformHints.ts`
- Test: existing Rust/TypeScript email tests or create focused tests beside the affected modules.

**Step 1:** Add a persisted Windows email configuration field for the Foxmail root, with an environment-variable fallback for existing installations.

**Step 2:** Pass `CATFISH_FOXMAIL_ROOT` only when the configured path exists, and return the subprocess diagnostic when it does not.

**Step 3:** Render “Foxmail directory not found / no local mail files” as an error state rather than “收件箱为空”.

**Step 4:** Run Rust and Vitest focused tests.

### Task 5: Make bootstrap idempotent and single-instance safe

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install_windows.rs`
- Modify: `edge/companion-app/src-tauri/src/lib.rs`
- Test: relevant Rust tests.

**Step 1:** Add a guard so a healthy core plus an already-installed optional component never starts PowerShell again.

**Step 2:** Persist optional-component result/state and log the resolved executable/resource paths before attempting installation.

**Step 3:** Ensure only one Companion process starts the bootstrap worker; other instances wait for the existing state instead of spawning another attempt.

**Step 4:** Run Tauri Rust tests and inspect Windows-specific compilation paths.

### Task 6: Full validation and commit

**Files:**
- Check: all changed files

**Step 1:** Run `bash scripts/check_file_sizes.sh --strict`.

**Step 2:** Run focused Python, Rust, and TypeScript tests plus `git diff --check`.

**Step 3:** Review the diff for hardcoded user paths, accidental platform coupling, and legacy MSI custom actions.

**Step 4:** Commit with a focused message and report the commit plus test results.
