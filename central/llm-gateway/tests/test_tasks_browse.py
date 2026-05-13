"""BL-HERMES013-RED-2 (5/13 鸿波拍板) — tasks_browse 单测.

   cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_tasks_browse.py -q

聚合数据源:
- ~/.catfish/tasks.jsonl              (background — tool-bridge 写)
- ~/.catfish/a2a_notifications.jsonl  (a2a_inbox — gateway 写)

测试覆盖:
- 空 (两个文件都不存在)
- background only
- a2a_inbox only
- 两个混合
- source 过滤
- hours_back 过滤
- 倒序排
- limit cap
- a2a 已答 → completed; 未答 → waiting
- status_summary 计数
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from catfish_gateway import tasks_browse


@pytest.fixture
def tmp_catfish_home(tmp_path, monkeypatch):
    """临时 CATFISH_HOME, 跑完自动清."""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    return tmp_path


def _write_tasks_jsonl(home: Path, records: list[dict]):
    path = home / "tasks.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_a2a_jsonl(home: Path, records: list[dict]):
    path = home / "a2a_notifications.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ─── 兜底 ─────────────────────────────────────────────


def test_empty_no_files(tmp_catfish_home):
    """两个 jsonl 都不存在 → 返空, 不挂."""
    cards = tasks_browse.list_my_tasks()
    assert cards == []
    assert tasks_browse.status_summary(cards) == {
        "pending": 0, "running": 0, "waiting": 0, "completed": 0, "failed": 0,
    }


# ─── background only ──────────────────────────────────


def test_background_only(tmp_catfish_home):
    _write_tasks_jsonl(tmp_catfish_home, [
        {
            "task_id": "task_aaa", "kind": "execute_code", "label": "做 PPT",
            "status": "completed", "started_at": time.time() - 100,
            "finished_at": time.time() - 50, "elapsed_s": 50, "error": None,
            "result_preview": "rc=0",
        },
    ])
    cards = tasks_browse.list_my_tasks()
    assert len(cards) == 1
    c = cards[0]
    assert c["source"] == "background"
    assert c["status"] == "completed"
    assert c["title"] == "做 PPT"
    assert c["kind"] == "execute_code"


def test_background_failed_carries_error(tmp_catfish_home):
    _write_tasks_jsonl(tmp_catfish_home, [
        {
            "task_id": "task_fail", "kind": "execute_code", "label": "炸了",
            "status": "failed", "started_at": time.time() - 10,
            "error": "OSError: boom",
        },
    ])
    cards = tasks_browse.list_my_tasks()
    assert cards[0]["status"] == "failed"
    assert cards[0]["error"] == "OSError: boom"


# ─── a2a_inbox only ───────────────────────────────────


def test_a2a_answered_is_completed(tmp_catfish_home):
    _write_a2a_jsonl(tmp_catfish_home, [
        {
            "ts": _now_iso(), "from_sub": "alice@ffcs.cn",
            "question": "资质审核?", "purpose": "expert_consult:资质",
            "answer_preview": "走 OA 工单...", "duration_ms": 800,
        },
    ])
    cards = tasks_browse.list_my_tasks()
    assert len(cards) == 1
    c = cards[0]
    assert c["source"] == "a2a_inbox"
    assert c["status"] == "completed"
    assert c["from_sub"] == "alice@ffcs.cn"
    assert c["kind"] == "expert_consult"
    assert "走 OA" in c["preview"]


def test_a2a_unanswered_is_waiting(tmp_catfish_home):
    _write_a2a_jsonl(tmp_catfish_home, [
        {
            "ts": _now_iso(), "from_sub": "bob@ffcs.cn",
            "question": "求救", "purpose": "help",
            "answer_preview": "",  # 空 → 还没答
        },
    ])
    cards = tasks_browse.list_my_tasks()
    assert cards[0]["status"] == "waiting"
    # waiting 的 preview 兜底 "来自 ..."
    assert "bob@ffcs.cn" in cards[0]["preview"]
    # 没 finished_at
    assert cards[0]["finished_at"] is None


# ─── 两个混合 ──────────────────────────────────────────


def test_mixed_sources(tmp_catfish_home):
    _write_tasks_jsonl(tmp_catfish_home, [
        {"task_id": "task_a", "kind": "execute_code", "label": "T1",
         "status": "completed", "started_at": time.time() - 100,
         "finished_at": time.time() - 50, "elapsed_s": 50},
    ])
    _write_a2a_jsonl(tmp_catfish_home, [
        {"ts": _now_iso(), "from_sub": "x@y", "question": "Q",
         "purpose": "p", "answer_preview": "A"},
    ])
    cards = tasks_browse.list_my_tasks()
    assert len(cards) == 2
    sources = {c["source"] for c in cards}
    assert sources == {"background", "a2a_inbox"}


# ─── source 过滤 ──────────────────────────────────────


def test_filter_by_source_background(tmp_catfish_home):
    _write_tasks_jsonl(tmp_catfish_home, [
        {"task_id": "task_a", "kind": "execute_code", "label": "T1",
         "status": "completed", "started_at": time.time()},
    ])
    _write_a2a_jsonl(tmp_catfish_home, [
        {"ts": _now_iso(), "from_sub": "x@y", "question": "Q",
         "purpose": "p", "answer_preview": "A"},
    ])
    cards = tasks_browse.list_my_tasks(sources=["background"])
    assert all(c["source"] == "background" for c in cards)
    assert len(cards) == 1


def test_filter_by_source_a2a(tmp_catfish_home):
    _write_tasks_jsonl(tmp_catfish_home, [
        {"task_id": "task_a", "kind": "execute_code", "label": "T1",
         "status": "completed", "started_at": time.time()},
    ])
    _write_a2a_jsonl(tmp_catfish_home, [
        {"ts": _now_iso(), "from_sub": "x@y", "question": "Q",
         "purpose": "p", "answer_preview": "A"},
    ])
    cards = tasks_browse.list_my_tasks(sources=["a2a_inbox"])
    assert all(c["source"] == "a2a_inbox" for c in cards)
    assert len(cards) == 1


# ─── hours_back 过滤 ──────────────────────────────────


def test_hours_back_filter_drops_old(tmp_catfish_home):
    _write_tasks_jsonl(tmp_catfish_home, [
        {"task_id": "task_new", "kind": "execute_code", "label": "新",
         "status": "completed", "started_at": time.time() - 10},
        {"task_id": "task_old", "kind": "execute_code", "label": "老",
         "status": "completed", "started_at": time.time() - 100 * 3600},
    ])
    cards = tasks_browse.list_my_tasks(hours_back=24)
    ids = [c["id"] for c in cards]
    assert "task_new" in ids
    assert "task_old" not in ids


def test_hours_back_none_returns_all(tmp_catfish_home):
    _write_tasks_jsonl(tmp_catfish_home, [
        {"task_id": "task_new", "kind": "execute_code", "label": "新",
         "status": "completed", "started_at": time.time()},
        {"task_id": "task_ancient", "kind": "execute_code", "label": "古",
         "status": "completed", "started_at": time.time() - 100 * 3600},
    ])
    cards = tasks_browse.list_my_tasks(hours_back=None)
    assert len(cards) == 2


# ─── 倒序 + limit ──────────────────────────────────────


def test_newest_first(tmp_catfish_home):
    base = time.time()
    _write_tasks_jsonl(tmp_catfish_home, [
        {"task_id": f"task_{i}", "kind": "execute_code", "label": f"T{i}",
         "status": "completed", "started_at": base - i * 10}
        for i in range(5)
    ])
    cards = tasks_browse.list_my_tasks()
    # task_0 最新, task_4 最老
    assert cards[0]["id"] == "task_0"
    assert cards[-1]["id"] == "task_4"


def test_limit_caps_results(tmp_catfish_home):
    _write_tasks_jsonl(tmp_catfish_home, [
        {"task_id": f"task_{i}", "kind": "execute_code", "label": f"T{i}",
         "status": "completed", "started_at": time.time() - i}
        for i in range(50)
    ])
    cards = tasks_browse.list_my_tasks(limit=10)
    assert len(cards) == 10


# ─── status_summary ───────────────────────────────────


def test_status_summary_counts(tmp_catfish_home):
    base = time.time()
    _write_tasks_jsonl(tmp_catfish_home, [
        {"task_id": "t1", "kind": "x", "label": "1", "status": "completed", "started_at": base},
        {"task_id": "t2", "kind": "x", "label": "2", "status": "completed", "started_at": base},
        {"task_id": "t3", "kind": "x", "label": "3", "status": "failed", "started_at": base, "error": "x"},
    ])
    _write_a2a_jsonl(tmp_catfish_home, [
        {"ts": _now_iso(), "from_sub": "a", "question": "Q1",
         "purpose": "p", "answer_preview": ""},  # waiting
    ])
    cards = tasks_browse.list_my_tasks()
    summary = tasks_browse.status_summary(cards)
    assert summary["completed"] == 2
    assert summary["failed"] == 1
    assert summary["waiting"] == 1
    assert summary["pending"] == 0
    assert summary["running"] == 0


# ─── 兜底 ─────────────────────────────────────────────


def test_corrupt_jsonl_skipped(tmp_catfish_home):
    """jsonl 含坏行不致命."""
    path = tmp_catfish_home / "tasks.jsonl"
    path.write_text(
        '{"task_id":"task_ok","kind":"x","label":"ok","status":"completed","started_at":' + str(time.time()) + '}\n'
        'completely-not-json\n'
        '{"task_id":"task_ok2","kind":"x","label":"ok2","status":"completed","started_at":' + str(time.time()) + '}\n',
        encoding="utf-8",
    )
    cards = tasks_browse.list_my_tasks()
    ids = {c["id"] for c in cards}
    assert ids == {"task_ok", "task_ok2"}


def test_unknown_status_normalized_to_pending(tmp_catfish_home):
    """jsonl 里 status 是 'weird-state' → 兜底 pending, 不报错."""
    _write_tasks_jsonl(tmp_catfish_home, [
        {"task_id": "task_x", "kind": "x", "label": "?", "status": "frobnicated",
         "started_at": time.time()},
    ])
    cards = tasks_browse.list_my_tasks()
    assert cards[0]["status"] == "pending"
