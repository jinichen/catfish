# Companion API Approval Compatibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restore Companion’s per-request approval flow under Hermes v2026.8.31 without globally approving unattended API sessions or `execute_code`.

**Architecture:** Keep Hermes’ fail-closed unattended policy for ordinary API/webhook requests. Add a narrow Catfish compatibility patch that treats only the current API session with a registered Companion SSE approval callback as an interactive gateway approval context. Reuse the existing P15/P15.2 approval route and UI; do not alter the global Hermes config or permanent approval policy.

**Tech Stack:** Python monkey-patch plugin, Hermes approval module compatibility layer, pytest, shell compatibility audit.

---

## Task 1: Lock the regression with focused tests

- Add tests for Hermes’ unattended predicate when the current session has a callable Companion gateway-notify callback.
- Verify ordinary unattended sessions, missing callbacks, and non-unattended contexts remain unchanged.
- Verify the compatibility patch is idempotent.

## Task 2: Implement the narrow approval compatibility patch

- Add a P15.3 patch in `plugin_approval.py`.
- Install it immediately after P15’s callback bridge and before requests are handled.
- Detect the active Hermes predicate dynamically so older Hermes versions remain compatible.
- Fail closed if session context or callback state cannot be inspected.

## Task 3: Make Hermes upgrade audits catch the dependency

- Extend `audit_hermes_compat.sh` to verify the new Hermes unattended predicate and the Catfish P15.3 bridge hook.
- Keep the checks diagnostic and version-tolerant; do not make an old Hermes version fail merely because it lacks the new predicate.

## Task 4: Verify the change

- Run focused P15.3 tests.
- Run the Hermes plugin test suite and relevant tool-bridge tests.
- Run `bash scripts/check_file_sizes.sh --strict`.
- Review the final diff and confirm no global `unattended_mode: approve` or permanent `execute_code` approval was introduced.
