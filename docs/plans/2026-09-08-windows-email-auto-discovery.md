# Windows Email Auto Discovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Windows Companion 自动发现 Outlook 和 Foxmail；外置 Foxmail 目录无法自动识别时，普通用户可在界面选择一次，不需要编辑 `companion.yaml`。

**Architecture:** 将邮件来源发现从“直接尝试两个 adapter”升级为结构化 discovery：分别探测 Outlook 和 Foxmail，返回来源、账号、路径和可解释的失败原因。唯一来源自动采用，多来源在 UI 中选择并持久化，已保存来源失效后自动重新扫描。`companion.yaml` 仅保留为企业部署和故障排查的显式 override。

**Tech Stack:** Python `catfish-email` adapters/CLI、Rust/Tauri commands、React/TypeScript Email UI、pytest、Vitest、Cargo。

---

### Task 1: 固定 Windows 邮件发现契约

**Files:**
- Modify: `edge/email-agent/src/catfish_email/inbox.py`
- Create: `edge/email-agent/src/catfish_email/discovery.py`
- Test: `edge/email-agent/tests/test_windows_email_discovery.py`

**Step 1: Write failing tests**

覆盖：无客户端时返回结构化失败原因；Outlook COM 失败不阻止 Foxmail；唯一有效 Foxmail Storage 自动选中；多个来源不擅自覆盖用户选择；显式路径和已保存选择优先；结果包含 `client/status/accounts/root/reason`，不包含密码和邮件正文。

**Step 2: Run focused tests**

```bash
cd edge/email-agent
PYTHONPATH=src pytest -q tests/test_windows_email_discovery.py
```

Expected: FAIL because the structured discovery API does not exist.

**Step 3: Implement**

- 新建 discovery result 数据结构；
- 将 Outlook/Foxmail 的可用性探测与实际读取分开；
- 将预期的 COM、目录不存在、无账号错误转换为状态；
- 保留 `get_adapter()` 和 `get_all_adapters()` 的兼容行为；
- Windows 改为独立探测 Outlook 和 Foxmail，Outlook 失败不能污染 Foxmail。

**Step 4: Verify**

重复 focused pytest，Expected: PASS。

**Step 5: Commit**

```bash
git add edge/email-agent/src/catfish_email/inbox.py edge/email-agent/src/catfish_email/discovery.py edge/email-agent/tests/test_windows_email_discovery.py
git commit -m "feat(email): add structured Windows client discovery"
```

### Task 2: 扩大 Foxmail 自动发现范围并保持安全边界

**Files:**
- Modify: `edge/email-agent/src/catfish_email/adapters/foxmail_discovery.py`
- Modify: `edge/email-agent/src/catfish_email/adapters/foxmail_win.py`
- Test: `edge/email-agent/tests/test_foxmail_discovery.py`

**Step 1: Add failing tests**

覆盖注册表只提供安装目录、安装目录参数文件指向 `E:\...`；UTF-8/UTF-16 参数文件；更深层参数文件；多候选去重；凭据字段中的路径不被识别；外置盘不可用时返回明确原因。

**Step 2: Run tests**

```bash
cd edge/email-agent
PYTHONPATH=src pytest -q tests/test_foxmail_discovery.py
```

Expected: relevant new cases fail.

**Step 3: Implement bounded discovery**

- 读取 Foxmail 相关注册表分支的路径型值；
- 对注册表发现的安装目录和附近参数文件有限深度扫描；
- 解析 Storage/Profile/Data 路径；
- 只检查目录结构和 `.box/.eml` 数据，不扫描整盘，不读取密码和正文；
- 规范化并去重候选，记录来源 `explicit/registry/config/default`；
- 显式路径失效时报告“已配置但不可用”，不能悄悄换读其他账号目录。

**Step 4: Verify**

```bash
PYTHONPATH=src pytest -q tests
```

Expected: all email-agent tests pass。

**Step 5: Commit**

```bash
git add edge/email-agent/src/catfish_email/adapters/foxmail_discovery.py edge/email-agent/src/catfish_email/adapters/foxmail_win.py edge/email-agent/tests/test_foxmail_discovery.py
git commit -m "fix(email): discover external Foxmail storage safely"
```

### Task 3: 增加无需手写配置的 Tauri 发现接口

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/email.rs`
- Modify: `edge/companion-app/src-tauri/src/services/email_config.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/mod.rs`
- Test: Rust command serialization and validation tests

**Step 1: Define command tests**

新增两个契约：

- `email_sources_discover`：返回所有来源的状态、账号、Foxmail 根目录和失败原因；
- `email_source_select`：校验来源仍有效后保存用户选择。

测试必须确认：Outlook 不可用时 Foxmail 仍可用；结果不包含密码/正文；已失效来源不能保存。

**Step 2: Implement bridge**

- Tauri 调用 `catfish-email discovery --json`，不复制 Python 的发现规则；
- 子进程使用隐藏窗口，不能弹 PowerShell/CMD 黑框；
- 保持现有邮件 command 的 JSON 兼容性；
- discovery 结果使用短 TTL 缓存，避免每次刷新重复 COM/注册表扫描。

**Step 3: Persist through the app**

- 用 Tauri command 自动保存客户端类型、规范化路径和来源指纹；
- 不保存密码；
- 启动时先验证已保存来源，失效后自动重新扫描；
- `companion.yaml` 只有显式存在时才作为最高优先级 override；
- 用户不需要打开或编辑 YAML。

**Step 4: Verify**

```bash
cd edge/companion-app/src-tauri
cargo check --no-default-features
```

Expected: PASS。

**Step 5: Commit**

```bash
git add edge/companion-app/src-tauri/src/commands/email.rs edge/companion-app/src-tauri/src/services/email_config.rs edge/companion-app/src-tauri/src/commands/mod.rs
git commit -m "feat(companion): expose Windows email source discovery"
```

### Task 4: 改造邮件页面的首次使用和失败降级体验

**Files:**
- Modify: `edge/companion-app/src/tabs/Email/EmailTab.tsx`
- Modify: `edge/companion-app/src/store/email.ts`
- Create: `edge/companion-app/src/lib/emailSourceDiscovery.ts`
- Test: `edge/companion-app/src/lib/emailSourceDiscovery.test.ts`
- Test: `edge/companion-app/src/tabs/Email/EmailTab.selfLoop.test.tsx`

**Step 1: Write failing UI tests**

验证：单来源自动加载；Outlook 失败但 Foxmail 可用时只显示 Foxmail；多来源显示一次选择；外置目录未发现时显示目录选择器而非 YAML 提示；选择后重启仍生效；无客户端时给明确下一步；安装修复中不重复触发。

**Step 2: Run tests**

```bash
cd edge/companion-app
npm test -- --run src/lib/emailSourceDiscovery.test.ts src/tabs/Email/EmailTab.selfLoop.test.tsx
```

Expected: FAIL until state and UI are implemented。

**Step 3: Implement UI**

- 页面加载先调用 discovery；
- 单来源自动选择，多来源显示来源卡片；
- Outlook 未安装、未配置、暂不可用均为非阻断状态；
- Foxmail 目录选择使用原生目录选择器，选择后调用 `email_source_select`；
- 支持重新扫描和切换来源；
- 错误必须显示真实原因，不能转成“收件箱为空”。

**Step 4: Verify**

```bash
npm test -- --run
npm run build
```

Expected: all tests pass and production build succeeds。

**Step 5: Commit**

```bash
git add src/tabs/Email/EmailTab.tsx src/store/email.ts src/lib/emailSourceDiscovery.ts src/lib/emailSourceDiscovery.test.ts src/tabs/Email/EmailTab.selfLoop.test.tsx
git commit -m "feat(email): add user-friendly source selection"
```

### Task 5: 消除重复安装、黑框和“文件存在即健康”的假判断

**Files:**
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install_windows.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install.rs`
- Modify: `edge/companion-app/src-tauri/src/services/process.rs`
- Test: Windows bootstrap Rust tests

**Step 1: Add failing health-check tests**

覆盖 exe 存在但 Python 包/`pywin32` 损坏时自动修复；健康时不重复安装；并发启动只有一个安装者；所有安装和邮件 CLI 子进程隐藏窗口；失败不循环弹窗。

**Step 2: Implement health-based repair**

- 不再只检查 `catfish-email.exe`；
- 用同一 venv 的 Python 做轻量导入/版本探测；
- 状态区分 `missing/repairing/ready/failed`；
- 使用一次性锁、退避和失败状态，避免页面刷新重复安装；
- 日志保留，UI 显示可读状态。

**Step 3: Verify**

```bash
cd edge/companion-app/src-tauri
cargo check --no-default-features
```

并在 Windows runner 实测：首次启动、重启、损坏 venv、Foxmail-only、Outlook-only、双客户端。

**Step 4: Commit**

```bash
git add edge/companion-app/src-tauri/src/commands/hermes_install_windows.rs edge/companion-app/src-tauri/src/commands/hermes_install.rs edge/companion-app/src-tauri/src/services/process.rs
git commit -m "fix(windows): repair email component without popup loops"
```

### Task 6: Windows 端到端验收和文档收口

**Files:**
- Modify: `edge/companion-app/src-tauri/resources/windows/README.md`
- Create: `docs/testing/windows-email-discovery.md`

**Step 1: Execute acceptance matrix**

| 场景 | 预期 |
|---|---|
| 仅 Outlook，有账号 | 自动显示 Outlook 邮箱 |
| 仅 Foxmail，默认目录 | 自动显示 Foxmail 邮箱 |
| Foxmail Storage 在 E 盘且参数可发现 | 自动显示 Foxmail 邮箱 |
| E 盘参数不可发现 | 页面选择目录一次并自动记住 |
| Outlook 不可用、Foxmail 可用 | 只显示 Foxmail，不出现 COM 错误 |
| 两者都可用 | 显示两个来源并可切换 |
| 两者都不可用 | 显示明确诊断和下一步，不显示空收件箱 |
| 重装 MSI/重启 Companion | 不删除选择、不重复弹黑框 |

**Step 2: Run repository checks**

```bash
bash scripts/check_file_sizes.sh --strict
git diff --check
```

修改 `.py/.ts/.tsx/.rs/.js` 后必须再次执行 `check_file_sizes.sh --strict`；源文件达到 800 行必须先拆分，不能提交。

**Step 3: Run CI and Windows verification**

- email-agent 全量 pytest；
- Companion 全量 Vitest 和 production build；
- Rust check/build，包含 Windows `deny(warnings)`；
- Windows MSI 安装后验证资源、隐藏安装、自动发现、目录选择和重启恢复；
- 确认 MSI 不执行 Hermes/邮件组件 CustomAction，首次启动安装不阻塞 MSI。

**Step 4: Update documentation**

文档明确：默认自动发现 Outlook/Foxmail；外置目录发现失败时在界面选择一次；`companion.yaml` 仅用于企业预配置和排障；不需要 PowerShell 临时环境变量，也不需要手动运行安装脚本。

**Step 5: Commit**

```bash
git add edge/companion-app/src-tauri/resources/windows/README.md docs/testing/windows-email-discovery.md
git commit -m "docs(windows): document automatic email discovery"
```
