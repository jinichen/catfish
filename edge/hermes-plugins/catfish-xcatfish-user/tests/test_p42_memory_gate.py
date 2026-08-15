"""P42 来源闸 —— 后台调用不进记忆。

见 plugin_memory_gate.py 的模块 docstring: 邮件评级走 agent loop, 于是邮件标题
和发件人被写进 employee_journal.md, 再被蒸成 wiki 条目。

这里不测 hermes 的 MemoryManager (CI 上没有), 测的是**闸门本身的判据** ——
哪些情况放行、哪些拦下, 以及拦下时绝不能把记忆搞挂。
"""
from __future__ import annotations

import contextvars

import pytest

from plugin_memory_gate import _patch_p42_memory_skip_background


def _make_fake_manager():
    """**每次都造一个新类**。

    第一版把假 MemoryManager 写成模块级类, 结果被闸门的幂等标记咬了:
    第一个用例打完闸, `_catfish_p42` 置位, 后面用例再调 _patch 直接 return,
    于是它们绑的还是第一个用例的 ContextVar —— 9 条全红。
    幂等本身是对的 (plugin 重载不能包两层), 是测试不该复用同一个类。
    """

    class _FakeMemoryManager:
        def __init__(self):
            self.calls = []

        def sync_all(self, user_content, assistant_content, session_id=""):
            self.calls.append((user_content, assistant_content, session_id))
            return "called"

    return _FakeMemoryManager


@pytest.fixture
def gated(monkeypatch):
    """把闸打到一个全新的假 MemoryManager 上, 返 (类, ContextVar)。"""
    cv = contextvars.ContextVar("catfish_source", default="")
    cls = _make_fake_manager()

    class _Mod:
        MemoryManager = cls

    monkeypatch.setitem(__import__("sys").modules, "agent.memory_manager", _Mod)
    monkeypatch.setitem(__import__("sys").modules, "agent", type("A", (), {})())
    _patch_p42_memory_skip_background(cv)
    return cls, cv


def test_员工聊天_没有source_照常记忆(gated):
    cls, cv = gated
    cv.set("")
    mm = cls()
    mm.sync_all("鸿波: 帮我看下这个", "小鲶: 好的")
    assert len(mm.calls) == 1, "员工聊天必须照常进记忆"


def test_邮件评级_有source_被拦下(gated):
    """8/8 实撞的那一条 —— email_scheduler 的评级调用。"""
    cls, cv = gated
    cv.set("companion-email-scheduler")
    mm = cls()
    mm.sync_all(
        "1. 主题: 转发: 关于CIC资质认证需要财务负责人提供的相关材料 | 发件人: 任何晓",
        '["中"]',
    )
    assert mm.calls == [], "邮件正文不该进记忆"


@pytest.mark.parametrize("source", [
    "companion-advisor",
    "companion-advisor-transform",
    "companion-briefing-card",
    "companion-email-draft",
    "companion-phishing-scan",
    "companion-profile",
    "companion-wiki-suggest",
])
def test_所有已知的后台来源都被拦(gated, source):
    """这七个是 grep 全仓拿到的实际会设 source 的调用点。

    新增后台调用**忘了设 source** 仍会漏进去 —— 这是这道闸已知的失效方向,
    在模块 docstring 里写明了。反过来 (员工聊天被误拦) 不会发生。
    """
    cls, cv = gated
    cv.set(source)
    mm = cls()
    mm.sync_all("x", "y")
    assert mm.calls == []


def test_source_两边有空格也算数(gated):
    cls, cv = gated
    cv.set("  companion-advisor  ")
    mm = cls()
    mm.sync_all("x", "y")
    assert mm.calls == []


def test_幂等_重复打闸不叠加(gated):
    """plugin 重载时会再跑一次 _apply_patches, 不能包两层。"""
    cls, cv = gated
    cv.set("")
    _patch_p42_memory_skip_background(cv)   # 第二次
    _patch_p42_memory_skip_background(cv)   # 第三次
    mm = cls()
    mm.sync_all("x", "y")
    assert len(mm.calls) == 1, "包多层会让一次调用变成多次或被吞"


def test_contextvar_抛异常时按员工聊天处理(gated):
    """CV 不该抛, 抛了也不能把记忆搞挂 —— 宁可多记, 不可静默丢。"""
    class _Boom:
        def get(self):
            raise RuntimeError("CV 炸了")

    cls = _make_fake_manager()   # 新类, 否则幂等标记会挡住这次打闸
    import sys as _sys
    _sys.modules["agent.memory_manager"] = type("M", (), {"MemoryManager": cls})
    _patch_p42_memory_skip_background(_Boom())
    mm = cls()
    mm.sync_all("x", "y")
    assert len(mm.calls) == 1


# ── 8/15: 员工交互来源豁免 ────────────────────────────────────────────
#
# 给聊天补 ?catfish_source=companion-chat 是为了让网关账本能把
# "员工聊天" 和 "后台任务" 分开 (8/15 查配额时 94% 的 token 落在「(无标记)」栏,
# 最大的一块答不上来)。但这道闸的判据是"有 source 就跳记忆", 前提正是
# 「员工聊天不设 source」—— 一补标记, 聊天就不进记忆了。
#
# 这两条钉的就是那个交叉点。它们红 = 员工的记忆悄悄停了, 而按本文件
# docstring 的说法, 这种失效"现场根本看不出来"。


def test_聊天带了source照样进记忆(gated):
    """companion-chat 在 INTERACTIVE_SOURCES 里 —— 有 source 但不是后台。"""
    cls, cv = gated
    cv.set("companion-chat")
    mm = cls()
    mm.sync_all("鸿波: 把这些错误的记忆都处理掉", "小鲶: 好的")
    assert len(mm.calls) == 1, "员工聊天被当成后台调用挡掉了 —— 记忆会悄悄停写"


@pytest.mark.parametrize("source", [
    "companion-email-scheduler",
    "companion-phishing-scan",
    "companion-advisor",
    "companion-briefing-card",
    "companion-wiki-suggest",
    "companion-profile",
    "companion-email-draft",
])
def test_后台来源仍然一律挡住(gated, source):
    """开了口子之后, 原来挡住的必须还挡着 —— 别把闸开成筛子。

    尤其 email-scheduler: 8/8 就是它把员工邮件正文写进了 employee_journal.md
    (1.3MB / 2856 条), 再被蒸成 wiki 条目。
    """
    cls, cv = gated
    cv.set(source)
    mm = cls()
    mm.sync_all("1. 主题: 某封邮件 | 发件人: x@y.com", '["中"]')
    assert mm.calls == [], f"{source} 是后台调用, 不该进记忆"


def test_豁免名单是显式的():
    """加一项就得改这条 —— 别让"员工交互"这个口子悄悄变大。"""
    from plugin_memory_gate import INTERACTIVE_SOURCES
    assert INTERACTIVE_SOURCES == frozenset({"companion-chat"})
