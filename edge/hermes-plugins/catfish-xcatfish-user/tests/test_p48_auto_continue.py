"""P48 自动续跑: "说了要做却没调工具" → 替员工按「继续」, 最多两次。

不 import hermes: 测 wrap_run_agent 工厂 + 意图判据, 用假 adapter。
"""
from __future__ import annotations

import asyncio

import plugin_auto_continue as p48


# ── 判据 ────────────────────────────────────────────────────────────────

def test_real_stop_points_from_1005_are_intents():
    # 10/5 晚三次真实停点的原话
    for s in (
        "台账内部两个口径打架，这个发现比门槛判定本身更要紧。逐项目比对。",
        "明细页的重复有两类性质完全不同，我上一版把26.9M全算成风险是错的。分开算。",
        "明细行有两类不同的重复，性质完全不同。分清楚后重新定性。",
    ):
        assert p48.looks_like_unfinished_intent(s), s


def test_questions_and_long_answers_are_not_intents():
    assert not p48.looks_like_unfinished_intent("要不要我先把 A107012 挂进任务库？")
    assert not p48.looks_like_unfinished_intent("先看一下，你定。")
    assert not p48.looks_like_unfinished_intent("")
    assert not p48.looks_like_unfinished_intent(None)
    assert not p48.looks_like_unfinished_intent("核完了。" + "结论如下：" * 30)
    assert not p48.looks_like_unfinished_intent("先算一下\n\n```python\nx=1\n```")
    # 正常的最终回答 (没有动作意图词) 也不续
    assert not p48.looks_like_unfinished_intent("好。不动它们。")
    # 最后一句是做完的陈述 (结果 / 数字 / "完了") → 是回答, 哪怕含"定位"这类词
    assert not p48.looks_like_unfinished_intent("定位完了：汇总表漏了 11 个项目。")
    assert not p48.looks_like_unfinished_intent("核完了，差额 6,644,674.54 元。")
    # 前面是现状、最后一句是打算 → 续
    assert p48.looks_like_unfinished_intent("汇总表和明细页对不上，差了6780万。我定位原因。")


# ── 续跑机制 ──────────────────────────────────────────────────────────

class _Adapter:
    pass


def _run(orig, **kwargs):
    patched = p48.wrap_run_agent(orig)
    return asyncio.run(patched(_Adapter(), **kwargs))


def test_nudges_once_then_returns_merged_result():
    calls = []
    deltas = []

    async def orig(self, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return (
                {"final_response": "汇总表和明细页对不上。先定位原因。",
                 "messages": [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
                              {"role": "tool", "content": "..."},
                              {"role": "assistant", "content": "汇总表和明细页对不上。先定位原因。"}],
                 "session_id": "s1"},
                {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )
        return (
            {"final_response": "定位完了：汇总表漏了 11 个项目。", "messages": [{"role": "assistant", "content": "定位完了：汇总表漏了 11 个项目。"}], "session_id": "s1"},
            {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27},
        )

    history = [{"role": "user", "content": "之前"}, {"role": "assistant", "content": "好"}]
    result, usage = _run(orig, user_message="核一下", conversation_history=history, session_id="s1",
                         stream_delta_callback=deltas.append)

    assert len(calls) == 2
    # 续跑: 固定提示 + 全 transcript (含第一轮的工具调用, 模型不会重做)
    assert calls[1]["user_message"] == p48.NUDGE_TEXT
    assert calls[1]["conversation_history"][: len(history)] == history
    assert calls[1]["conversation_history"][len(history)] == {"role": "user", "content": "核一下"}
    assert calls[1]["conversation_history"][-1]["content"] == "汇总表和明细页对不上。先定位原因。"
    assert calls[1]["session_id"] == "s1"
    # 结果合并
    assert result["final_response"] == "汇总表和明细页对不上。先定位原因。\n\n定位完了：汇总表漏了 11 个项目。"
    assert usage == {"input_tokens": 30, "output_tokens": 12, "total_tokens": 42}
    # 存库 transcript 是全量形状: history + user + 第一轮 + 续跑提示 + 第二轮
    msgs = result["messages"]
    assert msgs[: len(history)] == history
    assert {"role": "user", "content": p48.NUDGE_TEXT} in msgs
    assert msgs[-1]["content"] == "定位完了：汇总表漏了 11 个项目。"
    # 流式: 两段之间推了分隔
    assert deltas == ["\n\n"]


def test_stops_after_max_nudges():
    calls = []

    async def orig(self, **kw):
        calls.append(kw)
        return ({"final_response": "先算准再报。", "messages": [{"role": "assistant", "content": "先算准再报。"}]}, {"total_tokens": 1})

    result, usage = _run(orig, user_message="算", conversation_history=[])
    assert len(calls) == 1 + p48.MAX_NUDGES
    assert result["final_response"].count("先算准再报。") == 1 + p48.MAX_NUDGES
    assert usage == {"total_tokens": 1 + p48.MAX_NUDGES}


def test_no_nudge_for_normal_answer_or_failed_turn():
    calls = []

    async def orig(self, **kw):
        calls.append(kw)
        return ({"final_response": "结论：两套口径互不替代。", "messages": [{"role": "assistant", "content": "x"}]}, {})

    _run(orig, user_message="q", conversation_history=[])
    assert len(calls) == 1

    calls.clear()

    async def failed(self, **kw):
        calls.append(kw)
        return ({"final_response": "先算准再报。", "failed": True, "messages": []}, {})

    _run(failed, user_message="q", conversation_history=[])
    assert len(calls) == 1

    calls.clear()

    async def no_messages(self, **kw):
        calls.append(kw)
        return ({"final_response": "先算准再报。"}, {})

    _run(no_messages, user_message="q", conversation_history=[])
    assert len(calls) == 1


def test_positional_call_is_passed_through_untouched():
    calls = []

    async def orig(self, *a, **kw):
        calls.append((a, kw))
        return ({"final_response": "先算准再报。", "messages": [{"role": "assistant", "content": "x"}]}, {})

    patched = p48.wrap_run_agent(orig)
    asyncio.run(patched(_Adapter(), "msg", []))
    assert len(calls) == 1


def test_patch_is_idempotent():
    async def orig(self, **kw):
        return ({}, {})

    once = p48.wrap_run_agent(orig)
    assert getattr(once, "_p48_auto_continue", False) is True
