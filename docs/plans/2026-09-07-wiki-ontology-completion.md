# Wiki 本体闭环补全 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让本体关系整理工作台完整暴露并修复断链、缺少关系类型和歧义关系，同时保持人工确认、不自动猜测。

**Architecture:** 复用现有 `resolveWikiRef` 的唯一解析口径，在前端纯函数层把每条异常关系拆成独立整理任务；工作台确认时只替换当前异常关系，其他已确认关系原样保留。行动关联继续使用已提交的内置注册表和受控 skill bridge，不引入中央端依赖。

**Tech Stack:** React + TypeScript + Vitest；Tauri Rust 现有 Wiki read/write API；Markdown frontmatter。

---

### Task 1: 将异常关系纳入本体任务模型

**Files:**
- Modify: `edge/companion-app/src/tabs/Wiki/wikiRelationshipTasks.ts`
- Modify: `edge/companion-app/src/tabs/Wiki/wikiRelationshipTasks.test.ts`

**Step 1: Write the failing tests**

- 断链关系生成单独任务。
- 缺少 `rel` 的关系生成单独任务。
- 正常关系不生成异常任务。

**Step 2: Run the focused test**

Run: `npm test -- --run src/tabs/Wiki/wikiRelationshipTasks.test.ts`

Expected: new assertions fail before implementation.

**Step 3: Implement the minimal model**

- 新增 `broken` task kind。
- 每条异常关系生成一个任务，并保存 `relationName`。
- 使用 `resolveWikiRef` 判断 `miss` / `ambiguous`，不使用子串猜测。

**Step 4: Run the focused test**

Expected: all relationship task tests pass.

### Task 2: 让工作台展示并精确修复异常关系

**Files:**
- Modify: `edge/companion-app/src/tabs/Wiki/WikiOrganizer.tsx`
- Modify: `edge/companion-app/src/tabs/Wiki/WikiRelationshipWorkbench.tsx`
- Modify: `edge/companion-app/src/styles/globals.css`

**Step 1: Add the visible category**

- 在左侧摘要增加“关系异常”数量和筛选。
- 显示断链、歧义、缺少关系类型的具体原因。

**Step 2: Replace only the selected problem relation**

- 确认关系时按 `relationName` 移除当前异常关系。
- 保留该条目其他关系。
- 写入新的 typed relation，并将 `ontology_status` 置为 `active`。

**Step 3: Run frontend tests and build**

Run: `npm test -- --run`

Run: `npm run build`

Expected: all tests pass and Vite production build succeeds.

### Task 3: Repository-level verification

**Files:**
- No source changes.

**Step 1: Run size guard**

Run: `bash scripts/check_file_sizes.sh --strict`

Expected: no source file reaches 800 lines.

**Step 2: Run Wiki Rust tests**

Run: `cargo test --manifest-path edge/companion-app/src-tauri/Cargo.toml wiki_ontology wiki_actions`

Expected: all selected tests pass.

**Step 3: Review diff**

Run: `git diff --check`

Expected: no whitespace errors and no unrelated staged files included in the eventual commit.
