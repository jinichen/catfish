# CI Warning Cleanup Implementation Plan

> **For Codex:** Execute this plan task by task and verify each task before moving on.

**Goal:** Remove stale and actionable warnings from the Windows Companion build without masking the remaining upstream, compiler, or bundle-size diagnostics.

**Scope:** Windows CI packaging, local cross-platform resource scripts, the WiX installer template, and platform-specific Rust diagnostics in Companion.

## Tasks

1. Remove the deleted `catfish-todo-sync` plugin from all packaging copy lists and legacy comments; keep `catfish-memory` as the only bundled Catfish plugin.
2. Remove the duplicate manual npm cache step from Windows CI; `actions/setup-node` already owns npm caching.
3. Remove the obsolete WiX `REINSTALLMODE` property, stop treating same-version installs as upgrades, and place the uninstall shortcut with the Start Menu shortcuts so ICE90 does not flag `INSTALLDIR`.
4. Remove Windows-only Rust unused/dead-code warnings with narrow `cfg` boundaries; preserve macOS/Linux behavior.
5. Run shell/file-size/diff checks and relevant tests; inspect the final diff and commit only these changes.

The optional `ui-tui/node_modules` condition is reported as a notice rather than a warning because the installer intentionally keeps its network-install fallback. Vite chunking remains a separate performance follow-up; the Windows Rust unused/dead-code diagnostics are fixed here because they are caused by platform code being compiled outside its supported target.

## Verification

- `bash scripts/check_file_sizes.sh --strict`
- `bash -n edge/companion-app/scripts/build-mac-resources.sh`
- `git diff --check`
- `cargo check --manifest-path edge/companion-app/src-tauri/Cargo.toml --lib`
- `cargo check --manifest-path edge/companion-app/src-tauri/Cargo.toml --release --lib`
- `rg` confirms no packaging copy list references `catfish-todo-sync`.
- Static assertions confirm the Windows workflow uses `actions/upload-artifact@v7` and has no duplicate npm cache step.
