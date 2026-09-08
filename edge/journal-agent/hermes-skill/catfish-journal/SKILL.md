---
name: catfish-journal
description: 维护员工本机历史流水账 employee_journal.md。不得用于新增、查询、完成或删除用户待办；用户待办统一写入 catfish_create_task，本机任务库是事实源，macOS Reminders 仅按需同步。
version: 0.4.0
author: 鲶鱼 Catfish Platform Team
license: MIT
metadata:
  hermes:
    tags: [journal, history, catfish]
prerequisites:
  commands: [catfish-journal]
---

# catfish-journal

本 skill 只维护 `~/.catfish/employee_journal.md` 中已经存在的历史流水账内容，
不再承担用户待办管理。早安模块和对话共享本机任务库；macOS 上可按需把任务同步到 Reminders。

## 数据边界

- 用户说“提醒我”“新建待办”或要求记录行动时，使用 `catfish_create_task` 写入本机任务库。
- 查询待办使用 `catfish_list_tasks`；它是早安模块使用的统一任务来源。
- macOS 需要在系统 Reminders 中显示时，再使用 `catfish_sync_tasks_to_reminders`；该同步按 task_id 幂等。
- 直接查询或操作 Reminders 仅在用户明确要求查看系统提醒时使用 `catfish_list_reminders` / `catfish_create_reminder`。
- 完成或取消任务使用 `catfish_create_task` 更新 status；不能退回写 journal。
- `employee_journal.md` 是员工历史流水账，不是提醒系统，也不参与早安模块计数。
- `~/.catfish/current_todos.md` 已停用；不得创建、读取、同步或改写它。

## 允许的 journal 操作

`catfish-journal` CLI 仅用于兼容已有流水账中的显式 checkbox，或进行内部历史同步：

```text
catfish-journal list [--limit N] [--format json|markdown|human]
catfish-journal done --line N --hint TEXT
catfish-journal delete --line N --hint TEXT
catfish-journal add "TEXT" [--section SECTION]
```

这些命令不能作为用户待办入口。只有员工明确要求修改“员工日志 / 流水账”时才可调用。

## 安全约束

- 每次只改一条，并用 `line + hint` 双重定位。
- 不修改 TODO 之外的正文、标题或笔记。
- 不用 `sed`、`awk` 或临时脚本绕过 CLI。
- 不把邮件、日历内容自动复制进日志。
- 工具失败时报告错误，不以 `current_todos.md` 作为降级方案。
