"""BL-FIX24 (5/9): duplicate_tool_call_guard 单测.

15 单测覆盖:
- 重复同 productive tool 同 args 触发
- 不同 tool / 不同 args / 单次 / non-productive 不触发
- args JSON 顺序无关 / str vs dict 都识别
- 防重复注入 / 不修改原 messages / 边界
"""
from __future__ import annotations

from catfish_gateway.duplicate_tool_call_guard import (
    _DUP_THRESHOLD,
    _HINT_MARKER,
    _SCAN_DEPTH,
    inject_duplicate_guard_hint,
)


def _msg_assistant_tool_call(name: str, args: str | dict, tc_id: str = "abc"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": tc_id,
                "type": "function",
                "function": {"name": name, "arguments": args if isinstance(args, str) else __import__("json").dumps(args)},
            }
        ],
    }


# ─── 触发 case ──────────────────────────────────────────────────────────


def test_duplicate_execute_code_triggers():
    """重复 execute_code 同 args 2 次 → 触发."""
    code = "from docx import Document\ndoc = Document()\ndoc.save('x.docx')"
    messages = [
        {"role": "user", "content": "写 docx"},
        _msg_assistant_tool_call("execute_code", {"code": code}, "tc1"),
        {"role": "tool", "content": "ok", "tool_call_id": "tc1"},
        {"role": "user", "content": "立刻执行"},
        _msg_assistant_tool_call("execute_code", {"code": code}, "tc2"),
        {"role": "tool", "content": "ok", "tool_call_id": "tc2"},
        {"role": "user", "content": "立刻执行"},
    ]
    result = inject_duplicate_guard_hint(messages)
    assert len(result) == len(messages) + 1
    assert _HINT_MARKER in result[-1]["content"]
    assert "execute_code" in result[-1]["content"]
    assert "2" in result[-1]["content"]  # count


def test_duplicate_catfish_run_skill_triggers():
    """重复 catfish_run_skill 同 args → 触发."""
    args = {"skill_path": "department/weekly-report", "input": "5月"}
    messages = [
        {"role": "user", "content": "写周报"},
        _msg_assistant_tool_call("catfish_run_skill", args, "tc1"),
        {"role": "tool", "content": "ok", "tool_call_id": "tc1"},
        _msg_assistant_tool_call("catfish_run_skill", args, "tc2"),
        {"role": "tool", "content": "ok", "tool_call_id": "tc2"},
        {"role": "user", "content": "继续"},
    ]
    result = inject_duplicate_guard_hint(messages)
    assert len(result) == len(messages) + 1
    assert "catfish_run_skill" in result[-1]["content"]


def test_three_repeats_count_correct():
    """3 次重复 → count=3 (鸿波 demo 现场跑了 5 次, 反映真实场景)."""
    code = "doc.save('x')"
    messages = []
    for i in range(3):
        messages.append({"role": "user", "content": "立刻执行"})
        messages.append(_msg_assistant_tool_call("execute_code", {"code": code}, f"tc{i}"))
        messages.append({"role": "tool", "content": "ok", "tool_call_id": f"tc{i}"})
    result = inject_duplicate_guard_hint(messages)
    assert "3" in result[-1]["content"]


# ─── 不触发 case ────────────────────────────────────────────────────────


def test_different_tools_no_trigger():
    """不同 tool 各调一次 → 不触发 (browser_screenshot + execute_code)."""
    messages = [
        {"role": "user", "content": "截图 + 写 docx"},
        _msg_assistant_tool_call("execute_code", {"code": "x"}, "tc1"),
        _msg_assistant_tool_call("write_file", {"path": "y.txt"}, "tc2"),
    ]
    result = inject_duplicate_guard_hint(messages)
    assert result is messages or len(result) == len(messages)


def test_same_tool_different_args_no_trigger():
    """同 tool 不同 args → hash 不同, 不触发."""
    messages = [
        {"role": "user", "content": "写 2 个 docx"},
        _msg_assistant_tool_call("execute_code", {"code": "doc1.save()"}, "tc1"),
        _msg_assistant_tool_call("execute_code", {"code": "doc2.save()"}, "tc2"),
    ]
    result = inject_duplicate_guard_hint(messages)
    assert len(result) == len(messages)


def test_single_call_no_trigger():
    """单次调用 → 不触发."""
    messages = [
        {"role": "user", "content": "写 docx"},
        _msg_assistant_tool_call("execute_code", {"code": "x"}, "tc1"),
    ]
    result = inject_duplicate_guard_hint(messages)
    assert len(result) == len(messages)


def test_non_productive_tool_no_trigger():
    """非 productive tool (browser_screenshot 多次截图) → 不触发."""
    messages = [
        _msg_assistant_tool_call("catfish_browser_screenshot", {"selector": "#a"}, "tc1"),
        _msg_assistant_tool_call("catfish_browser_screenshot", {"selector": "#a"}, "tc2"),
        _msg_assistant_tool_call("catfish_browser_screenshot", {"selector": "#a"}, "tc3"),
    ]
    result = inject_duplicate_guard_hint(messages)
    assert len(result) == len(messages)


def test_empty_messages_no_trigger():
    """空 messages 不崩."""
    result = inject_duplicate_guard_hint([])
    assert result == []


def test_no_assistant_messages_no_trigger():
    """只有 user / system 没 assistant → 不触发."""
    messages = [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."},
    ]
    result = inject_duplicate_guard_hint(messages)
    assert len(result) == len(messages)


# ─── normalization ──────────────────────────────────────────────────────


def test_args_json_key_order_irrelevant():
    """args JSON key 顺序不同但内容一样 → hash 一致 → 触发."""
    args1 = '{"code": "x", "lang": "py"}'
    args2 = '{"lang": "py", "code": "x"}'  # 不同顺序
    messages = [
        _msg_assistant_tool_call("execute_code", args1, "tc1"),
        _msg_assistant_tool_call("execute_code", args2, "tc2"),
    ]
    result = inject_duplicate_guard_hint(messages)
    assert len(result) == len(messages) + 1


def test_args_dict_vs_string_same_content_triggers():
    """args 一次 dict 一次 str 但内容相同 → hash 一致 → 触发."""
    code = "doc.save('x')"
    msg1 = _msg_assistant_tool_call("execute_code", {"code": code}, "tc1")
    msg2 = _msg_assistant_tool_call("execute_code", {"code": code}, "tc2")
    # 手动改 msg2 的 arguments 为 dict (跳 _msg_assistant_tool_call 默认 dumps)
    msg2["tool_calls"][0]["function"]["arguments"] = {"code": code}
    result = inject_duplicate_guard_hint([msg1, msg2])
    assert len(result) == 3


# ─── hint marker / 防重复 ──────────────────────────────────────────────


def test_hint_marker_in_injected():
    """注入的 hint 含 marker, 防重复 + 调试可识别."""
    code = "x"
    messages = [
        _msg_assistant_tool_call("execute_code", {"code": code}, "tc1"),
        _msg_assistant_tool_call("execute_code", {"code": code}, "tc2"),
    ]
    result = inject_duplicate_guard_hint(messages)
    assert _HINT_MARKER in result[-1]["content"]


def test_no_double_inject_when_marker_present():
    """如果 messages 末尾已有 marker → 不再注入."""
    code = "x"
    messages = [
        _msg_assistant_tool_call("execute_code", {"code": code}, "tc1"),
        _msg_assistant_tool_call("execute_code", {"code": code}, "tc2"),
        {"role": "user", "content": f"{_HINT_MARKER}\n之前注入的"},
    ]
    result = inject_duplicate_guard_hint(messages)
    assert len(result) == len(messages)  # 没新增


def test_does_not_mutate_input():
    """不原地改 messages, 跟 self_critique 一致."""
    code = "x"
    original = [
        _msg_assistant_tool_call("execute_code", {"code": code}, "tc1"),
        _msg_assistant_tool_call("execute_code", {"code": code}, "tc2"),
    ]
    snapshot_len = len(original)
    inject_duplicate_guard_hint(original)
    assert len(original) == snapshot_len  # 原 messages 没变


# ─── 常量 ───────────────────────────────────────────────────────────────


def test_threshold_and_scan_depth_constants():
    """常量值合理, 防回归."""
    assert _DUP_THRESHOLD == 2  # 2 次就拦, 不放过 demo 现场症状
    assert _SCAN_DEPTH == 12   # 扫近 12 条, 覆盖 5 轮往返
