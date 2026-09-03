# Windows install and email platform hints implementation plan

> **For Claude:** REQUIRED SUB-SKILL: use the writing-plans skill to execute this plan task-by-task.

**Goal:** Remove the invalid Hermes uv timestamp warning from bundled Windows installs and make Companion's email failure guidance correct on Windows and macOS.

**Architecture:** Patch the generated offline installer at build time so the bundled Hermes source no longer contains the stale duration-valued `exclude-newer` setting. Centralize platform-specific email guidance in the Companion frontend, while making the Rust fallback error identify the correct per-platform log directory.

**Tech Stack:** Python build patch scripts, Rust/Tauri commands, React/TypeScript, Vitest, shell build scripts.

---

### Task 1: Locate and characterize the two platform defects

- Confirm the Windows warning comes from the bundled Hermes `pyproject.toml`.
- Confirm the email backend and both email UI surfaces contain macOS-only guidance.
- Record exact current behavior in focused tests before changing it.

### Task 2: Patch the bundled Hermes configuration

- Add a small idempotent build-time patch for the invalid global `exclude-newer` duration.
- Apply it to Windows and macOS Hermes bundles without modifying the developer's external Hermes checkout.
- Add tests for removal, idempotence, and rejection of unexpected variants.

### Task 3: Make email errors platform-aware

- Return Windows/macOS-appropriate log locations from the Rust email command.
- Centralize frontend email failure hints and use them in the email tab and dashboard card.
- Add Vitest coverage for Windows, macOS, and unknown platforms.

### Task 4: Verify the repair

- Run focused Python/TypeScript/Rust tests and the frontend build.
- Run `bash scripts/check_file_sizes.sh --strict` and `git diff --check`.
- Review the diff to ensure no unrelated dirty-worktree changes are included.
