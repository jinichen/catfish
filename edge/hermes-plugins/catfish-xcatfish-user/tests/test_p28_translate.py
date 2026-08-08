"""P28 outbound 中文化 —— 审批提示的**全部变体**都得翻掉.

# 为什么要有这个 test

`_P28_REPLACEMENTS` 是一张字面量对照表, 靠 `str.replace` 干活。hermes 每升一次
级就可能改原文, 老 pattern 直接 silent miss —— 员工在微信里看到一整段英文。
历史上已经踩过一次: v0.18→v0.19 把 `to execute,` 改成
`to execute this one operation,`, 那次是鸿波在微信里肉眼发现的 (7/22)。

8/8 升 v2026.8.3 时发现第二个更隐蔽的坑: 我们只翻了**一种**成品串, 而
`gateway/run.py:_format_exec_approval_fallback` 是**按开关拼**出来的:

    choices = ["Reply `/approve` to execute this one operation"]
    if not smart_denied and allow_session:
        choices.append("`/approve session` ...")
        if allow_permanent:
            choices.append("`/approve always` ...")
    choices.append("`/deny` to cancel")

三种成品, 我们只覆盖了一种。另外两种整条英文泄到微信。

所以这个 test 不写死字符串去对答案 —— 那还是在猜。它**直接调 hermes 自己的
渲染函数**, 把 flag 组合穷举一遍, 每种都要求翻译后不残留英文审批提示。
hermes 下次再改原文, 这里当场红, 不用等员工投诉。
"""
from __future__ import annotations

import itertools

import pytest

from plugin import _translate_hermes_zh

# 翻译后仍出现这些, 就说明有 pattern 没跟上
_ENGLISH_LEAKS = (
    "Reply `/approve`",
    "to execute this one operation",
    "to approve this pattern",
    "to approve permanently",
    "to cancel.",
    "Dangerous command requires approval",
    "Smart DENY",
)


def _assert_fully_translated(rendered: str, label: str) -> None:
    out = _translate_hermes_zh(rendered)
    leaked = [m for m in _ENGLISH_LEAKS if m in out]
    assert not leaked, (
        f"{label}: 这些英文没被翻掉 {leaked}\n"
        f"hermes 原文: {rendered!r}\n"
        f"翻译结果  : {out!r}\n"
        f"→ 去 plugin.py 的 _P28_REPLACEMENTS 补 pattern。"
    )
    # 翻掉了就必须真出现中文入口, 否则等于翻了个寂寞
    assert "批准" in out or "拒绝" in out, f"{label}: 没有中文命令入口: {out!r}"


# ── 第一层: 拿 hermes 真函数穷举 (本机装了 hermes 才跑) ────────────────


def _load_hermes_renderer():
    try:
        from gateway.run import _format_exec_approval_fallback
    except Exception:  # noqa: BLE001 — 没装 hermes / 上游改名都算不可用
        return None
    return _format_exec_approval_fallback


@pytest.mark.skipif(
    _load_hermes_renderer() is None,
    reason="需要本机装好的 hermes-agent (cd ~/.hermes/hermes-agent 后跑)",
)
@pytest.mark.parametrize(
    "allow_permanent,allow_session,smart_denied",
    list(itertools.product([True, False], repeat=3)),
)
def test_审批提示的每种变体都能翻成中文(allow_permanent, allow_session, smart_denied):
    render = _load_hermes_renderer()
    rendered = render(
        "rm -rf /tmp/x",
        "测试用途",
        "/",
        allow_permanent=allow_permanent,
        allow_session=allow_session,
        smart_denied=smart_denied,
    )
    _assert_fully_translated(
        rendered,
        f"allow_permanent={allow_permanent} allow_session={allow_session} "
        f"smart_denied={smart_denied}",
    )


# ── 第二层: 字面量固化, CI 没装 hermes 也有覆盖 ──────────────────────
#
# 这三条是 8/8 从 v2026.8.3 的 gateway/run.py:508 实际渲染出来的成品。
# 上面那层是"跟着 hermes 走", 这层是"钉住已知的三种", 两层都要。

_KNOWN_V020_VARIANTS = [
    # allow_session ∧ allow_permanent (升级前唯一覆盖到的那种)
    "Reply `/approve` to execute this one operation, `/approve session` to approve this pattern "
    "for the session, `/approve always` to approve permanently, or `/deny` to cancel.",
    # allow_session ∧ ¬allow_permanent
    "Reply `/approve` to execute this one operation, `/approve session` to approve this pattern "
    "for the session, or `/deny` to cancel.",
    # ¬allow_session (或 smart_denied)
    "Reply `/approve` to execute this one operation, or `/deny` to cancel.",
]


@pytest.mark.parametrize("rendered", _KNOWN_V020_VARIANTS)
def test_v020_三种已知成品串都翻掉(rendered):
    _assert_fully_translated(rendered, "v0.20 已知变体")


def test_只剩单次时不能骗员工说有本次会话():
    """¬allow_session 的那条: hermes 根本不收 `/approve session`,
    中文里再提"本次会话"就是把员工往死路上引。"""
    out = _translate_hermes_zh(
        "Reply `/approve` to execute this one operation, or `/deny` to cancel."
    )
    assert "本次会话" not in out, f"单次变体不该提本次会话: {out!r}"
    assert "仅此一次" in out


def test_smart_deny_标题也翻():
    out = _translate_hermes_zh("⚠️ **Smart DENY — owner override for one operation:**")
    assert "Smart DENY" not in out
    assert "管理员放行" in out


def test_非字符串输入原样返回不炸():
    for bad in (None, 123, b"bytes", [], {}):
        assert _translate_hermes_zh(bad) is bad


def test_员工正常聊天不被误改():
    plain = "今天的报销单我已经提交了, 麻烦你看一下。"
    assert _translate_hermes_zh(plain) == plain
