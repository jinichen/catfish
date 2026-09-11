# Wiki Graph Focus Visuals Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the wiki topology view clearer and more visually engaging by emphasizing focused relationships, semantic hierarchy, and purposeful interaction.

**Architecture:** Preserve the existing graph model and selection behavior. Add presentation-only graph attributes for focus state, relationship emphasis, and label density; update the graph shell and overview panel to expose the selected node and focused relationship context.

**Tech Stack:** React, TypeScript, Sigma, Graphology, existing Catfish CSS tokens.

---

### Task 1: Add focused graph visual hierarchy

**Files:**
- Modify: `edge/companion-app/src/tabs/Wiki/WikiGraph.tsx`
- Test: existing companion-app type/build checks

**Steps:**
1. Read the current graph construction and renderer lifecycle.
2. Add presentation-only attributes for selected node, direct neighbors, degree rank, and semantic kind.
3. Reduce labels in full-graph mode to important nodes while keeping focused-mode labels readable.
4. Encode confirmed/inferred relationship emphasis with opacity, size, and line style where Sigma supports it.
5. Preserve click-to-preview, expand, focus/full toggle, and incremental expansion behavior.
6. Run the app type/build checks.

### Task 2: Improve graph controls and overview hierarchy

**Files:**
- Modify: `edge/companion-app/src/tabs/Wiki/WikiGraph.tsx`
- Modify: `edge/companion-app/src/styles/globals.css`

**Steps:**
1. Separate graph count from action buttons in the header.
2. Add a compact selected-node/focus status treatment without exposing internal implementation terms.
3. Make the overview panel read as a focus inspector and keep the relation list scannable.
4. Add restrained active-path and node-focus transitions using existing brand colors.
5. Check narrow and expanded layouts for overlap.
6. Run CSS/type/build checks and the strict file-size check.

### Task 3: Verify the finished interaction

**Files:**
- Test: companion-app build and repository checks

**Steps:**
1. Run `git diff --check`.
2. Run the relevant frontend build/type checks.
3. Run `bash scripts/check_file_sizes.sh --strict`.
4. Review the diff for unrelated changes and confirm the working tree only contains this feature.
