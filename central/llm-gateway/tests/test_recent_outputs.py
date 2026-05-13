"""BL-FIX-TIMEOUT-OUTPUTS gateway recent_outputs 测试.

跟 tool-bridge recent_outputs 同源 (CATFISH_HOME/output/), 用于 timeout 友好
错误时列已写文件给员工.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from catfish_gateway import recent_outputs


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "fake_home"
    (home / ".catfish" / "output").mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(home / ".catfish"))
    yield home / ".catfish" / "output"


def _touch(d: Path, name: str, age_hours: float = 1.0):
    p = d / name
    p.write_text("x", encoding="utf-8")
    ts = time.time() - age_hours * 3600
    os.utime(p, (ts, ts))


def test_empty_returns_list(fake_home: Path):
    assert recent_outputs.list_recent() == []


def test_lists_recent(fake_home: Path):
    _touch(fake_home, "a.xlsx", 1)
    _touch(fake_home, "b.docx", 2)
    out = recent_outputs.list_recent(hours_back=24)
    assert len(out) == 2
    assert out[0]["name"] == "a.xlsx"  # 倒序最新先


def test_hours_back_filter(fake_home: Path):
    _touch(fake_home, "old.xlsx", 48)
    _touch(fake_home, "fresh.xlsx", 1)
    out = recent_outputs.list_recent(hours_back=24)
    assert len(out) == 1
    assert out[0]["name"] == "fresh.xlsx"


def test_limit(fake_home: Path):
    for i in range(20):
        _touch(fake_home, f"f{i}.txt")
    out = recent_outputs.list_recent(limit=5)
    assert len(out) == 5


def test_no_dir_returns_empty(monkeypatch, tmp_path: Path):
    """CATFISH_HOME/output/ 不存在 → 返 [] 不抛."""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "no_such"))
    assert recent_outputs.list_recent() == []


def test_returns_metadata_for_friendly_error(fake_home: Path):
    """timeout 友好错误用的字段都要有."""
    _touch(fake_home, "report.xlsx", 1)
    out = recent_outputs.list_recent()
    assert len(out) == 1
    assert "path" in out[0]
    assert "size_human" in out[0]
    assert "mtime_iso" in out[0]
