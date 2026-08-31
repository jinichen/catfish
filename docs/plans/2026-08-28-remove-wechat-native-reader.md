# Remove WeChat Native Reader Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 删除当前无法安全落地的本机微信原生读取分支，只保留用户主动选择导出文件的只读分析能力。

**Architecture:** Companion 只负责选择 JSON、JSONL 或 CSV 导出文件并绑定当前 Picker；Tool Bridge 只调用独立导出读取器。移除 native provider 路径、命令、状态字段、协议文档和相关测试，避免出现不可用按钮与死代码。

**Tech Stack:** React/TypeScript、Tauri/Rust、Python Tool Bridge、pytest、Cargo。

---

### Task 1: Remove native branches from the data path

**Files:**
- Modify: `edge/companion-app/src/tabs/Dashboard/WeChatArchiveCard.tsx`
- Modify: `edge/companion-app/src-tauri/src/commands/wechat_archive.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/invoke_handler.rs`
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/wechat_archive.py`
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tool_schemas_wechat.py`

Remove native status fields, native selection commands, native safety flags, native source branching, and native-provider wording. Keep export-file validation, Picker authorization, doctor checks, and bounded JSON results unchanged.

### Task 2: Remove native-only tests and documentation

**Files:**
- Modify: `edge/tool-bridge/tests/test_wechat_archive.py`
- Modify: `edge/companion-app/src-tauri/src/commands/wechat_archive.rs`
- Modify: `edge/wechat-reader/README.md`
- Delete: `edge/wechat-reader/NATIVE_PROVIDER.md`
- Modify: `docs/plans/2026-08-28-wechat-history-analysis.md`

Delete tests and documentation that advertise or validate an unimplemented native provider. Preserve export-reader documentation and tests.

### Task 3: Verify the remaining export flow

Run:

```bash
edge/tool-bridge/venv/bin/pytest -q edge/tool-bridge/tests/test_wechat_archive.py
PYTHONPATH=edge/wechat-reader/src edge/tool-bridge/venv/bin/pytest -q edge/wechat-reader/tests
npm --prefix edge/companion-app run build
cargo check --manifest-path edge/companion-app/src-tauri/Cargo.toml
bash scripts/check_file_sizes.sh --strict
git diff --check
```

Expected: all relevant tests and builds pass; `rg` finds no executable native-reader integration in Companion or Tool Bridge.
