# Output Budget Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop long conversations from repeatedly clipping tool-call output to 512 tokens.

**Architecture:** Share fixed-safety context capacity between admission and output allocation. Reject exhausted capacity rather than manufacturing a positive budget. Preserve real model output limits and explicit small requests.

**Tech Stack:** Python, FastAPI, pytest.

## Steps

1. Add regression tests in `central/llm-gateway/tests/test_output_budget_regression.py`: a 100000-token prompt in a 128000 window must allow 8192/16384, and cap 32768 at 25952; depleted capacity must reject.
2. Run `central/llm-gateway/venv/bin/pytest -q central/llm-gateway/tests/test_output_budget_regression.py` and confirm failure on current code.
3. Share capacity calculation in `central/llm-gateway/src/catfish_gateway/context_preflight.py`; use it in `llm_params.py`. Update obsolete floor assertions and admission boundaries.
4. Run gateway regression tests and `bash scripts/check_file_sizes.sh --strict`; inspect diff. No deployment or commit without user request.
