"""10/7「同一事件不同时间的状态并存」—— 蒸馏链路的四处修法, 每处一条回归。

实测根因: 单段蒸馏网关耗时中位 87.6s, 客户端超时 60s, 四分之三的段静默丢掉,
distilled_facts 停在两个月前, 头部却写「截至今天 以此为准」。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_memory_distill_reconcile import assemble, merge_segments_by_date, parse_segments, split_journal_entries
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
    assert _distill_cursor(tmp_path, j) == (0, [])
    _mark_distill_run(tmp_path, journal_consumed_chars=len(j), journal_text=j, journal_gaps=[(3, 9), (50, 60)])
    # 缺口只认落在游标之内的; (50,60) 超出游标被丢
    assert _distill_cursor(tmp_path, j) == (len(j), [(3, 9)])
    assert _distill_cursor(tmp_path, j + "## [2026-09-02 10:00] session | t\n\nmore\n")[0] == len(j)
    assert _distill_cursor(tmp_path, "## [2026-01-01 10:00] session | z\n\nreplaced\n") == (0, []), "文件换掉 → 全量"
    assert _distill_cursor(tmp_path, j[:5]) == (0, []), "变短 → 全量"


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
async def test_failed_chunk_becomes_gap_and_is_refilled_next_run(tmp_path: Path):
    """10/7 晚: 失败段不拖累其它段 —— 成功的照常写, 失败区间记缺口, 下次只补缺口。"""
    e1, e2, e3 = _entry("2026-09-01", "a" * 10), _entry("2026-09-02", "b" * 10), _entry("2026-09-03", "c" * 10)
    (tmp_path / "employee_journal.md").write_text(e1 + e2 + e3, encoding="utf-8")
    fed = []

    async def fake(text, model, *, progress_cb=None, prior_segments=None, report=None):
        fed.append(text)
        if len(fed) == 1:
            # 三段: 中间 e2 失败
            report.update({"chunks_total": 3, "chunks_failed": 1, "consumed_chars": len(text),
                           "failed_ranges": [(len(e1), len(e1) + len(e2))], "reconciled": False})
            return assemble([("2026-09-01", SEG_OLD), ("2026-09-03", SEG_NEW)], None, chunks_total=3, chunks_failed=1)
        # 第二次: 只喂缺口 e2, 补出来的段要插回 09-01 和 09-03 之间
        report.update({"chunks_total": 1, "chunks_failed": 0, "consumed_chars": len(text), "failed_ranges": [], "reconciled": True})
        from catfish_memory_distill_reconcile import merge_segments_by_date
        return assemble(merge_segments_by_date(list(prior_segments), [("2026-09-02", "## 项目\n- 中间段\n")]), "## 进行中\n(无)")

    r = await distill_incremental(tmp_path, "m", fake)
    assert r["ok"] and r["chunks_failed"] == 1 and r["reconciled"] is False
    state = json.loads((tmp_path / "memory_distill_state.json").read_text(encoding="utf-8"))
    assert state["journal_consumed_chars"] == len(e1 + e2 + e3), "游标推到末尾, 成功段不丢"
    assert state["journal_gaps"] == [[len(e1), len(e1) + len(e2)]]
    text = (tmp_path / "distilled_facts.md").read_text(encoding="utf-8")
    assert "1/3 段失败" in text and "蒸馏段 2（2026-09-03）" in text

    r2 = await distill_incremental(tmp_path, "m", fake)
    assert r2["ok"] and fed[1] == e2, "第二次只喂缺口"
    state = json.loads((tmp_path / "memory_distill_state.json").read_text(encoding="utf-8"))
    assert state["journal_gaps"] == []
    text = (tmp_path / "distilled_facts.md").read_text(encoding="utf-8")
    assert text.index("蒸馏段 3（2026-09-03）") < text.index("蒸馏段 2（2026-09-02）") < text.index("蒸馏段 1（2026-09-01）")
    r3 = await distill_incremental(tmp_path, "m", fake)
    assert r3["reason"] == "no_new_journal"


def test_to_absolute_maps_ranges_across_pieces():
    from catfish_memory_distill_run import _to_absolute
    # pieces: 缺口 [100,150) + 新增 [400,500); 拼接后相对 [40,70) 跨两个 piece
    assert _to_absolute([(100, 150), (400, 500)], [(40, 70)]) == [(140, 150), (400, 420)]
    assert _to_absolute([(100, 150)], [(0, 50)]) == [(100, 150)]


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
        if what.startswith("当前状态合并"):
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
    assert report == {"chunks_total": 4, "chunks_failed": 0, "failed_ranges": [], "consumed_chars": len(journal), "reconciled": True}, report
    assert attempts[2] == 2 and all(attempts[i] == 1 for i in (1, 3, 4))
    # 新在前: 段 4 (09-04) 排在段 1 (09-01) 前面, 且第 2 段补回来了
    assert out.index("段 2026-09-04") < out.index("段 2026-09-02") < out.index("段 2026-09-01")


def test_merge_segments_by_date_inserts_gap_segment_in_place():
    prior = [("2026-07-01 ~ 2026-07-05", "a"), ("", "a2"), ("2026-09-01", "c")]
    out = merge_segments_by_date(prior, [("2026-08-01", "b")])
    assert [t for _, t in out] == ["a", "a2", "b", "c"], "无标签段跟着前一个走, 缺口段按日期插中间"


@pytest.mark.asyncio
async def test_fallback_head_triggers_reconcile_only_next_run(tmp_path: Path):
    """10/7 晚: 合并超时 → 兜底头; 下次没新增也要只重跑一次合并 (空文本 + prior), 缺口保留。"""
    e1 = _entry("2026-09-01", "a" * 10)
    (tmp_path / "employee_journal.md").write_text(e1, encoding="utf-8")
    calls = []

    async def fake(text, model, *, progress_cb=None, prior_segments=None, report=None):
        calls.append((text, list(prior_segments or [])))
        if len(calls) == 1:
            report.update({"chunks_total": 1, "chunks_failed": 0, "consumed_chars": len(text), "failed_ranges": [], "reconciled": False})
            return assemble([("2026-09-01", SEG_OLD)], None)
        report.update({"chunks_total": 0, "chunks_failed": 0, "consumed_chars": 0, "failed_ranges": [], "reconciled": True})
        return assemble(list(prior_segments), "## 进行中\n- 合并成了")

    r1 = await distill_incremental(tmp_path, "m", fake)
    assert r1["ok"] and r1["reconciled"] is False
    r2 = await distill_incremental(tmp_path, "m", fake)
    assert r2["ok"] and r2["reason"] == "reconcile_only" and r2["chunks_total"] == 0
    assert calls[1][0] == "" and [l for l, _ in calls[1][1]] == ["2026-09-01"], "只喂 prior, 不喂文本"
    text = (tmp_path / "distilled_facts.md").read_text(encoding="utf-8")
    assert "合并成了" in text and "兜底生成" not in text
    r3 = await distill_incremental(tmp_path, "m", fake)
    assert r3["reason"] == "no_new_journal" and len(calls) == 2


@pytest.mark.asyncio
async def test_reconcile_shrinks_input_until_it_succeeds(monkeypatch):
    """10/7 深夜: 合并超时就把输入砍半再试 (1 → 1/2 → 1/4), 砍掉的是最旧的段。"""
    import catfish_memory_llm as llm

    monkeypatch.setattr(llm, "_gateway_dev_token", lambda: "t")
    sizes = []

    async def fake_post(client, headers, body, what):
        if what.startswith("当前状态合并"):
            n = len(body["messages"][0]["content"])
            sizes.append(n)
            return "## 进行中\n- ok" if len(sizes) == 3 else None
        return "## 项目\n- p\n\n## 任务状态\n- x | resolved\n"

    monkeypatch.setattr(llm, "_post_distill", fake_post)
    report = {}
    out = await llm._call_distill_llm(_entry("2026-09-01", "z" * 400), "m", report=report)
    assert report["reconciled"] is True and "以此为准" in out.splitlines()[0]
    assert len(sizes) == 3 and sizes[0] > sizes[1] > sizes[2], sizes


def test_reconcile_retry_is_throttled_and_keeps_cooldown(tmp_path: Path, monkeypatch):
    from catfish_memory_distill_state import _should_retry_reconcile, _should_run_distill
    import catfish_memory_distill_state as st
    j = _entry("2026-09-01", "x")
    (tmp_path / "employee_journal.md").write_text(j, encoding="utf-8")
    assert not _should_retry_reconcile(tmp_path), "没有 distilled 文件 → 不重试"
    (tmp_path / "distilled_facts.md").write_text(assemble([("2026-09-01", SEG_OLD)], None), encoding="utf-8")
    _mark_distill_run(tmp_path, journal_consumed_chars=len(j), journal_text=j)
    assert _should_retry_reconcile(tmp_path), "头部兜底、从没试过合并 → 该重试"
    assert not _should_run_distill(tmp_path), "24h cooldown 仍然在"
    before = json.loads((tmp_path / "memory_distill_state.json").read_text(encoding="utf-8"))["last_run_ts"]
    _mark_distill_run(tmp_path, journal_consumed_chars=len(j), journal_text=j, reconcile_attempt_only=True)
    after = json.loads((tmp_path / "memory_distill_state.json").read_text(encoding="utf-8"))
    assert after["last_run_ts"] == before and after["journal_consumed_chars"] == len(j), "只记尝试, 不重置 cooldown, 游标不丢"
    assert not _should_retry_reconcile(tmp_path), "1h 内不再试"
    monkeypatch.setattr(st, "_RECONCILE_RETRY_SECONDS", 0)
    assert _should_retry_reconcile(tmp_path)
    (tmp_path / "distilled_facts.md").write_text(assemble([("2026-09-01", SEG_OLD)], "## 进行中\n(无)"), encoding="utf-8")
    assert not _should_retry_reconcile(tmp_path), "头部已是 LLM 合并 → 不用再试"
