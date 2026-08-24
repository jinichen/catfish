# Reminders Tool Routing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Companion 对“本周待办、今天待办、所有待办”等请求直接读取 macOS Reminders.app，并阻止模型把这类请求误投给 Hermes 的会话规划 `todo` 工具。

**Architecture:** 在 Catfish tool-bridge 新增 `catfish_list_reminders`，沿现有 MCP 注册链暴露给 Hermes；在 Catfish Hermes 插件的 `pre_tool_call` 层只拦截语义明确的 Reminders 查询误调用。工具提升与 Gateway always-on 名单同步，确保渐进式工具披露和工具数量裁剪都不会隐藏它。全程不修改 Hermes 上游源码。

**Tech Stack:** Python 3、AppleScript/osascript、Hermes plugin hooks、pytest。

---

### Task 1: 锁定提醒事项读取契约

**Files:**
- Modify: `edge/tool-bridge/tests/test_reminders.py`
- Test: `edge/tool-bridge/tests/test_reminders.py`

1. 为 AppleScript 输出解析、按 today/week/overdue/all 筛选、完成状态、清单过滤和 limit 写失败测试。
2. 为非 macOS、权限失败和 tool dispatch 写失败测试。
3. 为 schema 名称、参数和“不可使用 Hermes todo 读取 Reminders”的描述写失败测试。
4. 运行 `pytest -q edge/tool-bridge/tests/test_reminders.py`，确认新测试先失败。

### Task 2: 实现并注册读取工具

**Files:**
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/reminders.py`
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tool_schemas_task.py`
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py`

1. 用 osascript 只读 Reminders.app，输出稳定的分隔格式并在 Python 端解析。
2. 按本地时区实现 today/week/overdue/all，默认排除已完成条目。
3. 返回结构化条目、数量和明确摘要；权限和平台错误保持可操作。
4. 遵守 schema 文件写明的“dispatch 不重组”约束，在原分发函数加入路由，并清理等量空行，避免 798 行的 `catfish_tools.py` 越过 800 行红线。
5. 运行提醒事项测试，确认通过。

### Task 3: 确保模型可见并阻止误路由

**Files:**
- Modify: `edge/hermes-plugins/catfish-xcatfish-user/plugin_core_tools.py`
- Modify: `central/llm-gateway/src/catfish_gateway/tools_sanitizer_constants.py`
- Create: `edge/hermes-plugins/catfish-xcatfish-user/todo_collision_guard.py`
- Modify: `edge/hermes-plugins/catfish-xcatfish-user/__init__.py`
- Create: `edge/hermes-plugins/catfish-xcatfish-user/tests/test_todo_collision_guard.py`
- Modify: `edge/hermes-plugins/catfish-xcatfish-user/tests/test_p43_core_tools.py`
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_plugin_baked.rs`

1. 先写 hook 测试：普通会话规划 todo 放行；“读取本周待办/Reminders”阻断并指向 `mcp__catfish_tools__catfish_list_reminders`。
2. 实现只针对明确外部提醒查询的 guard，不屏蔽 Hermes 正常任务规划。
3. 注册 guard；把新工具同步加入 P43 提升名单和 Gateway always-on 名单。
4. 把新 guard 加入 Companion baked 插件文件表，确保正式安装包不会漏文件。
5. 运行插件 hook、P43 一致性与 Companion baked 文件覆盖测试。

### Task 4: 回归与边界自检

**Files:**
- Test: all files above

1. 运行 tool-bridge 和插件针对性测试。
2. 运行 `bash scripts/check_file_sizes.sh --strict`。
3. 运行 `git diff --check`，复核 `git diff --stat` 和 `git status --short`。
4. 确认差异中没有 `~/.hermes/hermes-agent` 或任何 Hermes 上游源码改动。
