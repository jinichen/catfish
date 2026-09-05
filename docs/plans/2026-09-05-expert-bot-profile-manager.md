# Expert Bot Profile Manager Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current single hidden `catfish-advisor` experiment into a real local Expert Bot manager where employees can create multiple Hermes Profiles, assign them deterministically to supported Companion scenarios, inherit or pin the model policy, preserve employee identity/RBAC, and stop visible runs.

**Architecture:** Hermes remains the source of truth for Profile runtime state, persona, skills, and execution. Companion adds only a local management UI, Catfish-specific registration/model policy, deterministic scenario bindings, gateway multiplex reconciliation, credential synchronization, and run controls. Profile mutations go through the Hermes public CLI; Companion must not create a second agent runtime, profile database, dispatcher, or task engine. The first release binds only real call sites (`briefing.advisor` and `email.draft`); detached autonomous work is a later Hermes Kanban integration, not a custom Catfish scheduler.

**Tech Stack:** Rust/Tauri commands, React/TypeScript, Hermes 0.21 Profile CLI and `/p/<profile>/...` multiplex routes, YAML/JSON local configuration, Vitest, Cargo tests.

---

## Non-negotiable contracts

- Hermes Profile is the execution identity. Companion stores no duplicate Profile directory, session database, memory, skills, or runtime.
- A scenario resolves to zero or one enabled Profile. Version 1 has no LLM-selected or automatic Profile routing.
- `inherit_picker` is the default model policy. The employee's current model picker remains authoritative.
- A fixed model is an explicit per-Bot policy and is still sent as the request body's `model`; it does not alter central model configuration.
- Employee identity continues through `X-Catfish-User`; Gateway OAuth/RBAC remains authoritative for models, tools, skills, quotas, and audit.
- Secrets never enter React state, command return values, or logs.
- Companion may delete only a Profile carrying its ownership marker. External Hermes Profiles may be registered and bound, but not deleted by Companion.
- Disabling a Bot never deletes its Profile, memory, sessions, or skills.
- Normal chat remains on the default Profile in this release. Profile-aware chat requires profile-aware history/session listing and is deliberately out of scope.
- `briefing.advisor` Call 2 remains a direct Gateway structured-output transform; only the agent-loop Call 1 is Profile-routed.

## Local data model

`~/.hermes/profiles/<id>/` remains the Profile source of truth. Companion keeps only policy in `~/.catfish/companion.yaml`:

```yaml
expert_bots:
  schema_version: 2
  enabled: true
  registrations:
    catfish-advisor:
      enabled: true
      model_policy:
        mode: inherit_picker
    finance-reviewer:
      enabled: true
      model_policy:
        mode: fixed
        model_id: catfish-private-main
  bindings:
    briefing.advisor: catfish-advisor
    email.draft: finance-reviewer
  gateway_ownership:
    enabled_multiplex: true
    allowlist_entries:
      - catfish-advisor
      - finance-reviewer
```

The employee-facing name and role description live in Hermes `profile.yaml`; persona lives in `SOUL.md`. The marker `.catfish-managed-profile` is the deletion authority. The registration map means “visible and usable by Companion,” not “Profile storage.”

## Supported scenarios in version 1

| Scenario id | UI label | Existing call site | Routing behavior |
|---|---|---|---|
| `briefing.advisor` | 早安工作参谋 | `briefing_advisor.ts` Call 1 | Route through selected Profile; preserve current stop path |
| `email.draft` | 邮件起草 | `emailDraft.ts` | Route through selected Profile; add abort support |

Do not expose bindings for documents, phishing, email classification, or general chat until those call sites have a tested Profile route. Showing a non-functional binding would recreate the current “looks configurable but is still one Bot” problem.

---

### Task 1: Split the single-Bot implementation before adding behavior

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/expert_bots.rs`
- Create: `edge/companion-app/src-tauri/src/commands/expert_bots_types.rs`
- Create: `edge/companion-app/src-tauri/src/commands/expert_bots_config.rs`
- Create: `edge/companion-app/src-tauri/src/commands/expert_bots_profiles.rs`
- Create: `edge/companion-app/src-tauri/src/commands/expert_bots_gateway.rs`

**Step 1: Record current behavior with focused tests**

Keep regression coverage for the legacy toggle, hidden advisor creation, multiplex ownership, readiness, and unrelated YAML preservation.

**Step 2: Move code without changing behavior**

Keep `expert_bots.rs` as the Tauri command facade and re-export seam. Move types, config/migration, Hermes Profile adapter, and gateway reconciliation into focused modules. Do not let any source file approach the 800-line limit.

**Step 3: Run the focused Rust tests**

Run:

```bash
cargo test expert_bots --manifest-path edge/companion-app/src-tauri/Cargo.toml
bash scripts/check_file_sizes.sh --strict
```

Expected: existing behavior remains green and every new source file is below 500 lines where practical.

**Step 4: Commit the mechanical split**

```bash
git add edge/companion-app/src-tauri/src/commands/expert_bots*.rs
git commit -m "refactor(companion): split expert bot profile modules"
```

---

### Task 2: Add the versioned multi-Bot policy model and legacy migration

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/expert_bots_types.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/expert_bots_config.rs`
- Test: Rust unit tests in the same modules

**Step 1: Write failing migration tests**

Cover:

- Missing `expert_bots` produces schema version 2 with the feature disabled.
- The current legacy shape (`enabled`, `advisor_profile`, `managed_multiplex`, `managed_allowlist`) migrates in memory to one registration and one `briefing.advisor` binding.
- Migration preserves every unrelated `companion.yaml` key.
- Unknown future keys inside `expert_bots` survive a read-modify-write cycle where possible.
- Invalid Profile ids and unsupported scenario ids fail closed.
- `fixed` requires a non-empty `model_id`; `inherit_picker` removes stale `model_id`.

**Step 2: Implement explicit domain types**

Add:

- `ExpertBotsConfigV2`
- `BotRegistration`
- `ModelPolicy::{InheritPicker, Fixed { model_id }}`
- `ExpertScenario::{BriefingAdvisor, EmailDraft}`
- `GatewayOwnership { enabled_multiplex, allowlist_entries }`
- `BotReadiness::{Ready, Disabled, MissingProfile, MissingCredential, NotServed, InvalidConfig}`

Use stable serialized scenario ids and camelCase only at the Tauri boundary.

**Step 3: Save migrated data only on a user mutation**

Status/list calls remain read-only. Create, update, bind, delete, and global enable operations persist the normalized v2 shape atomically.

**Step 4: Run tests and commit**

```bash
cargo test expert_bots_config --manifest-path edge/companion-app/src-tauri/Cargo.toml
git add edge/companion-app/src-tauri/src/commands/expert_bots_types.rs edge/companion-app/src-tauri/src/commands/expert_bots_config.rs
git commit -m "feat(companion): add versioned expert bot policy model"
```

---

### Task 3: Implement a Hermes-backed Profile adapter

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/expert_bots_profiles.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/expert_bots.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/invoke_handler.rs`
- Modify: `edge/companion-app/src/lib/tauri_app.ts`
- Test: Rust tests with temporary Hermes homes and a fake Hermes executable

**Step 1: Write failing adapter tests**

Cover:

- List default and named Hermes Profiles while ignoring invalid/deleted directories.
- Parse `profile.yaml` display name/description without exposing paths or secrets to React.
- Detect Companion ownership only from `.catfish-managed-profile`.
- Build subprocess argv without a shell and without interpolating user input into command text.
- Windows uses `CREATE_NO_WINDOW`.
- Create fails if the id already exists.
- Delete refuses default and unmanaged Profiles.
- Updating description/display name/SOUL preserves unrelated metadata and uses atomic writes.
- Canonical Profile id cannot be renamed in version 1.

**Step 2: Use Hermes public CLI for lifecycle changes**

Create with:

```text
hermes profile create <id> --clone-from default --no-alias --description <text>
```

Delete with:

```text
hermes profile delete <id> --yes
```

Do not reimplement Hermes Profile creation, migration, skill seeding, or deletion semantics.

**Step 3: Add a versioned ownership marker**

Write the marker only after Hermes creation succeeds. A partial failure must either remove the incomplete Companion registration or report an actionable repair state; it must not claim ownership of a pre-existing Profile.

**Step 4: Add Tauri commands**

- `expert_bots_list`
- `expert_bot_create`
- `expert_bot_update`
- `expert_bot_register_existing`
- `expert_bot_unregister`
- `expert_bot_delete`
- `expert_bot_set_enabled`

Return sanitized `ExpertBotSummary` values only.

**Step 5: Run tests and commit**

```bash
cargo test expert_bots_profiles --manifest-path edge/companion-app/src-tauri/Cargo.toml
git add edge/companion-app/src-tauri/src/commands edge/companion-app/src/lib/tauri_app.ts
git commit -m "feat(companion): manage Hermes expert bot profiles"
```

---

### Task 4: Replace imperative multiplex toggling with reconciliation

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/expert_bots_gateway.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/expert_bots.rs`
- Test: Rust unit tests in `expert_bots_gateway.rs`

**Step 1: Write failing ownership tests**

Cover:

- Desired allowlist is the set of enabled, registered Profiles used by a supported binding.
- Enabling a second Bot adds one entry and preserves external entries.
- Disabling/unregistering removes only entries recorded in `gateway_ownership.allowlist_entries`.
- Existing user-managed multiplex settings are never disabled.
- Companion disables multiplex only when it originally enabled it, no desired Bot remains, and no external allowlist entry remains.
- A batch mutation causes at most one Gateway restart.
- Restart failure restores both Hermes config and Companion config.

**Step 2: Implement `reconcile_gateway(desired_profiles)`**

Treat Hermes `config.yaml` as an external shared file. Preserve top-level versus `gateway.*` precedence exactly as Hermes does. Write atomically and restart only when effective settings changed.

**Step 3: Add readiness diagnostics**

Each Bot summary reports whether its Profile exists, is enabled, is served by multiplex, has transport credentials, and has a valid binding/model policy. The UI must distinguish configuration errors from “feature globally disabled.”

**Step 4: Run tests and commit**

```bash
cargo test expert_bots_gateway --manifest-path edge/companion-app/src-tauri/Cargo.toml
git add edge/companion-app/src-tauri/src/commands/expert_bots_gateway.rs edge/companion-app/src-tauri/src/commands/expert_bots.rs
git commit -m "fix(companion): reconcile multi-profile gateway ownership"
```

---

### Task 5: Synchronize credentials and Gateway address into managed Profiles

**Files:**
- Create: `edge/companion-app/src-tauri/src/services/hermes_profile_sync.rs`
- Modify: `edge/companion-app/src-tauri/src/services/mod.rs`
- Modify: `edge/companion-app/src-tauri/src/services/hermes_jwt_sync.rs`
- Modify: `edge/companion-app/src-tauri/src/services/hermes_jwt_sync_tests.rs`
- Modify: `edge/companion-app/src-tauri/src/services/hermes_jwt_sync_tests_base_url.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/expert_bots.rs`

**Step 1: Write the credential-regression tests first**

Cover:

- Access-token refresh updates default plus registered Companion-managed Profiles.
- Service-token refresh updates `OPENAI_API_KEY` in each managed Profile `.env`.
- Server URL changes update the default plus each managed Profile config/env.
- `API_SERVER_KEY` is synchronized from the default Profile so `/p/<profile>/...` accepts the same local transport credential without exposing it to React.
- Per-Profile credential-pool failure state is reset when credentials rotate.
- Unregistered or unmanaged Hermes Profiles are never modified.
- Secret values never appear in log strings or command responses.

**Step 2: Extract reusable file mutation helpers**

Move profile-target enumeration and shared config/env/auth updates into `hermes_profile_sync.rs` so `hermes_jwt_sync.rs` stays below the file-size red line. Keep all writes atomic and permissions owner-only for `.env`.

**Step 3: Synchronize immediately after creation/adoption**

A newly created managed Profile must be usable before the create command returns. If synchronization fails, keep the Profile but leave the registration disabled with an actionable readiness reason.

**Step 4: Run tests and commit**

```bash
cargo test hermes_jwt_sync --manifest-path edge/companion-app/src-tauri/Cargo.toml
cargo test hermes_profile_sync --manifest-path edge/companion-app/src-tauri/Cargo.toml
bash scripts/check_file_sizes.sh --strict
git add edge/companion-app/src-tauri/src/services edge/companion-app/src-tauri/src/commands/expert_bots.rs
git commit -m "fix(companion): keep expert profile credentials synchronized"
```

---

### Task 6: Build the actual Expert Bot settings surface

**Files:**
- Modify: `edge/companion-app/src/tabs/Dashboard/DashboardTab.tsx`
- Modify: `edge/companion-app/src/tabs/Dashboard/AgentPrefsCard.tsx`
- Modify: `edge/companion-app/src/tabs/Dashboard/ExpertBotsCard.tsx`
- Create: `edge/companion-app/src/tabs/Dashboard/ExpertBotList.tsx`
- Create: `edge/companion-app/src/tabs/Dashboard/ExpertBotEditor.tsx`
- Create: `edge/companion-app/src/tabs/Dashboard/ExpertBotBindings.tsx`
- Create: `edge/companion-app/src/lib/expertBotTypes.ts`
- Modify: `edge/companion-app/src/lib/expertBots.ts`
- Test: `edge/companion-app/src/lib/expertBots.test.ts`
- Create: `edge/companion-app/src/tabs/Dashboard/ExpertBotsCard.test.tsx`

**Step 1: Move Expert Bots out of the name/persona card**

Add a separate collapsed Dashboard section named `🧠 专家 Bot`. Remove the nested one-toggle row from `AgentPrefsCard`. This is a first-class manager, not a hidden preference.

**Step 2: Add the list state**

Each card shows:

- Display name and immutable advanced id.
- Role description.
- Hermes/Companion ownership.
- Enabled/readiness state.
- `跟随当前模型` or fixed model name.
- Bound scenarios.
- Edit, disable, unregister, or delete actions according to ownership.

**Step 3: Add a create/edit dialog**

Creation fields:

- Bot name.
- Generated editable id before creation only.
- One- or two-sentence responsibility description.
- Base Profile, defaulting to `default`.
- Persona template, defaulting to a safe Catfish expert template.
- Model policy, defaulting to `inherit_picker`.

Do not expose raw `.env`, Profile paths, API keys, or arbitrary YAML.

**Step 4: Add deterministic scenario bindings**

Render exactly the supported scenarios. Each has `不使用专家 Bot` plus enabled ready Bots. Prevent deleting a bound Bot until the user confirms the affected bindings will be cleared.

**Step 5: Run frontend tests and commit**

```bash
cd edge/companion-app
npm test -- --run src/lib/expertBots.test.ts src/tabs/Dashboard/ExpertBotsCard.test.tsx
npm run build
git add src/tabs/Dashboard src/lib
git commit -m "feat(companion): add expert bot profile manager UI"
```

---

### Task 7: Add one generic, deterministic scenario resolver

**Files:**
- Modify: `edge/companion-app/src/lib/expertBots.ts`
- Modify: `edge/companion-app/src/lib/expertBots.test.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor.ts`
- Modify: `edge/companion-app/src/lib/emailDraft.ts`
- Modify: `edge/companion-app/src/lib/me.test.ts`
- Modify or create focused request tests for advisor and email draft

**Step 1: Write resolver tests**

`resolveExpertBotRequest({ scenario, pickerModel, baseUrl, useHermes })` must return:

- Default Hermes URL and picker model when no binding exists.
- `/p/<profile>/v1/chat/completions` for an enabled, ready binding.
- Picker model for `inherit_picker`.
- Configured model id for `fixed`.
- Default route plus an explicit diagnostic reason when the binding is invalid or unavailable.
- No Profile route when Hermes is disabled.

**Step 2: Replace advisor-specific URL resolution**

Remove the hard-coded `advisorProfile` status contract. Wire `briefing.advisor` Call 1 through the generic resolver. Preserve Call 2 direct Gateway behavior and evidence filtering.

**Step 3: Wire `email.draft`**

Use the same resolver and preserve the selected picker model unless the bound Bot explicitly pins a model. Do not route email classification or phishing checks in this task.

**Step 4: Preserve auth and identity behavior**

Both named Profile routes must still use `fetchWithAuth`; tests must prove Hermes API key auth plus `X-Catfish-User`, and must prove no OAuth relogin loop is introduced.

**Step 5: Run tests and commit**

```bash
cd edge/companion-app
npm test -- --run src/lib/expertBots.test.ts src/lib/me.test.ts
npm run build
git add src/lib
git commit -m "feat(companion): bind expert bots to supported workflows"
```

---

### Task 8: Generalize visible progress and stop controls without creating a second task engine

**Files:**
- Rename/modify: `edge/companion-app/src/lib/advisorRunControl.ts`
- Rename/modify: `edge/companion-app/src/lib/advisorRunControl.test.ts`
- Modify: `edge/companion-app/src/tabs/Briefing/AdvisorView.tsx`
- Modify: `edge/companion-app/src/tabs/Email/EmailTab.tsx`
- Modify: `edge/companion-app/src/lib/emailDraft.ts`

**Step 1: Write run-ownership tests**

Cover scenario-scoped request ids, idempotent cancellation, stale-completion rejection, cleanup after error/success, and no cross-cancellation between advisor and email draft.

**Step 2: Implement an in-process run registry**

Track only active Companion requests: `scenario`, `profile_id`, `started_at`, `phase`, and `AbortController`. Do not persist or resume these runs after app exit.

**Step 3: Connect stop buttons to real aborts**

Reuse `http_proxy_abortable`/`http_proxy_abort`. A stopped run must not write late cache or UI results. Display the selected Bot name and current phase while running.

**Step 4: Keep detached background jobs out of version 1**

If a future requirement is “continue after the window/app closes,” add a separate Hermes Kanban integration using its task status, heartbeat, and stop semantics. Do not introduce a Catfish background-task database.

**Step 5: Run tests and commit**

```bash
cd edge/companion-app
npm test -- --run
npm run build
git add src/lib src/tabs
git commit -m "feat(companion): add scenario scoped expert bot run controls"
```

---

### Task 9: Cross-platform and security verification

**Files:**
- Modify tests only unless verification exposes a defect
- Modify: `.github/workflows/ci.yml` only if a focused test is not already included
- Modify: `.github/workflows/build-windows-msi.yml` only if new runtime resources are added (none are expected)

**Step 1: Run all local checks**

```bash
cd edge/companion-app
npm test -- --run
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
cargo check --manifest-path src-tauri/Cargo.toml
cd ../..
bash scripts/check_file_sizes.sh --strict
git diff --check
```

**Step 2: Verify macOS behavior**

- Create two Bots.
- Bind different Bots to advisor and email draft.
- Switch the model picker and confirm an inheriting Bot changes model.
- Confirm a fixed Bot does not change model.
- Stop an advisor run and verify no late result writes cache.
- Refresh OAuth/service credentials and verify both managed Profiles remain healthy.

**Step 3: Verify Windows behavior**

- No Hermes/Profile operation opens a console window.
- Create/edit/bind/disable/delete behave the same as macOS.
- `%LOCALAPPDATA%` Hermes locations resolve through `catfish_paths`, with no `~/.hermes` hard-code.
- App startup does not create duplicate Companion processes or repeated gateway restarts.

**Step 4: Verify security boundaries**

- Path traversal and invalid ids are rejected.
- Default/unmanaged Profiles cannot be deleted.
- Secrets are absent from frontend payloads, logs, snapshots, and errors.
- Central Gateway audit shows the real employee identity and selected model.
- Disabled models/tools/skills remain blocked by central RBAC.

**Step 5: Commit final verification fixes**

```bash
git add -A
git commit -m "test(companion): verify multi-profile expert bots"
```

---

## Delivery sequence and effort

1. **Foundation:** Tasks 1-5. Multi-Profile policy, lifecycle, multiplex safety, and credentials. Estimated 3-4 engineering days.
2. **Usable product:** Tasks 6-8. Real settings UI, two scenario bindings, progress/stop. Estimated 2-3 engineering days.
3. **Release verification:** Task 9. macOS and Windows regression plus CI. Estimated 1-2 engineering days.

Expected total for a production-safe version: **6-9 engineering days**. The earlier one-toggle experiment is small; a real multi-Profile manager is not, because credential rotation, ownership-safe deletion, multiplex reconciliation, migration, and Windows behavior are part of correctness rather than optional polish.

## Acceptance criteria

- The Dashboard has a visible `专家 Bot` manager; it is no longer a hidden single toggle.
- The user can create at least two Hermes-backed Bots and bind each to a different supported scenario.
- Each run deterministically reports which Profile and model policy it used.
- Picker inheritance, fixed-model override, employee identity/RBAC, progress, and stop behavior are covered by automated tests.
- A credential refresh or Gateway URL change updates every registered managed Profile.
- An unmanaged Hermes Profile is never mutated or deleted without explicit adoption.
- Disabling the feature restores default-profile routing without deleting data.
- Normal chat and direct structured-output transforms remain unchanged.
- Frontend tests, Rust tests, builds, `git diff --check`, and strict file-size enforcement all pass.
