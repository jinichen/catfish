# Delivery Setup Configuration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the Dahua delivery installer generate reachable OIDC/HTTPS configuration on fresh installs and preserve secrets while updating deployment parameters on upgrades.

**Architecture:** Keep persistent secrets and customer business settings in `.env`, while treating server URL, OIDC issuer, CORS origin, HTTPS mode/port, and explicitly supplied worker counts as derived deployment settings. The installer will update only the derived settings during an upgrade and will never delete volumes.

**Tech Stack:** Bash, Docker Compose, shell regression tests.

---

### Task 1: Make the delivery template contain active runtime fields

**Files:**
- Modify: `delivery/dahua-poc/.env.example`

**Step 1:** Add active OIDC issuer, Identity issuer/CORS, HTTPS, and worker fields.

**Step 2:** Verify the template has one active line for each field.

### Task 2: Separate derived settings from persistent secrets

**Files:**
- Modify: `delivery/dahua-poc/setup.sh`

**Step 1:** Add a safe replace-or-append helper for env fields.

**Step 2:** On fresh install, copy the template; on upgrade, keep the existing file.

**Step 3:** On both paths, write derived settings and preserve/restore persistent secrets.

**Step 4:** Persist explicitly supplied worker counts without changing them implicitly.

### Task 3: Lock behavior with regression tests

**Files:**
- Modify: `delivery/dahua-poc/test_setup_env.sh`

**Step 1:** Assert fresh HTTP and HTTPS values are generated.

**Step 2:** Assert upgrade updates deployment values but preserves database, JWT, secret, API key, and external Identity values.

**Step 3:** Run the regression script.

### Task 4: Validate the delivery artifact inputs

**Files:**
- No source changes.

**Step 1:** Run Bash syntax checks.

**Step 2:** Run the setup regression test and Compose config service/image checks.

**Step 3:** Run `bash scripts/check_file_sizes.sh --strict`.

