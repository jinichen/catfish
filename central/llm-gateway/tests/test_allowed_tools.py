"""BL-RBAC-DAY4 (5/17) — User.can_use_tool + sanitize_tools RBAC 过滤测试.

跟 test_allowed_models 完全对称:
  - 空 list → 全允许
  - [t1, t2] → 只允许这俩 + ALWAYS_ON_TOOLS 兜底
  - sysadmin 永远绕过
  - sanitize_tools 不传 user → 不过滤 (兼容 loopback / 老 caller)
  - 历史 tool_calls 引用被 RBAC 干掉的 tool → 一并 scrub (BL-FIX5 套路)
"""
from __future__ import annotations

from catfish_gateway.auth.base import User
from catfish_gateway.tools_sanitizer import sanitize_tools


def _make_tool(name: str) -> dict:
    """造一个最小合法 OpenAI function tool."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }


# ── User.can_use_tool 单元测 ──────────────────────────────────────


def test_empty_allowed_tools_means_all_allowed():
    """空 list → 全允许."""
    u = User(sub="alice@x", effective_allowed_tools=[])
    assert u.can_use_tool("catfish_run_skill") is True
    assert u.can_use_tool("catfish_browser_open") is True
    assert u.can_use_tool("anything") is True


def test_none_allowed_tools_means_all_allowed():
    """None (没传) → __post_init__ 转空 list → 全允许."""
    u = User(sub="alice@x")
    assert u.effective_allowed_tools == []
    assert u.can_use_tool("catfish_browser_open") is True


def test_sales_no_browser_tools():
    """销售场景: 部门白名单不含 browser_*, 收紧."""
    u = User(
        sub="sales@x",
        department="sales",
        effective_allowed_tools=[
            "catfish_run_skill",
            "search_skills",
            "memory",
        ],
    )
    assert u.can_use_tool("catfish_run_skill") is True
    assert u.can_use_tool("memory") is True
    assert u.can_use_tool("catfish_browser_open") is False
    assert u.can_use_tool("catfish_read_url") is False


def test_sysadmin_bypasses_rbac():
    """sysadmin 即使 effective_allowed_tools 收紧也全允许."""
    u = User(
        sub="admin@x",
        role="sysadmin",
        effective_allowed_tools=["memory"],  # 表面只允许这个
    )
    # 但 sysadmin 任何 tool 都能用
    assert u.can_use_tool("catfish_browser_open") is True
    assert u.can_use_tool("any_secret_tool") is True


def test_admin_does_not_bypass_rbac_tools():
    """admin 不绕过 (跟 allowed_models 同语义)."""
    u = User(
        sub="admin@x",
        role="admin",
        effective_allowed_tools=["memory"],
    )
    assert u.can_use_tool("memory") is True
    assert u.can_use_tool("catfish_browser_open") is False


def test_empty_tool_name_rejected():
    """空 tool name 在白名单收紧下拒绝."""
    u = User(sub="x@y", effective_allowed_tools=["memory"])
    assert u.can_use_tool("") is False
    assert u.can_use_tool(None) is False  # type: ignore[arg-type]


# ── sanitize_tools 集成: user 给了过滤, 不给不过滤 ───────────────


def test_sanitize_tools_no_user_no_rbac_filter():
    """user=None → 不过滤 (兼容老 caller / loopback)."""
    body = {
        "tools": [
            _make_tool("catfish_browser_open"),
            _make_tool("memory"),
            _make_tool("foo_bar"),
        ]
    }
    out = sanitize_tools(body)
    names = [t["function"]["name"] for t in out["tools"]]
    assert "catfish_browser_open" in names
    assert "memory" in names
    assert "foo_bar" in names


def test_sanitize_tools_rbac_drops_disallowed():
    """user 给 + 白名单收紧 → drop 不允许的 tool, 但 ALWAYS_ON 永远保留."""
    user = User(
        sub="sales@x",
        department="sales",
        effective_allowed_tools=["foo_bar"],  # 只允许 foo_bar
    )
    body = {
        "tools": [
            _make_tool("catfish_browser_open"),  # 不在白名单 → drop
            _make_tool("memory"),  # 在 ALWAYS_ON → 保
            _make_tool("foo_bar"),  # 在白名单 → 保
            _make_tool("execute_code"),  # 在 ALWAYS_ON → 保
        ]
    }
    out = sanitize_tools(body, user=user)
    names = [t["function"]["name"] for t in out["tools"]]
    assert "catfish_browser_open" not in names  # RBAC drop
    assert "memory" in names  # ALWAYS_ON 兜底
    assert "foo_bar" in names  # 白名单
    assert "execute_code" in names  # ALWAYS_ON


def test_sanitize_tools_sysadmin_bypass():
    """sysadmin 即使 effective_allowed_tools 收紧也保留全 tool."""
    user = User(
        sub="root@x",
        role="sysadmin",
        effective_allowed_tools=["foo_bar"],
    )
    body = {
        "tools": [
            _make_tool("catfish_browser_open"),
            _make_tool("any_random_tool"),
        ]
    }
    out = sanitize_tools(body, user=user)
    names = [t["function"]["name"] for t in out["tools"]]
    assert "catfish_browser_open" in names
    assert "any_random_tool" in names


def test_sanitize_tools_empty_allowed_tools_full_pass():
    """空 effective_allowed_tools (默认 / 无 dept 配置) → 不过滤."""
    user = User(
        sub="dev@x",
        department="engineering",
        effective_allowed_tools=[],  # 全允许
    )
    body = {
        "tools": [
            _make_tool("catfish_browser_open"),
            _make_tool("memory"),
            _make_tool("foo_bar"),
        ]
    }
    out = sanitize_tools(body, user=user)
    names = [t["function"]["name"] for t in out["tools"]]
    assert "catfish_browser_open" in names
    assert "memory" in names
    assert "foo_bar" in names


def test_sanitize_tools_rbac_scrubs_history():
    """RBAC drop 的 tool 在历史里有 assistant.tool_calls → 一起 scrub.

    防 BL-FIX5 同款撞 Qwen Go gRPC adapter 'assistant 调过的 tool 必须在
    tools 列表里' 校验 → 空 reason 400.
    """
    user = User(
        sub="sales@x",
        department="sales",
        effective_allowed_tools=["memory"],
    )
    body = {
        "tools": [
            _make_tool("catfish_browser_open"),  # drop
            _make_tool("memory"),
        ],
        "messages": [
            {"role": "user", "content": "open google"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "catfish_browser_open",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "<html>...</html>",
            },
        ],
    }
    out = sanitize_tools(body, user=user)
    # 历史里 dropped tool 的 assistant.tool_calls + 对应 tool message 都被 scrub
    for msg in out.get("messages", []):
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []) or []:
                assert tc["function"]["name"] != "catfish_browser_open"
        elif msg.get("role") == "tool":
            assert msg.get("tool_call_id") != "call_1"
