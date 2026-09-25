# Windows runtime recovery Implementation Plan

**Goal:** Repair the demonstrated Windows Tool Bridge startup failure, prevent concurrent Chrome profile launches, and remove email upgrades from the live Hermes DLL environment.

**Architecture:** Keep Unix sandbox limits platform-local and fail closed when isolation is unavailable. Route Chrome startup through one serialized, protocol-verified lifecycle. Publish a validated isolated email environment through a pointer instead of replacing DLLs loaded by Hermes.

**Tech Stack:** Python, Rust/Tokio, PowerShell 5.1, Windows CI.

## 1. Tool Bridge

- Add a subprocess regression test blocking Unix-only imports before importing the real server.
- Run it before the fix to reproduce the reported `resource` failure.
- Move resource limits to `sandbox_limits.py`, re-export existing symbols, retain sandbox refusal, make timeout return codes portable.
- Run sandbox and server regression tests; add Windows real daemon health smoke using packaged sources and the real Hermes registry.

## 2. Chrome lifecycle

- Add `services/chrome_runtime.rs`; use a shared launch/stop lock, verify CDP readiness before success, reject unknown ownership before stopping.
- Replace duplicate launch implementations in `commands/chrome.rs` and `services/autostart.rs`.
- Test serialization, CDP reuse and malformed/unavailable endpoints without launching the user's browser.
- Inspect bundled Chromium discovery so Windows can use its shipped browser.

## 3. Email installer

- Install each Windows email upgrade into a new isolated venv; validate COM imports and CLI before publishing `email-runtime/current.txt`.
- Resolve the pointer consistently in Companion and Tool Bridge, retaining legacy fallback for existing installations.
- Never delete/replace active Hermes pywin32 DLLs or terminate unrelated Python processes.
- Add native Windows install/update smoke with an already-loaded DLL and verify shared Hermes files are untouched.

## 4. Verification and release

- Run targeted Python and Rust tests, Windows type checking and parity checks, and `bash scripts/check_file_sizes.sh --strict`.
- Keep all source files below 800 lines. Split touched large modules by responsibility with compatible re-exports.
- Synchronize the Companion patch version in all manifests and locks before a future release.
- Report local results separately from native Windows CI, which must pass before claiming Windows installation is verified.
- Do not commit or push without the user's request; preserve untracked `videos/`.

The writing-plans execution subskills are unavailable; implement and verify directly in this session.

## Verification recorded locally

- The new import regression first failed with `ModuleNotFoundError: resource`, then passed.
- The required-sandbox regression first exposed registry fallback, then passed after fail-closed dispatch.
- Related Python suite: 72 passed (includes actual macOS sandbox tests).
- Rust library suite: 586 passed, 2 existing ignored tests.
- Windows release + test cross-type checks: passed, no crate warnings.
- Windows parity R1–R8, version synchronization (1.0.22), diff whitespace and strict file sizes: passed.
- Native Windows packaged-daemon, Chromium lifecycle, PowerShell email upgrade/rollback gates are added but NOT run on this macOS host. They must pass in CI before release; no claim of Windows runtime verification yet.
- No commit or push performed. Untracked `videos/` untouched.
