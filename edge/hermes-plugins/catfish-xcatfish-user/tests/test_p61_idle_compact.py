"""P61 空闲后台压缩 (10/19): 回复完上下文超 80K 才在后台压; 压过之后再长 30K 才再压; 别处在压 / 出错都不影响回复。

hermes 的 agent / session_db 用假的; 真的 _compress_context 在 hermes 自己的测试里。
"""
from __future__ import annotations

import asyncio
import contextvars
import sys
import types

import pytest

import plugin_idle_compact as p61


@pytest.fixture(autouse=True)
def _reset():
    p61._inflight.clear()
    p61._last_done.clear()
    yield
    p61._inflight.clear()
    p61._last_done.clear()


def test_门槛_超80K才压_压过再长30K才再压():
    assert not p61.should_compact("s1", 79_999)
    assert not p61.should_compact(None, 200_000)
    assert p61.should_compact("s1", 85_000)
    assert not p61.should_compact("s1", 90_000), "正在压的不重复起"
    p61._finish("s1", 85_000, compressed=True)
    assert not p61.should_compact("s1", 100_000), "离上次没再长 30K"
    assert p61.should_compact("s1", 116_000)
    p61._finish("s1", 116_000, compressed=False)
    assert p61.should_compact("s1", 116_000), "没压成的不算数, 下次还能试"


class _DB:
    def __init__(self, holder=None, n=10):
        self.holder, self.n = holder, n

    def get_compression_lock_holder(self, sid):
        return self.holder

    def get_session(self, sid):
        return {"id": sid, "system_prompt": "x"}

    def get_messages_as_conversation(self, sid):
        return [{"role": "user", "content": str(i)} for i in range(self.n)]


class _Agent:
    def __init__(self, shrink=True):
        self.shrink, self.closed, self.calls = shrink, False, []
        self.context_compressor = types.SimpleNamespace(bind_session_state=lambda db, sid: None)

    def _compress_context(self, msgs, sysmsg, **kw):
        self.calls.append(kw)
        return (msgs[:3] if self.shrink else msgs), ""

    def close(self):
        self.closed = True


@pytest.fixture()
def fake_hermes(monkeypatch):
    conv = types.ModuleType("agent.conversation_compression")
    conv.CompressionCommitFence = lambda total_ceiling_seconds: ("fence", total_ceiling_seconds)
    run = types.ModuleType("gateway.run")
    run._GATEWAY_HYGIENE_PLATFORM = "gateway_hygiene"
    run._seed_hygiene_system_prompt = lambda agent, row: True
    for name, mod in {"agent": types.ModuleType("agent"), "agent.conversation_compression": conv,
                      "gateway": types.ModuleType("gateway"), "gateway.run": run}.items():
        monkeypatch.setitem(sys.modules, name, mod)


def test_压一次_in_place_用临时agent_不结束会话_用完关掉(fake_hermes):
    a = _Agent()
    assert p61.compact_now(lambda: a, _DB(), "s1", 90_000) is True
    kw = a.calls[0]
    assert kw["trigger"] == "idle" and kw["task_id"] == "s1" and kw["approx_tokens"] == 90_000
    assert a.compression_in_place is True and a._end_session_on_close is False and a.closed


def test_别处在压就不压_hermes判定不用压也照实返回(fake_hermes):
    a = _Agent()
    assert p61.compact_now(lambda: a, _DB(holder="pid=1"), "s1", 90_000) is False and not a.calls
    assert p61.compact_now(lambda: _Agent(shrink=False), _DB(), "s1", 90_000) is False


def test_后台出错不抛_回调照样跑_计数清掉(fake_hermes):
    seen = []

    def boom():
        raise RuntimeError("model down")

    assert p61.should_compact("s1", 90_000)
    asyncio.run(p61.run_in_background(boom, _DB(), "s1", 90_000, contextvars.copy_context(), after=seen.append))
    assert seen == [False] and "s1" not in p61._inflight


def test_工作台入口_超门槛才起后台任务_回复原样返回(fake_hermes, monkeypatch):
    started = []
    monkeypatch.setattr(p61, "run_in_background", lambda *a, **k: _noop(started, a))

    async def orig(self, **kw):
        return ({"final_response": "好", "last_prompt_tokens": kw.pop("_tokens")}, {})

    adapter = types.SimpleNamespace(_ensure_session_db=lambda: _DB())
    wrapped = p61.wrap_api_run_agent(orig)

    async def go(tokens, sid="s1"):
        return await wrapped(adapter, session_id=sid, _tokens=tokens)

    res = asyncio.run(go(50_000))
    assert res[0]["final_response"] == "好" and started == []
    asyncio.run(go(95_000))
    assert len(started) == 1 and started[0][2] == "s1" and started[0][3] == 95_000


async def _noop(started, args):
    started.append(args)


def test_网关入口_会话还在跑就不压(fake_hermes, monkeypatch):
    started = []
    monkeypatch.setattr(p61, "run_in_background", lambda *a, **k: _noop(started, a))

    async def orig(self, event):
        return "回复"

    entry = types.SimpleNamespace(session_id="w1", last_prompt_tokens=150_000)
    runner = types.SimpleNamespace(
        _session_key_for_source=lambda src: "k1", session_store=types.SimpleNamespace(lookup_by_session_key=lambda k: entry),
        _is_session_running=lambda k: True, _session_db=_DB(), config=types.SimpleNamespace(multiplex_profiles=False),
        _resolve_session_agent_runtime=lambda **kw: ("m", {}))
    wrapped = p61.wrap_gateway_handle_message(orig)
    ev = types.SimpleNamespace(source=object())
    assert asyncio.run(wrapped(runner, ev)) == "回复" and started == []
    runner._is_session_running = lambda k: False
    asyncio.run(wrapped(runner, ev))
    assert len(started) == 1 and started[0][2] == "w1"


def test_摘要篇幅上限_旧摘要截头尾_请求末尾加上限():
    class C:
        _previous_summary = "旧" * 5000 + "中间" * 20000 + "最近" * 6000

    def orig(self, content, budget, focus, memory, has_user):
        return f"PROMPT with previous={len(self._previous_summary)}"

    c = C()
    out = p61.wrap_build_summary_prompt(orig)(c, "x", 2000, None, "", True)
    assert len(c._previous_summary) < p61.PREV_SUMMARY_MAX_CHARS + 100
    assert c._previous_summary.startswith("旧") and c._previous_summary.endswith("最近"), "留开头和最近的"
    assert "session_search" in c._previous_summary
    assert out.startswith("PROMPT") and "HARD LENGTH LIMIT" in out and "不超过 12000 字" in out
    c2 = C()
    c2._previous_summary = "短的"
    p61.wrap_build_summary_prompt(orig)(c2, "x", 2000, None, "", True)
    assert c2._previous_summary == "短的", "短的不动"
