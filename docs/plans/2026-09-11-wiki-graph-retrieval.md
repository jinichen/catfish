# Wiki Graph Retrieval Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist Markdown wiki relationships in SQLite and use one-hop graph expansion to improve hybrid knowledge retrieval.

**Architecture:** Markdown remains the source of truth. A lightweight `~/.catfish/wiki_graph.db` mirror stores canonical nodes, resolved typed edges, unresolved references, and sync metadata. The mirror is incrementally synchronized by file fingerprint, while text, hybrid, semantic, and briefing retrieval share one bounded one-hop expansion/ranking layer; failures fall back to the original retrieval result.

**Tech Stack:** Rust, Tauri commands, rusqlite, existing wiki frontmatter parser, existing BM25/BGE-M3/RRF retrieval.

---

### Task 1: Make the SQLite graph mirror incremental and diagnosable

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/wiki_graph.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/mod.rs`

**Steps:**

1. Extend the schema with source fingerprints, unresolved references, and sync metadata while preserving existing databases.
2. Sync only new/changed/deleted Markdown files and rebuild edges only for affected sources; retain a transaction and busy timeout.
3. Resolve `related` names using normalized title, slug, aliases, and exact relative path matches; record unresolved and ambiguous references instead of silently losing diagnostics.
4. Add a read-only `wiki_graph_status` Tauri command returning node/edge/unresolved counts and last sync details.
5. Add unit tests for incremental no-op sync, changed/deleted files, unresolved/ambiguous references, and status output.

### Task 2: Improve graph-aware candidate expansion and ranking

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/wiki_graph.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/wiki_search.rs`

**Steps:**

1. Load one-hop neighbors for the initial result paths from SQLite and apply relation-type weights.
2. Penalize high-degree hub expansion and cap candidates per seed so dense graphs do not flood results.
3. Add unseen neighboring documents with bounded graph contribution based on seed rank, edge type, and direct-edge count.
4. Mark expanded results with `matched_in: ["graph-1hop"]` and merge that marker for existing results.
5. Keep original retrieval order dominant when graph data is unavailable or sync fails.
6. Add tests proving direct neighbors, type weighting, hub caps, and bounded scores.

### Task 3: Share graph retrieval with semantic and briefing paths

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/wiki_embed.rs`
- Modify: `edge/companion-app/src/lib/tauri_wiki.ts`
- Modify: `edge/companion-app/src/tabs/Wiki/WikiTree.tsx`

**Steps:**

1. Extend semantic hit metadata with `matched_in` while preserving old JSON consumers.
2. Apply the same graph expansion to standalone semantic results.
3. Keep the early-morning briefing path on the shared semantic API so it receives graph-enriched context.
4. Display graph match reasons consistently in the search result UI.
5. Add frontend tests for graph match labels and semantic-hit compatibility.

### Task 4: Add user-visible graph maintenance status

**Files:**
- Modify: `edge/companion-app/src/lib/tauri_wiki.ts`
- Modify: `edge/companion-app/src/tabs/Wiki/WikiTree.tsx`
- Modify: `edge/companion-app/src/tabs/Wiki/WikiTreeSearchResults.tsx`
- Modify: `edge/companion-app/src/styles/globals.css`

**Steps:**

1. Load graph status alongside the wiki file list.
2. Show compact node/edge/unresolved counts and last sync state without obstructing normal search.
3. Link unresolved count to a clear diagnostic message, not a hidden failure.
4. Add loading/error states that never block ordinary wiki browsing.

### Task 5: Verify the integration

**Files:**
- Modify: none

**Steps:**

1. Run targeted Rust graph, search, and embedding tests.
2. Run the complete Rust library test suite.
3. Run frontend tests and production build.
4. Run the repository file-size check required by `AGENTS.md`.
5. Inspect the final diff and verify macOS/Windows path handling remains platform-neutral.
