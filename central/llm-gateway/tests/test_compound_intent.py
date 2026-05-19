"""BL-COMPOUND-PLAN-EXECUTE (5/15 鸿波 '复合任务 agent 撑不住') 单测.

覆盖:
  - has_compound_intent: 连接词 + 多动作动词 命中, 单步不命中
  - inject_compound_plan_execute: 幂等 / 注入位置 (最后 system 末尾) /
    不动 messages 顺序
  - 跟 skill_guard 共存
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_gateway.compound_intent import (  # noqa: E402
    _PLAN_EXECUTE_MARKER,
    _QWEN_MARKER,
    _is_qwen_internal_model,
    has_compound_intent,
    inject_compound_plan_execute,
)


# ─── has_compound_intent: 命中 ───────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # 鸿波 5/15 16:48 实盘原话
        "分析这两份材料, 然后用归藏生成PPT",
        # 类似变体
        "先统计 CSV 数据再生成 PPT",
        "读取 Excel 然后写一份周报",
        "分析数据并创建可视化报告",
        "查询资质数据, 接着生成立项报告",
        "登录 EIS 后打卡, 再返回",
        # 英文
        "Analyze the data and then generate a report",
        # 显式多步词
        "我要分两步: 统计数据 + 生成 PPT",
        "拆分成: 1. 读 CSV 2. 写 HTML",
    ],
)
def test_compound_intent_hits(text):
    msgs = [{"role": "user", "content": text}]
    assert has_compound_intent(msgs), f"应触发: {text}"


# ─── has_compound_intent: 不命中 ─────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # 单步任务 — 没连接词
        "做一份 PPT",
        "生成杂志风 PPT",
        "分析这份 CSV",
        # 闲聊
        "你好",
        "今天天气怎么样",
        # 有连接词但只 1 个动词
        "我和小王讨论了一下",  # 没 ≥ 2 动作动词
        "然后呢",
        # 反例: "和"是名词连接, 不是动作
        "我跟你聊聊",
        # 有多个动词但没连接词
        "分析 统计 汇总",
    ],
)
def test_compound_intent_misses(text):
    msgs = [{"role": "user", "content": text}]
    assert not has_compound_intent(msgs), f"不该触发: {text}"


def test_compound_intent_only_latest_user_message():
    """只看最后一条 user — 历史复合任务但当前是简单问题不触发"""
    msgs = [
        {"role": "user", "content": "分析数据然后生成 PPT"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "你好吗"},  # 当前是闲聊
    ]
    assert not has_compound_intent(msgs)


def test_compound_intent_multimodal():
    """content 是 list (multimodal) — 拼 text 后扫"""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "分析这份数据, 然后生成 PPT"},
                {"type": "image_url", "image_url": {"url": "..."}},
            ],
        },
    ]
    assert has_compound_intent(msgs)


# ─── inject_compound_plan_execute ──────────────────────────


def test_inject_when_compound_hits():
    msgs = [
        {"role": "system", "content": "原 system prompt"},
        {"role": "user", "content": "分析 CSV 然后生成 PPT"},
    ]
    out = inject_compound_plan_execute(msgs)
    assert out is not msgs, "应返回新 list (deepcopy)"
    # 最后 system 段被追加
    assert _PLAN_EXECUTE_MARKER in out[0]["content"]
    # 原 prompt 保留
    assert out[0]["content"].startswith("原 system prompt")
    # 不动 messages 顺序
    assert out[1]["role"] == "user"
    assert out[1]["content"] == msgs[1]["content"]
    assert len(out) == len(msgs), "不该插新 message"


def test_no_inject_when_single_step():
    """单步任务不注入"""
    msgs = [
        {"role": "system", "content": "原 system"},
        {"role": "user", "content": "做一份 PPT"},
    ]
    out = inject_compound_plan_execute(msgs)
    assert out == msgs


def test_no_inject_when_no_system_message():
    """没 system 不插 — 防止异常"""
    msgs = [{"role": "user", "content": "分析然后生成"}]
    out = inject_compound_plan_execute(msgs)
    assert out == msgs


def test_inject_idempotent():
    """重复调不重复追加"""
    msgs = [
        {"role": "system", "content": "原 system"},
        {"role": "user", "content": "分析 CSV 然后生成 PPT"},
    ]
    once = inject_compound_plan_execute(msgs)
    twice = inject_compound_plan_execute(once)
    assert once[0]["content"] == twice[0]["content"]
    # marker 只出现 1 次
    assert once[0]["content"].count(_PLAN_EXECUTE_MARKER) == 1


def test_inject_targets_last_system():
    """有多个 system 时追加到最后那个 — 跟 skill_guard 同套路"""
    msgs = [
        {"role": "system", "content": "first"},
        {"role": "user", "content": "x"},
        {"role": "system", "content": "second"},
        {"role": "user", "content": "分析 CSV 然后生成 PPT"},
    ]
    out = inject_compound_plan_execute(msgs)
    assert out[0]["content"] == "first"  # 第一个不动
    assert _PLAN_EXECUTE_MARKER in out[2]["content"]  # 第二个被追加


def test_inject_preserves_user_text():
    """不动 user message"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "分析这份 CSV, 然后生成 PPT"},
    ]
    out = inject_compound_plan_execute(msgs)
    assert out[1]["content"] == "分析这份 CSV, 然后生成 PPT"


# ─── BL-LLM-PLAN-WITHOUT-ACT: qwen-aware 注入 ──────────


@pytest.mark.parametrize(
    "name",
    [
        "catfish-private-main",
        "catfish-public-qwen-flash",
        "qwen_v3_5_122b_a10b",
        "openai/qwen3.5-flash-2026-02-23",
        "QWEN-122B",  # case-insensitive
    ],
)
def test_is_qwen_internal_model_hits(name):
    assert _is_qwen_internal_model(name)


@pytest.mark.parametrize(
    "name",
    [
        "catfish-public-deepseek-flash",
        "gemini-2.5-flash",
        "claude-opus-4-7",
        None,
        "",
    ],
)
def test_is_qwen_internal_model_misses(name):
    assert not _is_qwen_internal_model(name)


def test_qwen_inject_on_single_step_task():
    """单步任务 + qwen → 注入 qwen 铁律 (即便没复合意图)"""
    msgs = [
        {"role": "system", "content": "原 system"},
        {"role": "user", "content": "帮我写周报"},  # 单步 (没连接词)
    ]
    assert not has_compound_intent(msgs)
    out = inject_compound_plan_execute(msgs, model_name="catfish-private-main")
    assert _QWEN_MARKER in out[0]["content"]
    assert _PLAN_EXECUTE_MARKER not in out[0]["content"]  # 单步不该有 plan-execute
    assert out[0]["content"].startswith("原 system")


def test_qwen_no_inject_on_deepseek():
    """单步 + 非 qwen → 不注入 (deepseek 自己 ReAct 强)"""
    msgs = [
        {"role": "system", "content": "原 system"},
        {"role": "user", "content": "帮我写周报"},
    ]
    out = inject_compound_plan_execute(msgs, model_name="catfish-public-deepseek-flash")
    assert _QWEN_MARKER not in out[0]["content"]
    assert _PLAN_EXECUTE_MARKER not in out[0]["content"]
    assert out == msgs


def test_qwen_inject_idempotent():
    msgs = [
        {"role": "system", "content": "原 system"},
        {"role": "user", "content": "登录 EIS"},
    ]
    once = inject_compound_plan_execute(msgs, model_name="qwen_v3_5_122b_a10b")
    twice = inject_compound_plan_execute(once, model_name="qwen_v3_5_122b_a10b")
    assert once[0]["content"] == twice[0]["content"]
    assert once[0]["content"].count(_QWEN_MARKER) == 1


def test_qwen_plus_compound_both_inject():
    """qwen + 复合任务 → 两块都注入 (qwen 在前, plan-execute 在后)"""
    msgs = [
        {"role": "system", "content": "原 system"},
        {"role": "user", "content": "分析 CSV 然后生成 PPT"},
    ]
    out = inject_compound_plan_execute(msgs, model_name="catfish-private-main")
    sys = out[0]["content"]
    assert _QWEN_MARKER in sys
    assert _PLAN_EXECUTE_MARKER in sys
    # qwen 块在前 (优先级高)
    assert sys.find(_QWEN_MARKER) < sys.find(_PLAN_EXECUTE_MARKER)


def test_backward_compat_no_model_name():
    """model_name 不传 (老 caller) — 仍按 has_compound_intent 决定"""
    msgs = [
        {"role": "system", "content": "原 system"},
        {"role": "user", "content": "分析 CSV 然后生成 PPT"},
    ]
    out = inject_compound_plan_execute(msgs)
    assert _PLAN_EXECUTE_MARKER in out[0]["content"]
    assert _QWEN_MARKER not in out[0]["content"]


# ─── 跟 skill_guard 共存 ─────────────────────────────────


def test_coexist_with_skill_guard():
    """compound 跟 skill_guard 同时注入: 用户说 '分析然后用 creative/.. 生成 PPT'
    应该 skill_guard 命中 + compound 命中"""
    from catfish_gateway.skill_guard import inject_skill_guard

    msgs = [
        {"role": "system", "content": "原 system"},
        {"role": "user", "content": "分析 CSV 然后用 creative/guizang-ppt-magazine 生成 PPT"},
    ]
    body = {
        "tools": [{"type": "function", "function": {"name": "catfish_run_skill"}}],
    }

    # 先 skill_guard, 再 compound — 跟 app.py 真实顺序一致
    step1 = inject_skill_guard(msgs, body)
    step2 = inject_compound_plan_execute(step1)

    sys_content = step2[0]["content"]
    assert "原 system" in sys_content
    assert "铁律 1" in sys_content, "skill_guard 块在"
    assert _PLAN_EXECUTE_MARKER in sys_content, "compound 块在"
    # 顺序: 原 system → skill_guard → compound
    idx_orig = sys_content.find("原 system")
    idx_sg = sys_content.find("铁律 1")
    idx_compound = sys_content.find(_PLAN_EXECUTE_MARKER)
    assert idx_orig < idx_sg < idx_compound
