"""catfish-memory plugin on_session_end 写路径单测.

BL-GATEWAY-CLEANUP-POST-HERMES Week 2 Step B (5/19 晚):
plugin 接 hermes on_session_end hook, 替代 gateway 旧 session_summarizer +
memory_distill module. 这套测试**只测 plugin 行为**, gateway 那边的 caller
保留, 由 Week 1 已有的 test_gateway_chat_integration.py + Week 2 baseline
test_memory_features_baseline.py 覆盖.

跑法 (从 plugin 目录):
    python -m pytest tests/test_on_session_end.py -v

设计:
  - 不调真 LLM — mock _call_summarize_llm + _call_distill_llm (它们是 module-level
    pure async functions, monkeypatch 简单)
  - 不开 thread — 用 monkeypatch 把 on_session_end 里的 threading.Thread.start
    替换成直接 asyncio.run (同步跑完, 测试 deterministic)
  - 文件落到 tmp_path, 不污染 ~/.catfish/
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

# 加 plugin 自己到 sys.path (跟 test_catfish_memory.py 同套路)
sys.path.insert(0, str(Path(__file__).parent.parent))

import catfish_memory  # noqa: E402
# 8/15 拆分: _summarize_and_distill_async 及其一众 helper 搬到
# catfish_memory_distill.py, 于是**它调的那几个 LLM 函数的绑定也跟着走了**。
# 下面的 monkeypatch 必须打在 catfish_memory_distill 上 —— 打在 catfish_memory
# 上只会改到 re-export 出来的那个名字, 真正被调用的是 distill 模块自己的 globals
# (`from X import name` 建的是新绑定, 不是别名)。
# 拆分当天这三个文件一共 19 条红, 全是这个原因。
import catfish_memory_distill  # noqa: E402

from catfish_memory import (  # noqa: E402
    CatfishMemoryProvider,
    _append_journal,
    _append_to_buffer,
    _extract_message_pairs,
    _format_journal_entry,
    _mark_distill_run,
    _read_full_journal,
    _should_run_distill,
    _write_distilled,
)


def _populate_buffer_from_messages(home: Path, messages: list, session_id: str) -> None:
    """BL-MEMORY-SYNC-TURN-REFACTOR (5/20) 后, on_session_end 读 buffer 不读 messages 参数.
    测试要先把 messages 当 sync_turn 同款推进 buffer, 再调 on_session_end 才走 LLM 路径."""
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            _append_to_buffer(home, role, content, session_id)


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
    p.initialize(session_id="test-session-abc123")
    return p


@pytest.fixture
def sync_thread(monkeypatch: pytest.MonkeyPatch):
    """让 on_session_end 里 spawn 的 thread **同步** 跑 — deterministic 测试.

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
def env_enable_summarize(monkeypatch: pytest.MonkeyPatch):
    """env 配置: 启用 summarize + 设 model + 设 dev token (3 项缺一个就 skip)"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "1")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", "catfish-public-qwen-flash")
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "test-dev-token-xxx")


# ── 纯 helper 单测 ──────────────────────────────────────


def test_extract_message_pairs_filters_system_and_tool():
    """system/tool/空 content 都过滤, 只留 user/assistant"""
    msgs = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "tool", "content": "tool result"},
        {"role": "user", "content": ""},  # 空过滤
        {"role": "assistant", "content": None},  # None 过滤
        {"role": "user", "content": "second"},
    ]
    pairs = _extract_message_pairs(msgs)
    assert pairs == [
        ("user", "hi"),
        ("assistant", "hello"),
        ("user", "second"),
    ]


def test_extract_message_pairs_caps_at_max():
    """超 _MAX_MESSAGES_PER_SUMMARY 取尾部"""
    msgs = [{"role": "user", "content": f"msg{i}"} for i in range(100)]
    pairs = _extract_message_pairs(msgs)
    assert len(pairs) == catfish_memory._MAX_MESSAGES_PER_SUMMARY
    # 最后一条是 msg99
    assert pairs[-1] == ("user", "msg99")


def test_format_journal_entry_includes_date_and_short_sid():
    # C3 (6/6): product code 改 backtick → pipe, test 同步
    entry = _format_journal_entry("session-abcdef123456", "### 主题\n\n正文")
    assert entry.startswith("## ")  # 日期开头
    # 8/4: 改断言 —— journal 现在写**完整** session_id。
    # 老行为截成后 6 位, 把溯源链掐断了 (wiki sources 只到日期 + journal 只有
    # 6 位后缀 → 回溯不到原始对话)。原文一直完整躺在 ~/.hermes/state.db,
    # 只是指针被截断。见 catfish_memory_helpers._format_journal_entry 的注释。
    assert "session-abcdef123456" in entry  # 完整 sid, 不再截断
    assert "### 主题" in entry
    assert "正文" in entry


def test_format_journal_entry_short_sid():
    """session_id 短的 (< 6 字符) 也不挂"""
    entry = _format_journal_entry("ab", "summary")
    # 8/4: 改断言 —— journal 现在写**完整** session_id。
    # 老行为截成后 6 位, 把溯源链掐断了 (wiki sources 只到日期 + journal 只有
    # 6 位后缀 → 回溯不到原始对话)。原文一直完整躺在 ~/.hermes/state.db,
    # 只是指针被截断。见 catfish_memory_helpers._format_journal_entry 的注释。
    assert "| ab" in entry  # 短 id 原样写入


def test_append_journal_creates_file_and_parent(fake_home: Path):
    target = fake_home / "employee_journal.md"
    assert not target.exists()
    _append_journal(fake_home, "## 2026-05-19 21:00\n\n第一段")
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "第一段" in content


def test_append_journal_is_truly_append(fake_home: Path):
    _append_journal(fake_home, "## 段 1\n\n第一")
    _append_journal(fake_home, "## 段 2\n\n第二")
    content = (fake_home / "employee_journal.md").read_text(encoding="utf-8")
    assert "第一" in content and "第二" in content
    # 段 2 在段 1 后面
    assert content.index("第一") < content.index("第二")


def test_read_full_journal_returns_empty_when_missing(fake_home: Path):
    assert _read_full_journal(fake_home) == ""


def test_read_full_journal_reads_full_text(fake_home: Path):
    (fake_home / "employee_journal.md").write_text("## 测试\n\n内容", encoding="utf-8")
    text = _read_full_journal(fake_home)
    assert "测试" in text and "内容" in text


def test_should_run_distill_true_when_no_state(fake_home: Path):
    """从没跑过 → 跑"""
    assert _should_run_distill(fake_home) is True


def test_should_run_distill_false_within_24h(fake_home: Path):
    """24h 内跑过 → 不再跑"""
    _mark_distill_run(fake_home)
    assert _should_run_distill(fake_home) is False


def test_should_run_distill_true_after_24h(fake_home: Path):
    """state 文件存在但 last_run_ts 超 24h → 跑"""
    state_path = fake_home / "memory_distill_state.json"
    state_path.write_text(
        json.dumps({"last_run_ts": time.time() - 25 * 3600}),
        encoding="utf-8",
    )
    assert _should_run_distill(fake_home) is True


def test_should_run_distill_true_when_state_corrupt(fake_home: Path):
    """state 文件 corrupt → 当作没跑过 (容错)"""
    (fake_home / "memory_distill_state.json").write_text(
        "this is not json", encoding="utf-8"
    )
    assert _should_run_distill(fake_home) is True


def test_write_distilled_overwrites(fake_home: Path):
    """蒸馏覆盖式写, 不追加"""
    _write_distilled(fake_home, "first batch")
    _write_distilled(fake_home, "second batch")
    content = (fake_home / "distilled_facts.md").read_text(encoding="utf-8")
    assert "second batch" in content
    assert "first batch" not in content


def test_write_distilled_includes_header(fake_home: Path):
    """生成时间戳 header 应该有 (跟老 gateway 输出对齐)"""
    _write_distilled(fake_home, "事实段")
    content = (fake_home / "distilled_facts.md").read_text(encoding="utf-8")
    assert "Generated by catfish-memory plugin" in content
    assert "事实段" in content


# ── on_session_end 行为单测 ─────────────────────────────


def test_on_session_end_noop_when_disabled_by_env(
    provider, monkeypatch, sync_thread, fake_home,
):
    """CATFISH_PLUGIN_SUMMARIZE=0 → 完全 skip, 不开 thread, 不写 journal"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "0")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", "x")
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "y")
    provider.on_session_end([
        {"role": "user", "content": "test"},
        {"role": "assistant", "content": "test back"},
    ])
    assert not (fake_home / "employee_journal.md").exists()


def test_on_session_end_noop_when_no_model_env(
    provider, monkeypatch, sync_thread, fake_home,
):
    """没 CATFISH_PLUGIN_SUMMARIZE_MODEL → skip (Step B 阶段双写期正常行为)"""
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "1")
    monkeypatch.delenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", raising=False)
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "y")
    provider.on_session_end([
        {"role": "user", "content": "test"},
        {"role": "assistant", "content": "back"},
    ])
    assert not (fake_home / "employee_journal.md").exists()


def test_on_session_end_noop_when_no_messages(
    provider, env_enable_summarize, sync_thread, fake_home,
):
    """空 messages → skip"""
    provider.on_session_end([])
    assert not (fake_home / "employee_journal.md").exists()


def test_on_session_end_skip_when_journal_recently_written(
    provider, env_enable_summarize, sync_thread, fake_home, monkeypatch,
):
    """journal mtime < 5min → 假设 gateway 刚写, skip (双写期幂等)"""
    # 预先建 journal 文件, mtime 是现在
    (fake_home / "employee_journal.md").write_text("已有内容\n", encoding="utf-8")

    # mock LLM call shouldn't even be invoked
    called = {"n": 0}
    async def fake_llm(*args, **kwargs):
        called["n"] += 1
        return "should not be called"
    monkeypatch.setattr(catfish_memory_distill, "_call_summarize_llm", fake_llm)

    provider.on_session_end([
        {"role": "user", "content": "test"},
        {"role": "assistant", "content": "back"},
    ])
    assert called["n"] == 0  # LLM 没被调


def test_on_session_end_proceeds_when_journal_older_than_dedup(
    provider, env_enable_summarize, sync_thread, fake_home, monkeypatch,
):
    """journal mtime 超 5min → 不 skip, 走总结流程"""
    journal = fake_home / "employee_journal.md"
    journal.write_text("旧内容\n", encoding="utf-8")
    # 改 mtime 到 10 分钟前
    old_ts = time.time() - 600
    import os
    os.utime(journal, (old_ts, old_ts))

    # mock 总结返一段内容
    captured = {"pairs": None, "model": None}
    async def fake_summarize(pairs, model):
        captured["pairs"] = pairs
        captured["model"] = model
        return "### 测试主题\n\n新一段总结"

    # mock 蒸馏 — 这次不该跑 (没满足 24h, journal 短)
    async def fake_distill(text, model):
        return None

    monkeypatch.setattr(catfish_memory_distill, "_call_summarize_llm", fake_summarize)
    monkeypatch.setattr(catfish_memory_distill, "_call_distill_llm", fake_distill)

    # BL-MEMORY-SYNC-TURN-REFACTOR (5/20): on_session_end 读 buffer 不读 messages 参数
    msgs = [
        {"role": "user", "content": "用户说啥"},
        {"role": "assistant", "content": "助手回啥"},
    ]
    _populate_buffer_from_messages(fake_home, msgs, "test-session-abc123")
    provider.on_session_end(msgs)

    # journal 应该被追加 (旧内容 + 新一段)
    content = journal.read_text(encoding="utf-8")
    assert "旧内容" in content
    assert "新一段总结" in content
    assert "测试主题" in content
    assert captured["model"] == "catfish-public-qwen-flash"


def test_on_session_end_skips_when_llm_returns_empty(
    provider, env_enable_summarize, sync_thread, fake_home, monkeypatch,
):
    """LLM 返空 → 不写 journal (跟老 gateway 行为一致)"""
    async def fake_summarize(pairs, model):
        return None  # LLM 总结失败
    monkeypatch.setattr(catfish_memory_distill, "_call_summarize_llm", fake_summarize)

    provider.on_session_end([
        {"role": "user", "content": "msg"},
        {"role": "assistant", "content": "back"},
    ])
    assert not (fake_home / "employee_journal.md").exists()


def test_on_session_end_distill_runs_when_24h_passed(
    provider, env_enable_summarize, sync_thread, fake_home, monkeypatch,
):
    """24h 内没跑过蒸馏 + summarize 成功 → 蒸馏跑, 写 distilled_facts.md"""
    async def fake_summarize(pairs, model):
        return "### 主题\n\n总结正文"
    distill_called = {"text": None, "model": None}
    async def fake_distill(text, model):
        distill_called["text"] = text
        distill_called["model"] = model
        return "## 长期事实\n\n- 鸿波偏好简短\n- 资质项目"

    monkeypatch.setattr(catfish_memory_distill, "_call_summarize_llm", fake_summarize)
    monkeypatch.setattr(catfish_memory_distill, "_call_distill_llm", fake_distill)

    # BL-MEMORY-SYNC-TURN-REFACTOR (5/20): on_session_end 读 buffer 不读 messages 参数
    msgs = [
        {"role": "user", "content": "msg"},
        {"role": "assistant", "content": "back"},
    ]
    _populate_buffer_from_messages(fake_home, msgs, "test-session-abc123")
    provider.on_session_end(msgs)

    # journal + distilled 都应该写
    assert (fake_home / "employee_journal.md").exists()
    assert (fake_home / "distilled_facts.md").exists()
    assert "长期事实" in (fake_home / "distilled_facts.md").read_text(encoding="utf-8")
    # distill 应该收到 journal 文本
    assert distill_called["text"] and "总结正文" in distill_called["text"]
    # state file 应该被打标
    assert (fake_home / "memory_distill_state.json").exists()


def test_on_session_end_distill_skipped_when_within_24h(
    provider, env_enable_summarize, sync_thread, fake_home, monkeypatch,
):
    """24h 内跑过蒸馏 → summarize 仍跑, 但 distill 跳"""
    _mark_distill_run(fake_home)  # 标已跑过 (现在)
    # 但是 _mark_distill_run 把 journal mtime 没动 — 我们要确保 journal 不存在
    # 才能让 summarize 走到 (避免 mtime dedup skip)
    async def fake_summarize(pairs, model):
        return "新总结"
    distill_called = {"n": 0}
    async def fake_distill(text, model):
        distill_called["n"] += 1
        return "should not be called"

    monkeypatch.setattr(catfish_memory_distill, "_call_summarize_llm", fake_summarize)
    monkeypatch.setattr(catfish_memory_distill, "_call_distill_llm", fake_distill)

    # BL-MEMORY-SYNC-TURN-REFACTOR (5/20): on_session_end 读 buffer 不读 messages 参数
    msgs = [
        {"role": "user", "content": "msg"},
        {"role": "assistant", "content": "back"},
    ]
    _populate_buffer_from_messages(fake_home, msgs, "test-session-abc123")
    provider.on_session_end(msgs)

    assert (fake_home / "employee_journal.md").exists()
    assert distill_called["n"] == 0  # 蒸馏没跑


def test_on_session_end_does_not_raise_on_exception(
    provider, env_enable_summarize, sync_thread, fake_home, monkeypatch,
):
    """LLM call 抛异常 → on_session_end 静默, 不挂"""
    async def fake_summarize(pairs, model):
        raise RuntimeError("LLM exploded")
    monkeypatch.setattr(catfish_memory_distill, "_call_summarize_llm", fake_summarize)

    # 不应该抛
    provider.on_session_end([
        {"role": "user", "content": "msg"},
        {"role": "assistant", "content": "back"},
    ])
    assert not (fake_home / "employee_journal.md").exists()


# ── LLM call helper 单测 (mock httpx) ───────────────────


@pytest.mark.asyncio
async def test_call_summarize_llm_no_token_returns_none(monkeypatch):
    """没 CATFISH_INTERNAL_DEV_TOKEN → 直接 None, 不发请求"""
    monkeypatch.delenv("CATFISH_INTERNAL_DEV_TOKEN", raising=False)
    result = await catfish_memory._call_summarize_llm(
        [("user", "hi"), ("assistant", "hello")], "some-model",
    )
    assert result is None


@pytest.mark.asyncio
async def test_call_summarize_llm_no_pairs_returns_none(monkeypatch):
    """空 pairs → 不发请求"""
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "x")
    result = await catfish_memory._call_summarize_llm([], "model")
    assert result is None


@pytest.mark.asyncio
async def test_call_summarize_llm_success(monkeypatch):
    """mock httpx 200 → 返 content"""
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "x")
    captured = {}

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "总结文本"}}]}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result = await catfish_memory._call_summarize_llm(
        [("user", "hi"), ("assistant", "hello")], "qwen-flash",
    )
    assert result == "总结文本"
    # 检查 headers 含 internal 标
    assert captured["headers"]["X-Catfish-Skip-Identity"] == "true"
    assert captured["headers"]["X-Catfish-Internal"] == "true"
    assert captured["headers"]["Authorization"] == "Bearer x"
    assert captured["body"]["model"] == "qwen-flash"


@pytest.mark.asyncio
async def test_call_summarize_llm_non_200_returns_none(monkeypatch):
    """HTTP 非 200 → None"""
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "x")

    class FakeResp:
        status_code = 500
        text = "internal error"

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result = await catfish_memory._call_summarize_llm(
        [("user", "hi")], "m",
    )
    assert result is None
