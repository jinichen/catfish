# Email Reply Context Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make email reply drafting use the current message, relevant thread history, locally parsed attachment previews, and topic-relevant local knowledge without automatically exposing unrelated reminders or mailbox data.

**Architecture:** Keep context assembly in Companion. The email agent remains the source of truth for reading messages and exporting attachments; the Companion invokes those existing local commands and sends only a bounded, labeled context to the selected gateway model. Thread matching uses RFC message identifiers only, while Wiki retrieval uses sender plus subject/body terms. Sensitive sources such as reminders remain opt-in and are not part of automatic drafting.

**Tech Stack:** React + TypeScript, Tauri commands, existing `catfish-email` CLI, existing local `parse_file.py` parser, Vitest, Vite build.

---

### Task 1: Add pure context-selection and formatting helpers

**Files:**
- Create: `edge/companion-app/src/lib/emailReplyContext.ts`
- Modify: `edge/companion-app/src/lib/emailDraft.ts`
- Test: `edge/companion-app/src/lib/emailReplyContext.test.ts`

**Step 1: Write the failing tests**

Cover RFC thread membership, recent-message ordering and limits, attachment preview formatting, and context-source labels. Verify that unrelated messages and reminders are excluded.

**Step 2: Run the focused tests**

Run: `npm test -- --run src/lib/emailReplyContext.test.ts`

Expected: FAIL until the new helper module exists.

**Step 3: Implement the minimal helpers**

Add normalized Message-ID/References parsing, strict same-thread matching, bounded recent-message selection, and labeled rendering for thread and attachment context. Keep the existing `DraftContextItem` contract compatible.

**Step 4: Run the focused tests again**

Run: `npm test -- --run src/lib/emailReplyContext.test.ts`

Expected: PASS, subject-only collisions must not be treated as the same thread.

### Task 2: Fetch full thread messages and parse attachment previews locally

**Files:**
- Modify: `edge/companion-app/src/lib/tauri_briefing.ts`
- Modify: `edge/companion-app/src/tabs/Email/components/ComposeCore.tsx`
- Modify: `edge/companion-app/src/tabs/Email/components/DetailPane.tsx`
- Test: `edge/companion-app/src/tabs/Email/components/ComposeCore.test.tsx` (or the existing email component test location)

**Step 1: Add typed wrappers for existing local Tauri commands**

Expose the reply-only attachment preview command through the existing `lib/tauri` barrel and reuse `emailExportAttachment` plus `parse_file`; do not add a second parser or upload the attachment to a remote service.

**Step 2: Build reply context on demand**

When the employee clicks draft, select only the current message’s RFC thread candidates from Inbox/Sent, read at most the recent bounded set, export and parse only supported small/medium attachments, and silently skip unsupported or failed attachments. Never write attachment copies to `~/.catfish/uploads`; use the existing temporary export and clean it after parsing where safe.

**Step 3: Pass labeled context to `draftEmailReply`**

Send the full current body up to a larger bounded budget, recent thread messages, attachment previews, and topic-relevant Wiki items. Keep reminders, calendar, unrelated emails, full Hermes history, and arbitrary business systems out of automatic drafting.

**Step 4: Preserve the visible quoted reply**

Keep the previously fixed “generated reply first, original quote after it” behavior and ensure context-loading failures do not block drafting from the current email.

### Task 3: Improve relevant Wiki retrieval and explainability

**Files:**
- Modify: `edge/companion-app/src/lib/emailDraftContext.ts`
- Modify: `edge/companion-app/src/tabs/Email/components/ComposeCore.tsx`
- Test: `edge/companion-app/src/lib/emailDraftContext.test.ts`

**Step 1: Expand the local query**

Use sender display name, subject, and a bounded body keyword string instead of sender alone. Deduplicate hits and retain tombstone filtering.

**Step 2: Keep provenance visible**

Extend the existing “参考了哪些资料” note to identify thread and attachment context separately, without displaying sensitive content in the status note.

**Step 3: Test fallback behavior**

Verify that search failures, parser failures, missing thread metadata, and unsupported attachment types fall back to current-email-only drafting.

### Task 4: Verify the whole change

**Files:**
- No new source files.

**Step 1: Run focused tests**

Run: `npm test -- --run src/lib/emailReplyContext.test.ts src/lib/emailDraftContext.test.ts`

**Step 2: Run the production build**

Run: `npm run build`

Expected: exit 0.

**Step 3: Run repository checks**

Run: `bash scripts/check_file_sizes.sh --strict`

Expected: no source file at or above 800 lines.

**Step 4: Inspect the diff**

Run: `git diff --check` and review only the intended email-context files; preserve unrelated working-tree changes.
