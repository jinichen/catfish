"""skill_guard 测试 (5/26 整体改造后).

# 5/26 改造背景

skills_loader 砍 (中央扫员工本机 SKILL.md 违反边界), discover_skills 不可用.
skill_guard 没法再做 intent detection / catalog injection.

新行为:
  - has_skill_intent() 总返 False (没数据源)
  - inject_skill_guard() no-op (原样返 messages)
  - has_skill_tool_in_request() 还工作 — 只查 body.tools 字段, 不依赖 skills_loader

LLM 调 skill 走 hermes 0.14 tool calling 框架: hermes 把每个 skill 当独立 tool 暴露,
LLM 看 tool list 自然知道. 不再需要 gateway 注入"必须调 catfish_run_skill" 铁律.

# 这个 test 覆盖什么 (5/26 重写)

  - has_skill_intent: 任意 user message → False (新员工 / 老员工同款)
  - inject_skill_guard: 任意 messages → 原样返 (幂等也成立)
  - has_skill_tool_in_request: body.tools 含 catfish_run_skill → True
  - has_skill_tool_in_request: body.tools 不含 / 空 / 缺字段 → False
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_gateway.skill_guard import (  # noqa: E402
    has_skill_intent,
    has_skill_tool_in_request,
    inject_skill_guard,
)


# ── has_skill_intent: 5/26 后总返 False ─────────────────────────


@pytest.mark.parametrize(
    "user_text",
    [
        "写一份给公司领导汇报材料",
        "帮我写工作汇报",
        "写一份请示件",
        "起草一份立项报告",
        "你好",
        "今天天气怎么样",
        "",
    ],
)
def test_intent_always_false_after_skills_loader_removed(user_text):
    """5/26 兑现校验: has_skill_intent 不再做检测, 总返 False.

    skills_loader 砍 → discover_skills 不可用 → 没法判断哪些词是 trigger.
    LLM 看 hermes tool list 自然知道调 skill, gateway 不再做 intent 检测.
    """
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": user_text},
    ]
    assert has_skill_intent(msgs) is False


def test_intent_false_for_empty_messages():
    assert has_skill_intent([]) is False


def test_intent_false_for_no_user_msg():
    assert has_skill_intent([{"role": "system", "content": "x"}]) is False


# ── has_skill_tool_in_request: 还工作 (不依赖 skills_loader) ────


def test_has_skill_tool_in_request_with_function_format():
    """OpenAI-style: tools=[{type: function, function: {name: ...}}]."""
    body = {
        "tools": [
            {"type": "function", "function": {"name": "catfish_run_skill", "parameters": {}}},
        ],
    }
    assert has_skill_tool_in_request(body) is True


def test_has_skill_tool_in_request_with_flat_name():
    """老 schema: tools=[{name: ..., ...}]."""
    body = {"tools": [{"name": "catfish_run_skill"}]}
    assert has_skill_tool_in_request(body) is True


def test_has_skill_tool_in_request_without():
    body = {"tools": [{"type": "function", "function": {"name": "execute_code"}}]}
    assert has_skill_tool_in_request(body) is False


def test_has_skill_tool_in_request_empty_tools():
    assert has_skill_tool_in_request({"tools": []}) is False
    assert has_skill_tool_in_request({}) is False
    assert has_skill_tool_in_request({"tools": None}) is False


def test_has_skill_tool_in_request_malformed_tools():
    """tools 不是 list 或元素不是 dict → False, 不抛."""
    assert has_skill_tool_in_request({"tools": "garbage"}) is False
    assert has_skill_tool_in_request({"tools": [None, "string", 42]}) is False


# ── inject_skill_guard: 5/26 后 no-op ──────────────────────────


def test_inject_skill_guard_returns_messages_unchanged():
    """5/26 兑现校验: inject_skill_guard no-op, 原样返."""
    msgs = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "帮我写汇报"},
    ]
    out = inject_skill_guard(msgs, body={"tools": []})
    assert out == msgs


def test_inject_skill_guard_idempotent():
    """no-op 一定幂等."""
    msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "汇报"}]
    once = inject_skill_guard(msgs)
    twice = inject_skill_guard(once)
    assert once == msgs
    assert twice == msgs


def test_inject_skill_guard_with_skills_arg_ignored():
    """老 caller 可能传 skills= 参数, no-op 应忽略."""
    msgs = [{"role": "user", "content": "x"}]
    out = inject_skill_guard(msgs, body={}, skills=[])
    assert out == msgs


def test_inject_skill_guard_empty_messages():
    assert inject_skill_guard([]) == []
