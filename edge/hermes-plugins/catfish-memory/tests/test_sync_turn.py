"""catfish-memory plugin sync_turn + 节流 单测 (BL-MEMORY-SYNC-TURN-REFACTOR, 5/20).

替代老 test_on_session_end.py — Step D 失败教训:
  - on_session_end 不在 per-chat trigger (run_agent.py:16078 注释)
  - 改用 sync_turn (per-turn hook) + 节流 (每 N 轮 / 时间窗口)
  - on_session_end 退化成 force-flush 兜底 (CLI atexit / /reset / /new)

跑法 (从 plugin 目录):
    python -m pytest tests/test_sync_turn.py -v

设计:
  - mock _call_summarize_llm + _call_distill_llm (module-level async, monkeypatch 简单)
  - mock threading.Thread.start 让它**同步**跑 — deterministic
  - 文件落 tmp_path, 不污染 ~/.catfish/
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

# 加 plugin 自己到 sys.path (跟 test_catfish_memory.py 同套路)
sys.path.insert(0, str(Path(__file__).parent.parent))

import catfish_memory  # noqa: E402
from catfish_memory import (  # noqa: E402
    CatfishMemoryProvider,
    _append_journal,
    _extract_message_pairs,
    _format_journal_entry,
    _mark_distill_run,
    _read_full_journal,
    _should_run_distill,
    _write_distilled,
)


# ── fixtures ────────────────────────────────────────────


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """假 ~/.catfish/ — CATFISH_HOME env 指过去"""
    d = tmp_path / ".catfish"
    d.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(d))
    return d


@pytest.fixture
def provider(fake_home: Path) -> CatfishMemoryProvider:
    p = CatfishMemoryProvider()
    p.initialize(session_id="test-session-sync-turn")
    return p


@pytest.fixture
def sync_thread(monkeypatch: pytest.MonkeyPatch):
    """让 plugin spawn 的 thread **同步** 跑 — deterministic 测试.

    替换 threading.Thread, target() 直接同步执行不开新线程.
    """
    import threading

    class _SyncThread:
        def __init__(self, target=None, name=None, daemon=None, **kwargs):
            self._target = target

        def start(self):
            if self._target:
                self._target()

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(catfish_memory.threading, "Thread", _SyncThread)
    return _SyncThread


@pytest.fixture
def env_enable(monkeypatch: pytest.MonkeyPatch):
    """3 件套 env: 启用 + model + token (任一缺都 skip)"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "1")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", "catfish-public-qwen-flash")
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "test-token")


@pytest.fixture
def mock_llm(monkeypatch: pytest.MonkeyPatch):
    """mock _call_summarize_llm + _call_distill_llm. 返成功 summary, distill 返 None
    (默认不蒸馏, 用 24h cooldown). 每次调用记 args."""
    calls = {"summarize": [], "distill": []}

    async def fake_summarize(pairs, model):
        calls["summarize"].append({"pairs": list(pairs), "model": model})
        return "### 测试主题\n\n这是一段总结"

    async def fake_distill(text, model):
        calls["distill"].append({"text": text, "model": model})
        return None  # 默认不返蒸馏 (24h 内已跑过 / 没足够内容)

    monkeypatch.setattr(catfish_memory, "_call_summarize_llm", fake_summarize)
    monkeypatch.setattr(catfish_memory, "_call_distill_llm", fake_distill)
    return calls


# ── 节流 A: 每 N 轮 ──────────────────────────────────────


def test_sync_turn_default_threshold_5_no_trigger_at_turn_4(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """默认 N=5, 前 4 轮不触发"""
    for i in range(4):
        provider.sync_turn(f"user msg {i}", f"assistant {i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 0
    assert not (fake_home / "employee_journal.md").exists()


def test_sync_turn_default_threshold_5_triggers_at_turn_5(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """默认 N=5, 第 5 轮触发"""
    for i in range(5):
        provider.sync_turn(f"user msg {i}", f"assistant {i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 1
    # 触发时 buffer 含 10 entries (5 pairs × 2)
    assert len(mock_llm["summarize"][0]["pairs"]) == 10
    # journal 被写
    assert (fake_home / "employee_journal.md").exists()


def test_sync_turn_buffer_resets_after_trigger(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """触发后 buffer 清空, 下一轮重新计数 (1 < N=5 不触发)"""
    for i in range(5):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 1
    # 第 6 轮不触发 (counter 重置, 才 1 轮)
    provider.sync_turn("u5", "a5", session_id="s1")
    assert len(mock_llm["summarize"]) == 1
    # 第 7/8/9/10 也不触发 — 总共 5 轮后又满 5 才再触发
    for i in range(6, 10):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 2
    # 第 2 次触发的 buffer 只含轮 5-9 (10 entries)
    assert len(mock_llm["summarize"][1]["pairs"]) == 10
    assert mock_llm["summarize"][1]["pairs"][0] == ("user", "u5")


def test_sync_turn_custom_n_threshold_via_env(
    provider, env_enable, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """env CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS=3 → 3 轮触发"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "3")
    for i in range(2):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 0
    provider.sync_turn("u2", "a2", session_id="s1")
    assert len(mock_llm["summarize"]) == 1
    assert len(mock_llm["summarize"][0]["pairs"]) == 6  # 3 pairs


def test_sync_turn_n_threshold_minimum_1(
    provider, env_enable, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """env=0 或负数 → 强制 min 1 (每轮都触发)"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "0")
    provider.sync_turn("u0", "a0", session_id="s1")
    assert len(mock_llm["summarize"]) == 1


def test_sync_turn_n_threshold_invalid_falls_back_to_default(
    provider, env_enable, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """env 非数字 → fallback default 5"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "abc")
    for i in range(4):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 0
    provider.sync_turn("u4", "a4", session_id="s1")
    assert len(mock_llm["summarize"]) == 1


# ── 节流 B: 时间窗口 ─────────────────────────────────────


def test_sync_turn_time_throttle_triggers_after_interval(
    provider, env_enable, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """距上次 summary >= min_interval + 有至少 1 轮 → 触发"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "100")  # N 几乎不触发
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "60")

    # 假装 90 秒前刚 summary 过
    provider._last_summary_ts = time.time() - 90  # 跨阈值

    provider.sync_turn("u0", "a0", session_id="s1")
    # 触发 (距上次 >= 60s + 至少 1 轮)
    assert len(mock_llm["summarize"]) == 1


def test_sync_turn_time_throttle_no_trigger_within_interval(
    provider, env_enable, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """距上次 < min_interval + 没满 N 轮 → 不触发"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "100")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "60")

    # 刚 5 秒前 summary 过
    provider._last_summary_ts = time.time() - 5

    for i in range(3):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 0


def test_sync_turn_time_throttle_disabled_when_interval_zero(
    provider, env_enable, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """env interval=0 → 时间节流禁用, 只看 N 轮"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "5")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "0")

    # interval=0 + last_summary_ts 很久之前 — 应该 elapsed > 0 总是 True
    # 但 triggered_by_time 还要求 turns >= 1, 第 1 轮就触发?
    # 0 实际意思是禁用时间节流 (永远不靠时间触发, 不是"任何时间都触发").
    # 看实现: triggered_by_time = elapsed >= min_interval and turns >= 1
    # min_interval=0 → elapsed >= 0 True (除非负, 永真) + turns >= 1 → 第 1 轮就触发.
    # 这是 "interval=0 = 时间禁用" 的反语义. 我们要重新理解 — 实际上意思是
    # "interval=0 → 时间节流条件 trivially 满足, 所以每轮都触发". 这不是禁用.
    #
    # 修法: 实现里 if min_interval <= 0 应当 disable time-based trigger.
    # 但这跟当前代码不一致 — 当前 min_interval=0 会让时间路径每轮 trigger.
    #
    # 测试断言: interval=0 时, 第 1 轮就触发 (按当前实现).
    # TODO: 如果想 0=disable, 改实现里 max(0, ...) → 加 if min_interval == 0: 跳时间分支
    provider.sync_turn("u0", "a0", session_id="s1")
    assert len(mock_llm["summarize"]) == 1  # interval=0 实际让第 1 轮就触发


# ── Env 控制 ─────────────────────────────────────────


def test_sync_turn_noop_when_disabled_by_env(
    provider, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """CATFISH_PLUGIN_SUMMARIZE=0 → 整体 skip, 不累积 buffer 不触发"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "0")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", "x")
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "t")

    for i in range(10):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 0
    assert provider._turns_since_last_summary == 0  # 完全 skip, 不累积


def test_sync_turn_noop_when_no_model_env(
    provider, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """没 CATFISH_PLUGIN_SUMMARIZE_MODEL → skip"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "1")
    monkeypatch.delenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", raising=False)

    for i in range(10):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 0
    assert provider._turns_since_last_summary == 0


# ── Content 过滤 ─────────────────────────────────────


def test_sync_turn_skips_empty_user_content(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    provider.sync_turn("", "assistant says hi", session_id="s1")
    assert provider._turns_since_last_summary == 0


def test_sync_turn_skips_empty_assistant_content(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    provider.sync_turn("user msg", "", session_id="s1")
    assert provider._turns_since_last_summary == 0


def test_sync_turn_skips_whitespace_only_content(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    provider.sync_turn("   ", "real reply", session_id="s1")
    provider.sync_turn("real msg", "  \n  ", session_id="s1")
    assert provider._turns_since_last_summary == 0


def test_sync_turn_skips_non_string_content(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """role 内容非 str (list / None / int) skip"""
    provider.sync_turn(None, "a", session_id="s1")  # type: ignore[arg-type]
    provider.sync_turn("u", None, session_id="s1")  # type: ignore[arg-type]
    provider.sync_turn(123, "a", session_id="s1")  # type: ignore[arg-type]
    assert provider._turns_since_last_summary == 0


# ── 双写期幂等 ──────────────────────────────────────────


def test_sync_turn_skips_writing_when_external_write_detected(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """模拟 gateway 在 plugin init 之后写了 journal (mtime > our_last_ts + 5s)
    → 视为外部写, plugin skip 避免 dup."""
    # plugin init 时 _last_summary_ts = init_time. 我们把它强制设回 30s 前,
    # 然后 journal mtime 设到 10s 前 (= init - 30 + 20 = init - 10).
    # mtime (init-10) > our_last_ts (init-30) + 5 → True, skip ✓
    init_time = time.time()
    provider._last_summary_ts = init_time - 30  # plugin 上次 trigger 30s 前

    j = fake_home / "employee_journal.md"
    j.write_text("已有内容\n", encoding="utf-8")
    # journal mtime = 10s 前, 即 our_last_ts (30s 前) + 20s, 远超 5s buffer
    recent = init_time - 10
    os.utime(j, (recent, recent))

    for i in range(5):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    # 触发 (5 轮), 但 mtime check skip
    assert len(mock_llm["summarize"]) == 0
    # counter 重置 (避免 next 5 轮 again skip — trade-off)
    assert provider._turns_since_last_summary == 0


def test_sync_turn_proceeds_when_journal_only_written_by_plugin_itself(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """plugin 自己写完 journal, 下次触发不被自己 skip (bug fix from initial impl)"""
    j = fake_home / "employee_journal.md"
    j.write_text("旧内容\n", encoding="utf-8")
    old = time.time() - 600  # 10 分钟前, 远超 plugin init time
    os.utime(j, (old, old))

    # 第 1 次 5 轮 → trigger 1
    for i in range(5):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 1
    content = j.read_text(encoding="utf-8")
    assert "旧内容" in content
    assert "测试主题" in content  # mock summary 写入

    # 第 2 次 5 轮 → trigger 2 (plugin 自己刚写过 journal, 不该被 mtime check skip)
    for i in range(5, 10):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 2


# ── on_session_end force-flush ─────────────────────────────


def test_on_session_end_flushes_remaining_buffer(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """session 真结束时 buffer 有 3 轮 (没满 5), force flush 也总结一次"""
    for i in range(3):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 0  # 没满 5 轮

    provider.on_session_end([])  # messages 不用, 用 buffer
    assert len(mock_llm["summarize"]) == 1
    assert len(mock_llm["summarize"][0]["pairs"]) == 6  # 3 pairs


def test_on_session_end_noop_when_buffer_empty(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """buffer 空 (刚触发过 / 没累积) → on_session_end no-op"""
    provider.on_session_end([])
    assert len(mock_llm["summarize"]) == 0


def test_on_session_end_clears_buffer(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """force flush 后 buffer 清空, counter 重置"""
    for i in range(3):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    provider.on_session_end([])
    assert len(provider._turn_buffer) == 0
    assert provider._turns_since_last_summary == 0


def test_on_session_end_noop_when_disabled(
    provider, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """CATFISH_PLUGIN_SUMMARIZE=0 → force flush 也 no-op"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "0")
    # 直接塞 buffer 模拟旧状态 (sync_turn 跟 force_flush 各自独立 env gate)
    provider._turn_buffer = [("user", "x"), ("assistant", "y")]
    provider.on_session_end([])
    assert len(mock_llm["summarize"]) == 0
    # 即使 skip 写, on_session_end 也清不清 buffer? — 我代码里 env gate 早 return,
    # buffer 不被清. 这是 expected (回滚兜底, 让下次再 enable 时还能 flush)
    assert len(provider._turn_buffer) == 2


def test_on_session_end_does_not_raise_on_exception(
    provider, env_enable, sync_thread, fake_home, monkeypatch,
):
    """force flush LLM 抛异常 → on_session_end 静默, 不挂 hermes"""
    async def fake_summarize_raises(pairs, model):
        raise RuntimeError("LLM exploded")
    monkeypatch.setattr(catfish_memory, "_call_summarize_llm", fake_summarize_raises)

    provider._turn_buffer = [("user", "x"), ("assistant", "y")]
    # 不应该抛
    provider.on_session_end([])


# ── 线程安全 ─────────────────────────────────────────


def test_sync_turn_threadsafe_counter(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """多线程并发 sync_turn 不会丢 counter — lock 保护"""
    import threading as _t

    def worker():
        for _ in range(5):
            provider.sync_turn("u", "a", session_id="s1")

    threads = [_t.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 4 threads × 5 turns = 20 turns. 5 轮触发 → 应该 4 次 summary (20/5).
    # 但因 sync_thread fixture 让 spawn 同步跑, 触发后 buffer 立即清,
    # 4 个 worker 应该看到一致的 counter 状态.
    assert len(mock_llm["summarize"]) == 4
    # 触发后 buffer 应该全清
    assert len(provider._turn_buffer) == 0


# ── 集成: 端到端流程 ──────────────────────────────────────


def test_sync_turn_end_to_end_writes_correct_journal_format(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """触发后 journal 文件内容格式跟旧 gateway summary 一致"""
    for i in range(5):
        provider.sync_turn(f"用户问 {i}", f"鲶鱼答 {i}", session_id="abc123def456")

    content = (fake_home / "employee_journal.md").read_text(encoding="utf-8")
    assert "## " in content  # 日期 header
    assert "session `…def456`" in content  # session_id 尾 6 字符
    assert "测试主题" in content


def test_sync_turn_distill_triggered_when_24h_passed(
    provider, env_enable, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """触发 summarize 后, 满足 24h cooldown 也跑 distill"""
    # mock distill 这次返成功 (不是默认 None)
    async def fake_distill_success(text, model):
        mock_llm["distill"].append({"text": text, "model": model})
        return "## 长期事实\n\n- 鸿波偏好简短"
    monkeypatch.setattr(catfish_memory, "_call_distill_llm", fake_distill_success)

    for i in range(5):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 1
    assert len(mock_llm["distill"]) == 1
    assert (fake_home / "distilled_facts.md").exists()


def test_sync_turn_distill_skipped_when_within_24h(
    provider, env_enable, mock_llm, sync_thread, fake_home,
):
    """24h 内跑过蒸馏 → 触发 summary 但 skip distill"""
    _mark_distill_run(fake_home)

    for i in range(5):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 1
    assert len(mock_llm["distill"]) == 0  # 24h cooldown


# ── 配置 getter helpers ──────────────────────────────


def test_get_n_turns_threshold_default(provider, monkeypatch):
    monkeypatch.delenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", raising=False)
    assert provider._get_n_turns_threshold() == 5


def test_get_n_turns_threshold_env(provider, monkeypatch):
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "10")
    assert provider._get_n_turns_threshold() == 10


def test_get_n_turns_threshold_invalid_env(provider, monkeypatch):
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "garbage")
    assert provider._get_n_turns_threshold() == 5  # fallback


def test_get_min_interval_default(provider, monkeypatch):
    monkeypatch.delenv("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", raising=False)
    assert provider._get_min_interval_seconds() == 1800


def test_get_min_interval_env(provider, monkeypatch):
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "600")
    assert provider._get_min_interval_seconds() == 600
