"""定时蒸馏。

8/4 鸿波「为什么超 24 小时蒸馏没自动启动, 要手动」。查下来根本没有定时器 ——
_summarize_and_distill_async 只有一个调用点, 挂在会话结束上; 没有会话结束就
永远不跑。而 UI 上写着"自动每 24h 跑"。
"""
from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

import catfish_memory as cm


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    tmp_path.mkdir(exist_ok=True)
    return tmp_path


def test_no_picker_model_means_no_run(home, monkeypatch):
    """★ 原则: 蒸馏必须用员工在 picker 选的模型。

    取不到模型就不跑 —— 绝不退到某个默认模型偷偷用别的。
    """
    monkeypatch.setattr(cm, "_read_picker_state_model", lambda h: "")
    called = []
    monkeypatch.setattr(cm, "run_distill_for_dream_engine",
                        lambda *a, **k: called.append(1))
    cm._try_distill_once()
    assert called == [], "没选模型却跑了蒸馏"


def test_uses_the_picked_model_not_a_default(home, monkeypatch):
    monkeypatch.setattr(cm, "_read_picker_state_model", lambda h: "catfish-public-qwen-flash")
    seen = {}

    async def fake(model, *, force=True, **kw):
        seen["model"] = model
        seen["force"] = force
        return {"ok": True, "chunks_total": 3, "bytes_written": 10, "took_seconds": 1.0}

    monkeypatch.setattr(cm, "run_distill_for_dream_engine", fake)
    cm._try_distill_once()
    assert seen["model"] == "catfish-public-qwen-flash"
    assert seen["force"] is False, "定时器必须尊重 24h cooldown, 不能 force"


def test_lock_held_elsewhere_skips(home, monkeypatch):
    """两个进程同时过 cooldown 会抢着写同一个 distilled_facts.md。"""
    monkeypatch.setattr(cm, "_read_picker_state_model", lambda h: "m")
    called = []
    monkeypatch.setattr(cm, "run_distill_for_dream_engine",
                        lambda *a, **k: called.append(1))

    lock = home / "memory_distill.lock"
    with open(lock, "w", encoding="utf-8") as other:
        fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        cm._try_distill_once()
    assert called == [], "锁被别人占着还是跑了"


def test_exception_does_not_escape(home, monkeypatch):
    """定时器绝不能把 plugin 带崩。"""
    monkeypatch.setattr(cm, "_read_picker_state_model", lambda h: "m")

    async def boom(*a, **k):
        raise RuntimeError("LLM 挂了")

    monkeypatch.setattr(cm, "run_distill_for_dream_engine", boom)
    cm._try_distill_once()   # 不该抛


def test_missing_home_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "nope"))
    monkeypatch.setattr(cm, "_read_picker_state_model", lambda h: "m")
    cm._try_distill_once()   # 不该抛


def test_timer_starts_only_once(monkeypatch):
    monkeypatch.setattr(cm, "_distill_timer_started", False)
    started = []
    class FakeThread:
        def __init__(self, **kw): started.append(kw.get("name"))
        def start(self): pass
    monkeypatch.setattr(cm.threading, "Thread", FakeThread)
    cm.start_distill_timer()
    cm.start_distill_timer()
    assert started == ["catfish-distill-timer"], f"起了多个线程: {started}"
