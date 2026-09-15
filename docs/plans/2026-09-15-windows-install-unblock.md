# Windows installation unblock implementation plan

**Goal:** Prevent temporary source cleanup from blocking Hermes installation; make release identity diagnosable.

**Architecture:** Keep the extracted source directory instead of synchronously recursively deleting tens of thousands of files. Log the retained directory and stage boundaries. Preserve existing runtime backups and installation health checks.

**Tech Stack:** Python-generated PowerShell installer, Rust bootstrap, GitHub Actions MSI build.

## Tasks

1. Add a regression in `edge/hermes-fork/test_patch_install_ps1_offline.py` asserting no recursive temporary-source deletion in the offline repository stage; run it and confirm failure.
2. Update `edge/hermes-fork/patch_install_ps1_offline.py` to retain temporary extraction with an explicit message and log Git preparation boundaries. Run the patch tests against the actual upstream installer.
3. Inspect `.github/workflows/build-windows-msi.yml` and Rust installation arguments for artifact/version mismatch. Add diagnostic identity where missing; do not infer a successful Windows upgrade from local tests.
4. Run `bash scripts/check_file_sizes.sh --strict`, relevant tests, and `git diff --check`. Report Windows-only acceptance still required. Do not commit or publish without authorization.

## Windows acceptance

- After robocopy succeeds, logs identify Git preparation and retained extraction path; Python installation must continue without waiting for recursive cleanup.
- Failure in core dependencies must remain fatal. Existing runtime backups must remain intact.
- Check the installed executable against the release artifact and ensure script arguments include `-Json`, `-SkipSetup`, and `-Commit`.
