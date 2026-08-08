"""P44 进度快照出口 —— 判据 + 跟 hermes 的形状契约。

分两半:

**判据 (不需要 hermes)**: 取数路径上每个"拿不到"的岔口都返明确的原因码, 而不是
假数据、也不是异常。这半在 CI 上就能跑。

**形状契约 (静态读 hermes 源码, 不 import)**: `get_activity_summary` 还在、
还带我们 UI 依赖的那几个键, gateway 侧两个取数锚点还在。hermes 升级把它改了,
这条要红 —— 这正是这个功能最容易静默坏掉的地方: 端点照样 200、turns 照样有值,
只是 UI 上那行进度永远空着, 没人会发现。

装了 hermes 就跑, 没装就跳, **不挑解释器版本也不需要 hermes 的 venv**。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import activity_probe


# ── 判据: 每个岔口都返明确原因 ──────────────────────────────────

def test_没有_runner_时返_no_runner(monkeypatch):
    monkeypatch.setattr(activity_probe, "_gateway_runner", lambda: None)
    out = activity_probe.collect_activity()
    assert out == {
        "available": False,
        "reason": activity_probe.REASON_NO_RUNNER,
        "turns": [],
    }


def test_没有正在跑的_turn_跟探测失败要分得开(monkeypatch):
    """这两件事在 UI 上的处理完全不同 —— 前者是正常态, 后者要退回盲等。"""
    class _EmptyRunner:
        def _running_agent_items(self):
            return []

    monkeypatch.setattr(activity_probe, "_gateway_runner", lambda: _EmptyRunner())
    assert activity_probe.collect_activity()["reason"] == activity_probe.REASON_NO_RUNNING_TURN


def test_正常返回把_session_key_并进快照(monkeypatch):
    class _Agent:
        def get_activity_summary(self):
            return {"current_tool": "read_file", "api_call_count": 3}

    class _Runner:
        def _running_agent_items(self):
            return [("api-abc123", _Agent())]

    monkeypatch.setattr(activity_probe, "_gateway_runner", lambda: _Runner())
    out = activity_probe.collect_activity()
    assert out["available"] is True
    assert out["turns"] == [
        {"session_key": "api-abc123", "current_tool": "read_file", "api_call_count": 3}
    ]


def test_快照原样透出_不加工(monkeypatch):
    """不改名、不补默认值、不删字段 —— hermes 加了新字段要能自己流过来。"""
    payload = {"a": 1, "b": None, "嵌套": {"x": [1, 2]}, "未来新增字段": "whatever"}

    class _Agent:
        def get_activity_summary(self):
            return dict(payload)

    class _Runner:
        def _running_agent_items(self):
            return [("k", _Agent())]

    monkeypatch.setattr(activity_probe, "_gateway_runner", lambda: _Runner())
    turn = activity_probe.collect_activity()["turns"][0]
    for k, v in payload.items():
        assert turn[k] == v


def test_单个_agent_抛异常不影响其他(monkeypatch):
    """一条坏的不该让整个端点变成"什么都没有"。"""
    class _Bad:
        def get_activity_summary(self):
            raise RuntimeError("boom")

    class _Good:
        def get_activity_summary(self):
            return {"current_tool": "grep"}

    class _Runner:
        def _running_agent_items(self):
            return [("bad", _Bad()), ("good", _Good())]

    monkeypatch.setattr(activity_probe, "_gateway_runner", lambda: _Runner())
    out = activity_probe.collect_activity()
    assert [t["session_key"] for t in out["turns"]] == ["good"]


def test_返的不是_dict_就跳过_不硬塞(monkeypatch):
    class _Weird:
        def get_activity_summary(self):
            return ["不是 dict"]

    class _Runner:
        def _running_agent_items(self):
            return [("k", _Weird())]

    monkeypatch.setattr(activity_probe, "_gateway_runner", lambda: _Runner())
    assert activity_probe.collect_activity()["reason"] == activity_probe.REASON_NO_RUNNING_TURN


def test_没有_get_activity_summary_的对象被挡掉(monkeypatch):
    """pending sentinel 就是这种 —— 抢到槽位但 agent 还没建好。"""
    class _Runner:
        def _running_agent_items(self):
            return [("k", object())]

    monkeypatch.setattr(activity_probe, "_gateway_runner", lambda: _Runner())
    assert activity_probe.collect_activity()["reason"] == activity_probe.REASON_NO_RUNNING_TURN


def test_没有_running_agent_items_时退回_dict_view(monkeypatch):
    """老版本 hermes 只有 _running_agents dict view。"""
    class _Agent:
        def get_activity_summary(self):
            return {"current_tool": "bash"}

    class _OldRunner:
        _running_agents = {"legacy-key": None}

        def __init__(self):
            self._running_agents = {"legacy-key": _Agent()}

    monkeypatch.setattr(activity_probe, "_gateway_runner", lambda: _OldRunner())
    out = activity_probe.collect_activity()
    assert out["turns"][0]["session_key"] == "legacy-key"


def test_取数整体炸了也返结构化结果(monkeypatch):
    class _Explode:
        def _running_agent_items(self):
            raise RuntimeError("registry 挂了")

        @property
        def _running_agents(self):
            raise RuntimeError("这个也挂了")

    monkeypatch.setattr(activity_probe, "_gateway_runner", lambda: _Explode())
    out = activity_probe.collect_activity()
    assert out["available"] is False
    assert out["reason"] in activity_probe.ALL_REASONS
    assert out["turns"] == []


def test_原因码是封闭词表():
    """UI 会显示它, 所以每个值都得是我们自己写的常量, 不能是上游异常原文。"""
    assert activity_probe.REASON_NO_RUNNER in activity_probe.ALL_REASONS
    assert activity_probe.REASON_AUTH_UNAVAILABLE in activity_probe.ALL_REASONS
    for r in activity_probe.ALL_REASONS:
        assert r.replace("_", "").isalnum(), f"{r} 不像个码"


# ── 形状契约: 静态读 hermes 源码, **不 import** ─────────────────
#
# 第一版写成 `from run_agent import AIAgent` + `inspect.getsource(...)`。
# 8/9 鸿波在 Mac 上跑, 撞 "No module named pytest" —— 因为那样写等于要求
# **在 hermes 自己的 venv 里跑 pytest**, 而那个 venv 是运行时环境, 不该为了
# 跑我们的测试往里装开发依赖 (何况 hermes 重装会把它冲掉)。
#
# 更根本的问题: 这几条测试从头到尾**只是在读源码字面量**, 却为此要 import
# 整个 agent 框架。plugin.py 的 _check_attr_in_source / _check_class_method_
# in_source 早就是静态读法, 而且注释写明了理由 ——「不 import, 避开 hermes
# 0.15.1 circular」。照它做就行。
#
# 改成静态读之后: CI、沙箱、Mac 上都能跑, 不挑解释器版本, 不动 hermes venv。

def _hermes_root() -> Path:
    """跟 conftest / plugin.py 同一个解析方式。"""
    return Path(
        os.environ.get("HERMES_ROOT") or os.path.expanduser("~/.hermes/hermes-agent")
    )


def _hermes_source(rel_path: str) -> str | None:
    """读 hermes 里某个文件的源码; 文件不在返 None。"""
    f = _hermes_root() / rel_path
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        return None


requires_hermes_source = pytest.mark.skipif(
    not (_hermes_root() / "run_agent.py").exists(),
    reason=f"没找到 hermes 源码 ({_hermes_root()}); 设 HERMES_ROOT 或装好 hermes",
)


def _slice_def(src: str, name: str) -> str:
    """粗切一个 def 的函数体 —— 从 `def name(` 到下一个同级 `def`/`class`。

    够用就行: 我们只是想确认某个键名出现在**这个函数里**, 而不是文件别处。
    切不出来就返全文 (宁可放松也不假红)。
    """
    start = src.find(f"def {name}(")
    if start == -1:
        return src
    rest = src[start:]
    for marker in ("\n    def ", "\n\ndef ", "\nclass "):
        end = rest.find(marker, 1)
        if end != -1:
            rest = rest[:end]
    return rest


@requires_hermes_source
def test_契约_hermes_还有_get_activity_summary():
    """升级 hermes 后这条先红, 而不是等 UI 上进度默默空着。"""
    src = _hermes_source("run_agent.py")
    assert src is not None, "读不到 run_agent.py"
    assert "def get_activity_summary(" in src, (
        "run_agent.py 里没有 get_activity_summary —— hermes 改了契约, "
        "P44 端点会一直返 no_running_turn"
    )


@requires_hermes_source
def test_契约_gateway_侧的两个取数锚点还在():
    """activity_probe 靠这两个东西找到正在跑的 agent。"""
    src = _hermes_source("gateway/run.py")
    assert src is not None, "读不到 gateway/run.py"
    for anchor in ("_gateway_runner_ref", "_AGENT_PENDING_SENTINEL"):
        assert anchor in src, f"gateway/run.py 里没有 {anchor} —— 取数路径断了"


@requires_hermes_source
def test_契约_快照带着我们_UI_依赖的键():
    """只钉 UI 真读的那几个 —— 钉全集会因为 hermes 加字段而假红。

    agentActivity.ts 的 describeTurn() 读: current_tool / api_call_count /
    max_iterations / last_activity_description。少任何一个, UI 上那行进度
    就残缺, **而端点照样返 200** —— 这是这个功能最容易的坏法。
    """
    src = _hermes_source("run_agent.py")
    assert src is not None
    body = _slice_def(src, "get_activity_summary")
    for key in (
        "current_tool",
        "api_call_count",
        "max_iterations",
        "last_activity_description",
    ):
        assert key in body, (
            f"get_activity_summary 里找不到 {key!r} —— hermes 改了契约, "
            f"agentActivity.ts 的 describeTurn 要跟着改"
        )


@requires_hermes_source
def test_契约_seconds_since_activity_由共享模块产出():
    """idle 判据的来源。它在 agent/session_activity.build_activity_snapshot 里,
    不在 get_activity_summary 的字面量里 —— 分开钉, 免得改一处漏一处。"""
    src = _hermes_source("agent/session_activity.py")
    assert src is not None, "读不到 agent/session_activity.py"
    body = _slice_def(src, "build_activity_snapshot")
    assert "seconds_since_activity" in body
