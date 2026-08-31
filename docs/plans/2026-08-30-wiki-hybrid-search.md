# Wiki Hybrid Search Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade Companion knowledge-base search from separate keyword/whole-document semantic modes to chunk-level lexical + BGE-M3 semantic retrieval fused with RRF, without changing the embedding model or user Markdown data.

**Architecture:** Extract search code from the already oversized `wiki_read.rs` into a dedicated `wiki_search.rs` module. Store chunk-level BGE-M3 vectors in a versioned SQLite cache, implement deterministic lightweight Chinese tokenization and BM25, retrieve independent lexical and semantic candidate lists, then fuse by Reciprocal Rank Fusion (RRF). Keep the existing `wiki_search_text` and `wiki_search_semantic` commands compatible while adding a `wiki_search_hybrid` command and making the Wiki UI use it by default.

**Tech Stack:** Rust/Tauri, rusqlite, existing Companion embedding provider, React/TypeScript, Vitest/Rust unit tests.

---

### Task 1: Extract the existing text-search command

**Files:**
- Create: `edge/companion-app/src-tauri/src/commands/wiki_search.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/wiki_read.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/mod.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/invoke_handler.rs`

**Steps:**

1. Move `WikiSearchHit` and the existing `wiki_search_text` implementation into `wiki_search.rs`, preserving the Tauri command name and JSON shape.
2. Re-export the moved public symbols from `wiki_read.rs` if existing callers need the old Rust path.
3. Register the command from `commands::wiki_search` and keep the external command string `wiki_search_text` unchanged.
4. Add pure tokenizer/BM25/RRF helpers with unit tests before wiring the new command.
5. Run the targeted Rust tests and `bash scripts/check_file_sizes.sh --strict`.

### Task 2: Add chunk-level semantic cache

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/wiki_embed.rs`
- Modify: `edge/companion-app/src-tauri/src/services/embed_cache_meta.rs` only if the existing identity metadata cannot be reused

**Steps:**

1. Add a versioned `wiki_chunk_embed` SQLite table containing `chunk_id`, `rel_path`, `title`, `kind`, `heading`, `chunk_index`, `snippet`, `text`, `vector`, `mtime`, and `content_hash`.
2. Split each Markdown document by headings/paragraph boundaries into bounded chunks with a small character overlap, retaining the heading in the embedded text.
3. Incrementally rebuild only changed documents; delete rows for removed files and stale chunk indexes.
4. Reuse the current provider selection, normalization, model identity reconciliation, and BGE-M3 model; do not introduce a new model file.
5. Keep `wiki_search_semantic` response fields compatible while sourcing hits from chunks and deduplicating to a maximum of two chunks per document.
6. Run Rust tests for chunk boundaries, cache invalidation, and semantic result deduplication.

### Task 3: Implement hybrid retrieval and RRF

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/wiki_search.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/wiki_embed.rs` as needed for shared semantic candidate retrieval
- Modify: `edge/companion-app/src-tauri/src/commands/invoke_handler.rs`

**Steps:**

1. Implement real BM25 over chunk text plus title/heading/tag boosts, with deterministic tokenization for Chinese character n-grams, ASCII words, numbers, and exact phrases.
2. Retrieve lexical and semantic candidates independently, each capped at 50.
3. Fuse candidates with `1 / (60 + rank)` per source, preserving source ranks and returning the existing hit shape plus `matched_in` values.
4. Apply document diversity limits and return the top 20 candidates.
5. Add the `wiki_search_hybrid` Tauri command with `query` and optional `topK`, and unit-test intersection, disjoint lists, duplicate chunks, and top-K truncation.
6. Make the existing text and semantic commands continue to work for compatibility.

### Task 4: Make the UI use hybrid search by default

**Files:**
- Modify: `edge/companion-app/src/tabs/Wiki/WikiTree.tsx`
- Modify: `edge/companion-app/src/lib/tauri_wiki.ts`

**Steps:**

1. Add `wikiSearchHybrid` and its result types to the Tauri wrapper.
2. Add an `智能` search mode and make it the default when a user enters a query.
3. Keep title/body/semantic modes available as explicit fallback/debug modes.
4. Show whether a result came from lexical, semantic, or both sources without exposing implementation details in the normal result card.
5. Preserve debounce, loading, empty, and model-unavailable behavior.
6. Run the frontend typecheck/build and relevant Wiki tests.

### Task 5: Verify and document the first-round upgrade

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/wiki_search.rs` tests as needed
- Create: `edge/companion-app/src-tauri/src/commands/wiki_search_test_cases.json` only if fixture data is needed
- Modify: relevant Companion Wiki documentation

**Steps:**

1. Run targeted Rust tests, frontend tests/build, and `bash scripts/check_file_sizes.sh --strict`.
2. Verify the cache rebuilds without deleting source Markdown and that old cache rows do not produce 404 results.
3. Verify fallback behavior when embedding is unavailable: lexical search still returns results.
4. Record the behavior, cache location, rebuild trigger, and rollback/compatibility notes.

