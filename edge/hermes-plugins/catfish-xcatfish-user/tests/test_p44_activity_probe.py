"""P44 进度快照出口 —— 判据 + 跟 hermes 的形状契约。

分两半:

**判据 (不需要 hermes)**: 取数路径上每个"拿不到"的岔口都返明确的原因码, 而不是
假数据、也不是异常。这半在 CI 上就能跑。

**形状契约 (需要真 hermes)**: `AIAgent.get_activity_summary()` 还在、还返 dict、
还带我们 UI 依赖的那几个键。hermes 升级把它改了, 这条要红 —— 这正是这个功能
最容易静默坏掉的地方: 端点照样 200、turns 照样有值, 只是 UI 上那行进度永远
空着, 没人会发现。
"""
from __future__ import annotations

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


# ── 形状契约: 需要真 hermes ────────────────────────────────────

def _hermes_ready() -> bool:
    try:
        import run_agent  # noqa: F401,PLC0415
        return True
    except Exception:  # noqa: BLE001
        return False


requires_hermes = pytest.mark.skipif(
    not _hermes_ready(),
    reason="需要真 hermes (跑法: cd ~/.hermes/hermes-agent 后再 pytest)",
)


@requires_hermes
def test_契约_hermes_还有_get_activity_summary():
    """升级 hermes 后这条先红, 而不是等 UI 上进度默默空着。"""
    missing = activity_probe.verify_patch_targets()
    assert missing == [], f"P44 依赖的 hermes 目标缺了: {missing}"


@requires_hermes
def test_契约_快照带着我们_UI_依赖的键():
    """只钉 UI 真读的那几个 —— 钉全集会因为 hermes 加字段而假红。

    agentActivity.ts 的 describeTurn() 读: current_tool / api_call_count /
    max_iterations / seconds_since_activity / last_activity_description。
    少任何一个, UI 上那行进度就残缺。
    """
    import inspect

    from run_agent import AIAgent

    src = inspect.getsource(AIAgent.get_activity_summary)
    for key in (
        "current_tool",
        "api_call_count",
        "max_iterations",
        "last_activity_description",
    ):
        assert key in src, (
            f"AIAgent.get_activity_summary 里找不到 {key!r} —— hermes 改了契约, "
            f"agentActivity.ts 的 describeTurn 要跟着改"
        )


@requires_hermes
def test_契约_seconds_since_activity_由共享模块产出():
    """idle 判据的来源。它在 agent/session_activity.build_activity_snapshot 里,
    不在 get_activity_summary 的字面量里 —— 分开钉, 免得改一处漏一处。"""
    import inspect

    from agent.session_activity import build_activity_snapshot

    assert "seconds_since_activity" in inspect.getsource(build_activity_snapshot)
