"""增量蒸馏 driver (10/7) —— Dream 按钮和 session_end 两条路共用。

# 为什么 (10/7 鸿波「历史、记忆里同一事件不同时间的状态并存」)

10/7 实测 distilled_facts.md 停在 8/8: journal 820K 字切 103 段, 每天**全量**重蒸,
单段网关耗时中位 87.6s 而客户端超时 60s —— 四分之三的段静默丢掉, 合并也超时,
头部却写着「截至 2026-10-07 以此为准」。三件事这里一起改:

  1. 只蒸 journal 游标之后的新增 (catfish_memory_distill_state._distill_cursor),
     旧段从上次的 distilled_facts.md 还原 (parse_segments) 直接复用;
  2. 成功段全部保留, 失败段的字符区间记成缺口 (state journal_gaps), 下次只补缺口 +
     新增, 补出来的段按日期插回原位 (10/7 晚: 第一版"失败段之后全丢"在补跑仍有
     段失败时会把 157 段成果扔掉);
  3. 结果字典带 chunks_failed / reconciled / incremental_from, Dream UI 和日志
     能看见"蒸了但有缺口", 不再 exit 0 装没事。

call_llm 由调用方传入 (catfish_memory_distill 的 `_call_distill_llm` 模块名),
tests 一直 monkeypatch 那个名字, 这里不直接 import 它。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from .catfish_memory_distill_reconcile import head_needs_reconcile, parse_segments
    from .catfish_memory_distill_state import _distill_cursor, _mark_distill_run, _write_distilled
    from .catfish_memory_helpers import _DISTILL_CHUNK_CHARS, _read_full_journal
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_distill_reconcile import head_needs_reconcile, parse_segments
    from catfish_memory_distill_state import _distill_cursor, _mark_distill_run, _write_distilled
    from catfish_memory_helpers import _DISTILL_CHUNK_CHARS, _read_full_journal

logger = logging.getLogger("catfish.memory.plugin")


def _read_distilled(home: Path) -> str:
    try:
        return (home / "distilled_facts.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def _to_absolute(pieces: List[Tuple[int, int]], rel_ranges) -> List[Tuple[int, int]]:
    """把相对拼接文本的失败区间映射回 journal 绝对偏移 (一个区间可能跨两个 piece)."""
    out: List[Tuple[int, int]] = []
    for r in rel_ranges:
        try:
            rs, re_ = int(r[0]), int(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        off = 0
        for a, b in pieces:
            n = b - a
            lo, hi = max(rs, off), min(re_, off + n)
            if lo < hi:
                out.append((a + lo - off, a + hi - off))
            off += n
    return out


async def distill_incremental(
    home: Path,
    model: str,
    call_llm,
    *,
    progress_cb=None,
) -> Dict[str, Any]:
    """读 journal → 游标之后的新增 → call_llm(new_text, model, prior_segments=旧段) → 写盘 + 推游标。

    返回 {ok, reason, chunks_total, chunks_failed, bytes_written, model, took_seconds,
          incremental_from, reconciled}。
    reason: empty_journal / no_new_journal / reconcile_only / llm_fail / "".
    """
    started = time.time()
    base: Dict[str, Any] = {
        "ok": False, "reason": "", "chunks_total": 0, "chunks_failed": 0,
        "bytes_written": 0, "model": model, "took_seconds": 0.0,
        "incremental_from": 0, "reconciled": False,
    }

    journal_text = _read_full_journal(home)
    if not journal_text.strip():
        return {**base, "reason": "empty_journal", "took_seconds": time.time() - started}

    cursor, gaps = _distill_cursor(home, journal_text)
    prior = parse_segments(_read_distilled(home)) if cursor > 0 else []
    if cursor > 0 and not prior:
        # 游标在、旧段却还原不出来 (文件被手改成别的格式) → 全量
        cursor, gaps = 0, []
    # 本次要蒸的 = 旧缺口 (按位置) + 游标之后的新增, 拼成一段文本交给 call_llm;
    # 它报回的失败区间是相对这段文本的, 下面 _to_absolute 映射回 journal 绝对偏移
    pieces = [(a, b) for a, b in gaps] + ([(cursor, len(journal_text))] if cursor < len(journal_text) else [])
    pieces = [(a, b) for a, b in pieces if journal_text[a:b].strip()]
    new_text = "".join(journal_text[a:b] for a, b in pieces)
    base["incremental_from"] = cursor

    reconcile_only = False
    if not new_text.strip():
        if prior and head_needs_reconcile(_read_distilled(home)):
            # 10/7 晚: 没新增也没缺口, 但上次"当前状态"合并没成 (头部是兜底) → 只重跑
            # 合并这一次 LLM 调用, 不重蒸任何段。call_llm 拿到空文本 + prior 就只做合并。
            reconcile_only = True
        else:
            # 没新增也没缺口: 不动文件、不调 LLM, 但 cooldown 照记, 免得每次 session_end 都进来
            _mark_distill_run(home, journal_consumed_chars=cursor, journal_text=journal_text, journal_gaps=[])
            return {**base, "ok": True, "reason": "no_new_journal", "took_seconds": time.time() - started}

    estimated = max(1, (len(new_text) + _DISTILL_CHUNK_CHARS - 1) // _DISTILL_CHUNK_CHARS)
    if progress_cb is not None:
        try:
            progress_cb(0, estimated)
        except Exception:  # noqa: BLE001
            pass

    report: Dict[str, Any] = {}
    distilled = await call_llm(
        new_text, model, progress_cb=progress_cb, prior_segments=prior, report=report,
    )
    chunks_total = int(report["chunks_total"]) if "chunks_total" in report else estimated
    chunks_failed = int(report.get("chunks_failed") or 0)
    out = {
        **base, "chunks_total": chunks_total, "chunks_failed": chunks_failed,
        "reconciled": bool(report.get("reconciled")),
    }
    if not distilled:
        return {**out, "reason": "llm_fail", "took_seconds": time.time() - started}

    _write_distilled(home, distilled)
    # 只重跑合并时没喂任何 piece, 旧缺口原样保留
    new_gaps = gaps if reconcile_only else _to_absolute(pieces, report.get("failed_ranges") or [])
    _mark_distill_run(
        home, journal_consumed_chars=len(journal_text), journal_text=journal_text,
        journal_gaps=new_gaps,
    )
    if chunks_failed:
        logger.warning(
            "catfish-memory 蒸馏完成但 %d/%d 段失败, 记成 %d 个缺口 (%d 字符) 下次补",
            chunks_failed, chunks_total, len(new_gaps), sum(b - a for a, b in new_gaps),
        )
    return {
        **out, "ok": True,
        "reason": "reconcile_only" if reconcile_only else "",
        "bytes_written": len(distilled.encode("utf-8")),
        "took_seconds": time.time() - started,
    }
