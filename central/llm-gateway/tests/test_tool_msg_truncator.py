"""BL-FIX41 tool_msg_truncator 单测."""

from __future__ import annotations

from catfish_gateway.tool_msg_truncator import (
    MAX_BYTES_PER_TOOL,
    MIN_BYTES_TO_TRUNCATE,
    _truncate_text,
    truncate_tool_messages,
)


def test_short_content_untouched():
    s = "hello world" * 10
    out, cut = _truncate_text(s, 2000)
    assert out == s
    assert cut == 0


def test_long_english_byte_capped():
    s = "x" * 5000
    out, cut = _truncate_text(s, 2000)
    assert cut == 3000
    assert len(out.encode("utf-8")) <= MIN_BYTES_TO_TRUNCATE
    assert "已截断" in out


def test_long_chinese_byte_safe():
    """中文一字符 3 字节, byte 级切可能切坏多字节字符 — errors=ignore 兜底."""
    s = "这是一段很长的工具输出日志" * 500  # ~19500 bytes
    out, cut = _truncate_text(s, 2000)
    assert cut > 0
    # decode 不应该崩
    assert isinstance(out, str)
    assert "已截断" in out


def test_message_count_preserved():
    """Hermes ReAct chain: assistant ↔ tool 一对一, 不能少一条."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "a", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "a", "content": "short stdout"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "b", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "b", "content": "PASSED " * 2000},  # ~14KB
    ]
    out = truncate_tool_messages(msgs)
    assert len(out) == len(msgs)
    # 短的不动
    assert out[3]["content"] == "short stdout"
    # 长的截
    assert "已截断" in out[5]["content"]
    assert len(out[5]["content"].encode("utf-8")) <= MIN_BYTES_TO_TRUNCATE


def test_idempotent():
    msgs = [
        {"role": "tool", "tool_call_id": "a", "content": "x" * 10000},
    ]
    out1 = truncate_tool_messages(msgs)
    out2 = truncate_tool_messages(out1)
    assert out1[0]["content"] == out2[0]["content"]


def test_non_tool_content_untouched():
    """只截 role=tool. user / assistant / system 不动."""
    msgs = [
        {"role": "user", "content": "x" * 10000},
        {"role": "assistant", "content": "y" * 10000},
        {"role": "system", "content": "z" * 10000},
    ]
    out = truncate_tool_messages(msgs)
    for i, m in enumerate(msgs):
        assert out[i]["content"] == m["content"]


def test_non_string_content_skipped():
    """role=tool 但 content 是 list (BL-FIX2 unwrap 之前的多模态) → 跳过."""
    msgs = [
        {
            "role": "tool",
            "tool_call_id": "a",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}],
        },
    ]
    out = truncate_tool_messages(msgs)
    assert out[0]["content"] == msgs[0]["content"]


def test_empty_messages():
    assert truncate_tool_messages([]) == []
    assert truncate_tool_messages(None) is None  # type: ignore[arg-type]


def test_no_mutation_of_original():
    """原 messages 不应被改写."""
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 10000}]
    original_content = msgs[0]["content"]
    _ = truncate_tool_messages(msgs)
    assert msgs[0]["content"] == original_content


def test_realistic_overflow_scenario():
    """模拟鸿波 demo 前夜的 199 messages / tool_msgs=63 场景."""
    big = [{"role": "system", "content": "sys"}]
    for i in range(60):
        big.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"t{i}", "type": "function", "function": {"name": "x", "arguments": "{}"}}]})
        # 200 行 × 9B = 1800B, 不到 2KB 阈值 — 不截
        big.append({"role": "tool", "tool_call_id": f"t{i}", "content": "log line\n" * 200})
    for i in range(60, 63):
        big.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"t{i}", "type": "function", "function": {"name": "x", "arguments": "{}"}}]})
        # 50KB 巨型 stdout — 一定截
        big.append({"role": "tool", "tool_call_id": f"t{i}", "content": "BIG\n" + ("x" * 50000)})

    before = sum(
        len(m.get("content", "").encode("utf-8"))
        if isinstance(m.get("content"), str) else 0
        for m in big
    )
    out = truncate_tool_messages(big)
    after = sum(
        len(m.get("content", "").encode("utf-8"))
        if isinstance(m.get("content"), str) else 0
        for m in out
    )
    assert len(out) == len(big), "消息数量不能变"
    # 至少省 140KB (3 条 50KB 巨型 → 6KB)
    assert before - after > 140_000


def test_min_trigger_just_above_cap():
    """临界场景: 内容刚好等于 cap 不截; cap + 50 不截 (slack); cap + 250 截."""
    just_cap = "x" * MAX_BYTES_PER_TOOL
    msgs = [{"role": "tool", "tool_call_id": "a", "content": just_cap}]
    out = truncate_tool_messages(msgs)
    assert out[0]["content"] == just_cap, "刚到 cap 不截"

    in_slack = "x" * (MAX_BYTES_PER_TOOL + 100)
    msgs = [{"role": "tool", "tool_call_id": "a", "content": in_slack}]
    out = truncate_tool_messages(msgs)
    assert out[0]["content"] == in_slack, "slack 范围内不截"

    over = "x" * (MAX_BYTES_PER_TOOL + 500)
    msgs = [{"role": "tool", "tool_call_id": "a", "content": over}]
    out = truncate_tool_messages(msgs)
    assert "已截断" in out[0]["content"], "超 slack 必截"
