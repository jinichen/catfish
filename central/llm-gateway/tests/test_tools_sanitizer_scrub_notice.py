"""BL-LOOP-C (5/21 鸿波) — scrub 后注入"工具已砍"反馈测试.

抽自 test_tools_sanitizer.py (拆分超 800 行红线时 5/21 抽).

# 历史

老 bug: BL-FIX4 dedupe / BL-TOOL-CAP / BL-RBAC 砍 tool 后, scrub 把 assistant.
tool_calls + tool message 整条删. LLM 收到 history 看不到自己调过被砍工具, 下轮
又 emit 同名 tool_call → loop (5/21 鸿波 19:30 日志诊断: c90300056cee /
e49d475cec4a 两 session 30s 反复同 3 个工具 browser_snapshot /
confirm_expertise / create_calendar_event).

修法: scrub 完成后调 _inject_scrub_notice — 在 system message 末尾追加"工具
状态告知", 给 LLM 显式信号. 不动 assistant/tool 历史 shape (避免上游 Qwen Go
gRPC 校验 400). 用 SCRUB_NOTICE_MARKER 标记位置, 跑两次自动 strip 老 notice
重写 (幂等).

# 这个 test 覆盖什么

  - _inject_scrub_notice helper 单测:
    - 已有 system → notice 追加, 含 marker
    - 没 system → 不主动 insert (老 test 不破)
    - dropped_names 空 → 不动
    - 幂等性: 跑两次结果一样
    - 更新 dropped_names → 老 notice strip, 新 notice 写入
    - 老 system text 保留, 不被截
    - system content 是 list (multipart) → 跳过
  - sanitize_tools 端到端集成: dedupe + scrub → system 含 notice
"""
from __future__ import annotations

from catfish_gateway.tools_sanitizer import (
    SCRUB_NOTICE_MARKER,
    _inject_scrub_notice,
    sanitize_tools,
)


def _make_assistant_tool_call(call_id: str, name: str, args: str = "{}") -> dict:
    """跟 test_tools_sanitizer.py 同款 helper (本文件独立, 复制一份)."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": args},
        }],
    }


# ── helper 单测 ────────────────────────────────────────────────


def test_inject_scrub_notice_appends_to_existing_system() -> None:
    """已有 system message → notice 追加到末尾, 含 marker."""
    messages: list = [
        {"role": "system", "content": "你是鲶鱼 AI 副手."},
        {"role": "user", "content": "登录 EIS"},
    ]
    _inject_scrub_notice(messages, {"browser_vision", "browser_back"})
    assert messages[0]["role"] == "system"
    content = messages[0]["content"]
    assert "你是鲶鱼 AI 副手." in content  # 老内容保留
    assert SCRUB_NOTICE_MARKER in content
    assert "browser_vision" in content
    assert "browser_back" in content


def test_inject_scrub_notice_no_system_skips_silently() -> None:
    """没 system message → 不主动 insert (老 test 不破 + 测试/直接 curl 不污染)."""
    messages: list = [
        {"role": "user", "content": "登录 EIS"},
    ]
    _inject_scrub_notice(messages, {"browser_vision"})
    # messages 不变 — 没插 system
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


def test_inject_scrub_notice_empty_dropped_no_op() -> None:
    """dropped_names 空 → 不动 messages."""
    messages: list = [{"role": "system", "content": "原 sys"}]
    _inject_scrub_notice(messages, set())
    assert messages == [{"role": "system", "content": "原 sys"}]


def test_inject_scrub_notice_idempotent_strips_old_notice() -> None:
    """跑两次只剩一份 notice (marker 之后老的被 strip 重写)."""
    messages: list = [
        {"role": "system", "content": "你是 AI."},
    ]
    _inject_scrub_notice(messages, {"foo_tool"})
    once = messages[0]["content"]
    # 第二次注同 dropped → 内容相同 (不堆叠)
    _inject_scrub_notice(messages, {"foo_tool"})
    twice = messages[0]["content"]
    assert once == twice
    # marker 在 system 里只出现 1 次
    assert twice.count(SCRUB_NOTICE_MARKER) == 1


def test_inject_scrub_notice_idempotent_update_dropped_set() -> None:
    """第二次 dropped_names 变了 → 老 notice strip, 新 notice 反映新集合."""
    messages: list = [
        {"role": "system", "content": "你是 AI."},
    ]
    _inject_scrub_notice(messages, {"foo_tool"})
    _inject_scrub_notice(messages, {"bar_tool", "baz_tool"})
    content = messages[0]["content"]
    assert content.count(SCRUB_NOTICE_MARKER) == 1
    assert "foo_tool" not in content  # 老 notice 被替换
    assert "bar_tool" in content
    assert "baz_tool" in content


def test_inject_scrub_notice_preserves_system_text() -> None:
    """老 system content (非 notice 部分) 保留, 不被截."""
    messages: list = [
        {"role": "system", "content": "原始 prompt 多行\n第二行内容"},
    ]
    _inject_scrub_notice(messages, {"x"})
    content = messages[0]["content"]
    assert content.startswith("原始 prompt 多行\n第二行内容")
    assert SCRUB_NOTICE_MARKER in content


def test_inject_scrub_notice_non_str_content_skipped() -> None:
    """system content 是 list (multipart) → 跳过, 不破坏."""
    messages: list = [
        {"role": "system", "content": [{"type": "text", "text": "rich"}]},
    ]
    _inject_scrub_notice(messages, {"x"})
    # 不动
    assert messages[0]["content"] == [{"type": "text", "text": "rich"}]


# ── 端到端集成: sanitize_tools 触发 dedupe → scrub → notice ──


def test_full_sanitize_integration_injects_notice() -> None:
    """端到端: sanitize_tools 触发 dedupe + scrub 后, system 含 notice."""
    body = {
        "messages": [
            {"role": "system", "content": "你是 AI."},
            {"role": "user", "content": "登录"},
            _make_assistant_tool_call("c1", "browser_vision"),
            {"role": "tool", "content": "err", "tool_call_id": "c1"},
        ],
        "tools": [
            {"type": "function", "function": {
                "name": "catfish_browser_goto",
                "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {
                "name": "browser_vision",
                "parameters": {"type": "object", "properties": {}}}},
        ],
    }
    out = sanitize_tools(body)
    sys_msg = out["messages"][0]
    assert sys_msg["role"] == "system"
    assert SCRUB_NOTICE_MARKER in sys_msg["content"]
    assert "browser_vision" in sys_msg["content"]
