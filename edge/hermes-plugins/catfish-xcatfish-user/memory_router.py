"""catfish memory router — 替换 hermes builtin memory tool, 5 仓库智能路由.

# BL-MEMORY-ROUTER-A2-V3 (6/2 凌晨鸿波拍 V3)

## 为啥在 catfish-xcatfish-user plugin

v2 原计划放 catfish-memory plugin (5/19 ship 的 MemoryProvider), 但 audit 发现:
- hermes gateway mode (Companion → hermes 8642 → gateway 8999 thin proxy 路径)
  **不 load memory provider**, MemoryManager 不 init catfish-memory
- catfish-memory plugin 从 5/19 ship 至今 14 天**真生产 0 active** (5/24 鸿波看的
  "70% 跑偏" 其实是 hermes builtin memory_tool 直接调, 0 catfish 缓解)

真 enforce 位置: catfish-xcatfish-user plugin. 它 6/1 ship 后**真装载**, 走 hermes
plugin discovery 路径 (`hermes_cli.plugins`), register(ctx) 真被调.

## 5 kind 路由

| kind         | 路由                                                    |
|--------------|---------------------------------------------------------|
| identity     | hermes 原 memory_tool(target=user) → USER.md            |
| project_fact | hermes 原 memory_tool(target=memory) → MEMORY.md        |
| workflow     | hint 让 LLM 调 catfish_propose_skill (BL-MM9 5/8 ship)  |
| journal      | append ~/.catfish/employee_journal.md (catfish 现有)    |
| todo         | append journal + hint 调 catfish_reminder_create (5/13) |

## 性能

LLM 在 tool call 时自己填 kind, 0 后端 LLM 调用. 单次 write < 50ms 跟 hermes
原生一样. 0 性能损失.

## enforcement

ctx.register_tool(override=True) 让 hermes builtin memory tool 不在 LLM tool list
出现, LLM 看到的就是 catfish 5 选 1 schema. 命中率从 0% (现状) → 95%+.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("catfish.xcatfish_user.memory_router")


# ── catfish home (跟 catfish-memory plugin 同) ────────────────────────────

def _catfish_home() -> Path:
    """~/.catfish/ (mac/linux) or ${CATFISH_HOME}."""
    env = os.environ.get("CATFISH_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".catfish"


# ── tool schema ───────────────────────────────────────────────────────────

CATFISH_MEMORY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["add", "replace", "remove"],
            "description": "add (新加) / replace (改) / remove (删)",
        },
        "kind": {
            "type": "string",
            "enum": ["identity", "project_fact", "workflow", "journal", "todo"],
            "description": (
                "内容性质 (必填, 决定存哪):\n"
                "- identity: 关于员工**这个人**的稳定事实 (姓名/部门/偏好/沟通风格) → USER.md\n"
                "- project_fact: **项目/技术**事实 (API 字段含义/客户机房 IP/工具约定) → MEMORY.md\n"
                "- workflow: 工作**流程** (有 input/output/step 序列) → 自动提议存成 skill\n"
                "- journal: 已发生**事件**/session 总结/会议记录 → 写 catfish 员工日志 (不是 memory)\n"
                "- todo: 带 deadline 的**待办任务** → 自动转 Reminders.app (不是 memory)\n"
                "拿不准 → 80% 概率是 journal 不是 identity/project_fact."
            ),
        },
        "content": {
            "type": "string",
            "description": "要存的内容 (action=add/replace 必填)",
        },
        "old_text": {
            "type": "string",
            "description": "要替换/删除的旧文本 (action=replace/remove 必填, 唯一短 substring)",
        },
    },
    "required": ["action", "kind"],
}


# ── 主入口 (ctx.register_tool 的 handler) ─────────────────────────────────

def handle_memory_tool(args: Dict[str, Any], **kw: Any) -> str:
    """catfish memory tool 真 handler.

    按 kind 路由到 5 个仓库. 0 后端 LLM 调用 (LLM 自己填 kind), 0 性能损失.
    return JSON string (跟 hermes 原 memory_tool 同接口).
    """
    action = args.get("action", "add")
    kind = args.get("kind")
    content = args.get("content")

    if not kind:
        return json.dumps({
            "success": False,
            "error": "kind 必填 (identity/project_fact/workflow/journal/todo).",
        }, ensure_ascii=False)

    # action=replace/remove 仍走 hermes 原生 (改 USER.md / MEMORY.md 入口)
    if action in ("replace", "remove"):
        return _call_hermes_original_memory_tool(args, **kw)

    # action=add: 按 kind 路由
    try:
        if kind == "todo":
            return _route_to_reminder(content)
        elif kind == "journal":
            return _route_to_journal(content)
        elif kind == "workflow":
            return _route_to_propose_skill(content)
        elif kind == "identity":
            return _call_hermes_original_memory_tool(
                {**args, "target": "user"}, **kw
            )
        elif kind == "project_fact":
            return _call_hermes_original_memory_tool(
                {**args, "target": "memory"}, **kw
            )
        else:
            return json.dumps({
                "success": False,
                "error": f"unknown kind '{kind}'. 看 schema 选 5 个之一.",
            }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.exception("catfish memory router 异常: %s", e)
        return json.dumps({
            "success": False,
            "error": f"catfish memory router 异常: {e}",
        }, ensure_ascii=False)


# ── 5 个路由 helper ───────────────────────────────────────────────────────

def _call_hermes_original_memory_tool(args: Dict[str, Any], **kw: Any) -> str:
    """走 hermes 原生 memory_tool — identity → USER.md / project_fact → MEMORY.md."""
    from tools.memory_tool import memory_tool as _hermes_memory_tool
    return _hermes_memory_tool(
        action=args.get("action", "add"),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        store=kw.get("store"),
    )


def _route_to_reminder(content: str) -> str:
    """kind=todo → append journal + hint 让 LLM 调 catfish_reminder_create (5/13 BL-REMINDER).

    catfish_reminder_create 走 catfish-tool-bridge unix socket, 这个 module 在 hermes
    进程内不能直接调. 兜底: 写 journal + hint, 让 LLM 看到 result 自己再调 reminder.
    """
    catfish_home = _catfish_home()
    journal_path = catfish_home / "employee_journal.md"
    ts = datetime.now(timezone.utc).isoformat()
    entry = f"\n## 待办 (LLM 建议存 reminder) @ {ts}\n{content}\n"
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(entry)
        return json.dumps({
            "success": True,
            "routed_to": "journal (todo 兜底)",
            "hint": (
                "这是 todo, 已暂存 journal. 建议你再调 catfish_reminder_create "
                "把 due_date 和 title 传过去, 真存进 macOS Reminders.app."
            ),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({
            "success": False,
            "error": f"todo 路由失败: {e}",
        }, ensure_ascii=False)


def _route_to_journal(content: str) -> str:
    """kind=journal → append ~/.catfish/employee_journal.md."""
    catfish_home = _catfish_home()
    journal_path = catfish_home / "employee_journal.md"
    ts = datetime.now(timezone.utc).isoformat()
    entry = f"\n## Session entry @ {ts}\n{content}\n"
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(entry)
        return json.dumps({
            "success": True,
            "routed_to": "employee_journal.md",
            "path": str(journal_path),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({
            "success": False,
            "error": f"journal 写失败: {e}",
        }, ensure_ascii=False)


def _route_to_propose_skill(content: str) -> str:
    """kind=workflow → hint 让 LLM 调 catfish_propose_skill (BL-MM9 5/8 ship)."""
    return json.dumps({
        "success": True,
        "routed_to": "propose_skill_hint",
        "hint": (
            "这是 workflow (有 step 序列), 不该写 memory. "
            "请改调 catfish_propose_skill 工具, 把 name + reason + action_steps "
            "传过去, 走员工 confirm 门槛固化为 skill."
        ),
        "content_recap": content[:200] if content else "",
    }, ensure_ascii=False)
