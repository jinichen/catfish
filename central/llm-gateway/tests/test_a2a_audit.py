"""测试 Plan D · a2a_audit (五一 sprint Day 5).

覆盖:
- write_audit append-only
- ts 自动填
- env override 路径
- 失败静默不抛
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_gateway import a2a_audit


def test_write_audit_creates_file(tmp_path: Path, monkeypatch) -> None:
    audit_path = tmp_path / "a2a_audit.jsonl"
    monkeypatch.setenv("CATFISH_A2A_AUDIT_PATH", str(audit_path))

    a2a_audit.write_audit({
        "direction": "outbound",
        "to_sub": "bob@ffcs.cn",
        "status": "ok",
    })

    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["direction"] == "outbound"
    assert event["to_sub"] == "bob@ffcs.cn"
    assert event["status"] == "ok"
    assert "ts" in event  # 自动加


def test_write_audit_appends(tmp_path: Path, monkeypatch) -> None:
    audit_path = tmp_path / "a2a_audit.jsonl"
    monkeypatch.setenv("CATFISH_A2A_AUDIT_PATH", str(audit_path))

    a2a_audit.write_audit({"event": 1})
    a2a_audit.write_audit({"event": 2})
    a2a_audit.write_audit({"event": 3})

    lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    assert json.loads(lines[0])["event"] == 1
    assert json.loads(lines[2])["event"] == 3


def test_write_audit_preserves_explicit_ts(tmp_path: Path, monkeypatch) -> None:
    audit_path = tmp_path / "a2a_audit.jsonl"
    monkeypatch.setenv("CATFISH_A2A_AUDIT_PATH", str(audit_path))

    custom_ts = "2026-05-05T10:30:00+00:00"
    a2a_audit.write_audit({"ts": custom_ts, "event": "x"})

    line = audit_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["ts"] == custom_ts


def test_write_audit_silently_handles_unwritable(tmp_path: Path, monkeypatch) -> None:
    """写入失败 (例如目录不可写) 不抛异常, 静默 log."""
    # 设一个不可写路径
    monkeypatch.setenv(
        "CATFISH_A2A_AUDIT_PATH",
        "/proc/不可写/audit.jsonl",  # 真不可写, write 会抛但函数捕获
    )
    # 不应抛
    a2a_audit.write_audit({"event": "x"})


def test_write_audit_unicode(tmp_path: Path, monkeypatch) -> None:
    """中文 audit content 不应被 \\u escape."""
    audit_path = tmp_path / "a2a_audit.jsonl"
    monkeypatch.setenv("CATFISH_A2A_AUDIT_PATH", str(audit_path))

    a2a_audit.write_audit({"question": "项目 X 上周进展"})
    text = audit_path.read_text(encoding="utf-8")
    assert "项目 X 上周进展" in text  # 不是 项...
