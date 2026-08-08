"""BL-COMPRESSION-GATEWAY (5/15 早) — conversation_compressor 单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_conversation_compressor.py -q

覆盖:
  - estimate_tokens: str / list multimodal / tool_calls
  - maybe_compress_messages: 不触发条件 / 触发条件 / cooldown / 失败 silent
  - env disable 总关
  - is_compression_internal_request 防自递归
  - LLM 调用 mock (不真打 gateway)
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from catfish_gateway.conversation_compressor import (
    DEFAULT_ABS_TOKEN_CAP,
    DEFAULT_KEEP_FIRST,
    DEFAULT_KEEP_LAST,
    ENV_ABS_TOKEN_CAP,
    ENV_DISABLE,
    _abs_token_cap,
    _strip_orphan_tool_boundary,
    estimate_tokens,
    is_compression_internal_request,
    maybe_compress_messages,
    strip_dangling_tail_tool_calls,
)


# ─── estimate_tokens ──────────────────────────────────


def test_estimate_tokens_empty():
    assert estimate_tokens([]) == 0


def test_estimate_tokens_short_string_content():
    msgs = [{"role": "user", "content": "hi"}]
    # 2 chars + role 'user' 4 chars + 4 overhead = ~10 chars / 2.5 = 4
    n = estimate_tokens(msgs)
    assert 0 < n <= 10


def test_estimate_tokens_long_string_content():
    """1000 char content → ~400 token"""
    msgs = [{"role": "user", "content": "x" * 1000}]
    n = estimate_tokens(msgs)
    assert 380 < n < 420  # 1000/2.5 = 400 ± overhead


def test_estimate_tokens_multimodal_content():
    """content 是 list (含 text + image url) — 只算 text 部分"""
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "x" * 500},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},  # 不算
        ],
    }]
    n = estimate_tokens(msgs)
    assert 180 < n < 220  # 500/2.5 = 200


def test_estimate_tokens_tool_calls():
    """assistant 含 tool_calls — arguments JSON 也算 token"""
    msgs = [{
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "x",
                    "arguments": '{"path":"/long/file/path/' + "x" * 200 + '"}',
                },
            },
        ],
    }]
    n = estimate_tokens(msgs)
    assert n > 80  # 200+ chars arg / 2.5


def test_estimate_tokens_multi_message():
    msgs = [
        {"role": "system", "content": "x" * 1000},
        {"role": "user", "content": "y" * 500},
        {"role": "assistant", "content": "z" * 300},
    ]
    n = estimate_tokens(msgs)
    assert 700 < n < 760  # 1800/2.5 = 720 ± overhead


# ─── maybe_compress_messages 触发条件 ─────────────


@pytest.mark.asyncio
async def test_skip_when_messages_too_few():
    """messages 总数不够 (≤ keep_first + keep_last + 6 中段) — 不压"""
    msgs = [{"role": "user", "content": "x" * 10000} for _ in range(5)]
    new, stats = await maybe_compress_messages(
        msgs, user_sub="alice", model_context_window=10000,
    )
    assert stats is None
    assert new is msgs


@pytest.mark.asyncio
async def test_skip_when_under_threshold():
    """estimated < context * threshold — 不压"""
    msgs = [{"role": "user", "content": "x" * 10}] * 30  # 短消息够多, 但 token 少
    new, stats = await maybe_compress_messages(
        msgs, user_sub="alice", model_context_window=128000, threshold_ratio=0.5,
    )
    assert stats is None


@pytest.mark.asyncio
async def test_env_disable_kills_compression(monkeypatch):
    """env CATFISH_DISABLE_GATEWAY_COMPRESSION=1 总开关"""
    monkeypatch.setenv(ENV_DISABLE, "1")
    # 造一个超长 messages 应该触发
    msgs = [{"role": "user", "content": "x" * 10000} for _ in range(30)]
    new, stats = await maybe_compress_messages(
        msgs, user_sub="alice", model_context_window=128000,
    )
    assert stats is None
    assert new is msgs


@pytest.mark.asyncio
async def test_compress_triggers_when_over_threshold(monkeypatch):
    """estimated > context * threshold + 中段足够 → 触发压缩"""
    # Mock _summarize_middle 返假摘要
    monkeypatch.setattr(
        "catfish_gateway.conversation_compressor._summarize_middle",
        AsyncMock(return_value="(压缩摘要: 员工跟鲶鱼讨论 X, 决定 Y, 关键路径 /foo)"),
    )
    # 造长 messages: 30 条 × 2000 char = 60000 char / 2.5 = 24000 token
    # context 30000, threshold 0.5 → cap 15000 < 24000, 触发
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 2000}
        for i in range(30)
    ]
    new, stats = await maybe_compress_messages(
        msgs,
        user_sub="alice",
        model_context_window=30000,
    )
    assert stats is not None
    assert stats["compressed_count"] > 0
    assert stats["pre_token"] > stats["post_token"]
    assert stats["saved_pct"] > 0
    # 新 messages: 头 2 + 1 摘要 + 尾 8 = 11
    assert len(new) == DEFAULT_KEEP_FIRST + 1 + DEFAULT_KEEP_LAST
    # 中间那条是 system 摘要
    assert new[DEFAULT_KEEP_FIRST]["role"] == "system"
    assert "压缩摘要" in new[DEFAULT_KEEP_FIRST]["content"]


@pytest.mark.asyncio
async def test_cooldown_blocks_repeat_compression(monkeypatch):
    """同 sub 5 分钟内重复压 — 第 2 次 cooldown 跳过"""
    monkeypatch.setattr(
        "catfish_gateway.conversation_compressor._summarize_middle",
        AsyncMock(return_value="摘要"),
    )
    msgs = [
        {"role": "user", "content": "x" * 2000} for _ in range(30)
    ]

    # 第 1 次: 压
    new1, stats1 = await maybe_compress_messages(
        msgs, user_sub="alice_cool", model_context_window=30000,
    )
    assert stats1 is not None

    # 第 2 次同 sub: cooldown 跳过
    new2, stats2 = await maybe_compress_messages(
        msgs, user_sub="alice_cool", model_context_window=30000,
    )
    assert stats2 is None


@pytest.mark.asyncio
async def test_llm_failure_returns_original_silently(monkeypatch):
    """LLM 调挂 (_summarize_middle 返 None) — silent 用原 messages"""
    monkeypatch.setattr(
        "catfish_gateway.conversation_compressor._summarize_middle",
        AsyncMock(return_value=None),
    )
    msgs = [
        {"role": "user", "content": "x" * 2000} for _ in range(30)
    ]
    new, stats = await maybe_compress_messages(
        msgs, user_sub="alice_fail", model_context_window=30000,
    )
    assert stats is None
    assert new is msgs  # 原 messages


@pytest.mark.asyncio
async def test_summary_too_long_not_committed(monkeypatch):
    """摘要太长没省 (< 15% 节省) — 不 commit, 用原 messages"""
    # 造一个摘要本身就跟原 message 一样长的 mock
    monkeypatch.setattr(
        "catfish_gateway.conversation_compressor._summarize_middle",
        AsyncMock(return_value="x" * 50000),  # 摘要本身 50K char = 20K token
    )
    # 原 messages 大概 24K token, 摘要 20K, 节省太少 — 应该不 commit
    msgs = [
        {"role": "user", "content": "x" * 2000} for _ in range(30)
    ]
    new, stats = await maybe_compress_messages(
        msgs, user_sub="alice_nosave", model_context_window=30000,
    )
    assert stats is None  # 节省不足跳过


# ─── 防自递归 ─────────────────────────────────────


def test_is_compression_internal_request_true():
    headers = {"X-Catfish-Compression-Internal": "true"}
    assert is_compression_internal_request(headers) is True


def test_is_compression_internal_request_false():
    headers = {}
    assert is_compression_internal_request(headers) is False
    headers = {"X-Catfish-Internal": "true"}  # 不一样的 header
    assert is_compression_internal_request(headers) is False


def test_is_compression_internal_request_case_insensitive():
    """Header 大小写不敏感"""
    # dict.get 是 case-sensitive 但生产代码用 starlette Headers (case-insensitive)
    # 我们的逻辑用 .lower() 比较 value, key 假设 caller 已经传对了
    headers = {"X-Catfish-Compression-Internal": "TRUE"}
    assert is_compression_internal_request(headers) is True


# ─── BL-COMPRESS-BOUNDARY (5/15 14:11 鸿波撞 Qwen 122B 400) ────
#
# 机械切 keep_last 会把 tool 消息切到 assistant_with_tool_calls 之前 —
# Qwen Go gRPC 校验 "tool 必须紧跟匹配 assistant.tool_calls", orphan tool 直 400.
# 修: _strip_orphan_tool_boundary 把段头 orphan tool 跳过 + 段尾悬挂 tool_calls 清掉.


def test_strip_orphan_tool_skips_leading_tool():
    """切点正好落在 tool 上 — 父 assistant 在 middle 被压, tool 进段头 = orphan."""
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "调", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "r1"},
        {"role": "assistant", "content": "答"},
        {"role": "user", "content": "q2"},
    ]
    seg = _strip_orphan_tool_boundary(msgs, 2)  # 切点在 t1 (tool) 上
    assert seg[0]["role"] != "tool", "orphan tool 应被跳过"
    # cut=2 → tool 跳过 → cut=3, segment = messages[3:] = [assistant '答', user 'q2']
    assert len(seg) == 2, f"应剩 2 条 (assistant '答' + user 'q2'), 实际 {len(seg)}"
    assert seg[0]["content"] == "答"
    assert seg[1]["role"] == "user"


def test_strip_orphan_tool_skips_consecutive_orphans():
    """同一 assistant 调了 N 个 tool, 切点把第 1 个 tool 落进段头 — N 个都得跳."""
    msgs = [
        {"role": "assistant", "tool_calls": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "r1"},
        {"role": "tool", "tool_call_id": "t2", "content": "r2"},
        {"role": "tool", "tool_call_id": "t3", "content": "r3"},
        {"role": "assistant", "content": "合并答"},
    ]
    seg = _strip_orphan_tool_boundary(msgs, 1)  # 切点在第 1 个 tool 上
    assert seg[0]["role"] == "assistant", f"3 个 orphan tool 都得跳, 实际 {seg[0]}"
    assert seg[0]["content"] == "合并答"


def test_strip_orphan_tool_normal_user_boundary_unchanged():
    """切点落在正常 user message — 不动."""
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    seg = _strip_orphan_tool_boundary(msgs, 2)
    assert seg == msgs[2:], f"正常对话不该改动: {seg}"


def test_strip_orphan_tool_normal_assistant_boundary_unchanged():
    """切点落在正常 assistant (无 tool_calls) — 不动."""
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "纯文字答, 无 tool"},
    ]
    seg = _strip_orphan_tool_boundary(msgs, 0)
    assert seg == msgs, f"正常 assistant 不该改动: {seg}"


def test_strip_orphan_tool_dangling_trailing_tool_calls():
    """段尾是 assistant.tool_calls, 但后面没 tool reply — 悬挂, 清 tool_calls."""
    msgs = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "我去查",
            "tool_calls": [{"id": "t9", "type": "function", "function": {"name": "search", "arguments": "{}"}}],
        },
    ]
    seg = _strip_orphan_tool_boundary(msgs, 0)
    assert "tool_calls" not in seg[-1], "悬挂 tool_calls 应被清掉"
    assert seg[-1]["content"] == "我去查", "content 应保留"


def test_strip_orphan_tool_dangling_assistant_with_empty_content():
    """段尾悬挂 assistant.tool_calls 且 content 空 — 清完 tool_calls 后用空串 (Qwen 不嫌空)."""
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]},
    ]
    seg = _strip_orphan_tool_boundary(msgs, 0)
    assert "tool_calls" not in seg[-1]
    assert seg[-1].get("content") == "", "空 content 应规范化成 ''"


def test_strip_orphan_tool_cut_beyond_length():
    """cut 超出 messages 长度 — 返空 list."""
    msgs = [{"role": "user", "content": "x"}]
    assert _strip_orphan_tool_boundary(msgs, 99) == []


def test_strip_orphan_tool_empty_messages():
    """空 messages — 不挂."""
    assert _strip_orphan_tool_boundary([], 0) == []


def test_strip_orphan_tool_preserves_valid_tool_pair():
    """段头是 assistant.tool_calls + tool — 完整配对, 不动."""
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "调", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "r1"},
        {"role": "assistant", "content": "答"},
    ]
    seg = _strip_orphan_tool_boundary(msgs, 1)  # 切点在 assistant.tool_calls 上
    # assistant 在段头, tool 紧跟, 配对完整 — 不动
    assert seg[0]["role"] == "assistant"
    assert seg[0].get("tool_calls"), "完整配对的 tool_calls 不该清"
    assert seg[1]["role"] == "tool"


# ─── 8/8: 绝对 token 上限 ────────────────────────────────
#
# 起因: qwen-flash 的 context_window 标 1,000,000, 比例阈值 0.5 → cap 50 万。
# 鸿波实盘单请求 prompt 361,405 token 都不触发。prefill 是按真实 token 数
# 付钱付延迟的, 不按"占 context 的百分比"。


@pytest.mark.asyncio
async def test_abs_cap_triggers_on_huge_context_model(monkeypatch):
    """1M context 的模型: 比例阈值 50 万够不着, 绝对上限 12 万接得住。"""
    monkeypatch.setattr(
        "catfish_gateway.conversation_compressor._summarize_middle",
        AsyncMock(return_value="(摘要)"),
    )
    # 每条 5000 字 × 40 条 = 20 万字符 → estimate 8 万 < 12 万, 不该触发
    small = [{"role": "user", "content": "x" * 5000} for _ in range(40)]
    _, stats = await maybe_compress_messages(
        small, user_sub="u-abs-1", model_context_window=1_000_000, origin_model="m",
    )
    assert stats is None, "8 万 < 绝对上限 12 万, 不该压"

    # 每条 5000 字 × 100 条 = 50 万字符 → estimate 20 万 > 12 万, 该触发
    big = [{"role": "user", "content": "x" * 5000} for _ in range(100)]
    assert estimate_tokens(big) > DEFAULT_ABS_TOKEN_CAP
    assert estimate_tokens(big) < 1_000_000 * 0.5, "构造前提: 比例阈值必须够不着"
    _, stats = await maybe_compress_messages(
        big, user_sub="u-abs-2", model_context_window=1_000_000, origin_model="m",
    )
    assert stats is not None, "超绝对上限就该压 —— 这正是 8/8 之前漏掉的那批请求"


@pytest.mark.asyncio
async def test_abs_cap_does_not_loosen_small_context_model(monkeypatch):
    """小 context 模型仍按比例走 —— 取 min, 绝对上限只会更严不会更松。"""
    monkeypatch.setattr(
        "catfish_gateway.conversation_compressor._summarize_middle",
        AsyncMock(return_value="(摘要)"),
    )
    # 128K 模型: 比例 cap = 6.4 万, 比绝对上限 12 万小, 该以 6.4 万为准
    msgs = [{"role": "user", "content": "x" * 5000} for _ in range(50)]  # estimate 10 万
    est = estimate_tokens(msgs)
    assert 64_000 < est < DEFAULT_ABS_TOKEN_CAP, "构造前提: 夹在两个阈值中间"
    _, stats = await maybe_compress_messages(
        msgs, user_sub="u-abs-3", model_context_window=128_000, origin_model="m",
    )
    assert stats is not None, "比例 cap 更小时应该以比例为准"


@pytest.mark.asyncio
async def test_abs_cap_env_override(monkeypatch):
    monkeypatch.setenv(ENV_ABS_TOKEN_CAP, "1000000")
    monkeypatch.setattr(
        "catfish_gateway.conversation_compressor._summarize_middle",
        AsyncMock(return_value="(摘要)"),
    )
    msgs = [{"role": "user", "content": "x" * 5000} for _ in range(100)]
    _, stats = await maybe_compress_messages(
        msgs, user_sub="u-abs-4", model_context_window=1_000_000, origin_model="m",
    )
    assert stats is None, "env 把上限抬到 100 万后不该再触发"


def test_abs_cap_bad_env_falls_back_to_default(monkeypatch):
    """配错不能把压缩关掉 —— 静默变成"永不触发"是最难查的那种故障。"""
    for bad in ("abc", "", "  ", "-1", "0"):
        monkeypatch.setenv(ENV_ABS_TOKEN_CAP, bad)
        assert _abs_token_cap() == DEFAULT_ABS_TOKEN_CAP


# ─── 8/8: 头段悬挂 tool_calls ─────────────────────────────


def test_strip_dangling_tail_removes_trailing_tool_calls():
    seg = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "调工具", "tool_calls": [{"id": "c1"}]},
    ]
    out = strip_dangling_tail_tool_calls(seg)
    assert "tool_calls" not in out[1]
    assert out[1]["content"] == "调工具"
    assert seg[1]["tool_calls"] == [{"id": "c1"}], "不该改原对象"


def test_strip_dangling_tail_noop_when_paired():
    seg = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "结果"},
    ]
    out = strip_dangling_tail_tool_calls(seg)
    assert out is seg, "没东西要改就原样返回"


def test_strip_dangling_tail_empty():
    assert strip_dangling_tail_tool_calls([]) == []


@pytest.mark.asyncio
async def test_head_segment_dangling_tool_calls_cleaned(monkeypatch):
    """keep_first 段末尾挂着 tool_calls 时, 拼出来的 messages 不能带着它。

    原来只清尾段, 头段 `messages[:keep_first]` 是原样照抄的。真出现
    messages[1] = assistant.tool_calls 时, 它的 tool 回复被压进摘要,
    留下悬挂 —— DashScope 这类严校验上游直接 400 (8/8 那次的同族问题)。
    """
    monkeypatch.setattr(
        "catfish_gateway.conversation_compressor._summarize_middle",
        AsyncMock(return_value="(摘要)"),
    )
    head = [
        {"role": "system", "content": "s" * 100},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "head-call"}]},
    ]
    middle = [{"role": "user", "content": "x" * 20000} for _ in range(20)]
    tail = [{"role": "user", "content": "尾"} for _ in range(DEFAULT_KEEP_LAST)]
    msgs = head + middle + tail

    new, stats = await maybe_compress_messages(
        msgs, user_sub="u-head", model_context_window=128_000, origin_model="m",
    )
    assert stats is not None, "构造的量级应该触发压缩"
    dangling = [
        m for m in new
        if m.get("role") == "assistant" and m.get("tool_calls")
        and not any(
            n.get("role") == "tool"
            for n in new[new.index(m) + 1: new.index(m) + 2]
        )
    ]
    assert not dangling, f"压完不该留悬挂 tool_calls: {dangling}"
    assert msgs[1]["tool_calls"] == [{"id": "head-call"}], "原 messages 不该被写穿"
