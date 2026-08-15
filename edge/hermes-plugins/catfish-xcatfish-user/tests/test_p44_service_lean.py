"""P44 服务式调用瘦身 —— 只掐两个后台分类器, 别的一律不许动。

见 plugin_service_lean.py 的模块 docstring: 一次邮件评级 42K token 换 185 token,
8/15 的周配额 83 分钟里 83% 烧在这上面。

**这个文件的重点不是"闸能掐"**, 而是"闸掐不到不该掐的东西"。鸿波 8/15 的要求
原话是「不能影响到聊天、早安、知识库的使用」, 而早安和知识库都是**带 source**
的调用 —— 沿用 P42 那条"有 source 就跳"的宽判据会把它们一起误伤。所以下面
针对聊天 / 早安 / 知识库各有专门的用例, 它们红了就是这个 patch 不能上。

跟 test_p42_memory_gate.py 同样的坑: 闸有幂等标记, 假类必须每个用例新造一个,
否则第二个用例开始 patch 直接 return, 绑的还是第一个用例的 ContextVar。
"""
from __future__ import annotations

import contextvars
import sys

import pytest

from plugin_service_lean import (
    LEAN_SOURCES,
    _patch_p44_service_call_lean,
    _patch_toolsets,
)


# ── 工具闸 ────────────────────────────────────────────────────────────────


@pytest.fixture
def tools_gate(monkeypatch):
    """把工具闸打到一个全新的假 tools_config 上, 返 (模块, ContextVar)。

    假的 _get_platform_tools 返回一个有 4 个元素的集合, 好跟"被掐成空集"区分。
    """
    cv = contextvars.ContextVar("catfish_source", default="")

    class _FakeToolsConfig:
        @staticmethod
        def _get_platform_tools(config, platform, **kwargs):
            return {"hermes-core", "browser", "memory", "mcp-catfish-tools"}

    monkeypatch.setitem(sys.modules, "hermes_cli", type("H", (), {})())
    monkeypatch.setitem(sys.modules, "hermes_cli.tools_config", _FakeToolsConfig)
    _patch_toolsets(cv)
    return _FakeToolsConfig, cv


def test_邮件评级_不给工具(tools_gate):
    mod, cv = tools_gate
    cv.set("companion-email-scheduler")
    assert mod._get_platform_tools({}, "api_server") == set()


def test_钓鱼复审_不给工具(tools_gate):
    mod, cv = tools_gate
    cv.set("companion-phishing-scan")
    assert mod._get_platform_tools({}, "api_server") == set()


def test_员工聊天_工具一个不少(tools_gate):
    """聊天不设 source。这条红 = 员工聊天没工具可用了。"""
    mod, cv = tools_gate
    cv.set("")
    assert len(mod._get_platform_tools({}, "api_server")) == 4


@pytest.mark.parametrize("source", [
    "companion-briefing-card",      # 早安
    "companion-advisor",            # 早安
    "companion-advisor-transform",  # 早安
    "companion-wiki-suggest",       # 知识库
    "companion-email-draft",
    "companion-profile",
    # 8/15: 聊天从这天起也带 source 了 (?catfish_source=companion-chat,
    # 为了让网关账本能把员工聊天跟后台任务分开)。它当然更不能被砍工具 ——
    # 员工问"去知识库核对福富资质"就是靠这些工具。
    "companion-chat",
])
def test_早安和知识库_工具一个不少(tools_gate, source):
    """**鸿波 8/15 的硬约束**: 不能影响聊天、早安、知识库。

    这几个来源都**带 source**, 所以如果哪天有人把判据从白名单改成 P42 那条
    "有 source 就跳", 这几条会立刻红。这正是它们存在的理由。
    """
    mod, cv = tools_gate
    cv.set(source)
    assert len(mod._get_platform_tools({}, "api_server")) == 4, (
        f"{source} 被误伤了 —— 判据是不是被改宽了?"
    )


def test_只掐api_server平台(tools_gate):
    """hermes 的 cli / telegram / cron 走同一个函数, 不能连它们一起掐。"""
    mod, cv = tools_gate
    cv.set("companion-email-scheduler")
    for plat in ("cli", "telegram", "discord", "cron"):
        assert len(mod._get_platform_tools({}, plat)) == 4, f"{plat} 被误伤"


def test_白名单就是这两项():
    """名单变更必须是显式动作 —— 加一项就得改这条测试。"""
    assert LEAN_SOURCES == frozenset({
        "companion-email-scheduler", "companion-phishing-scan",
    })


# ── 记忆闸 ────────────────────────────────────────────────────────────────


@pytest.fixture
def mem_gate(monkeypatch):
    cv = contextvars.ContextVar("catfish_source", default="")

    class _FakeMemoryManager:
        def __init__(self):
            self.prefetch_calls = []
            self.queue_calls = []

        def prefetch_all(self, query, *, session_id=""):
            self.prefetch_calls.append(query)
            return "员工的长期记忆: 做资质咨询..."

        def queue_prefetch_all(self, query, *, session_id=""):
            self.queue_calls.append(query)
            return "queued"

    class _Mod:
        MemoryManager = _FakeMemoryManager

    monkeypatch.setitem(sys.modules, "agent", type("A", (), {})())
    monkeypatch.setitem(sys.modules, "agent.memory_manager", _Mod)
    from plugin_service_lean import _patch_memory_prefetch
    _patch_memory_prefetch(cv)
    return _FakeMemoryManager, cv


def test_邮件评级_不读记忆(mem_gate):
    cls, cv = mem_gate
    cv.set("companion-email-scheduler")
    mm = cls()
    assert mm.prefetch_all("给这几封邮件评级") == ""
    assert mm.prefetch_calls == [], "闸没拦住, 记忆还是被读了"


def test_员工聊天_照常读记忆(mem_gate):
    cls, cv = mem_gate
    cv.set("")
    mm = cls()
    assert "长期记忆" in mm.prefetch_all("我上周说的那个项目")
    assert len(mm.prefetch_calls) == 1


@pytest.mark.parametrize("source", [
    "companion-briefing-card", "companion-advisor", "companion-wiki-suggest",
])
def test_早安和知识库_照常读记忆(mem_gate, source):
    """早安要结合当天情况写建议, 知识库要对照已有条目 —— 记忆不能掐。"""
    cls, cv = mem_gate
    cv.set(source)
    mm = cls()
    assert "长期记忆" in mm.prefetch_all("今天有什么要紧事"), f"{source} 的记忆被掐了"


def test_预热也跳过(mem_gate):
    """queue_prefetch_all 不跳的话, 后台线程照样把记忆读一遍。"""
    cls, cv = mem_gate
    cv.set("companion-phishing-scan")
    mm = cls()
    assert mm.queue_prefetch_all("x") is None
    assert mm.queue_calls == []


# ── 失效模式 ──────────────────────────────────────────────────────────────


def test_CV读失败时走原路(monkeypatch):
    """闸的失败方向必须是"漏优化", 不是"误伤"。

    CV 抛异常时 _is_lean_call 返 False → 工具照给。贵, 但不坏。
    """
    class _BoomCV:
        def get(self):
            raise RuntimeError("CV 挂了")

    class _FakeToolsConfig:
        @staticmethod
        def _get_platform_tools(config, platform, **kwargs):
            return {"a", "b"}

    monkeypatch.setitem(sys.modules, "hermes_cli", type("H", (), {})())
    monkeypatch.setitem(sys.modules, "hermes_cli.tools_config", _FakeToolsConfig)
    _patch_toolsets(_BoomCV())
    assert len(_FakeToolsConfig._get_platform_tools({}, "api_server")) == 2


def test_幂等_重复装不叠加(monkeypatch):
    """plugin 可能被重载。装两次不能包成两层。"""
    cv = contextvars.ContextVar("catfish_source", default="")

    class _FakeToolsConfig:
        @staticmethod
        def _get_platform_tools(config, platform, **kwargs):
            return {"a"}

    monkeypatch.setitem(sys.modules, "hermes_cli", type("H", (), {})())
    monkeypatch.setitem(sys.modules, "hermes_cli.tools_config", _FakeToolsConfig)
    _patch_toolsets(cv)
    first = _FakeToolsConfig._get_platform_tools
    _patch_toolsets(cv)
    assert _FakeToolsConfig._get_platform_tools is first, "包了两层"


def test_hermes没有这个函数时不炸(monkeypatch):
    """hermes 升级把 _get_platform_tools 改名/删掉 → 打不上闸, 但不能崩。"""
    cv = contextvars.ContextVar("catfish_source", default="")
    monkeypatch.setitem(sys.modules, "hermes_cli", type("H", (), {})())
    monkeypatch.setitem(sys.modules, "hermes_cli.tools_config", type("T", (), {})())
    _patch_toolsets(cv)   # 不抛就算过


def test_入口函数两个闸都装(monkeypatch):
    cv = contextvars.ContextVar("catfish_source", default="")

    class _FakeToolsConfig:
        @staticmethod
        def _get_platform_tools(config, platform, **kwargs):
            return {"a"}

    class _FakeMemoryManager:
        def prefetch_all(self, query, *, session_id=""):
            return "记忆"

        def queue_prefetch_all(self, query, *, session_id=""):
            return "q"

    monkeypatch.setitem(sys.modules, "hermes_cli", type("H", (), {})())
    monkeypatch.setitem(sys.modules, "hermes_cli.tools_config", _FakeToolsConfig)
    monkeypatch.setitem(sys.modules, "agent", type("A", (), {})())
    monkeypatch.setitem(sys.modules, "agent.memory_manager",
                        type("M", (), {"MemoryManager": _FakeMemoryManager}))
    _patch_p44_service_call_lean(cv)

    cv.set("companion-email-scheduler")
    assert _FakeToolsConfig._get_platform_tools({}, "api_server") == set()
    assert _FakeMemoryManager().prefetch_all("x") == ""
