"""skill_guard 测试 — gateway 工程级强制保护.

验证:
  - 意图检测准确 (汇报/请示/立项 等命中, 闲聊不命中)
  - tools 列表里有 catfish_run_skill → 加 REQUIRED block (强制调 skill)
  - 缺 catfish_run_skill → 加 MISSING block (警告员工 Cmd+R)
  - 没意图 → 不动 messages
  - 幂等
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


# ── 意图检测 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "user_text",
    [
        "写一份给公司领导汇报材料",
        "帮我写工作汇报",
        "写一份请示件",
        "起草一份立项报告",
        "写一份呈批件",
        "给王总写一份汇报",
        "给集团汇报",
        "上报材料一份",
        "决策事项报告",
    ],
)
def test_intent_hits(user_text):
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": user_text},
    ]
    assert has_skill_intent(msgs) is True


@pytest.mark.parametrize(
    "user_text",
    [
        "你好",
        "今天天气怎么样",
        "帮我写个 Python 函数",
        "解释一下什么是 OAuth",
        "写一段代码",  # 不含汇报/请示等
    ],
)
def test_intent_misses(user_text):
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": user_text},
    ]
    assert has_skill_intent(msgs) is False


def test_intent_only_checks_last_user_message():
    """历史里有触发词不算, 只看最后一条 user."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "之前我让你写过汇报"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "现在帮我查个天气"},
    ]
    assert has_skill_intent(msgs) is False


def test_intent_handles_multimodal_content():
    """user content 是 list (multimodal) 时也能扫."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [
            {"type": "text", "text": "写一份请示件"},
            {"type": "image_url", "image_url": {"url": "..."}},
        ]},
    ]
    assert has_skill_intent(msgs) is True


# ── tools 检测 ──────────────────────────────────────────────────


def test_tool_present_openai_format():
    body = {"tools": [
        {"type": "function", "function": {"name": "catfish_run_skill"}},
        {"type": "function", "function": {"name": "execute_code"}},
    ]}
    assert has_skill_tool_in_request(body) is True


def test_tool_present_flat_format():
    """兼容某些客户端用平铺格式 {name: ...}."""
    body = {"tools": [{"name": "catfish_run_skill"}]}
    assert has_skill_tool_in_request(body) is True


def test_tool_missing():
    body = {"tools": [
        {"type": "function", "function": {"name": "execute_code"}},
        {"type": "function", "function": {"name": "terminal"}},
    ]}
    assert has_skill_tool_in_request(body) is False


def test_tool_no_tools_field():
    """body 没 tools 字段 → 视为缺."""
    assert has_skill_tool_in_request({}) is False
    assert has_skill_tool_in_request({"tools": None}) is False
    assert has_skill_tool_in_request({"tools": []}) is False


# ── inject 行为 ────────────────────────────────────────────────


def test_inject_required_when_intent_and_tool_present():
    """意图触发 + 工具就位 → REQUIRED block."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "写一份请示件"},
    ]
    body = {
        "tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}]
    }
    out = inject_skill_guard(msgs, body)
    sys_content = out[0]["content"]
    assert "必须" in sys_content
    assert "catfish_run_skill" in sys_content
    assert "严禁" in sys_content
    # 关键: 严禁 execute_code 字样
    assert "execute_code" in sys_content


def test_inject_missing_when_intent_but_no_tool():
    """意图触发 + 工具缺失 → MISSING block + Cmd+R 引导."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "写一份请示件"},
    ]
    body = {"tools": []}  # 没 catfish_run_skill
    out = inject_skill_guard(msgs, body)
    sys_content = out[0]["content"]
    assert "未注册" in sys_content
    assert "Cmd" in sys_content  # 引导用户 Cmd+R
    assert "刷新" in sys_content


def test_inject_no_intent_does_nothing():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "你好"},
    ]
    body = {"tools": []}
    out = inject_skill_guard(msgs, body)
    assert out == msgs


def test_inject_no_system_message_does_nothing():
    msgs = [{"role": "user", "content": "写汇报"}]
    out = inject_skill_guard(msgs, {})
    assert out == msgs


def test_inject_idempotent():
    """同样内容连调两次, 不重复追加."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "写汇报"},
    ]
    body = {
        "tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}]
    }
    once = inject_skill_guard(msgs, body)
    twice = inject_skill_guard(once, body)
    assert once[0]["content"] == twice[0]["content"]


def test_inject_does_not_mutate_input():
    msgs = [
        {"role": "system", "content": "原始"},
        {"role": "user", "content": "写汇报"},
    ]
    original = msgs[0]["content"]
    inject_skill_guard(msgs, {"tools": []})
    assert msgs[0]["content"] == original


def test_inject_targets_last_system_when_multiple():
    msgs = [
        {"role": "system", "content": "first"},
        {"role": "user", "content": "u1"},
        {"role": "system", "content": "second"},
        {"role": "user", "content": "写汇报"},
    ]
    body = {
        "tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}]
    }
    out = inject_skill_guard(msgs, body)
    assert out[0]["content"] == "first"  # 第一段不动
    assert "catfish_run_skill" in out[2]["content"]  # 第二段被追加


def test_inject_without_body_assumes_tool_present():
    """body=None 时假设工具就位 (避免误警告)."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "写汇报"},
    ]
    out = inject_skill_guard(msgs, None)
    assert "必须" in out[0]["content"]  # REQUIRED 路径
    assert "未注册" not in out[0]["content"]
