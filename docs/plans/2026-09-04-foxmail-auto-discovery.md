# Foxmail Windows 自动发现 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Windows 版 Companion 自动发现 Foxmail 的自定义 Storage 目录，减少对 `email.foxmail_root` 手工配置的依赖，同时保留明确、可验证的兜底配置。

**Architecture:** `catfish-email` 在 Windows 上按优先级读取显式环境变量、Foxmail 参数/注册表中的路径、有限范围的默认目录；每个候选路径必须通过 Foxmail 邮件目录签名校验后才能使用。不会扫描整个磁盘，也不读取账号密码；多个候选无法唯一确定时返回可操作错误，Companion 继续把真实错误展示给用户。

**Tech Stack:** Python、Windows `winreg`（按平台延迟导入）、Tauri/Rust、pytest、Vitest。

---

### Task 1: 固定当前探测边界

**Files:**
- Inspect: `edge/email-agent/src/catfish_email/adapters/foxmail_win.py`
- Inspect: `edge/companion-app/src-tauri/src/commands/email.rs`
- Inspect: `edge/companion-app/src-tauri/src/services/email_config.rs`
- Test: `edge/email-agent/tests/test_adapter_foxmail_win.py`

**Step 1:** 记录当前候选目录、环境变量优先级和 Foxmail Storage 签名。

**Step 2:** 确认新增代码不会读取整个 C/E 盘，也不会读取密码字段。

**Expected:** 当前默认探测仅覆盖固定目录；自定义目录只能通过环境变量或 YAML 进入。

### Task 2: 增加自动发现测试

**Files:**
- Modify: `edge/email-agent/tests/test_adapter_foxmail_win.py`
- Create: `edge/email-agent/tests/test_foxmail_discovery.py`

**Step 1:** 编写测试，验证显式环境变量优先于自动发现。

**Step 2:** 编写测试，验证参数文件中的 Windows 路径会被提取并通过 Storage 签名校验。

**Step 3:** 编写测试，验证无效路径、密码字段和不相关文本不会成为候选。

**Step 4:** 运行新增测试，确认实现前测试失败。

### Task 3: 实现安全的 Foxmail 自动发现

**Files:**
- Create: `edge/email-agent/src/catfish_email/adapters/foxmail_discovery.py`
- Modify: `edge/email-agent/src/catfish_email/adapters/foxmail_win.py`

**Step 1:** 实现有限来源读取：Windows 注册表 Foxmail 分支、Foxmail 常见配置文件、现有默认目录。

**Step 2:** 仅提取看起来像路径的字符串，拒绝 URL、密码键和值、超长文本和不在允许目录结构内的路径。

**Step 3:** 用 `Mail`、`.box`、`.eml` 等实际数据签名验证候选，去重并按优先级排序。

**Step 4:** 环境变量存在时不访问注册表；候选唯一时自动使用；候选冲突时返回候选摘要而不是随机选择。

**Step 5:** 运行新增测试和全部 email-agent 测试。

### Task 4: 接入 Companion 与诊断

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/email.rs`
- Modify: `edge/companion-app/src-tauri/resources/windows/README.md`
- Modify: `edge/companion-app/src/lib/emailPlatformHints.ts`
- Test: existing Rust and Vitest suites

**Step 1:** 保留 YAML `email.foxmail_root` 作为最高优先级兜底，不在代码中写入用户盘符。

**Step 2:** 将自动发现结果和候选冲突原因透传到错误提示。

**Step 3:** 更新 Windows 文档，说明自动发现范围、隐私边界和手动配置方式。

**Step 4:** 运行 Rust、TypeScript 构建和测试。

### Task 5: 完整检查并提交

**Files:**
- Check: all changed source and test files

**Step 1:** 运行 `bash scripts/check_file_sizes.sh --strict` 和 `git diff --check`。

**Step 2:** 审查 Windows 条件导入、路径穿越、配置泄露和多候选行为。

**Step 3:** 提交一个聚焦 commit，记录自动发现和验证结果。
