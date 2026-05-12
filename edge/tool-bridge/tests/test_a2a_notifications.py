"""BL-FED2.6 测试 — a2a 通知 jsonl 读 + catfish_list_a2a_help tool."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from catfish_tool_bridge import a2a_notifications


@pytest.fixture(autouse=True)
def isolated_catfish_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "fake_catfish"
    home.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(home))
    yield home


def _write_jsonl(home: Path, entries: list[dict]) -> None:
    p = home / "a2a_notifications.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_ago(h: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


# ── load_notifications ───────────────────────────────────


def test_load_empty_when_no_file(isolated_catfish_home: Path):
    assert a2a_notifications.load_notifications() == []


def test_load_skip_corrupt_line(isolated_catfish_home: Path):
    """坏 JSON 行跳过, 不抛异常."""
    p = isolated_catfish_home / "a2a_notifications.jsonl"
    p.write_text(
        '{"ts":"2026-05-12T10:00:00+00:00","from_sub":"alice@ffcs.cn"}\n'
        "this is not json\n"
        '{"ts":"2026-05-12T11:00:00+00:00","from_sub":"bob@ffcs.cn"}\n',
        encoding="utf-8",
    )
    items = a2a_notifications.load_notifications()
    assert len(items) == 2
    assert items[0]["from_sub"] == "alice@ffcs.cn"
    assert items[1]["from_sub"] == "bob@ffcs.cn"


# ── filter_notifications ─────────────────────────────────


def test_filter_hours_back():
    entries = [
        {"ts": _hours_ago(1), "from_sub": "a@x"},
        {"ts": _hours_ago(30), "from_sub": "b@x"},  # 超出 24h
    ]
    out = a2a_notifications.filter_notifications(entries, hours_back=24)
    assert len(out) == 1
    assert out[0]["from_sub"] == "a@x"


def test_filter_hours_back_none_returns_all():
    entries = [
        {"ts": _hours_ago(1), "from_sub": "a@x"},
        {"ts": _hours_ago(1000), "from_sub": "b@x"},
    ]
    out = a2a_notifications.filter_notifications(entries, hours_back=None)
    assert len(out) == 2


def test_filter_unseen_only():
    entries = [
        {"ts": _now(), "from_sub": "a@x", "seen": False},
        {"ts": _now(), "from_sub": "b@x", "seen": True},
        {"ts": _now(), "from_sub": "c@x"},  # 无 seen 字段 = 未读
    ]
    out = a2a_notifications.filter_notifications(entries, unseen_only=True)
    assert len(out) == 2
    subs = {x["from_sub"] for x in out}
    assert subs == {"a@x", "c@x"}


def test_filter_from_sub():
    entries = [
        {"ts": _now(), "from_sub": "alice@ffcs.cn"},
        {"ts": _now(), "from_sub": "bob@ffcs.cn"},
    ]
    out = a2a_notifications.filter_notifications(entries, from_sub="alice@ffcs.cn")
    assert len(out) == 1
    assert out[0]["from_sub"] == "alice@ffcs.cn"


def test_filter_tag_substr_case_insensitive():
    entries = [
        {"ts": _now(), "from_sub": "a@x", "purpose": "expert_consult:资质审核"},
        {"ts": _now(), "from_sub": "b@x", "purpose": "expert_consult:外勤报销"},
    ]
    out = a2a_notifications.filter_notifications(entries, tag_substr="资质")
    assert len(out) == 1
    assert out[0]["from_sub"] == "a@x"


def test_filter_skips_invalid_ts():
    """ts 解析失败的 entry 被跳过 (而不是认作'过去 0h')."""
    entries = [
        {"ts": "not-a-date", "from_sub": "bad@x"},
        {"ts": _now(), "from_sub": "good@x"},
    ]
    out = a2a_notifications.filter_notifications(entries, hours_back=1)
    assert len(out) == 1
    assert out[0]["from_sub"] == "good@x"


# ── tool_list_a2a_help E2E ───────────────────────────────


def test_tool_basic(isolated_catfish_home: Path):
    _write_jsonl(isolated_catfish_home, [
        {
            "ts": _hours_ago(2),
            "from_sub": "alice@ffcs.cn",
            "question": "资质审核怎么搞?",
            "purpose": "expert_consult:资质审核",
            "answer_preview": "走 OA",
            "chunks_count": 3,
            "duration_ms": 500,
        },
        {
            "ts": _hours_ago(5),
            "from_sub": "bob@ffcs.cn",
            "question": "报销标准多少?",
            "purpose": "expert_consult:外勤报销",
            "answer_preview": "出差城市定",
            "chunks_count": 2,
            "duration_ms": 300,
        },
    ])
    r = a2a_notifications.tool_list_a2a_help({})
    assert r["ok"] is True
    assert r["total"] == 2
    assert len(r["items"]) == 2
    # items 是倒序
    assert r["items"][0]["from_sub"] == "alice@ffcs.cn"  # 更近
    assert r["by_sub"] == {"alice@ffcs.cn": 1, "bob@ffcs.cn": 1}
    assert "alice@ffcs.cn(1)" in r["summary"]


def test_tool_hours_back_filter(isolated_catfish_home: Path):
    _write_jsonl(isolated_catfish_home, [
        {"ts": _hours_ago(2), "from_sub": "recent@x", "purpose": "p1"},
        {"ts": _hours_ago(50), "from_sub": "old@x", "purpose": "p2"},
    ])
    r = a2a_notifications.tool_list_a2a_help({"hours_back": 24})
    assert r["total"] == 1
    assert r["items"][0]["from_sub"] == "recent@x"


def test_tool_zero_hours_returns_all(isolated_catfish_home: Path):
    """hours_back=0 = 全部 (不限时)."""
    _write_jsonl(isolated_catfish_home, [
        {"ts": _hours_ago(2), "from_sub": "a@x", "purpose": "p"},
        {"ts": _hours_ago(1000), "from_sub": "b@x", "purpose": "p"},
    ])
    r = a2a_notifications.tool_list_a2a_help({"hours_back": 0})
    assert r["total"] == 2


def test_tool_max_items_clamped(isolated_catfish_home: Path):
    _write_jsonl(isolated_catfish_home, [
        {"ts": _hours_ago(1), "from_sub": f"u{i}@x", "purpose": "p"} for i in range(60)
    ])
    r = a2a_notifications.tool_list_a2a_help({"max_items": 10})
    assert len(r["items"]) == 10
    assert r["total"] == 60  # total 不被 max_items 影响


def test_tool_max_items_upper_bound(isolated_catfish_home: Path):
    """max_items 上限 200, 防爆 prompt."""
    _write_jsonl(isolated_catfish_home, [
        {"ts": _hours_ago(1), "from_sub": f"u{i}@x", "purpose": "p"} for i in range(300)
    ])
    r = a2a_notifications.tool_list_a2a_help({"max_items": 9999})
    assert len(r["items"]) == 200


def test_tool_by_sub_and_purpose_breakdown(isolated_catfish_home: Path):
    _write_jsonl(isolated_catfish_home, [
        {"ts": _hours_ago(1), "from_sub": "alice@x", "purpose": "expert_consult:资质审核"},
        {"ts": _hours_ago(2), "from_sub": "alice@x", "purpose": "expert_consult:资质审核"},
        {"ts": _hours_ago(3), "from_sub": "bob@x", "purpose": "expert_consult:外勤报销"},
    ])
    r = a2a_notifications.tool_list_a2a_help({})
    assert r["by_sub"] == {"alice@x": 2, "bob@x": 1}
    assert r["by_purpose"]["expert_consult:资质审核"] == 2
    assert r["by_purpose"]["expert_consult:外勤报销"] == 1


def test_tool_no_file_returns_zero(isolated_catfish_home: Path):
    r = a2a_notifications.tool_list_a2a_help({})
    assert r["ok"] is True
    assert r["total"] == 0
    assert r["items"] == []


def test_tool_tag_filter(isolated_catfish_home: Path):
    _write_jsonl(isolated_catfish_home, [
        {"ts": _hours_ago(1), "from_sub": "a@x", "purpose": "expert_consult:资质审核"},
        {"ts": _hours_ago(1), "from_sub": "b@x", "purpose": "expert_consult:外勤报销"},
    ])
    r = a2a_notifications.tool_list_a2a_help({"tag_substr": "资质"})
    assert r["total"] == 1
    assert r["items"][0]["from_sub"] == "a@x"


def test_tool_path_in_response(isolated_catfish_home: Path):
    r = a2a_notifications.tool_list_a2a_help({})
    assert r["path"].endswith("a2a_notifications.jsonl")
    # 路径走 CATFISH_HOME 不是 ~/.catfish (隐私边界, 跟 employee_journal 一致)
    assert str(isolated_catfish_home) in r["path"]
