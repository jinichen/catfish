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


def test_plan_only_short_ack_no_trigger():
    """简单 ack ('好' / 'OK' / 'thanks') 不含承诺关键词 → 不触发, 防误判.

    含承诺关键词的 (如 '已生成 docx') 即使短也要触发 — 这是 LLM
    plan-only 的典型形态, 外层 4 条 AND (没 tool_call + 反馈 + retries)
    保证不会误兜.
    """
    assert not _is_plan_only_content("好")
    assert not _is_plan_only_content("OK")
    assert not _is_plan_only_content("thanks")
    assert not _is_plan_only_content("收到")
    # '已生成' 短 (3 字) 但含关键词 → 触发. 外层会拿 cumulative_has_tool_call
    # 过滤真做了的 case.
    assert _is_plan_only_content("已生成")


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
    # L7 (5/11): marker 从 L5 升 L7, 防止误更
    assert "[BL-FIX23 L7 plan-only-retry]" in _PLAN_ONLY_HARD_HINT
    assert "tool_call" in _PLAN_ONLY_HARD_HINT
    assert "execute_code" in _PLAN_ONLY_HARD_HINT


def test_max_retries_is_1():
    """L6 改: 单 request 内 retry 上限 1 次 (避免死循环)."""
    assert _MAX_PLAN_ONLY_RETRIES == 1


# ─── BL-FIX23 L7 新加 helpers ────────────────────────────────────────────


def test_has_future_intent_triggers():
    """未来意图词 — 鸿波 5/11 场景 'tool → 现在自动填入用户名 stop'."""
    from catfish_gateway.app import _has_future_intent
    assert _has_future_intent("现在自动填入用户名")
    assert _has_future_intent("接下来我去打开浏览器")
    assert _has_future_intent("下一步是检查待办")
    assert _has_future_intent("我去验证一下")
    assert _has_future_intent("继续执行后续步骤")
    assert _has_future_intent("我现在重新生成")


def test_has_future_intent_no_trigger_on_completion():
    """完成态词不算未来意图 (避免误判)."""
    from catfish_gateway.app import _has_future_intent
    assert not _has_future_intent("已生成 docx")
    assert not _has_future_intent("数据已经完成调整")


def test_has_future_intent_empty():
    from catfish_gateway.app import _has_future_intent
    assert not _has_future_intent("")
    assert not _has_future_intent(None)  # type: ignore[arg-type]


def test_has_completion_claim_triggers():
    """完成态词 — 真做完了汇报."""
    from catfish_gateway.app import _has_completion_claim
    assert _has_completion_claim("文档已生成")
    assert _has_completion_claim("已保存到 ~/Desktop")
    assert _has_completion_claim("已完成全部修改")
    assert _has_completion_claim("已经写入数据库")


def test_has_completion_no_trigger_on_future():
    """未来意图不算完成态."""
    from catfish_gateway.app import _has_completion_claim
    assert not _has_completion_claim("现在自动填入用户名")
    assert not _has_completion_claim("我立刻调整")
    assert not _has_completion_claim("接下来去 X")


def test_has_completion_empty():
    from catfish_gateway.app import _has_completion_claim
    assert not _has_completion_claim("")
    assert not _has_completion_claim(None)  # type: ignore[arg-type]


# ─── L7 决策矩阵 (real_completion_after_tool 计算) ─────────────────────
#
# 4 个维度组合, 每个 case 一个测试覆盖 helper 行为:
#
# | last_is_tool | future_intent | completion | 应 retry? | 场景               |
# |--------------|---------------|------------|-----------|--------------------|
# | False        | False         | False      | 不 (无触发) | 一般闲聊            |
# | False        | True          | False      | (看用户反馈) | 老 L5 路径          |
# | True         | True          | False      | **是**     | 鸿波 mid-task case  |
# | True         | False         | True       | **不**     | 真做完汇报          |
# | True         | True          | True       | **是**    | 混合: "已生成 X, 接下来 Y" |


def test_l7_mid_task_future_intent_with_tool():
    """鸿波 5/11 真实场景: tool → '现在自动填入' stop → 应识别为 mid-task."""
    from catfish_gateway.app import _has_future_intent, _has_completion_claim
    content = "验证码识别为 2fW2. 现在自动填入用户名."
    assert _has_future_intent(content)
    assert not _has_completion_claim(content)
    # 决策: last_is_tool=True + future_intent=True + completion=False
    # → real_completion_after_tool = False → 不被 block, 应 retry


def test_l7_real_completion_after_tool():
    """真做完场景: tool → '已完成检查, 无待办' → 不应 retry."""
    from catfish_gateway.app import _has_future_intent, _has_completion_claim
    content = "已完成检查, 系统中共 0 项未处理待办."
    assert _has_completion_claim(content)
    assert not _has_future_intent(content)
    # 决策: last_is_tool=True + completion=True + future=False
    # → real_completion_after_tool = True → 应 block (避免死循环)


def test_l7_mixed_completion_plus_intent_with_tool():
    """混合: tool → '已生成 X, 接下来去做 Y' → 还在 mid-task, 应 retry."""
    from catfish_gateway.app import _has_future_intent, _has_completion_claim
    content = "已生成验证码图片, 接下来去填表单"
    assert _has_completion_claim(content)
    assert _has_future_intent(content)
    # 决策: last_is_tool=True + completion=True + future=True
    # → real_completion = (completion AND NOT future) = False → 不 block, 应 retry
