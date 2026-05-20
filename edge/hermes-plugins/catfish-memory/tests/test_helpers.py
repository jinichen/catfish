"""catfish-memory plugin module-level helper 函数单测 (5/20 split from test_on_session_end.py).

测的纯函数:
  - _extract_message_pairs
  - _format_journal_entry
  - _append_journal
  - _read_full_journal
  - _should_run_distill / _mark_distill_run
  - _write_distilled
  - _call_summarize_llm (async, mock httpx)

跟 sync_turn behavior 解耦 — 这些 helpers 不直接绑 sync_turn / on_session_end,
也不会因为 hook trigger 机制变化而失效 (BL-MEMORY-SYNC-TURN-REFACTOR 5/20 拆出).

跑法:
    python -m pytest tests/test_helpers.py -v
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import catfish_memory  # noqa: E402
from catfish_memory import (  # noqa: E402
    _append_journal,
    _extract_message_pairs,
    _format_journal_entry,
    _mark_distill_run,
    _read_full_journal,
    _should_run_distill,
    _write_distilled,
)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / ".catfish"
    d.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(d))
    return d


# ── _extract_message_pairs ────────────────────────────


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
    assert pairs[-1] == ("user", "msg99")


# ── _format_journal_entry ─────────────────────────────


def test_format_journal_entry_includes_date_and_short_sid():
    entry = _format_journal_entry("session-abcdef123456", "### 主题\n\n正文")
    assert entry.startswith("## ")
    assert "session `…123456`" in entry
    assert "### 主题" in entry
    assert "正文" in entry


def test_format_journal_entry_short_sid():
    """session_id 短的 (< 6 字符) 也不挂"""
    entry = _format_journal_entry("ab", "summary")
    assert "session `…ab`" in entry


# ── _append_journal ─────────────────────────────


def test_append_journal_creates_file_and_parent(fake_home: Path):
    target = fake_home / "employee_journal.md"
    assert not target.exists()
    _append_journal(fake_home, "## 2026-05-19 21:00\n\n第一段")
    assert target.exists()
    assert "第一段" in target.read_text(encoding="utf-8")


def test_append_journal_is_truly_append(fake_home: Path):
    _append_journal(fake_home, "## 段 1\n\n第一")
    _append_journal(fake_home, "## 段 2\n\n第二")
    content = (fake_home / "employee_journal.md").read_text(encoding="utf-8")
    assert "第一" in content and "第二" in content
    assert content.index("第一") < content.index("第二")


# ── _read_full_journal ─────────────────────────────


def test_read_full_journal_returns_empty_when_missing(fake_home: Path):
    assert _read_full_journal(fake_home) == ""


def test_read_full_journal_reads_full_text(fake_home: Path):
    (fake_home / "employee_journal.md").write_text("## 测试\n\n内容", encoding="utf-8")
    text = _read_full_journal(fake_home)
    assert "测试" in text and "内容" in text


# ── _should_run_distill ─────────────────────────────


def test_should_run_distill_true_when_no_state(fake_home: Path):
    assert _should_run_distill(fake_home) is True


def test_should_run_distill_false_within_24h(fake_home: Path):
    _mark_distill_run(fake_home)
    assert _should_run_distill(fake_home) is False


def test_should_run_distill_true_after_24h(fake_home: Path):
    state_path = fake_home / "memory_distill_state.json"
    state_path.write_text(
        json.dumps({"last_run_ts": time.time() - 25 * 3600}),
        encoding="utf-8",
    )
    assert _should_run_distill(fake_home) is True


def test_should_run_distill_true_when_state_corrupt(fake_home: Path):
    (fake_home / "memory_distill_state.json").write_text(
        "this is not json", encoding="utf-8"
    )
    assert _should_run_distill(fake_home) is True


# ── _write_distilled ─────────────────────────────


def test_write_distilled_overwrites(fake_home: Path):
    _write_distilled(fake_home, "first batch")
    _write_distilled(fake_home, "second batch")
    content = (fake_home / "distilled_facts.md").read_text(encoding="utf-8")
    assert "second batch" in content
    assert "first batch" not in content


def test_write_distilled_includes_header(fake_home: Path):
    _write_distilled(fake_home, "事实段")
    content = (fake_home / "distilled_facts.md").read_text(encoding="utf-8")
    assert "Generated by catfish-memory plugin" in content
    assert "事实段" in content


# ── _call_summarize_llm (mock httpx) ─────────────────────


@pytest.mark.asyncio
async def test_call_summarize_llm_no_token_returns_none(monkeypatch):
    monkeypatch.delenv("CATFISH_INTERNAL_DEV_TOKEN", raising=False)
    result = await catfish_memory._call_summarize_llm(
        [("user", "hi"), ("assistant", "hello")], "some-model",
    )
    assert result is None


@pytest.mark.asyncio
async def test_call_summarize_llm_no_pairs_returns_none(monkeypatch):
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "x")
    result = await catfish_memory._call_summarize_llm([], "model")
    assert result is None


@pytest.mark.asyncio
async def test_call_summarize_llm_success(monkeypatch):
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
    assert captured["headers"]["X-Catfish-Skip-Identity"] == "true"
    assert captured["headers"]["X-Catfish-Internal"] == "true"
    assert captured["headers"]["Authorization"] == "Bearer x"
    assert captured["body"]["model"] == "qwen-flash"


@pytest.mark.asyncio
async def test_call_summarize_llm_non_200_returns_none(monkeypatch):
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

    result = await catfish_memory._call_summarize_llm([("user", "hi")], "m")
    assert result is None
