# Windows mail runtime repair Implementation Plan

**Goal:** Prevent stale mail helpers passing health checks and cached paths blocking discovery.

**Architecture:** Validate installed CLI capabilities as well as resource fingerprints. Treat cached Foxmail directories as hints, preserving explicit overrides. Audit hidden startup and legacy cleanup separately; native Windows verification remains required.

**Tech Stack:** Rust, Python, PowerShell.

### 1. Discovery regression
- Add tests in `edge/email-agent/tests/test_foxmail_discovery.py` for an empty cached directory and a valid cached directory.
- Update `adapters/foxmail_discovery.py` to accept `CATFISH_FOXMAIL_HINT` as a candidate, not a forced override.
- Update `commands/email.rs` to pass the cache as that hint.
- Run `PYTHONPATH=edge/email-agent/src python3 -m pytest edge/email-agent/tests -q`.

### 2. Runtime capability verification
- Update `commands/hermes_install_windows.rs` to require `discover --help` before skipping install and after install.
- Update `resources/windows/install-catfish-email.ps1` to validate the same contract.
- Log bootstrap entry even when no installer launches, to distinguish skipped install from missing execution.

### 3. Verification
- Audit `services/windows_lifecycle.rs` and its maintenance script; do not remove unknown user processes/tasks.
- Run Rust compilation and `bash scripts/check_file_sizes.sh --strict`.
- On Windows, verify installed CLI capability, actual discovery, and console process ownership. Do not claim these native checks passed on macOS.
