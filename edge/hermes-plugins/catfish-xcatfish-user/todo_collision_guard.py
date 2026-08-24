"""阻止 Hermes 会话规划 ``todo`` 冒充 macOS Reminders 查询工具。

Hermes todo 的职责是保存 Agent 当前任务执行计划；它不会读取系统提醒事项。
本 hook 只在 todo 条目文本明确表达“查询/读取 Reminders 或系统待办”时阻断，
并把模型引导到 tool-bridge 暴露的只读工具。普通 Agent 计划和空参数读取放行。
"""
from __future__ import annotations

import json
import re
from typing import Any


_REMINDERS_TOOL = "mcp__catfish_tools__catfish_list_reminders"
_QUERY_VERB_RE = re.compile(r"查询|读取|查看|列出|获取|检查|检索|汇总|list|read|show", re.I)
_EXPLICIT_REMINDERS_RE = re.compile(r"reminders(?:\.app)?|提醒事项", re.I)
_SCOPED_TODO_RE = re.compile(
    r"(?:本周|这周|今天|今日|所有|全部|逾期|未完成).{0,10}(?:待办|提醒)"
    r"|(?:待办|提醒).{0,10}(?:列表|清单|状态)",
    re.I,
)
_IMPLEMENTATION_CONTEXT_RE = re.compile(
    r"实现|开发|修复|重构|编写|测试|调试|分析.{0,8}(?:代码|接口|工具)|implement|develop|fix|test",
    re.I,
)


def _todo_texts(args: Any) -> list[str]:
    if not isinstance(args, dict):
        return []
    todos = args.get("todos")
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [todos]
    if not isinstance(todos, list):
        return []
    texts: list[str] = []
    for item in todos:
        if isinstance(item, dict):
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
        elif isinstance(item, str) and item.strip():
            texts.append(item.strip())
    return texts


def _is_external_reminders_query(text: str) -> bool:
    if _IMPLEMENTATION_CONTEXT_RE.search(text):
        return False
    return bool(
        _QUERY_VERB_RE.search(text)
        and (_EXPLICIT_REMINDERS_RE.search(text) or _SCOPED_TODO_RE.search(text))
    )


def _scope_for_text(text: str) -> str:
    if re.search(r"本周|这周|this week", text, re.I):
        return "week"
    if re.search(r"今天|今日|today", text, re.I):
        return "today"
    if re.search(r"逾期|overdue", text, re.I):
        return "overdue"
    return "all"


def todo_collision_guard_hook(
    *,
    tool_name: str = "",
    args: Any = None,
    task_id: str = "",
    **_: Any,
) -> dict[str, str] | None:
    """Hermes ``pre_tool_call`` hook；只阻断有明确语义证据的误调用。"""
    if tool_name != "todo":
        return None
    for text in _todo_texts(args):
        if not _is_external_reminders_query(text):
            continue
        scope = _scope_for_text(text)
        return {
            "action": "block",
            "message": (
                "Hermes todo 只管理当前会话的 Agent 执行计划，不能读取 macOS "
                "Reminders.app。请立即调用 "
                f"{_REMINDERS_TOOL}，参数 {{\"scope\":\"{scope}\"}}；"
                "不要重复调用 todo。"
            ),
        }
    return None


__all__ = ["todo_collision_guard_hook"]
