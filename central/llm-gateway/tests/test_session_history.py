"""inject_session_history (档 1) + employee_journal (档 2) 测试."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_gateway.employee_journal import (  # noqa: E402
    append_to_journal,
    inject_employee_journal,
    journal_path,
    read_journal,
)
from catfish_gateway.inject_session_history import (  # noqa: E402
    format_block,
    get_recent_sessions,
    inject_session_history,
)


# ── 档 1: session_history ──────────────────────────────────────


def _setup_test_state_db(home_dir: Path) -> Path:
    """建一个 hermes state.db schema, 塞几条 session + message."""
    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True, exist_ok=True)
    db_path = hermes_dir / "state.db"

    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            model TEXT,
            model_config TEXT,
            system_prompt TEXT,
            started_at REAL,
            ended_at REAL,
            end_reason TEXT,
            message_count INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            reasoning_tokens INTEGER DEFAULT 0,
            title TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL,
            token_count INTEGER,
            finish_reason FloatTEXT
        );
    """)
    # 加 2 个 session
    import time
    now = time.time()
    conn.execute(
        "INSERT INTO sessions(id, started_at, message_count, title) VALUES (?, ?, ?, ?)",
        ("20260430_134512_abc123", now - 3600, 195, "汇报材料协商"),
    )
    conn.execute(
        "INSERT INTO sessions(id, started_at, message_count, title) VALUES (?, ?, ?, ?)",
        ("20260429_200705_xyz", now - 86400, 82, None),
    )
    # 每个 session 的首条 user message
    conn.execute(
        "INSERT INTO messages(session_id, role, content) VALUES (?, ?, ?)",
        ("20260430_134512_abc123", "user", "基于以下资质情况, 写一份给公司领导的汇报材料"),
    )
    conn.execute(
        "INSERT INTO messages(session_id, role, content) VALUES (?, ?, ?)",
        ("20260429_200705_xyz", "user", "写一份本周周报"),
    )
    conn.commit()
    conn.close()
    return db_path


def test_get_recent_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _setup_test_state_db(tmp_path)
    sessions = get_recent_sessions()
    assert len(sessions) == 2
    # 倒序: 最新在前
    assert sessions[0][0] == "20260430_134512_abc123"
    assert sessions[1][0] == "20260429_200705_xyz"


def test_get_recent_sessions_no_db(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # ~/.hermes/state.db 不存在
    assert get_recent_sessions() == []


def test_format_block_includes_session_info(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _setup_test_state_db(tmp_path)
    sessions = get_recent_sessions()
    block = format_block(sessions)
    assert "员工最近 7 天 session 历史" in block
    # session id 显示前 21 字符 (跟 hermes UI 一致)
    assert "20260430_134512_abc12" in block
    assert "汇报材料协商" in block
    assert "基于以下资质情况" in block
    assert "写一份本周周报" in block


def test_format_block_empty():
    assert format_block([]) == ""


def test_inject_session_history(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _setup_test_state_db(tmp_path)
    msgs = [
        {"role": "system", "content": "原始 system"},
        {"role": "user", "content": "你好"},
    ]
    out = inject_session_history(msgs)
    assert "员工最近 7 天 session 历史" in out[0]["content"]
    assert "原始 system" in out[0]["content"]


def test_inject_session_history_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _setup_test_state_db(tmp_path)
    msgs = [{"role": "system", "content": "sys"}]
    once = inject_session_history(msgs)
    twice = inject_session_history(once)
    assert once[0]["content"] == twice[0]["content"]


def test_inject_no_system_message_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _setup_test_state_db(tmp_path)
    msgs = [{"role": "user", "content": "hi"}]
    out = inject_session_history(msgs)
    assert out == msgs


def test_inject_does_not_mutate_input(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _setup_test_state_db(tmp_path)
    msgs = [{"role": "system", "content": "sys"}]
    original = msgs[0]["content"]
    inject_session_history(msgs)
    assert msgs[0]["content"] == original


# ── 档 2: employee_journal ─────────────────────────────────────


def _setup_test_journal(home_dir: Path, content: str) -> Path:
    catfish_dir = home_dir / ".catfish"
    catfish_dir.mkdir(parents=True, exist_ok=True)
    journal = catfish_dir / "employee_journal.md"
    journal.write_text(content, encoding="utf-8")
    return journal


def test_read_journal_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.JOURNAL_PATH",
        tmp_path / ".catfish" / "employee_journal.md",
    )
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.journal_path",
        lambda: tmp_path / ".catfish" / "employee_journal.md",
    )
    assert read_journal() == ""


def test_read_journal_returns_content(tmp_path, monkeypatch):
    fake_journal = tmp_path / ".catfish" / "employee_journal.md"
    fake_journal.parent.mkdir(parents=True)
    fake_journal.write_text("## 2026-04-30 - 测试日记\n内容...\n", encoding="utf-8")
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.journal_path", lambda: fake_journal
    )
    assert "测试日记" in read_journal()


def test_read_journal_truncates_to_max_bytes(tmp_path, monkeypatch):
    """超 50KB 自动截断, 保留尾部最新."""
    fake_journal = tmp_path / ".catfish" / "employee_journal.md"
    fake_journal.parent.mkdir(parents=True)
    # 写 100KB
    big = "## old entry\n" + "x" * 60_000 + "\n## new entry\n最新\n"
    fake_journal.write_text(big, encoding="utf-8")
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.journal_path", lambda: fake_journal
    )
    text = read_journal()
    assert len(text.encode("utf-8")) <= 60_000
    # 截断后保留最新
    assert "最新" in text


def test_append_to_journal_creates_file(tmp_path, monkeypatch):
    fake_journal = tmp_path / ".catfish" / "employee_journal.md"
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.journal_path", lambda: fake_journal
    )
    append_to_journal("## 2026-04-30 - 第一条\n内容...\n")
    assert fake_journal.exists()
    text = fake_journal.read_text(encoding="utf-8")
    assert "第一条" in text
    # 再 append 一条
    append_to_journal("## 2026-04-30 - 第二条\n内容...\n")
    text = fake_journal.read_text(encoding="utf-8")
    assert "第一条" in text and "第二条" in text


def test_inject_employee_journal(tmp_path, monkeypatch):
    fake_journal = tmp_path / ".catfish" / "employee_journal.md"
    fake_journal.parent.mkdir(parents=True)
    fake_journal.write_text(
        "## 2026-04-29 - 资质汇报\n鸿波偏好 4 段格式.\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.journal_path", lambda: fake_journal
    )
    msgs = [{"role": "system", "content": "sys"}]
    out = inject_employee_journal(msgs)
    assert "员工长期日记" in out[0]["content"]
    assert "鸿波偏好 4 段格式" in out[0]["content"]


def test_inject_employee_journal_empty_noop(tmp_path, monkeypatch):
    """journal **+ distilled** 都不存在 → 不动 messages.

    BL-EMPLOYEE-JOURNAL-EMPTY-NOOP fix (5/20): inject_employee_journal 的 noop
    条件是 journal 跟 distilled 都空 (employee_journal.py:177). 老测试只 monkeypatch
    了 journal_path, 没 monkeypatch distilled_facts.md 路径 (它走 hardcoded
    Path.home() / ".catfish" / "distilled_facts.md"). 真 mac 上 distilled 文件
    有 2KB 内容, 导致测试在生产 mac 跑会 fail (inject 真触发).

    修法: 同时 monkeypatch distilled_facts.md 路径指向 tmp_path 的 missing 文件,
    保证 distilled 也读不到, 触发真 noop 路径.
    """
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.journal_path",
        lambda: tmp_path / ".catfish" / "missing.md",
    )
    # 同步 monkeypatch distilled_facts 路径, 防真 mac ~/.catfish/distilled_facts.md
    # 内容污染测试. 5/20 修复.
    monkeypatch.setattr(
        "catfish_gateway.memory_distill.DISTILLED_FACTS_PATH",
        tmp_path / ".catfish" / "distilled_missing.md",
    )
    msgs = [{"role": "system", "content": "sys"}]
    out = inject_employee_journal(msgs)
    assert out == msgs


def test_inject_employee_journal_idempotent(tmp_path, monkeypatch):
    fake_journal = tmp_path / ".catfish" / "employee_journal.md"
    fake_journal.parent.mkdir(parents=True)
    fake_journal.write_text("## entry\n内容\n", encoding="utf-8")
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.journal_path", lambda: fake_journal
    )
    msgs = [{"role": "system", "content": "sys"}]
    once = inject_employee_journal(msgs)
    twice = inject_employee_journal(once)
    assert once[0]["content"] == twice[0]["content"]
