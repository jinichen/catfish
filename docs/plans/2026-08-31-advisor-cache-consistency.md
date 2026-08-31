# Advisor Cache Consistency Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent resolved or historical tasks from reappearing in the morning briefing, while preserving stable task IDs and chat summaries for genuinely current tasks.

**Architecture:** Treat the current briefing inputs (Reminders, Calendar, mail, and other explicitly current sources) as the only authority for whether a task is active. Treat `advisor_cache.json` as a performance and continuity cache, never as a task database. Add versioned source identity metadata to the cache, invalidate continuity data when the current input window changes, and apply a deterministic final guard before displaying or saving advisor output.

**Tech Stack:** TypeScript/React Companion client, existing Tauri cache commands, Vitest, Rust/Tauri integration tests where serialization changes are needed.

---

### Task 1: Define cache validity and source identity

**Files:**
- Modify: `edge/companion-app/src/lib/advisor_cache.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor_common.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor.ts`

**Steps:**

1. Add a cache schema version and a deterministic `inputFingerprint` containing normalized stable identities for the current Reminders, Calendar events, emails, the natural-week window, model, and advisor prompt/schema version.
2. Store `windowStart`, `windowEnd`, and source counts with the cache so a cache generated for a different week or data snapshot cannot be reused silently.
3. Implement one shared validity helper. A cache is continuity-valid only when its schema, natural-week window, fingerprint, model/prompt version, and age all match the current input.
4. Treat old caches without the new metadata as invalid for `result.mainTasks` injection. They may be migrated by the next successful save, but must not be trusted as active-task evidence.
5. Unit-test stable ordering, changed reminder/event/email identity, week-boundary changes, old-cache rejection, and same-input acceptance.

### Task 2: Stop stale cached tasks from entering the LLM context

**Files:**
- Modify: `edge/companion-app/src/lib/briefing_advisor.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor_quality.ts` if the evidence contract needs a small shared helper
- Modify: `edge/companion-app/src/lib/briefing_advisor_parse.ts`

**Steps:**

1. Before building `previousTasks`, validate the cache against the current input snapshot. On mismatch, do not inject cached `mainTasks` merely to reuse task UIDs.
2. When the snapshot matches, retain only tasks that have a current-source identity or an explicit current chat/manual-state identity. A historical task that exists only in `distilled_facts.md` or an old cache is not eligible for `previousTasks`.
3. Preserve task UID continuity for matching current tasks and preserve their chat summaries/statuses.
4. Keep `previousTasks` excluded from the evidence corpus so a stale cache can never self-authorize an advisor result.
5. Add regression tests for the exact Qin Shupeng case: an old cached task, no current Reminder/Event/Mail evidence, and a historical `resolved` fact must produce no active task.

### Task 3: Add a deterministic no-revival guard

**Files:**
- Modify: `edge/companion-app/src/lib/briefing_advisor_parse.ts`
- Modify: `edge/companion-app/src/lib/advisor_cache.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor.ts`

**Steps:**

1. Build an active-source index from the current input and attach source references to normalized task candidates where available (`reminder`, `event`, `email`, or explicit current task context).
2. Before accepting an advisor task, require either current evidence overlap or a valid continuity match to a current source. Do not use title equality alone to revive a task that has disappeared from all current sources.
3. Apply manual and chat statuses only after the candidate passes the current-source guard. `resolved` and `paused` remain deterministic drop decisions; an unknown historical status must not be treated as pending current work.
4. Ensure dropped historical tasks are not copied into the next cache’s `mainTasks` or `taskChatSummaries` unless they are retained as explicit, separately labeled handled history.
5. Test: disappeared source, changed title with same source ID, same title with a new source, resolved manual state, paused state, and a newly created current task.

### Task 4: Make cache writes replace, prune, and migrate atomically

**Files:**
- Modify: `edge/companion-app/src/lib/briefing_advisor.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor_summaries.ts`
- Modify: `edge/companion-app/src-tauri/src/commands/advisor_cache.rs` only if the new metadata or pruning needs Rust validation

**Steps:**

1. Save a complete current snapshot rather than merging old `mainTasks` into the new result.
2. Prune `taskChatSummaries` to current task UIDs plus explicitly retained current chat/manual records; never carry an unbounded historical summary set forward.
3. Make summary refresh use the same cache-validity and source-identity rules as the main advisor path, so it cannot reintroduce a task through a parallel write.
4. Keep the existing atomic temp-file-and-rename behavior and add schema validation for malformed/partial cache files.
5. Add a migration test proving that an old cache is ignored safely and replaced after a successful current run without touching Markdown, Reminders, Calendar, or the user’s chat history.

### Task 5: Remove the misleading “morning briefing script” assumption and document ownership

**Files:**
- Modify: relevant Companion advisor documentation
- Create: `docs/ADVISOR-CACHE-OPERATIONS.md` if no existing document covers this contract

**Steps:**

1. Document that `~/.catfish/advisor_cache.json` is a client-side derived cache, not the source of truth for active tasks.
2. Document the current source-of-truth boundary: current Reminders/Calendar/mail inputs plus explicit task-chat/manual status records; `distilled_facts.md` is historical context and cannot create a live Reminder.
3. Document invalidation triggers: source fingerprint change, natural-week boundary, model/prompt/schema change, manual completion/ignore/pause, and malformed cache.
4. Document the safe recovery command/action: clear only the derived advisor cache when diagnosing, then run a fresh briefing; never delete the database, Reminder data, Calendar data, or Markdown knowledge base as a cache fix.

### Task 6: Verify the complete fix

**Steps:**

1. Run the new and existing targeted advisor Vitest tests, including `briefing_advisor_resolved.test.ts`.
2. Run the Companion frontend build and the relevant Tauri/Rust tests.
3. Run `bash scripts/check_file_sizes.sh --strict` and confirm no changed source file crosses 800 lines.
4. Run `git diff --check`.
5. Perform a manual smoke test with: (a) an old resolved task present only in cache/history, (b) a current Reminder task, (c) completing that Reminder, and (d) the next briefing. Confirm the old task does not return and the current task retains its UID/chat status until resolved.

