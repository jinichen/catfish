"""10/7「同一事件不同时间的状态并存」—— 蒸馏链路的四处修法, 每处一条回归。

实测根因: 单段蒸馏网关耗时中位 87.6s, 客户端超时 60s, 四分之三的段静默丢掉,
distilled_facts 停在两个月前, 头部却写「截至今天 以此为准」。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_memory_distill_reconcile import assemble, parse_segments, split_journal_entries
from catfish_memory_distill_run import distill_incremental
from catfish_memory_distill_state import _distill_cursor, _mark_distill_run
from catfish_memory_helpers import RAW_FALLBACK_TAG, _format_journal_entry


def _entry(day: str, body: str, kind: str = "session") -> str:
    return f"## [{day} 10:00] {kind} | s-{day}\n\n{body}\n\n"


SEG_OLD = "## 项目\n- CMMI-5 准备\n\n## 任务状态\n- CMMI-5 评审 | paused\n"
SEG_NEW = "## 项目\n- CMMI-5 已拿证\n\n## 任务状态\n- CMMI-5 评审 | resolved\n"


# ── 段头: 兜底不许冒充"截至今天" ─────────────────────────────

def test_fallback_head_does_not_claim_today():
    out = assemble([("2026-08-08", SEG_OLD)], None)
    head = out.split("### 蒸馏段 1")[0]
    assert "兜底生成" in head and "2026-08-08" in head
    assert "以此为准" not in head, "合并没跑成, 不能让下游把旧状态当今天的"


def test_llm_reconciled_head_still_says_authoritative():
    out = assemble([("2026-08-08", SEG_OLD)], "## 进行中\n- x")
    assert "以此为准" in out.splitlines()[0]


def test_failed_chunks_are_announced_in_head():
    out = assemble([("2026-08-08", SEG_OLD)], "## 进行中\n- x", chunks_total=10, chunks_failed=3)
    assert "3/10 段失败" in out.split("### 蒸馏段 1")[0]


# ── parse_segments: 上次的段能还原, 旧→新, 段 0 不算 ───────────

def test_parse_segments_roundtrip_old_to_new():
    text = assemble([("2026-06-01", SEG_OLD), ("2026-08-11", SEG_NEW)], "## 进行中\n(无)")
    segs = parse_segments(text)
    assert [l for l, _ in segs] == ["2026-06-01", "2026-08-11"]
    assert "已拿证" in segs[1][1] and "进行中" not in "".join(t for _, t in segs)


# ── 切段: 只在条目边界切 ───────────────────────────────────────

def test_split_journal_on_entry_boundaries():
    e1, e2, e3 = _entry("2026-09-01", "a" * 50), _entry("2026-09-02", "b" * 50), _entry("2026-09-03", "c" * 50)
    chunks = split_journal_entries(e1 + e2 + e3, max_chars=len(e1) + len(e2) + 1)
    assert len(chunks) == 2
    assert chunks[0] == e1 + e2 and chunks[1] == e3, "一条摘要不能被劈成两半落在两段里"


def test_split_keeps_oversized_entry_whole():
    big = _entry("2026-09-01", "x" * 500)
    assert split_journal_entries(big, max_chars=100) == [big]


# ── 游标 ───────────────────────────────────────────────────────

def test_cursor_zero_without_state_or_when_journal_replaced(tmp_path: Path):
    j = "## [2026-09-01 10:00] session | s\n\nhello\n"
    assert _distill_cursor(tmp_path, j) == 0
    _mark_distill_run(tmp_path, journal_consumed_chars=len(j), journal_text=j)
    assert _distill_cursor(tmp_path, j) == len(j)
    assert _distill_cursor(tmp_path, j + "## [2026-09-02 10:00] session | t\n\nmore\n") == len(j)
    assert _distill_cursor(tmp_path, "## [2026-01-01 10:00] session | z\n\nreplaced\n") == 0, "文件换掉 → 全量"
    assert _distill_cursor(tmp_path, j[:5]) == 0, "变短 → 全量"


# ── driver: 增量只喂新增, 旧段复用, 失败段不推游标 ─────────────

@pytest.mark.asyncio
async def test_incremental_feeds_only_new_text_and_reuses_prior(tmp_path: Path):
    old = _entry("2026-08-08", "CMMI 评审准备")
    new = _entry("2026-10-07", "CMMI 已拿证")
    journal = tmp_path / "employee_journal.md"
    journal.write_text(old, encoding="utf-8")
    seen = []

    async def fake(text, model, *, progress_cb=None, prior_segments=None, report=None):
        seen.append({"text": text, "prior": list(prior_segments or [])})
        segs = list(prior_segments or []) + [("2026-10-07" if "拿证" in text else "2026-08-08", SEG_NEW if "拿证" in text else SEG_OLD)]
        report.update({"chunks_total": 1, "chunks_failed": 0, "consumed_chars": len(text), "reconciled": True})
        return assemble(segs, "## 进行中\n(无)")

    r1 = await distill_incremental(tmp_path, "m", fake)
    assert r1["ok"] and r1["incremental_from"] == 0
    journal.write_text(old + new, encoding="utf-8")
    r2 = await distill_incremental(tmp_path, "m", fake)
    assert r2["ok"] and r2["incremental_from"] == len(old)
    assert seen[1]["text"] == new, "第二次只能喂新增的那条"
    assert [l for l, _ in seen[1]["prior"]] == ["2026-08-08"], "旧段要从上次文件里还原复用"
    out = (tmp_path / "distilled_facts.md").read_text(encoding="utf-8")
    assert "蒸馏段 2（2026-10-07）" in out and "蒸馏段 1（2026-08-08）" in out
    r3 = await distill_incremental(tmp_path, "m", fake)
    assert r3["ok"] and r3["reason"] == "no_new_journal" and len(seen) == 2


@pytest.mark.asyncio
async def test_failed_chunk_holds_cursor_back(tmp_path: Path):
    e1, e2 = _entry("2026-09-01", "a" * 10), _entry("2026-09-02", "b" * 10)
    (tmp_path / "employee_journal.md").write_text(e1 + e2, encoding="utf-8")

    async def fake(text, model, *, progress_cb=None, prior_segments=None, report=None):
        # 第 1 段成功、第 2 段失败: 只消费到 e1 末尾
        report.update({"chunks_total": 2, "chunks_failed": 1, "consumed_chars": len(e1), "reconciled": False})
        return assemble([("2026-09-01", SEG_OLD)], None, chunks_total=2, chunks_failed=1)

    r = await distill_incremental(tmp_path, "m", fake)
    assert r["ok"] and r["chunks_failed"] == 1 and r["reconciled"] is False
    state = json.loads((tmp_path / "memory_distill_state.json").read_text(encoding="utf-8"))
    assert state["journal_consumed_chars"] == len(e1), "失败段开头就是下次的起点"
    assert "1/2 段失败" in (tmp_path / "distilled_facts.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_llm_fail_leaves_file_and_cursor_alone(tmp_path: Path):
    (tmp_path / "employee_journal.md").write_text(_entry("2026-09-01", "x"), encoding="utf-8")

    async def fake(text, model, **kw):
        return None

    r = await distill_incremental(tmp_path, "m", fake)
    assert not r["ok"] and r["reason"] == "llm_fail"
    assert not (tmp_path / "distilled_facts.md").exists()
    assert not (tmp_path / "memory_distill_state.json").exists()


# ── raw fallback 条目可辨认 ────────────────────────────────────

def test_raw_fallback_entry_is_tagged():
    head = _format_journal_entry("sid-1", f"{RAW_FALLBACK_TAG} 原文", kind="session-raw").splitlines()[0]
    assert head.startswith("## [") and "] session-raw | sid-1" in head
    assert _format_journal_entry("sid-1", "摘要").splitlines()[0].endswith("] session | sid-1")


# ── 并发 + 补跑: 第一轮失败的段补跑成功后不留缺口, 顺序不乱 ────────

@pytest.mark.asyncio
async def test_failed_chunk_is_retried_in_second_pass_and_order_kept(monkeypatch):
    import catfish_memory_llm as llm

    monkeypatch.setattr(llm, "_gateway_dev_token", lambda: "t")
    monkeypatch.setattr(llm, "_DISTILL_CHUNK_CHARS", 80)
    attempts = {}

    async def fake_post(client, headers, body, what):
        if what == "当前状态合并":
            return "## 进行中\n(无)"
        content = body["messages"][0]["content"]
        idx = int(what.split("第 ")[1].split("/")[0])
        attempts[idx] = attempts.get(idx, 0) + 1
        if idx == 2 and attempts[idx] == 1:
            return None  # 第 2 段第一轮失败 (模拟 504), 补跑成功
        day = content.split("## [")[1][:10]
        return f"## 项目\n- 段 {day}\n\n## 任务状态\n- x | resolved\n"

    monkeypatch.setattr(llm, "_post_distill", fake_post)
    journal = "".join(_entry(f"2026-09-0{i}", "z" * 30) for i in range(1, 5))
    report = {}
    out = await llm._call_distill_llm(journal, "m", report=report)
    assert report == {"chunks_total": 4, "chunks_failed": 0, "consumed_chars": len(journal), "reconciled": True}, report
    assert attempts[2] == 2 and all(attempts[i] == 1 for i in (1, 3, 4))
    # 新在前: 段 4 (09-04) 排在段 1 (09-01) 前面, 且第 2 段补回来了
    assert out.index("段 2026-09-04") < out.index("段 2026-09-02") < out.index("段 2026-09-01")
