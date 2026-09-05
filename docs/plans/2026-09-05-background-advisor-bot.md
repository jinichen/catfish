# Background Advisor Bot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add one opt-in, hidden “工作参谋” Hermes profile for the Companion briefing flow while preserving the employee-selected model, Catfish identity/RBAC headers, visible progress, and cancellation behavior.

**Architecture:** Reuse Hermes 0.21 profiles and its `/p/<profile>/...` multiplexed API routes. Companion owns only an `expert_bots` feature flag, profile provisioning, readiness diagnostics, and routing decision. The normal Companion chat remains on the default Hermes profile; only briefing advisor Call 1 may use the named profile. Call 2 remains a direct Catfish Gateway structured-output transform.

**Tech Stack:** Rust/Tauri commands, React/TypeScript, Hermes CLI/profile API, Vitest, Cargo tests.

---

### Task 1: Add a persistent feature flag and readiness model

**Files:**
- Create: `edge/companion-app/src-tauri/src/commands/expert_bots.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/mod.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/invoke_handler.rs`

**Step 1: Write failing Rust tests**

Cover these contracts:

- Missing `expert_bots` YAML means disabled.
- `expert_bots.enabled`, `advisor_profile`, and `managed_multiplex` round-trip without deleting unrelated YAML keys.
- Profile names accept only Hermes-compatible lowercase alphanumeric slugs.
- Readiness is false when the profile directory or multiplex configuration is missing.

**Step 2: Run the focused test and confirm failure**

Run: `cargo test expert_bots --manifest-path edge/companion-app/src-tauri/Cargo.toml`

Expected: FAIL because the module and contracts do not exist.

**Step 3: Implement the minimal config/status layer**

Add Tauri commands:

- `expert_bots_status`
- `expert_bots_set_enabled`

The status must report `enabled`, `ready`, `advisor_profile`, and a user-facing reason. The config lives under `expert_bots` in `~/.catfish/companion.yaml`; the default is disabled.

**Step 4: Re-run focused tests**

Expected: PASS.

### Task 2: Provision exactly one Hermes advisor profile

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/expert_bots.rs`
- Test: Rust unit tests colocated in that module

**Step 1: Write failing tests for command construction and state ownership**

Verify that enabling constructs non-shell subprocess arguments for:

- `hermes profile create advisor --clone --no-alias --description ...`
- `hermes config set gateway.multiplex_profiles true`
- `hermes config set gateway.multiplex_profile_allowlist ["advisor"]`

Verify that disabling does not delete the profile and only disables multiplex settings when Companion previously marked them as managed.

**Step 2: Implement provisioning**

Resolve the executable with `catfish_paths::hermes_venv_tool`. Never invoke a shell. Create the profile only when absent, configure Hermes multiplexing, restart the Hermes gateway, and persist `managed_multiplex: true` only after all steps succeed.

**Step 3: Add fail-closed diagnostics**

Do not enable routing if Hermes is missing, the profile is absent, multiplex is off, the allowlist excludes the profile, or the named profile has no usable API server key.

**Step 4: Run focused Rust tests**

Expected: PASS.

### Task 3: Add typed frontend access and settings UI

**Files:**
- Create: `edge/companion-app/src/lib/expertBots.ts`
- Create: `edge/companion-app/src/lib/expertBots.test.ts`
- Create: `edge/companion-app/src/tabs/Dashboard/ExpertBotsCard.tsx`
- Modify: `edge/companion-app/src/tabs/Dashboard/AgentPrefsCard.tsx`
- Modify: `edge/companion-app/src/lib/tauri_app.ts`

**Step 1: Write failing Vitest tests**

Cover URL construction for default and named profile routes, including preservation of query parameters and safe profile encoding.

**Step 2: Implement typed Tauri wrappers and route helper**

Keep the feature default off and cache only a positive ready status for the current app session. Any command failure must fall back to the default profile.

**Step 3: Add an experimental toggle**

Show “后台工作参谋（实验）” with disabled/enabling/ready/error states. Explain that it creates an isolated Hermes profile and restarts the local Hermes gateway once.

**Step 4: Run focused frontend tests**

Run: `npm test -- --run src/lib/expertBots.test.ts` from `edge/companion-app`.

Expected: PASS.

### Task 4: Route briefing advisor Call 1 through the profile

**Files:**
- Modify: `edge/companion-app/src/lib/briefing_advisor.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor_request.test.ts`
- Modify: `edge/companion-app/src/lib/me.test.ts`

**Step 1: Write failing tests**

Verify:

- Disabled/not-ready uses the existing `/v1/chat/completions` route.
- Enabled/ready uses `/p/advisor/v1/chat/completions`.
- The request body still uses `input.model`, so the employee picker wins.
- `fetchWithAuth` treats `/p/advisor/v1/chat/completions` as a Hermes-owned route and sends the Hermes API key plus `X-Catfish-User`.
- Call 2 remains on Catfish Gateway with `catfish_direct=1`.

**Step 2: Implement routing with fail-open fallback**

Resolve the advisor route immediately before Call 1. If status lookup fails, use the existing default profile and log one concise warning. Do not alter normal chat routing.

**Step 3: Re-run focused tests**

Expected: PASS.

### Task 5: Preserve progress and add a real stop path

**Files:**
- Create: `edge/companion-app/src/lib/advisorRunControl.ts`
- Create: `edge/companion-app/src/lib/advisorRunControl.test.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor.ts`
- Modify: `edge/companion-app/src/tabs/Briefing/AdvisorView.tsx`
- Modify: `edge/companion-app/src/tabs/Briefing/components/LoadingProgress.tsx`

**Step 1: Write failing tests for run ownership**

Cover a single active advisor run, idempotent stop, stale completion rejection, and cleanup after success/error.

**Step 2: Implement run control**

Give Call 1 a stable request/run id. The stop button must abort only the active advisor run and prevent late results from overwriting newer cache/UI state. Preserve the current progress phases.

**Step 3: Run focused tests**

Expected: PASS.

### Task 6: Verify boundaries and regressions

**Files:**
- Modify tests only if an existing assertion needs the new opt-in route

**Step 1: Run frontend checks**

Run from `edge/companion-app`:

- `npm test -- --run`
- `npm run build`

**Step 2: Run Rust checks**

Run:

- `cargo test --manifest-path edge/companion-app/src-tauri/Cargo.toml`
- `cargo check --manifest-path edge/companion-app/src-tauri/Cargo.toml`

**Step 3: Run repository file-size enforcement**

Run: `bash scripts/check_file_sizes.sh --strict`

Expected: PASS; no source file reaches 800 lines.

**Step 4: Inspect the final diff**

Run:

- `git diff --check`
- `git status --short`

Confirm the change does not modify Hermes source, normal chat routing, Catfish Gateway fallback chains, or group-chat behavior.
