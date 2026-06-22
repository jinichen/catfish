"""catfish-bookkeep plugin 单测 (P3.5.75).

测 3 层:
  1. helpers (纯函数): _gen_id / _parse_date_to_iso / _append_record / _read_all_records
     / _filter_records / _summarize
  2. handler (CatfishBookkeepProvider.handle_*): add/query/summarize round-trip
  3. schema 合法性 (3 个 schema 都是 valid JSON Schema)

跑法:
    cd edge/hermes-plugins/catfish-bookkeep
    python -m pytest tests/ -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import bookkeep  # noqa: E402  (from conftest sys.path)
from bookkeep import (  # noqa: E402
    DEFAULT_CATEGORIES,
    CatfishBookkeepProvider,
    _append_record,
    _filter_records,
    _gen_id,
    _parse_date_to_iso,
    _read_all_records,
    _summarize,
    get_bookkeep_add_schema,
    get_bookkeep_query_schema,
    get_bookkeep_summarize_schema,
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


def test_gen_id_format():
    """id 形如 bk_YYYYMMDD_HHMMSS_<4位 hex> — split('_') 4 段."""
    sid = _gen_id()
    assert sid.startswith("bk_")
    parts = sid.split("_")
    assert len(parts) == 4
    assert parts[0] == "bk"
    assert len(parts[1]) == 8  # YYYYMMDD
    assert len(parts[2]) == 6  # HHMMSS
    assert len(parts[3]) == 4  # 4 hex


def test_gen_id_unique():
    """连 100 次 id 真不重 (suffix 防 race)."""
    ids = {_gen_id() for _ in range(100)}
    # 即使 100 次都同秒, 4 hex (65536) 应几乎不撞
    assert len(ids) >= 95  # 留 5 撞余地


def test_parse_date_iso_full():
    """完整 ISO datetime 不变."""
    s = _parse_date_to_iso("2026-06-21T15:30:00")
    assert "2026-06-21" in s
    assert "15:30:00" in s


def test_parse_date_iso_date_only():
    """仅日期补正午."""
    s = _parse_date_to_iso("2026-06-21")
    assert s.startswith("2026-06-21T12:00:00")


def test_parse_date_iso_empty_fallback_now():
    """空 / None fallback now (tz aware)."""
    s = _parse_date_to_iso(None)
    # 至少应该是 ISO + tz 偏移
    assert "T" in s
    assert ("+" in s) or s.endswith("Z") or ("-" in s.split("T")[1])


def test_parse_date_iso_bad_fallback_now():
    """坏 input fallback now, 不抛."""
    s = _parse_date_to_iso("garbage-not-date")
    assert "T" in s


def test_append_and_read_round_trip(fake_home: Path):
    """append 一条 → read_all 拿到."""
    rec = {
        "id": "bk_test_001",
        "ts": "2026-06-22T10:00:00+08:00",
        "kind": "支出",
        "amount": 5.0,
        "category": "餐饮",
        "note": "煎饼",
    }
    _append_record(rec)
    records = _read_all_records()
    assert len(records) == 1
    assert records[0] == rec


def test_append_multiple_keeps_order(fake_home: Path):
    """连续 append 3 条, 读出顺序 = 写入顺序."""
    for i in range(3):
        _append_record({
            "id": f"bk_test_{i:03d}",
            "ts": f"2026-06-22T1{i}:00:00+08:00",
            "kind": "支出",
            "amount": float(i + 1),
            "category": "餐饮",
            "note": f"#{i}",
        })
    records = _read_all_records()
    assert len(records) == 3
    assert [r["id"] for r in records] == ["bk_test_000", "bk_test_001", "bk_test_002"]


def test_read_all_records_missing_file(fake_home: Path):
    """jsonl 不存在 → 空 list (不抛)."""
    assert _read_all_records() == []


def test_read_all_records_skip_bad_lines(fake_home: Path):
    """坏行静默跳过, 好行保留."""
    path = fake_home / "bookkeep.jsonl"
    path.write_text(
        '{"id":"bk_a","ts":"2026-06-22T10:00:00+08:00","kind":"支出","amount":5}\n'
        'this is not json\n'
        '{"id":"bk_b","ts":"2026-06-22T11:00:00+08:00","kind":"收入","amount":100}\n',
        encoding="utf-8",
    )
    records = _read_all_records()
    assert len(records) == 2
    assert records[0]["id"] == "bk_a"
    assert records[1]["id"] == "bk_b"


def test_filter_by_kind():
    records = [
        {"ts": "2026-06-22T10:00:00+08:00", "kind": "支出", "amount": 5, "category": "餐饮"},
        {"ts": "2026-06-22T11:00:00+08:00", "kind": "收入", "amount": 100, "category": "工资"},
        {"ts": "2026-06-22T12:00:00+08:00", "kind": "支出", "amount": 32, "category": "交通"},
    ]
    out = _filter_records(records, kind="支出")
    assert len(out) == 2
    assert all(r["kind"] == "支出" for r in out)


def test_filter_by_category():
    records = [
        {"ts": "2026-06-22T10:00:00+08:00", "kind": "支出", "amount": 5, "category": "餐饮"},
        {"ts": "2026-06-22T11:00:00+08:00", "kind": "支出", "amount": 32, "category": "交通"},
    ]
    out = _filter_records(records, category="餐饮")
    assert len(out) == 1
    assert out[0]["category"] == "餐饮"


def test_summarize_basic():
    records = [
        {"ts": "2026-06-22T10:00:00+08:00", "kind": "支出", "amount": 5, "category": "餐饮"},
        {"ts": "2026-06-22T11:00:00+08:00", "kind": "支出", "amount": 30, "category": "餐饮"},
        {"ts": "2026-06-22T12:00:00+08:00", "kind": "收入", "amount": 100, "category": "工资"},
    ]
    s = _summarize(records, group_by="category")
    assert s["total_income"] == 100.0
    assert s["total_expense"] == 35.0
    assert s["net"] == 65.0
    assert s["count"] == 3
    assert s["by_category"]["餐饮"]["expense"] == 35.0
    assert s["by_category"]["餐饮"]["count"] == 2
    assert s["by_category"]["工资"]["income"] == 100.0


def test_summarize_group_by_kind():
    records = [
        {"ts": "2026-06-22T10:00:00+08:00", "kind": "支出", "amount": 5, "category": "餐饮"},
        {"ts": "2026-06-22T11:00:00+08:00", "kind": "收入", "amount": 100, "category": "工资"},
    ]
    s = _summarize(records, group_by="kind")
    assert "by_kind" in s
    assert s["by_kind"]["支出"]["expense"] == 5.0
    assert s["by_kind"]["收入"]["income"] == 100.0


# ──────────────────────────────────────────────────────────────────────────────
# handler (CatfishBookkeepProvider) — round-trip
# ──────────────────────────────────────────────────────────────────────────────


def test_handle_add_success(fake_home: Path):
    p = CatfishBookkeepProvider()
    out = json.loads(p.handle_bookkeep_add({
        "kind": "支出",
        "amount": 5.5,
        "category": "餐饮",
        "note": "煎饼",
    }))
    assert out["success"] is True
    assert out["id"].startswith("bk_")
    assert out["recorded"]["amount"] == 5.5

    # 真落 jsonl
    records = _read_all_records()
    assert len(records) == 1


def test_handle_add_validation_missing_kind(fake_home: Path):
    p = CatfishBookkeepProvider()
    out = json.loads(p.handle_bookkeep_add({"amount": 5}))
    assert out["success"] is False
    assert "kind" in out["error"]


def test_handle_add_validation_bad_amount(fake_home: Path):
    p = CatfishBookkeepProvider()
    out = json.loads(p.handle_bookkeep_add({"kind": "支出", "amount": "not-a-number"}))
    assert out["success"] is False


def test_handle_add_validation_negative_amount(fake_home: Path):
    p = CatfishBookkeepProvider()
    out = json.loads(p.handle_bookkeep_add({"kind": "支出", "amount": -10}))
    assert out["success"] is False


def test_handle_add_default_category(fake_home: Path):
    """没填 category 默认 '其他'."""
    p = CatfishBookkeepProvider()
    out = json.loads(p.handle_bookkeep_add({"kind": "支出", "amount": 5}))
    assert out["success"] is True
    assert out["recorded"]["category"] == "其他"


def test_handle_add_with_date(fake_home: Path):
    """LLM 填 date 回填 (e.g. 员工说 '昨天买的')."""
    p = CatfishBookkeepProvider()
    out = json.loads(p.handle_bookkeep_add({
        "kind": "支出",
        "amount": 32,
        "category": "交通",
        "date": "2026-06-21",
    }))
    assert out["success"] is True
    assert out["recorded"]["ts"].startswith("2026-06-21")


def test_handle_query_returns_recent_first(fake_home: Path):
    """query 返结构化 list, 倒序 (最新先)."""
    p = CatfishBookkeepProvider()
    # 准备 3 笔不同 ts
    for i, ts in enumerate([
        "2026-06-20T10:00:00+08:00",
        "2026-06-21T10:00:00+08:00",
        "2026-06-22T10:00:00+08:00",
    ]):
        _append_record({
            "id": f"bk_q_{i}",
            "ts": ts,
            "kind": "支出",
            "amount": float(i + 1),
            "category": "餐饮",
            "note": "",
        })

    out = json.loads(p.handle_bookkeep_query({"days": 365}))
    assert out["success"] is True
    assert out["count"] == 3
    assert out["records"][0]["id"] == "bk_q_2"  # 最新先
    assert out["records"][-1]["id"] == "bk_q_0"


def test_handle_query_filter_kind(fake_home: Path):
    p = CatfishBookkeepProvider()
    _append_record({
        "id": "bk_a", "ts": "2026-06-22T10:00:00+08:00",
        "kind": "支出", "amount": 5, "category": "餐饮", "note": "",
    })
    _append_record({
        "id": "bk_b", "ts": "2026-06-22T11:00:00+08:00",
        "kind": "收入", "amount": 100, "category": "工资", "note": "",
    })
    out = json.loads(p.handle_bookkeep_query({"days": 30, "kind": "支出"}))
    assert out["count"] == 1
    assert out["records"][0]["kind"] == "支出"


def test_handle_summarize(fake_home: Path):
    p = CatfishBookkeepProvider()
    _append_record({
        "id": "bk_a", "ts": "2026-06-22T10:00:00+08:00",
        "kind": "支出", "amount": 5, "category": "餐饮", "note": "",
    })
    _append_record({
        "id": "bk_b", "ts": "2026-06-22T11:00:00+08:00",
        "kind": "收入", "amount": 100, "category": "工资", "note": "",
    })
    out = json.loads(p.handle_bookkeep_summarize({"days": 30}))
    assert out["success"] is True
    assert out["summary"]["total_income"] == 100.0
    assert out["summary"]["total_expense"] == 5.0
    assert out["summary"]["net"] == 95.0


# ──────────────────────────────────────────────────────────────────────────────
# schema 合法性
# ──────────────────────────────────────────────────────────────────────────────


def _assert_schema_valid(schema):
    """JSON Schema 基本合法: type=object + properties dict."""
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    assert isinstance(schema.get("properties"), dict)
    # required 字段如果有, 必须是 list[str]
    req = schema.get("required", [])
    assert isinstance(req, list)
    for r in req:
        assert isinstance(r, str)
        assert r in schema["properties"]


def test_schema_add_valid():
    s = get_bookkeep_add_schema()
    _assert_schema_valid(s)
    assert "kind" in s["properties"]
    assert "amount" in s["properties"]
    assert "kind" in s["required"]
    assert "amount" in s["required"]


def test_schema_query_valid():
    _assert_schema_valid(get_bookkeep_query_schema())


def test_schema_summarize_valid():
    _assert_schema_valid(get_bookkeep_summarize_schema())


def test_default_categories_8():
    """默认 8 类不变 (跟 README / docs 一致)."""
    assert len(DEFAULT_CATEGORIES) == 8
    assert "餐饮" in DEFAULT_CATEGORIES
    assert "工资" in DEFAULT_CATEGORIES
