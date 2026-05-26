"""BL-LEARN-RECMODE V2 #68 (5/15) — DOM mutation summary 单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_recmode_v2.py -q

# 5/25 BL-RECMODE-MIGRATE-TO-EDGE: selector_repair 测试搬到
# edge/tool-bridge/tests/recmode/test_selector_repair.py (跟 selector_repair.py
# 同时搬). 这里只剩 dom_summary 测试 — dom_summary 模块只算 DOM diff 不读盘
# 不调 LLM, 不属于"录屏数据 leak" 范畴, 留中央.
"""
from __future__ import annotations

from catfish_gateway.recmode import dom_summary as ds


# ─── dom_summary ──────────────────────────────────────────


def test_summarize_empty():
    assert ds.summarize_mutation_snapshot({"added": {}, "removed": {}, "attrs": 0}) == "DOM 无变化"
    assert ds.summarize_mutation_snapshot({}) == "DOM 无变化"


def test_summarize_added_only():
    snap = {"added": {"div.app-icon": 12, "span.label": 12}, "removed": {}, "attrs": 0}
    s = ds.summarize_mutation_snapshot(snap)
    assert "+12 div.app-icon" in s
    assert "+12 span.label" in s


def test_summarize_added_removed_attrs():
    snap = {
        "added": {"div.app-icon": 12},
        "removed": {"div.login-form": 3, "input.user": 1},
        "attrs": 5,
    }
    s = ds.summarize_mutation_snapshot(snap)
    assert "+12 div.app-icon" in s
    assert "-3 div.login-form" in s
    assert "5 attr 变化" in s


def test_summarize_top_5_only():
    """超 5 个 selector 只取 top 5 (按 count 降序)"""
    added = {f"div.x-{i}": 100 - i for i in range(10)}  # x-0=100, x-1=99, ...
    snap = {"added": added, "removed": {}, "attrs": 0}
    s = ds.summarize_mutation_snapshot(snap)
    # x-0 至 x-4 应在, x-5+ 不在
    assert "+100 div.x-0" in s
    assert "+96 div.x-4" in s
    assert "div.x-5" not in s


def test_is_significant_mutation():
    assert ds.is_significant_mutation({"added": {"a": 3, "b": 3}, "removed": {}, "attrs": 0}, threshold=5) is True
    assert ds.is_significant_mutation({"added": {"a": 2}, "removed": {}, "attrs": 0}, threshold=5) is False
    # 仅 attr 变化不算 significant
    assert ds.is_significant_mutation({"added": {}, "removed": {}, "attrs": 100}, threshold=5) is False
    assert ds.is_significant_mutation({}, threshold=5) is False


def test_merge_snapshots():
    a = {"added": {"div.x": 5}, "removed": {"span.y": 2}, "attrs": 1}
    b = {"added": {"div.x": 3, "p.z": 4}, "removed": {}, "attrs": 5}
    merged = ds.merge_snapshots([a, b])
    assert merged["added"]["div.x"] == 8
    assert merged["added"]["p.z"] == 4
    assert merged["removed"]["span.y"] == 2
    assert merged["attrs"] == 6


def test_merge_empty():
    merged = ds.merge_snapshots([])
    assert merged == {"added": {}, "removed": {}, "attrs": 0}


def test_inject_observer_js_is_iife():
    """JS 是 IIFE, 装幂等 (再调返 'already_installed')"""
    js = ds.INJECT_OBSERVER_JS
    assert js.strip().startswith("(function()")
    assert "MutationObserver" in js
    assert "__catfishGetMutations" in js
    assert "already_installed" in js
