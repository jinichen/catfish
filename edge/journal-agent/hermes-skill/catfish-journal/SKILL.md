---
name: catfish-journal
description: 维护员工本机历史流水账 employee_journal.md。不得用于新增、查询、完成或删除用户待办；用户待办统一调用 Hermes 的 catfish_create_reminder / catfish_list_reminders 等 Reminders 工具。
version: 0.3.0
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
不再承担用户待办管理。早安模块、对话和系统提醒必须共享同一个 Reminders 数据源。

## 数据边界

- 用户说“提醒我”“新建待办”“本周有什么待办”时，使用 Hermes 原生 Reminders 工具。
- 查询待办使用 `catfish_list_reminders`。
- 新建待办使用 `catfish_create_reminder`。
- 完成或删除待办使用 Hermes 对应的 Reminders 工具；如果当前版本尚未暴露，明确告知用户，不能退回写 journal。
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
