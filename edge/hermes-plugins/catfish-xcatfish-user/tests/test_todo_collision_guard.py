"""Hermes todo 与 macOS Reminders 语义冲突保护测试。"""
from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import todo_collision_guard  # noqa: E402


def test_non_todo_tool_passes_through():
    assert todo_collision_guard.todo_collision_guard_hook(
        tool_name="read_file",
        args={"path": "/tmp/example"},
        task_id="task-1",
    ) is None


def test_normal_agent_plan_todo_passes_through():
    assert todo_collision_guard.todo_collision_guard_hook(
        tool_name="todo",
        args={
            "todos": [{
                "id": "analyze",
                "content": "分析接口实现并运行测试",
                "status": "in_progress",
            }],
        },
        task_id="task-1",
    ) is None


def test_implementation_task_about_reminders_passes_through():
    """开发 Reminders 能力本身仍是合法的 Agent 计划，不能被业务路由误伤。"""
    assert todo_collision_guard.todo_collision_guard_hook(
        tool_name="todo",
        args={
            "todos": [{
                "id": "implement",
                "content": "实现并测试读取本周待办的 Catfish 工具",
                "status": "in_progress",
            }],
        },
        task_id="task-dev",
    ) is None


def test_reminders_query_in_todo_is_blocked_with_exact_route():
    result = todo_collision_guard.todo_collision_guard_hook(
        tool_name="todo",
        args={
            "todos": [{
                "id": "check_reminders",
                "content": "查询 Reminders 中当前所有待办事项",
                "status": "in_progress",
            }],
        },
        task_id="task-incident",
    )

    assert result is not None
    assert result["action"] == "block"
    assert "mcp__catfish_tools__catfish_list_reminders" in result["message"]
    assert '"scope":"all"' in result["message"]
    assert "不要重复调用 todo" in result["message"]


def test_weekly_todo_query_routes_to_week_scope():
    result = todo_collision_guard.todo_collision_guard_hook(
        tool_name="todo",
        args={
            "todos": [{
                "id": "check_week",
                "content": "读取本周所有待办和状态",
                "status": "in_progress",
            }],
        },
        task_id="task-week",
    )

    assert result is not None
    assert result["action"] == "block"
    assert '"scope":"week"' in result["message"]


def test_todo_json_string_shape_is_also_checked():
    result = todo_collision_guard.todo_collision_guard_hook(
        tool_name="todo",
        args={
            "todos": '[{"id":"check","content":"查看提醒事项列表",'
                     '"status":"in_progress"}]',
        },
        task_id="task-json",
    )

    assert result is not None
    assert result["action"] == "block"


def test_empty_todo_read_is_not_blocked_without_semantic_evidence():
    """只读 Hermes 自己的 plan 仍合法；没有文本证据时不能猜是 Reminders。"""
    assert todo_collision_guard.todo_collision_guard_hook(
        tool_name="todo",
        args={},
        task_id="task-plan-read",
    ) is None
