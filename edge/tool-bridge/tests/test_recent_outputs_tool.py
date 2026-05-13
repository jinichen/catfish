"""BL-FIX-TIMEOUT-OUTPUTS 测试 — catfish_list_my_outputs tool."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import recent_outputs


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "fake_home"
    (home / ".catfish" / "output").mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(home / ".catfish"))
    yield home / ".catfish" / "output"


def _touch(d: Path, name: str, content: str = "x", age_hours: float = 1.0):
    p = d / name
    p.write_text(content, encoding="utf-8")
    ts = time.time() - age_hours * 3600
    os.utime(p, (ts, ts))
    return p


def test_empty_dir_returns_empty(fake_home: Path):
    r = recent_outputs.tool_list_my_outputs({})
    assert r["ok"] is True
    assert r["count"] == 0
    assert "没找到" in r["summary"]


def test_lists_recent_files(fake_home: Path):
    _touch(fake_home, "a.xlsx", "AAA", age_hours=1)
    _touch(fake_home, "b.docx", "BBB", age_hours=2)
    _touch(fake_home, "c.md", "CCC", age_hours=3)
    r = recent_outputs.tool_list_my_outputs({"hours_back": 24})
    assert r["ok"] is True
    assert r["count"] == 3
    # 按时间倒序: a (1h) 先
    assert r["items"][0]["name"] == "a.xlsx"


def test_hours_back_filter(fake_home: Path):
    _touch(fake_home, "old.xlsx", age_hours=48)  # 2 天前
    _touch(fake_home, "fresh.xlsx", age_hours=1)
    r = recent_outputs.tool_list_my_outputs({"hours_back": 24})
    assert r["count"] == 1
    assert r["items"][0]["name"] == "fresh.xlsx"


def test_ext_filter(fake_home: Path):
    _touch(fake_home, "a.xlsx")
    _touch(fake_home, "b.docx")
    _touch(fake_home, "c.md")
    r = recent_outputs.tool_list_my_outputs({"ext_filter": ".xlsx"})
    assert r["count"] == 1
    assert r["items"][0]["name"] == "a.xlsx"


def test_ext_filter_without_dot(fake_home: Path):
    """ext_filter='xlsx' 也能识别 (不要求带 .)."""
    _touch(fake_home, "a.xlsx")
    _touch(fake_home, "b.docx")
    r = recent_outputs.tool_list_my_outputs({"ext_filter": "xlsx"})
    assert r["count"] == 1


def test_limit_clamp(fake_home: Path):
    for i in range(150):
        _touch(fake_home, f"f{i}.txt")
    r = recent_outputs.tool_list_my_outputs({"limit": 9999})
    assert r["count"] == 100  # 上限钳到 100


def test_hours_back_zero_means_all_time(fake_home: Path):
    """hours_back=0 当 1 年用 (覆盖 long-term archive 场景)."""
    _touch(fake_home, "old.xlsx", age_hours=24 * 30 * 6)  # 6 个月前
    r = recent_outputs.tool_list_my_outputs({"hours_back": 0})
    assert r["count"] == 1


def test_summary_includes_size_and_time(fake_home: Path):
    _touch(fake_home, "report.xlsx", "x" * 5000)  # ~5KB
    r = recent_outputs.tool_list_my_outputs({"hours_back": 24})
    assert "report.xlsx" in r["summary"]
    assert "KB" in r["summary"] or "B" in r["summary"]


def test_by_ext_breakdown(fake_home: Path):
    _touch(fake_home, "a.xlsx")
    _touch(fake_home, "b.xlsx")
    _touch(fake_home, "c.docx")
    r = recent_outputs.tool_list_my_outputs({})
    assert r["by_ext"][".xlsx"] == 2
    assert r["by_ext"][".docx"] == 1


def test_invalid_args_falls_back(fake_home: Path):
    """invalid hours_back / limit 退化, 不抛."""
    _touch(fake_home, "a.xlsx")
    r = recent_outputs.tool_list_my_outputs({"hours_back": "xyz", "limit": "abc"})
    assert r["ok"] is True


def test_dispatch_via_catfish_tools(fake_home: Path):
    from catfish_tool_bridge import catfish_tools
    _touch(fake_home, "test.xlsx")
    r = catfish_tools.dispatch_native("catfish_list_my_outputs", {})
    assert r["ok"] is True
    assert r["count"] == 1


def test_schema_in_native_tools_list():
    from catfish_tool_bridge import catfish_tools
    assert "catfish_list_my_outputs" in catfish_tools.NATIVE_TOOL_NAMES
    schema = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_list_my_outputs"
    )
    # description 必须告诉 LLM "上游卡时先调这个看有没已经写过"
    assert "execute_code" in schema["description"]


def test_human_size():
    assert recent_outputs._human_size(500) == "500 B"
    assert recent_outputs._human_size(1500) == "1.5 KB"
    assert recent_outputs._human_size(2_500_000) == "2.4 MB"
