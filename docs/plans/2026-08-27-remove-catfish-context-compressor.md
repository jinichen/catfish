# Remove Catfish Context Compressor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove Catfish's request-level semantic context compressor so Hermes is the only semantic compression owner.

**Architecture:** Keep Catfish's deterministic message preparation, tool-result truncation, tool-pair normalization, quota checks, and final context preflight. Remove only the Catfish LLM-based middle-history summarizer and its recursive loopback path. Hermes continues to own session history and semantic compaction.

**Tech Stack:** Python, FastAPI, pytest, ripgrep.

---

### Task 1: Remove the production compression hook

**Files:**
- Modify: `central/llm-gateway/src/catfish_gateway/app.py`
- Modify: `central/llm-gateway/src/catfish_gateway/chat_prepare.py`
- Test: `central/llm-gateway/tests/test_compression_hook_policy.py`

**Step 1:** Remove the `maybe_compress` call from the chat pipeline while preserving preparation, tool sanitization, quota enforcement, and dispatch order.

**Step 2:** Remove the Catfish-only `maybe_compress` helper and compression request-state writes from `chat_prepare.py`.

**Step 3:** Replace compression-presence tests with an invariant that the production chat pipeline contains no Catfish semantic compression hook.

### Task 2: Delete unreachable compressor implementation and dedicated tests

**Files:**
- Delete: `central/llm-gateway/src/catfish_gateway/conversation_compressor.py`
- Delete: `central/llm-gateway/tests/test_conversation_compressor.py`
- Delete or update: `central/llm-gateway/tests/test_compression_hook_policy.py`

**Step 1:** Confirm no remaining source import or runtime call exists.

**Step 2:** Delete only code and tests exclusive to the removed compressor. Keep shared deterministic preparation and tool handling.

### Task 3: Remove stale references without changing Hermes boundaries

**Files:**
- Modify: `central/llm-gateway/src/catfish_gateway/thinking_guard.py` if its helper becomes unused.
- Modify: `central/llm-gateway/src/catfish_gateway/context_preflight.py`, `message_normalize.py`, `config.py`, and relevant docs only where they describe Catfish semantic compression as active.

**Step 1:** Re-run repository-wide references and distinguish historical plans from active operational documentation.

**Step 2:** Update active comments/docs to state that semantic compression belongs to Hermes; do not remove historical records.

### Task 4: Verify

**Commands:**
- `bash scripts/check_file_sizes.sh --strict`
- `cd central/llm-gateway && PYTHONPATH=src python -m pytest -q`
- `rg -n "conversation_compressor|maybe_compress_messages|CATFISH_DISABLE_GATEWAY_COMPRESSION" central edge`

**Expected:** strict file-size check passes; gateway tests pass; no active Catfish source/runtime reference remains; deterministic preparation, tools, quota, and preflight tests remain green.
