# Knowledge Base Integrity Repair Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make local knowledge retrieval deterministic, current-only by default, provenance-aware, and safe against stale or deprecated records.

**Architecture:** Keep raw materials and historical records as source data, but exclude deprecated and non-current records from default answer retrieval. Keep the existing resolver able to resolve explicit historical links. Apply a reversible local migration that marks stale facts and repairs only confirmed metadata and links; explicitly requested low-value orphan concepts may be deleted only after backup.

**Tech Stack:** Python, Markdown frontmatter, BM25 wiki search, existing wiki health/lint scripts, pytest.

---

### Task 1: Lock down current behavior with tests — Completed

**Files:**
- Modify: `edge/tool-bridge/tests/test_wiki_search.py`
- Modify: `edge/hermes-plugins/catfish-memory/tests/test_wiki_resolve.py`

**Step 1:** Add tests proving `deprecated: true` files are not returned by `catfish_wiki_search`, while deprecated nodes remain resolvable for explicit historical links.

**Step 2:** Add tests for `status: expired` exclusion from default search. Historical retrieval remains explicit through `wiki_read`; no new history mode was invented.

**Step 3:** Focused tests passed: tool-bridge wiki search and Hermes memory wiki resolver/migration tests.

### Task 2: Apply current-only wiki search filtering — Completed

**Files:**
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/wiki_search.py`

**Step 1:** Parse `deprecated` and lifecycle status in wiki search metadata.

**Step 2:** Filter deprecated and non-current lifecycle statuses from default search; retain the existing explicit resolver behavior for old links.

**Step 3:** Keep own wiki and optional department wiki as separate sources, without scanning raw sources or backup directories.

**Step 4:** Run the focused tests and verify they pass.

### Task 3: Make health/lint checks use current-only rules — Completed

**Files:**
- Modify: `edge/catfish-cli/scripts/lint_wiki.py`
- Modify: `edge/hermes-plugins/catfish-memory/wiki_health.py`
- Add or modify: shared parser/resolver module under `edge/hermes-plugins/catfish-memory/`

**Step 1:** Make both checks ignore tombstones as active records but still validate their canonical targets.

**Step 2:** Keep existing link resolution and report ambiguity separately; exclude deprecated entries from active counts, orphan checks, and dead-end checks.

**Step 3:** The migration repairs the confirmed reversed date and known expired record. Missing provenance, out-of-vocabulary types, ambiguous facts, and contradictory statistics remain reported for human review rather than being guessed.

**Step 4:** Run both checks against a temporary fixture and the real local wiki in read-only mode.

### Task 4: Add and run a reversible local migration — Completed

**Files:**
- Add: `scripts/migrate_wiki_integrity.py`
- Add: `edge/hermes-plugins/catfish-memory/tests/test_migrate_wiki_integrity.py`

**Step 1:** Create a timestamped backup manifest and never remove raw/source files.

**Step 2:** Repair confirmed links, the reversed email-rule date, the stale CMMI statement, and the known expired record; add the missing department entity from the confirmed official organization source.

**Step 3:** Leave ambiguous facts unchanged but emit a migration report requiring human confirmation.

**Step 4:** Run the migration only after confirming the target is `/Users/chenhongbo/.catfish/wiki`.

### Task 5: Rebuild and verify retrieval — Completed

**Step 1:** Narrow `search-scope.yaml` so default local search does not include the entire `~/.catfish` tree or backup/archive/raw directories.

**Step 2:** Clean stale search index entries after the scope change.

**Step 3:** Run wiki health, wiki lint, focused tests, full relevant tests, and `bash scripts/check_file_sizes.sh --strict`.

**Step 4:** Baseline before the entity merge was 235 active wiki nodes, 30 deprecated nodes, 0 broken links, 13 orphan concepts, and 35 ambiguous relations.

### Task 6: Merge the confirmed duplicate entity and remove requested orphan concepts — Completed

**Step 1:** Merge the scoped 2026-08-15 “福富” qualification statistic into `中电福富信息科技有限公司`, preserving aliases `中电福富` and `福富` for existing references.

**Step 2:** Back up and delete the duplicate `wiki/entities/福富.md` and the 13 user-specified orphan concepts. No raw/source or deprecated history files were touched.

**Step 3:** Repair only the four resulting stale links: three confirmed same-concept mappings and one removal where no reliable replacement exists.

**Step 4:** Fix the lint parser’s `related: [[...]]` handling and active-kind counts so it no longer reports false orphan or inconsistent totals.

**Step 5:** Final state: 221 active wiki files (153 entities, 66 concepts, 2 queries), 30 deprecated files, 0 broken links, 0 orphan concepts, and 0 ambiguous relations. Four dead-end files remain non-blocking warnings. The migration backups are under `/Users/chenhongbo/.catfish/.wiki-integrity-backups/`.
