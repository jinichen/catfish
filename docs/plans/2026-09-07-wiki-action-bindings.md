# Wiki Action Bindings Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement the plan task-by-task.

**Goal:** 让普通员工可以在知识库中从可读的行动列表直接选择并执行行动；注册表由 Companion 内置，本机覆盖仅作为高级扩展，不依赖中央端。

**Architecture:** Wiki Markdown frontmatter 只保存 `action_refs: ["..."]`。Tauri 侧先解析随二进制发布的内置注册表，再合并可选的本机高级覆盖文件；显式 `CATFISH_ACTION_REGISTRY` 仍可用于开发和测试。前端通过 Tauri 命令读取可用行动并用下拉选择器绑定，隐藏 action_id/skill_path；执行仍需明确确认并复用现有 `catfish_run_skill` bridge，绝不引入中央端依赖。

**Tech Stack:** Rust/Tauri, serde_yaml, React/TypeScript, Vitest, Cargo tests.

---

### Task 1: Add the built-in action registry and layered loader

**Files:**
- Create `edge/contracts/action_registry.default.yaml`
- Modify `edge/contracts/action_registry.example.yaml`
- Create `edge/companion-app/src-tauri/src/commands/wiki_actions.rs`
- Modify `edge/companion-app/src-tauri/src/commands/mod.rs`
- Modify `edge/companion-app/src-tauri/src/commands/invoke_handler.rs`

**Work:**
- Define versioned registry fields: `id`, `label`, `description`, `executor`, `skill_path`, `approval`, `enabled`, `platforms`.
- Parse the packaged default registry on every load.
- Merge optional `~/.catfish/action-registry.yaml` entries by action id; an explicit `CATFISH_ACTION_REGISTRY` remains a full override for development/tests.
- Expose a list command for the UI; keep unknown or unsupported actions unavailable.
- Return definitions for requested refs, including `available` and a reason; reject malformed IDs and unsupported executors for direct execution.
- Expose a command that executes only registered `skill` actions through the existing tool bridge RPC, with explicit approval enforced by the command input.
- Add unit tests for missing registry, unknown action, platform filtering, malformed entries, and skill execution argument construction.

### Task 2: Persist and expose action references without changing Wiki read compatibility

**Files:**
- Modify `edge/companion-app/src-tauri/src/commands/wiki_write.rs`
- Modify `edge/companion-app/src/lib/tauri_wiki.ts`
- Create `edge/companion-app/src/lib/wikiActions.ts`
- Create `edge/companion-app/src/lib/wikiActions.test.ts`

**Work:**
- Keep old Wiki frontmatter readable; add helpers that parse and update only the `action_refs` list.
- Add typed registry/action result interfaces and invoke wrappers.
- Preserve unrelated frontmatter lines and avoid duplicate action IDs.

### Task 3: Add employee-facing action selection to the Wiki workbench

**Files:**
- Modify `edge/companion-app/src/tabs/Wiki/WikiRelationshipWorkbench.tsx`
- Modify the relevant stylesheet under `edge/companion-app/src`

**Work:**
- Show “可执行行动” separately from graph relations.
- Load the action catalog and allow an employee to add/remove actions by label, never by typing IDs.
- Keep an advanced diagnostic hint only for unavailable legacy bindings.
- Resolve registry definitions and show unavailable reasons.
- Require confirmation for actions marked `approval: required`, then use the existing controlled execution path.
- Show running/success/error states and never trigger actions on tab switch or file load.

### Task 4: Verify the closed loop

**Commands:**
- `bash scripts/check_file_sizes.sh --strict`
- `cargo fmt --manifest-path edge/companion-app/src-tauri/Cargo.toml -- --check`
- `cargo test --manifest-path edge/companion-app/src-tauri/Cargo.toml wiki_actions`
- `npm --prefix edge/companion-app test -- --run src/lib/wikiActions.test.ts`
- `git diff --check`

**Checks:**
- Existing Wiki relation tests remain green.
- No source file crosses 800 lines.
- Action execution is impossible without a matching registry entry and uses no direct shell/model call.
- A clean machine with no `~/.catfish/action-registry.yaml` still sees the packaged action catalog.
- Report any environment-only test limitation separately.
