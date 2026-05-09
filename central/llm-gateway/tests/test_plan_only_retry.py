"""BL-FIX23 L5 (5/9): gateway plan-only retry 单测.

测的是触发判定 helpers (_is_plan_only_content + _last_user_message_is_feedback).
真 stream retry 流程需要 mock LiteLLM acompletion + iterator, 工程量大, 5/14
demo 之前先靠 helper 单测 + 鸿波真机验证. 端到端 mock test 排 demo 后做.
"""
from __future__ import annotations

from catfish_gateway.app import (
    _PLAN_ONLY_HARD_HINT,
    _MAX_PLAN_ONLY_RETRIES,
    _is_plan_only_content,
    _last_user_message_is_feedback,
)


# ─── _is_plan_only_content ────────────────────────────────────────────────


def test_plan_only_promise_keyword_triggers():
    """含 '已生成' / '已修改' 等承诺关键词 → 触发."""
    assert _is_plan_only_content("文档已生成, 已保存到 ~/Documents/x.docx")
    assert _is_plan_only_content("我已修改完毕, 请查看")
    assert _is_plan_only_content("数据已经完成调整")


def test_plan_only_future_intent_triggers():
    """含 '我立刻' / '马上动手' / '现在重新生成' → 触发."""
    assert _is_plan_only_content("明白鸿波, 我立刻调整 X 现在重新生成")
    assert _is_plan_only_content("好的, 马上动手做")
    assert _is_plan_only_content("我现在重新调整内容区")


def test_plan_only_short_content_no_trigger():
    """很短 content (< 10 字) 不触发, 防误判."""
    assert not _is_plan_only_content("好")
    assert not _is_plan_only_content("OK")
    assert not _is_plan_only_content("已生成")  # 7 字, 不触发


def test_plan_only_no_keyword_no_trigger():
    """没承诺/未来词 → 不触发."""
    assert not _is_plan_only_content("这是公司 5 月份的资质统计数据, 共 25 项资质涉及 18 个部门.")
    assert not _is_plan_only_content("根据你的数据, 主要分布是工程类 60%, 管理类 30%.")


def test_plan_only_empty_or_none():
    """空内容不触发."""
    assert not _is_plan_only_content("")
    assert not _is_plan_only_content(None)  # type: ignore[arg-type]


# ─── _last_user_message_is_feedback ──────────────────────────────────────


def test_feedback_short_user_message_triggers():
    """短 user message (< 30 字) 默认认为是反馈."""
    msgs = [
        {"role": "user", "content": "帮我做 docx 工作通知单"},
        {"role": "assistant", "content": "已生成"},
        {"role": "user", "content": "改"},
    ]
    assert _last_user_message_is_feedback(msgs)


def test_feedback_keyword_in_long_message():
    """长 message 但含反馈关键词 ('调整' / '错' / '继续' / '完成' 等) → 触发."""
    msgs = [
        {"role": "user", "content": "做 docx"},
        {"role": "assistant", "content": "明白"},
        {"role": "user", "content": "你这个内容写错了, 资质不应该包含 CMMI 五级, 调整一下"},
    ]
    assert _last_user_message_is_feedback(msgs)


def test_feedback_long_no_keyword_no_trigger():
    """长 user message + 没反馈词 → 不算反馈 (是正经 prompt 不是 follow-up)."""
    msgs = [
        {"role": "user", "content": "请你帮我撰写一份关于公司 2026 年第一季度市场拓展情况的详细分析报告, 涉及华东、华南、华北三大区域, 每个区域分别列举 5 个主要客户案例."},
    ]
    assert not _last_user_message_is_feedback(msgs)


def test_feedback_multipart_text_extracted():
    """user message multipart (含 text + image) 正确提取 text 部分."""
    msgs = [
        {"role": "user", "content": [
            {"type": "text", "text": "改"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]},
    ]
    assert _last_user_message_is_feedback(msgs)


def test_feedback_no_user_message():
    """没 user message → 不触发."""
    assert not _last_user_message_is_feedback([])
    assert not _last_user_message_is_feedback([{"role": "system", "content": "..."}])


def test_feedback_skips_assistant_and_tool():
    """倒序找 user, 跳过 assistant / tool messages."""
    msgs = [
        {"role": "user", "content": "继续干啊"},
        {"role": "assistant", "content": "明白", "tool_calls": [{"id": "x"}]},
        {"role": "tool", "content": "result"},
        {"role": "assistant", "content": "明白鸿波, 我立刻..."},
    ]
    # 最近 user 是 "继续干啊" (短), 触发
    assert _last_user_message_is_feedback(msgs)


# ─── 常量 ─────────────────────────────────────────────────────────────────


def test_hard_hint_marker_present():
    """硬 hint 含 marker, 防回归."""
    assert "[BL-FIX23 L5 plan-only-retry]" in _PLAN_ONLY_HARD_HINT
    assert "tool_call" in _PLAN_ONLY_HARD_HINT
    assert "execute_code" in _PLAN_ONLY_HARD_HINT


def test_max_retries_is_2():
    """重发上限固定 2 次, 防死循环."""
    assert _MAX_PLAN_ONLY_RETRIES == 2
