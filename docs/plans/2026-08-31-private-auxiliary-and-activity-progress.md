# Private Auxiliary Routing and Activity Progress Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure a private-model Companion request can never silently fall back to a public auxiliary model, and replace the misleading `第 1/90 轮` display with truthful activity status.

**Architecture:** Keep Catfish's Hermes integration in the existing plugin layer so Hermes upgrades remain clean. The plugin will suppress LLM-enhanced titles for internal service calls and private models while retaining Hermes' local instant title, and it will fail closed for every auxiliary fallback when the active model is `catfish-private-*`. Companion will continue reading Hermes activity snapshots but treat `max_iterations` as a safety ceiling rather than progress.

**Tech Stack:** Python monkey patches and pytest for Hermes integration; React/TypeScript and Vitest for Companion activity rendering.

---

### Task 1: Pin private auxiliary calls to the selected private model

**Files:**
- Modify: `edge/hermes-plugins/catfish-xcatfish-user/plugin_runtime.py`
- Test: `edge/hermes-plugins/catfish-xcatfish-user/tests/test_p47_private_auxiliary.py`

**Step 1: Write failing tests**

Cover all auxiliary fallback entry points: configured task fallback, main fallback and built-in provider discovery. Assert that they return no candidate while a `catfish-private-*` runtime is active, and retain upstream behavior for public models.

**Step 2: Run the focused test and verify failure**

Run: `pytest -q edge/hermes-plugins/catfish-xcatfish-user/tests/test_p47_private_auxiliary.py`

Expected: FAIL because P47 is not installed yet.

**Step 3: Implement the privacy guard**

Wrap Hermes' auxiliary execution context and fallback selectors. In a private runtime, allow the original selected model request but return no fallback candidate after failure. Never substitute `catfish-auto`, OpenRouter, Nous or an API-key provider.

**Step 4: Run focused tests**

Run: `pytest -q edge/hermes-plugins/catfish-xcatfish-user/tests/test_p47_private_auxiliary.py`

Expected: PASS.

### Task 2: Stop cosmetic title LLM calls for private and service requests

**Files:**
- Modify: `edge/hermes-plugins/catfish-xcatfish-user/plugin_session.py`
- Modify: `edge/hermes-plugins/catfish-xcatfish-user/session_registry.py`
- Test: `edge/hermes-plugins/catfish-xcatfish-user/tests/test_p47_private_auxiliary.py`

**Step 1: Add failing title tests**

Assert that private-model and Companion service sessions do not call Hermes' `auto_title_session`, while public `companion-chat` sessions still do.

**Step 2: Implement local-title-only behavior**

Record request source by session alongside the Catfish user. In the existing auto-title wrapper, return before the auxiliary LLM call for `catfish-private-*` and internal service sources. Hermes has already written its deterministic instant title before this function is dispatched, so sessions remain named without network traffic.

**Step 3: Run focused tests**

Run: `pytest -q edge/hermes-plugins/catfish-xcatfish-user/tests/test_p47_private_auxiliary.py`

Expected: PASS.

### Task 3: Replace the fake 90-round progress indicator

**Files:**
- Modify: `edge/companion-app/src/lib/agentActivity.ts`
- Modify: `edge/companion-app/src/lib/agentActivity.test.ts`

**Step 1: Update tests first**

Assert that `api_call_count` and `max_iterations` are never rendered as progress; the UI should show only real activity descriptions, tool names, and an idle hint when applicable.

**Step 2: Implement truthful rendering**

Ignore `api_call_count` and `max_iterations` in user-facing copy. Show only the translated activity/tool text and the idle hint when appropriate; these counters remain available in raw diagnostics.

**Step 3: Run Companion tests**

Run: `npm test -- --run src/lib/agentActivity.test.ts`

Expected: PASS.

### Task 4: Deploy and verify on this Mac

**Files:**
- No source changes.

**Step 1: Run project checks**

Run focused Hermes and Companion tests, then `bash scripts/check_file_sizes.sh --strict`.

Expected: all checks PASS and no source file reaches 800 lines.

**Step 2: Deploy the plugin and restart Hermes**

Run the existing Catfish plugin deployment path, then restart the local Hermes gateway.

**Step 3: Verify logs and behavior**

Trigger a private-model morning briefing. Confirm there is no `Auxiliary title_generation` network call, no `catfish-auto`/DeepSeek request caused by that turn, and the UI no longer contains `/90`.
