# Remove incomplete WeChat automatic reader Implementation Plan

**Goal:** Remove the unfinished database/Provider path while preserving export-file analysis and account binding.

**Architecture:** Restore the export-only UI, Rust commands, Python CLI and Tool Bridge contract. Reject obsolete database configurations without touching user databases or unrelated Briefing changes.

**Tech Stack:** React/TypeScript, Tauri/Rust, Python/pytest.

## Steps
1. Review scoped diffs and save a recoverable archive of the removed implementation.
2. Remove database selection, Provider execution/manifest modules, periodic checks, packaging additions and obsolete implementation plan. Restore export-only behavior in the affected files.
3. Add regression tests rejecting obsolete database configuration and removed CLI entry points; retain JSON/JSONL/CSV tests.
4. Run Python tests, frontend build, targeted Rust tests, diff checks and strict source-size checks. Do not commit or modify personal data.

No extra execution skills are available; execute directly in this existing workspace to preserve unrelated edits.
