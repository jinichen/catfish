# Chat runtime failures Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Preserve chat failure details and determine the Windows credential and MCP import failures without bypassing authentication.

**Architecture:** Decode both OpenAI and Hermes error envelopes in one frontend helper. Keep credential repair conditional on evidence from the affected runtime; a gateway 401 alone cannot distinguish expiry, issuer mismatch, or stale credentials. Inspect the MCP SDK import directly before changing dependency versions.

**Tech Stack:** TypeScript/Vitest, Python, Windows PowerShell.

---

### Task 1: Stream error preservation

Files: create `edge/companion-app/src/lib/chatStreamError.ts` and `chatStreamError.test.ts`; modify `edge/companion-app/src/lib/chat.ts`.

1. Test top-level string/object errors, Hermes nested errors, terminal error without details, and normal chunks.
2. Run `npm test -- src/lib/chatStreamError.test.ts` in `edge/companion-app`; confirm missing helper fails.
3. Implement a pure decoder returning the specific message or an explicit missing-detail message; route failures to onError, never onDone.
4. Run targeted chat tests and `npm run build`.

### Task 2: Credential and MCP diagnosis

1. Compare runtime token selection and refresh paths, without logging credentials.
2. Obtain affected Windows MCP import traceback and credential source/expiry diagnostics; local macOS state is not a substitute.
3. Reproduce the observed source-selection/import failure in tests before changing credential precedence or dependencies.
4. Keep scope/identity checks intact; do not bypass TLS or replace an employee identity with a privileged credential.

### Task 3: Release checks

Run `bash scripts/check_file_sizes.sh --strict`, `git diff --check`, and version-sync checks before any requested commit. Windows runtime success requires the native CI gates; do not claim local macOS tests prove it. Leave `videos/` untouched. No automatic commit or push.

### Task 4: Background credentials and Windows loop witness

Files: `hermes_token_renewal.py`, `tests/test_service_token_bootstrap.py` in the existing plugin; Companion `src-tauri/src/lib.rs` and `services/hermes_jwt_sync.rs`; `edge/hermes-fork/patch_windows_watchdog.py` and `verify_windows_watchdog.py`; Windows MSI workflow.

1. Test missing-token mint, late Windows provisioning reload, employee credential isolation, and unchanged macOS process-credential precedence.
2. Allow mint with configured client credentials when service token is missing. Read only service keys from Windows `.env`, without interpolation.
3. Return a retryable error when installation has not created the environment. Retry startup and periodic sync failures after 60 seconds, retaining the normal 25-day successful renewal interval.
4. Patch only the Windows upstream bundle: do not attempt the unsupported Unix witness. Preserve file heartbeats and UNKNOWN (not WEDGED) semantics. Fail packaging if upstream anchors change.
5. Execute actual patched heartbeat function with Windows and POSIX capability mocks; require a heartbeat, correct witness flag, and no warning. Run this check during Windows packaging.

Validation: plugin suite 388 passed / 30 skipped; Rust JWT sync 28 passed; patched heartbeat Windows/POSIX behavior and idempotence passed locally. Native Windows release remains unverified until CI and affected-machine smoke testing. Shared provisioning retry code affects all platforms; no zero-impact claim.
