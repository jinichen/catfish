# Email Chat Handoff Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the email “让小鲶处理” action create one real Companion user request, use the normal chat/session persistence path, and expose only the per-terminal email tools needed to inspect the selected message.
**Architecture:** Keep email context assembly in Companion. Queue a typed email-chat action in the UI store, consume it once in `ChatTab`, start an isolated session through the existing `useChat.send()` path, and retain Hermes customization in the out-of-tree Catfish plugin. The handoff contains the email handle and treats all message metadata/content as untrusted external data.
**Tech Stack:** React + TypeScript, Zustand, existing `useChat` session flow, Hermes Catfish plugin, Vitest, pytest, Vite.

---

### Task 1: Replace the email handoff prompt

**Files:**
- Modify: `edge/companion-app/src/lib/emailHandoff.ts`
- Test: `edge/companion-app/src/lib/emailHandoff.test.ts`

Use the selected email ID as the only operational handle. Remove the body snippet from the prompt, explicitly request the full message through the currently available local email tool with `mark_read=false`, label email data as untrusted, and forbid sending mail, creating reminders, or writing wiki content without a later explicit confirmation.

### Task 2: Route email handoff through normal chat send

**Files:**
- Modify: `edge/companion-app/src/store/ui.ts`
- Modify: `edge/companion-app/src/tabs/Email/EmailTab.tsx`
- Modify: `edge/companion-app/src/tabs/Chat/ChatTab.tsx`
- Modify: `edge/companion-app/src/tabs/Email/EmailTab.selfLoop.test.tsx`

Add a typed, deduplicated pending email-chat action. `ChatTab` consumes it exactly once, starts a fresh chat session with `reset()`, and calls the existing `send()` function. Track the active email handoff so repeated clicks do not enqueue duplicate requests while the first request is running. Leave the legacy proactive-notification path unchanged for scheduler/onboarding notifications.

### Task 3: Keep local email tools visible without changing Hermes upstream

**Files:**
- Modify: `edge/hermes-plugins/catfish-xcatfish-user/plugin_core_tools.py`
- Test: `edge/hermes-plugins/catfish-xcatfish-user/tests/test_p43_core_tools.py`

Promote only the email full-read and attachment-inspection tools next to the existing email search tool. The names remain resolved against the current terminal’s Tool Bridge/MCP registry; no central tool directory or Hermes source modification is introduced.

### Task 4: Verify the change

Run:

```bash
npm run build
bash scripts/check_file_sizes.sh --strict
git diff --check
```

Run the focused email handoff and plugin tests where the local test environment supports them. Review the diff to ensure no upstream Hermes files, credentials, or unrelated working-tree changes are included.
