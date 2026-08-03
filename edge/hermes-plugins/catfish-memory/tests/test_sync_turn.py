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

    # 预设 file state: 90 秒前刚 summary 过 (file 持久化 state, 跨 instance)
    import catfish_memory as _cm
    _cm._write_state(fake_home, {"last_summary_ts": time.time() - 90})

    provider.sync_turn("u0", "a0", session_id="s1")
    # 触发 (距上次 >= 60s + 至少 1 轮)
    assert len(mock_llm["summarize"]) == 1


def test_sync_turn_time_throttle_no_trigger_within_interval(
    provider, env_enable, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """距上次 < min_interval + 没满 N 轮 → 不触发"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "100")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "60")

    # 预设 file state: 刚 5 秒前 summary 过
    import catfish_memory as _cm
    _cm._write_state(fake_home, {"last_summary_ts": time.time() - 5})

    for i in range(3):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 0


def test_sync_turn_time_throttle_disabled_when_no_prior_summary(
    provider, env_enable, mock_llm, sync_thread, fake_home, monkeypatch,
):
    """plugin 没跑过 (file state 没 last_summary_ts) → 时间节流不触发, 只靠 N 轮.

    新设计 (5/20 Day 2): time-based trigger 要求 last_summary_ts > 0. 这避免了
    plugin 首次启动 (state 没值) 撞 "elapsed=inf, 第 1 轮就触发" trivially case.
    """
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "5")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "0")
    # 不写 state file → last_summary_ts 默认 0

    provider.sync_turn("u0", "a0", session_id="s1")
    # 第 1 轮: file_pairs=1, 不满 N=5, 时间分支要求 last_ts>0 (默认 0) 不触发
    assert len(mock_llm["summarize"]) == 0
    # 累积 5 轮才触发
    for i in range(1, 5):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    assert len(mock_llm["summarize"]) == 1


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
    """模拟 gateway 在 plugin 上次 summary 之后写了 journal (mtime > our_last_ts + 5s)
    → 视为外部写, plugin skip 避免 dup."""
    import catfish_memory as _cm
    init_time = time.time()
    # 预设 file state: plugin 上次 summary 在 30s 前
    _cm._write_state(fake_home, {"last_summary_ts": init_time - 30})

    j = fake_home / "employee_journal.md"
    j.write_text("已有内容\n", encoding="utf-8")
    # journal mtime = 10s 前 = our_last_ts (30s 前) + 20s, 远超 5s buffer → 判为外部
    recent = init_time - 10
    os.utime(j, (recent, recent))

    for i in range(5):
        provider.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    # 触发 (n_pairs=5), 但 mtime check skip
    assert len(mock_llm["summarize"]) == 0
    # buffer file 已被 clear (即使 skip 写, trigger 路径走完了)
    assert _cm._read_buffer(fake_home) == []


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
    # C3 (6/6): product code 改 backtick → pipe, test 同步
    # 8/4: 改断言 —— journal 现在写**完整** session_id。
    # 老行为截成后 6 位, 把溯源链掐断了 (wiki sources 只到日期 + journal 只有
    # 6 位后缀 → 回溯不到原始对话)。原文一直完整躺在 ~/.hermes/state.db,
    # 只是指针被截断。见 catfish_memory_helpers._format_journal_entry 的注释。
    assert "abc123def456" in content  # 完整 sid, 不再截断
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


# ── 文件持久化 buffer + state 跨 instance (BL-MEMORY-SYNC-TURN-REFACTOR Day 2) ─
#
# hermes api_server 每个 chat completion 创建新 plugin instance, instance state
# 不能累积. 验证文件持久化跨 instance 工作.


def test_buffer_persists_across_instances(fake_home, env_enable, mock_llm, sync_thread):
    """5 个不同 provider instance 各调 1 次 sync_turn → 5 轮累积 → 第 5 个触发"""
    import catfish_memory as _cm

    for i in range(5):
        # 新 instance (模拟 hermes api_server 每 chat new AIAgent)
        p = _cm.CatfishMemoryProvider()
        p.initialize(session_id=f"session-{i}")
        p.sync_turn(f"user msg {i}", f"assistant reply {i}", session_id=f"session-{i}")

    # 5 个 instance 各 1 turn, 累积 5 pair → 第 5 次应该触发
    assert len(mock_llm["summarize"]) == 1
    assert len(mock_llm["summarize"][0]["pairs"]) == 10  # 5 pair × 2 entries


def test_buffer_file_cleared_after_trigger(fake_home, env_enable, mock_llm, sync_thread):
    """触发后 buffer file 被清空 (file 不存在或为空)"""
    import catfish_memory as _cm

    for i in range(5):
        p = _cm.CatfishMemoryProvider()
        p.initialize(session_id="s1")
        p.sync_turn(f"u{i}", f"a{i}", session_id="s1")

    assert _cm._read_buffer(fake_home) == []  # buffer cleared


def test_state_file_updated_after_trigger(fake_home, env_enable, mock_llm, sync_thread):
    """触发后 state file 的 last_summary_ts 被更新到当前时间"""
    import catfish_memory as _cm

    before = time.time()
    for i in range(5):
        p = _cm.CatfishMemoryProvider()
        p.initialize(session_id="s1")
        p.sync_turn(f"u{i}", f"a{i}", session_id="s1")
    after = time.time()

    state = _cm._read_state(fake_home)
    last_ts = state.get("last_summary_ts", 0)
    assert before <= last_ts <= after


def test_buffer_file_corrupt_recovers_gracefully(fake_home, env_enable, mock_llm, sync_thread):
    """buffer file 内容 corrupt (非 json 行) 不挂, 跳过 corrupt 行只读合法的"""
    import catfish_memory as _cm

    buf_path = _cm._buffer_file_path(fake_home)
    buf_path.parent.mkdir(parents=True, exist_ok=True)
    buf_path.write_text(
        '{"ts": 1, "role": "user", "content": "valid"}\n'
        'this is not json\n'
        '{"ts": 2, "role": "assistant", "content": "valid back"}\n'
        '{}\n',  # missing role/content
        encoding="utf-8",
    )

    pairs = _cm._read_buffer(fake_home)
    assert len(pairs) == 2  # 只读到 valid 两个, corrupt 跳过
    assert pairs[0] == ("user", "valid")
    assert pairs[1] == ("assistant", "valid back")


def test_state_file_corrupt_recovers_gracefully(fake_home, env_enable, mock_llm, sync_thread):
    """state file 内容 corrupt → 返空 dict, 不挂"""
    import catfish_memory as _cm

    state_path = _cm._state_file_path(fake_home)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("this is not json", encoding="utf-8")

    state = _cm._read_state(fake_home)
    assert state == {}


# ── yaml 配置 (BL-PLUGIN-CONFIG-YAML Day 2.5) ─────────────────────


def _write_plugin_yaml(home: Path, content: str) -> None:
    """写测试用 yaml 配置到 fake home"""
    import catfish_memory as _cm
    path = _cm._plugin_config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_yaml_overrides_env_n_turns(fake_home, monkeypatch, provider):
    """yaml every_n_turns 优先于 env"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "100")
    _write_plugin_yaml(fake_home, "summarize:\n  every_n_turns: 3\n")
    assert provider._get_n_turns_threshold() == 3


def test_yaml_overrides_env_min_interval(fake_home, monkeypatch, provider):
    """yaml min_interval_seconds 优先于 env"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "9999")
    _write_plugin_yaml(fake_home, "summarize:\n  min_interval_seconds: 600\n")
    assert provider._get_min_interval_seconds() == 600


def test_yaml_overrides_env_model(fake_home, monkeypatch, provider):
    """yaml summarize.model 优先于 env"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", "env-model")
    _write_plugin_yaml(fake_home, "summarize:\n  model: yaml-model\n")
    assert provider._get_summarize_model() == "yaml-model"


def test_yaml_overrides_env_enabled(fake_home, monkeypatch, provider):
    """yaml enabled: false 关掉, 即使 env=1"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "1")
    _write_plugin_yaml(fake_home, "enabled: false\n")
    assert provider._is_summarize_enabled() is False


def test_yaml_enabled_true_overrides_env_zero(fake_home, monkeypatch, provider):
    """yaml enabled: true 启用, 即使 env=0"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "0")
    _write_plugin_yaml(fake_home, "enabled: true\n")
    assert provider._is_summarize_enabled() is True


def test_yaml_missing_falls_back_to_env(fake_home, monkeypatch, provider):
    """yaml 不存在 → 走 env"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "7")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", "env-fallback")
    # 不写 yaml file
    assert provider._get_n_turns_threshold() == 7
    assert provider._get_summarize_model() == "env-fallback"


def test_yaml_corrupt_falls_back_to_env(fake_home, monkeypatch, provider):
    """yaml 文件 corrupt → 走 env, 不挂"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "11")
    _write_plugin_yaml(fake_home, "this is not yaml: [invalid")
    assert provider._get_n_turns_threshold() == 11


def test_yaml_partial_config(fake_home, monkeypatch, provider):
    """yaml 只配 1 项 (model), 其他走 env / default"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "9")
    _write_plugin_yaml(fake_home, "summarize:\n  model: yaml-only-model\n")
    assert provider._get_summarize_model() == "yaml-only-model"
    assert provider._get_n_turns_threshold() == 9  # env 兜底
    assert provider._get_min_interval_seconds() == 1800  # default 兜底


def test_yaml_full_real_world_example(fake_home, monkeypatch, provider):
    """完整真实 yaml 配置例子"""
    monkeypatch.delenv("CATFISH_PLUGIN_SUMMARIZE", raising=False)
    monkeypatch.delenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", raising=False)
    monkeypatch.delenv("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", raising=False)
    monkeypatch.delenv("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", raising=False)

    _write_plugin_yaml(
        fake_home,
        """
enabled: true
summarize:
  model: catfish-private-vision
  every_n_turns: 8
  min_interval_seconds: 3600
""",
    )

    assert provider._is_summarize_enabled() is True
    assert provider._get_summarize_model() == "catfish-private-vision"
    assert provider._get_n_turns_threshold() == 8
    assert provider._get_min_interval_seconds() == 3600
