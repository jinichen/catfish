# Local Task Library Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make a local structured task library the source of truth for current actions, with Reminders as a synchronized personal reminder projection rather than the task database.

**Architecture:** Add a small SQLite-backed task library in the local tool bridge. Explicit actions are written through a task tool; existing Reminders data is imported on read, and current-week actions can be projected back to Reminders with a stable marker for idempotency. The Companion morning briefing reads a task-list tool backed by this library. Background execution tasks remain separate.

**Tech Stack:** Python stdlib `sqlite3`, existing native tool bridge, TypeScript/Tauri tool bridge wrapper, pytest and Vitest.

---

### Task 1: Add the local task-library store

**Files:**
- Create: `edge/tool-bridge/src/catfish_tool_bridge/task_library.py`
- Test: `edge/tool-bridge/tests/test_task_library.py`

**Steps:**
1. Write tests for schema creation, idempotent upsert, status updates, local-week filtering, overdue filtering, and isolated test database path.
2. Run the focused tests and verify they fail because the module does not exist.
3. Implement a compact SQLite store with `task_id`, title, status, due date, body, priority, source, source ID, list name, and timestamps.
4. Add `upsert_task`, `upsert_reminders`, and `list_tasks` functions. Use `source:<source_id>` as the fallback stable ID and keep all date comparisons in local time.
5. Run focused tests and verify they pass.

### Task 2: Expose task-library native tools and import/sync Reminders

**Files:**
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tool_schemas_task.py`
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py`
- Test: `edge/tool-bridge/tests/test_task_library.py`

**Steps:**
1. Add `catfish_create_task`, `catfish_list_tasks`, and `catfish_sync_tasks_to_reminders` schemas and document that the task library is the local task source of truth.
2. Add dispatch to task-library implementations.
3. On macOS, import the current Reminders snapshot before querying; on other platforms, return local task records without invoking macOS-only APIs.
4. Make outbound sync add a stable task marker and skip already-projected actions, so weekly refreshes are idempotent.
5. Preserve the existing Reminders tools for direct client operations and backward compatibility.
6. Test native schema registration, dispatch, macOS import/sync with mocked data, and Windows/non-macOS local-only behavior.

### Task 3: Route morning briefing TODO reads through the task library

**Files:**
- Modify: `edge/companion-app/src/lib/tauri_briefing.ts`
- Test: `edge/companion-app/src/lib/tauri_briefing.test.ts`

**Steps:**
1. Add a wrapper that calls `catfish_list_tasks` and maps task records to the existing `ReminderTodo` shape.
2. Keep the existing error contract so the briefing diagnostics still show a source failure.
3. Update `remindersWeekFetch` to use the task-library wrapper while retaining its name for caller compatibility.
4. Add tests asserting the task tool name, scope, and field mapping.

### Task 4: Verify regression safety

**Files:**
- Existing modified files: `edge/companion-app/src/lib/briefing_sources.ts`, `edge/tool-bridge/src/catfish_tool_bridge/reminders.py`, `edge/tool-bridge/tests/test_reminders.py`

**Steps:**
1. Run task-library and Reminders focused tests.
2. Run the full tool-bridge test suite and frontend Vitest/build checks.
3. Run `bash scripts/check_file_sizes.sh --strict` and `git diff --check`.
4. Review the diff to ensure no report parser or background task manager was made the task source, and leave commit/push for explicit user approval.
