"""P3.5.78 expense kind (第 6 kind 记账) 单测.

测 3 层:
  1. helpers (纯函数): _expense_gen_id / _expense_parse_date_to_iso /
     _expense_append_record / _expense_read_all / _expense_summarize_window
  2. handler: handle_memory_tool(kind=expense) round-trip + validation
  3. schema: 6 kind enum + amount/direction 字段, _render_schema 决策树有第 4 条

跑法:
    cd edge/hermes-plugins/catfish-memory
    python -m pytest tests/test_expense.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import catfish_memory  # noqa: E402  (from conftest sys.path)
from catfish_memory import (  # noqa: E402
    _expense_append_record,
    _expense_gen_id,
    _expense_parse_date_to_iso,
    _expense_read_all,
    _expense_summarize_window,
)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """单测专属 ~/.catfish/, 不污染真用户数据."""
    home = tmp_path / ".catfish"
    home.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(home))
    return home


# ──────────────────────────────────────────────────────────────────────────────
# helpers (纯函数)
# ──────────────────────────────────────────────────────────────────────────────


def test_expense_gen_id_format():
    """id = bk_YYYYMMDD_HHMMSS_<4hex> — 跟 P3.5.75 兼容."""
    sid = _expense_gen_id()
    assert sid.startswith("bk_")
    parts = sid.split("_")
    assert len(parts) == 4
    assert parts[0] == "bk"
    assert len(parts[1]) == 8  # YYYYMMDD
    assert len(parts[2]) == 6  # HHMMSS
    assert len(parts[3]) == 4  # 4 hex


def test_expense_gen_id_unique():
    """100 次 id 几乎不重."""
    ids = {_expense_gen_id() for _ in range(100)}
    assert len(ids) >= 95


def test_parse_date_iso_full():
    s = _expense_parse_date_to_iso("2026-06-21T15:30:00")
    assert "2026-06-21" in s and "15:30:00" in s


def test_parse_date_iso_date_only():
    """仅日期补正午."""
    s = _expense_parse_date_to_iso("2026-06-21")
    assert s.startswith("2026-06-21T12:00:00")


def test_parse_date_iso_empty_fallback_now():
    s = _expense_parse_date_to_iso(None)
    assert "T" in s


def test_parse_date_iso_bad_fallback_now():
    """坏 date 不抛, fallback now."""
    s = _expense_parse_date_to_iso("garbage")
    assert "T" in s


def test_append_and_read_round_trip(fake_home: Path):
    record = {
        "id": "bk_test_001",
        "ts": "2026-06-22T10:00:00+08:00",
        "kind": "支出",
        "amount": 13.0,
        "category": "餐饮",
        "note": "午饭",
    }
    _expense_append_record(fake_home, record)
    records = _expense_read_all(fake_home)
    assert len(records) == 1
    assert records[0] == record


def test_append_multiple_preserves_order(fake_home: Path):
    for i in range(3):
        _expense_append_record(fake_home, {
            "id": f"bk_test_{i:03d}",
            "ts": f"2026-06-22T1{i}:00:00+08:00",
            "kind": "支出",
            "amount": float(i + 1),
            "category": "餐饮",
            "note": f"#{i}",
        })
    records = _expense_read_all(fake_home)
    assert [r["id"] for r in records] == ["bk_test_000", "bk_test_001", "bk_test_002"]


def test_read_all_missing_file(fake_home: Path):
    assert _expense_read_all(fake_home) == []


def test_read_all_skip_bad_lines(fake_home: Path):
    (fake_home / "bookkeep.jsonl").write_text(
        '{"id":"bk_a","ts":"2026-06-22T10:00:00+08:00","kind":"支出","amount":5}\n'
        'not json\n'
        '{"id":"bk_b","ts":"2026-06-22T11:00:00+08:00","kind":"收入","amount":100}\n',
        encoding="utf-8",
    )
    records = _expense_read_all(fake_home)
    assert len(records) == 2
    assert records[0]["id"] == "bk_a"


def test_summarize_window_basic():
    records = [
        {"ts": "2026-06-22T10:00:00+08:00", "kind": "支出", "amount": 5},
        {"ts": "2026-06-22T11:00:00+08:00", "kind": "支出", "amount": 30},
        {"ts": "2026-06-22T12:00:00+08:00", "kind": "收入", "amount": 100},
    ]
    total_in, total_out, n = _expense_summarize_window(records, 0)
    assert total_in == 100.0
    assert total_out == 35.0
    assert n == 3


# ──────────────────────────────────────────────────────────────────────────────
# handler — handle_memory_tool(kind=expense) round-trip
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def provider(fake_home: Path):
    """构造 CatfishMemoryProvider, 不真 initialize (单测不需要 hermes runtime)."""
    p = catfish_memory.CatfishMemoryProvider()
    # mock initialized + catfish_home cache
    p._initialized = True
    p._catfish_home_cached = fake_home
    return p


def test_handle_expense_add_success(provider, fake_home: Path):
    out = json.loads(provider.handle_memory_tool({
        "action": "add",
        "kind": "expense",
        "direction": "支出",
        "amount": 13,
        "category": "餐饮",
        "note": "午饭",
    }))
    assert out["success"] is True
    assert out["routed_to"] == "bookkeep.jsonl"
    assert out["id"].startswith("bk_")
    assert out["recorded"]["amount"] == 13.0
    assert out["recorded"]["kind"] == "支出"

    # jsonl 真落
    records = _expense_read_all(fake_home)
    assert len(records) == 1


def test_handle_expense_income(provider, fake_home: Path):
    out = json.loads(provider.handle_memory_tool({
        "action": "add",
        "kind": "expense",
        "direction": "收入",
        "amount": 25000,
        "category": "工资",
    }))
    assert out["success"] is True
    assert out["recorded"]["kind"] == "收入"
    assert out["recorded"]["amount"] == 25000.0


def test_handle_expense_default_category(provider, fake_home: Path):
    """没填 category 默认 '其他'."""
    out = json.loads(provider.handle_memory_tool({
        "action": "add",
        "kind": "expense",
        "direction": "支出",
        "amount": 5,
    }))
    assert out["success"] is True
    assert out["recorded"]["category"] == "其他"


def test_handle_expense_validation_missing_direction(provider, fake_home: Path):
    out = json.loads(provider.handle_memory_tool({
        "action": "add",
        "kind": "expense",
        "amount": 5,
    }))
    assert out["success"] is False
    assert "direction" in out["error"]


def test_handle_expense_validation_bad_amount(provider, fake_home: Path):
    out = json.loads(provider.handle_memory_tool({
        "action": "add",
        "kind": "expense",
        "direction": "支出",
        "amount": "not-a-number",
    }))
    assert out["success"] is False


def test_handle_expense_validation_negative_amount(provider, fake_home: Path):
    out = json.loads(provider.handle_memory_tool({
        "action": "add",
        "kind": "expense",
        "direction": "支出",
        "amount": -10,
    }))
    assert out["success"] is False


def test_handle_expense_with_date(provider, fake_home: Path):
    """LLM 填 date 回填 (员工说 '19号加油300')."""
    out = json.loads(provider.handle_memory_tool({
        "action": "add",
        "kind": "expense",
        "direction": "支出",
        "amount": 300,
        "category": "交通",
        "note": "加油",
        "date": "2026-06-19",
    }))
    assert out["success"] is True
    assert out["recorded"]["ts"].startswith("2026-06-19")


# ──────────────────────────────────────────────────────────────────────────────
# schema — 6 kind + expense 字段
# ──────────────────────────────────────────────────────────────────────────────


def test_schema_6_kind_includes_expense(provider):
    schema = provider.get_catfish_memory_schema()
    kinds = schema["properties"]["kind"]["enum"]
    assert "expense" in kinds
    assert len(kinds) == 6


def test_schema_expense_fields_present(provider):
    schema = provider.get_catfish_memory_schema()
    props = schema["properties"]
    # P3.5.78 加的 expense 专用字段
    assert "direction" in props
    assert "amount" in props
    assert "category" in props
    assert "date" in props
    # direction enum 是 支出/收入
    assert props["direction"]["enum"] == ["支出", "收入"]


def test_schema_render_has_expense_in_decision_tree(provider):
    rendered = provider._render_schema()
    # 决策树第 4 条 = expense
    assert "expense" in rendered
    assert "bookkeep.jsonl" in rendered
    assert "6 kind memory router" in rendered  # 5 → 6 改了


# ──────────────────────────────────────────────────────────────────────────────
# _render_expense_summary — prefetch 注入
# ──────────────────────────────────────────────────────────────────────────────


def test_render_expense_summary_empty(provider, fake_home: Path):
    """没数据返空 string (不影响其它 prefetch section)."""
    out = provider._render_expense_summary(fake_home)
    assert out == ""


def test_render_expense_summary_with_data(provider, fake_home: Path):
    """有数据返 markdown summary."""
    import datetime as dt
    now = dt.datetime.now().astimezone()
    today_iso = now.replace(hour=10, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    _expense_append_record(fake_home, {
        "id": "bk_001",
        "ts": today_iso,
        "kind": "支出",
        "amount": 13.0,
        "category": "餐饮",
        "note": "",
    })
    out = provider._render_expense_summary(fake_home)
    assert "💸" in out
    assert "13" in out
    assert "支出" in out or "13.00" in out
