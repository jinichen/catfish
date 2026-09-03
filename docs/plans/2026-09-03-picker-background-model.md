# Picker Background Model Consistency Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure every catfish-memory background LLM request resolves the current Companion picker model at send time, so a queued job cannot continue using a model captured by an old session.

**Architecture:** Keep the existing picker/yaml/env resolver as the single model authority, but stop passing a resolved model into fire-and-forget workers. The worker will resolve immediately before each LLM stage and record the decision context in logs. Add deterministic tests covering a picker change after trigger and the no-model path.

**Tech Stack:** Python, pytest, Hermes catfish-memory plugin, JSON picker state, logging.

---

### Task 1: Define the stale-background-model regression

**Files:**
- Modify: `edge/hermes-plugins/catfish-memory/tests/test_sync_turn.py`

**Step 1: Write the failing test**

Add a test that triggers a synchronous background worker while the picker state initially names an old public model, changes `picker_state.json` to the private model before the worker executes, and asserts the LLM receives the private model.

**Step 2: Run the focused test**

Run: `central/llm-gateway/venv/bin/pytest -q edge/hermes-plugins/catfish-memory/tests/test_sync_turn.py -k picker`

Expected: FAIL because `_spawn_summarize_thread` currently passes the model resolved at trigger time.

### Task 2: Resolve the picker at background send time

**Files:**
- Modify: `edge/hermes-plugins/catfish-memory/catfish_memory_distill.py`

**Step 1: Remove the stale model parameter from the worker boundary**

Keep the trigger-time resolution only as an enablement check, but do not pass that value into `_spawn_summarize_thread` or `_summarize_and_distill_async`.

**Step 2: Add a send-time resolver**

Resolve through `_get_summarize_model()` inside the worker immediately before each LLM stage. If no model is available, log a skip with the session and return without making a gateway request.

**Step 3: Add decision logging**

Log `source=plugin:memory-distill`, session id, trigger model, send-time model, and whether the model changed. Do not log tokens or message contents.

**Step 4: Run the regression test**

Run: `central/llm-gateway/venv/bin/pytest -q edge/hermes-plugins/catfish-memory/tests/test_sync_turn.py -k picker`

Expected: PASS.

### Task 3: Cover force-flush and missing picker behavior

**Files:**
- Modify: `edge/hermes-plugins/catfish-memory/tests/test_sync_turn.py`

**Step 1: Add a force-flush test**

Verify `_force_flush` also uses the worker’s current model rather than a model captured before the picker changes.

**Step 2: Add a missing-model test**

Verify a worker with no picker, yaml, role, or environment model exits cleanly and does not call the LLM helper.

**Step 3: Run all memory plugin tests**

Run: `central/llm-gateway/venv/bin/pytest -q edge/hermes-plugins/catfish-memory/tests`

Expected: PASS.

### Task 4: Fix the explicit memory tool override configuration

**Files:**
- Modify: the Hermes configuration template/source used by Companion bootstrap (locate the repository-owned config template before editing)

**Step 1: Add the `catfish-memory` entry with `allow_tool_override: true` only if the product contract is to replace Hermes’ built-in memory tool.**

This removes the repeated startup warning and makes the configured memory provider behavior match the intended plugin takeover.

**Step 2: Add or update a configuration test**

Assert the generated config contains the explicit entry and does not rely on a developer machine’s `~/.hermes/config.yaml`.

### Task 5: Repository self-check

**Step 1: Run size and diff checks**

Run: `bash scripts/check_file_sizes.sh --strict`

Run: `git diff --check`

**Step 2: Run focused and full relevant tests**

Run the memory plugin test suite and any Companion/Hermes integration test that covers picker model propagation.

**Step 3: Review the diff**

Confirm no model name is hard-coded, no fallback chain is changed, and no credentials or user content are added to logs.

**Step 4: Commit**

```bash
git add edge/hermes-plugins/catfish-memory/catfish_memory_distill.py edge/hermes-plugins/catfish-memory/tests/test_sync_turn.py <resolved-config-template>
git commit -m "fix(memory): resolve picker model before background sends"
```
