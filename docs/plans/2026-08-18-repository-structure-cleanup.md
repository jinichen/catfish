# Repository Structure Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Catfish 仓库的入口文档、构建入口、历史资料和代码维护边界与当前实际状态一致。

**Architecture:** 先修复公开入口和唯一状态来源，再统一 Companion 的构建/安装说明；历史材料只做可追溯归档，不直接删除业务代码；最后按项目既有拆分纪律处理超大源文件。

**Tech Stack:** Markdown、现有 npm/Tauri/WiX/PowerShell/Shell 构建脚本、Git 历史。

---

### Task 1: 修复仓库公开入口

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `edge/companion-app/README.md`
- Modify: `edge/companion-app/scripts/README-windows.md`
- Modify: `edge/companion-app/scripts/build-windows-msi-local.md`

**Checks:**
- 所有命令必须存在于当前仓库；
- 不再引用仓库外绝对路径；
- 不再把已完成模块标为 Week 1/2/3 或骨架阶段；
- 明确 macOS、Windows、中央服务的真实入口。

### Task 2: 建立唯一当前状态文档

**Files:**
- Create: `docs/STATUS.md`
- Modify: `docs/README.md`
- Modify: `README.md`
- Modify: `docs/PROJECT-STATUS.md`
- Modify: `docs/FEATURE-TRACKS.md`

**Checks:**
- 当前状态、路线图、历史日志三者职责分开；
- 明确快照日期和未验证项目；
- 旧文档只能作为历史参考，不再充当主入口。

### Task 3: 统一安装与构建入口

**Files:**
- Modify: `installer/README.md`
- Modify: `onboarding/README.md`
- Modify: `edge/companion-app/scripts/README-deploy.md`
- Modify: `edge/companion-app/package.json` only if a command is genuinely wrong
- Create or modify: one canonical Windows release build guide

**Checks:**
- 明确开发构建、macOS 发布、Windows MSI/Burn 发布的区别；
- 删除或标记不存在的 installer 文件说明；
- 不把跨编译 raw exe 当成正式 Windows 安装包。

### Task 4: 归档历史资料和清理根目录噪声

**Files:**
- Move only explicitly historical documents to `docs/archive/` or their existing archive location;
- Remove tracked empty files `300` and `hi` after confirming they contain no data;
- Do not touch user-generated untracked files.

**Checks:**
- 归档文件仍可通过索引访问；
- README 不再把归档材料当当前状态；
- `git diff --check` passes。

### Task 5: 按红线拆分超大源文件

**Files:**
- Only after Tasks 1–4 are complete, select one bounded module at a time.

**Checks:**
- 遵守 `AGENTS.md` 的 re-export 和 monkeypatch 兼容协议；
- 每次修改源文件后运行 `bash scripts/check_file_sizes.sh --strict`；
- 运行对应服务测试，避免把文档整理和大规模代码重构混在一个提交。
