# Configurable Agent Loop Guard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent long-running agent requests from losing their thinking mode or looping indefinitely, using explicit request/config policy instead of model-name hardcoding.

**Architecture:** Keep model-level `param_overrides` for genuinely universal provider requirements, and add an explicit scope for overrides that are only valid when `tool_choice` is forced. Add a gateway request guard with configurable limits for accumulated tool messages/calls, so a runaway client loop receives a clear error before another expensive upstream call. The guard will be provider/model agnostic and will not choose fallback models.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, LiteLLM, Pytest, YAML configuration.

---

### Task 1: Define configuration semantics for scoped parameter overrides

**Files:**
- Modify: `central/llm-gateway/src/catfish_gateway/config.py`
- Modify: `central/llm-gateway/src/catfish_gateway/config_providers.py`
- Modify: `central/llm-gateway/tests/test_provider_split.py`

**Step 1: Write the failing tests**

Cover that `UpstreamConfig` accepts an explicit `param_overrides_scope` with a default of `all`, accepts `forced_tool_choice`, and preserves the field through provider merge/model serialization.

**Step 2: Run the focused tests to verify they fail**

Run: `PYTHONPATH=src venv/bin/pytest -q tests/test_provider_split.py`

Expected: FAIL because the scope field does not exist or is not preserved.

**Step 3: Implement the minimal configuration fields**

Add a typed scope field with a safe default preserving current behavior. Do not infer scope from a model name, provider name, or timeout. Preserve the field in provider split/merge and validation paths.

**Step 4: Run the focused tests to verify they pass**

Run: `PYTHONPATH=src venv/bin/pytest -q tests/test_provider_split.py`

Expected: PASS.

### Task 2: Apply scoped overrides at request construction

**Files:**
- Modify: `central/llm-gateway/src/catfish_gateway/llm_params.py`
- Modify: `central/llm-gateway/src/catfish_gateway/thinking_guard.py`
- Modify: `central/llm-gateway/tests/test_litellm_params_max_tokens.py`
- Modify: `central/llm-gateway/tests/test_thinking_guard.py`

**Step 1: Write failing tests**

Verify an override scoped to `forced_tool_choice` is not applied to `tool_choice=auto`, is applied to `required`/named tool choice, and that universal overrides remain universal. Verify the existing thinking guard still supplies provider-specific parameters only for forced tool choice.

**Step 2: Run focused tests to verify failure**

Run: `PYTHONPATH=src venv/bin/pytest -q tests/test_litellm_params_max_tokens.py tests/test_thinking_guard.py`

Expected: FAIL for the new scoped-override cases because all current overrides are unconditional.

**Step 3: Implement request-scoped merge**

Introduce a small, provider-agnostic helper that decides whether a model override applies from the explicit scope and the request's `tool_choice`. Apply only eligible overrides, log skipped scoped overrides, then run the existing thinking guard. Keep nested dictionaries intact and preserve all existing override behavior for the default `all` scope.

**Step 4: Run focused tests**

Run: `PYTHONPATH=src venv/bin/pytest -q tests/test_litellm_params_max_tokens.py tests/test_thinking_guard.py`

Expected: PASS.

### Task 3: Add a configurable agent-loop circuit breaker

**Files:**
- Create: `central/llm-gateway/src/catfish_gateway/tool_loop_guard.py`
- Modify: `central/llm-gateway/src/catfish_gateway/config.py`
- Modify: `central/llm-gateway/src/catfish_gateway/app.py`
- Create: `central/llm-gateway/tests/test_tool_loop_guard.py`

**Step 1: Write failing tests**

Test counting assistant tool calls and tool result messages, allowing normal short loops, and raising a clear HTTP 4xx error when either configured limit is exceeded. Test that missing limits use configuration defaults and that requests without tool messages are unaffected.

**Step 2: Run the new tests to verify failure**

Run: `PYTHONPATH=src venv/bin/pytest -q tests/test_tool_loop_guard.py`

Expected: FAIL because the guard and configuration fields do not exist.

**Step 3: Implement the guard**

Add configuration fields for the maximum accumulated tool messages and assistant tool calls per request, with documented environment-variable overrides if the project’s config convention supports them. Invoke the guard after message preparation/sanitization and before any LiteLLM call. Return a client-visible error that explains the loop was stopped and asks the caller to retry with a smaller task; never select another model or silently mutate the transcript.

**Step 4: Run the new tests**

Run: `PYTHONPATH=src venv/bin/pytest -q tests/test_tool_loop_guard.py`

Expected: PASS.

### Task 4: Update seeded configuration and operational documentation

**Files:**
- Modify: `central/llm-gateway/config/models.yaml.example`
- Modify: `central/llm-gateway/config/models.yaml`
- Modify: `central/llm-gateway/README.md` (or the nearest gateway configuration guide)

**Step 1: Add explicit scope to affected seeded configurations**

Mark thinking-disabling overrides that are only valid for structured single-shot calls with `param_overrides_scope: forced_tool_choice`. Leave provider requirements that are genuinely universal at `all`.

**Step 2: Document runtime source of truth**

Document that admin-edited database model rows must be updated to the same explicit scope; YAML seeding does not overwrite existing database rows. Document the loop-limit settings and the meaning of the resulting error.

**Step 3: Validate configuration parsing**

Run: `PYTHONPATH=src venv/bin/pytest -q tests/test_config_interpolate.py tests/test_model_store_assembly.py tests/test_provider_split.py`

Expected: PASS.

### Task 5: Full verification and diff review

**Files:**
- Test: all gateway tests.

**Step 1: Run focused regression tests**

Run: `PYTHONPATH=src venv/bin/pytest -q tests/test_thinking_guard.py tests/test_litellm_params_max_tokens.py tests/test_provider_split.py tests/test_tool_loop_guard.py tests/test_gateway_chat_integration.py`

Expected: PASS.

**Step 2: Run the project size guard**

Run: `bash scripts/check_file_sizes.sh --strict`

Expected: exit 0; no source file reaches 800 lines.

**Step 3: Review the final diff and working tree**

Run: `git diff --check && git status --short && git diff --stat`

Expected: no whitespace errors; only intended source, test, configuration, documentation, and plan changes are present.
