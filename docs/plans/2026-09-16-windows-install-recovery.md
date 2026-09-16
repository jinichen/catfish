# Windows install recovery implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent optional setup from blocking first use or destroying a usable Hermes runtime on retry.

**Architecture:** Reuse independently verified pinned core installations before considering reinstall. Skip interactive setup and optional Computer Use provisioning in desktop bootstrap. Validate the installed reader executable with strict typed safety fields, bind successful validation to bundle/script fingerprints, and exercise the actual PowerShell installer in Windows CI.

**Tech Stack:** Rust, Python pytest, Windows PowerShell 5.1, GitHub Actions.

## Implementation

1. Add regressions under `edge/hermes-fork/test_windows_bootstrap_recovery.py` for skip flags, verified-core reuse, reader fingerprint, strict report validation and CI integration. Run pytest to establish failures.
2. Update `hermes_install_windows.rs` and Windows `bootstrap_locked` in `hermes_install.rs`; reuse only the pinned version with working Python imports and CLI. Preserve failures for unsafe/missing reader reports.
3. Harden `resources/windows/install-wechat-reader.ps1`: reinstall exact offline wheel, validate all safety fields and emit bounded field diagnostics. Add PowerShell executable integration tests using fresh temporary runtimes.
4. Run Python regressions, reader tests, Rust checks where available, and strict file-size checks. Windows-only execution must be verified on Windows CI; do not claim this Mac run proves the user's machine is repaired.
5. Preserve unrelated workflow/UI edits. Do not commit, deploy, stop user processes, or remove user runtimes without a request.
