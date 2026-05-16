"""BL-MEMORY-DISTILL-LIVE (5/16 鸿波 'memory_distill 真上线') 测试.

覆盖:
  - read_distilled_facts / write_distilled_facts 落盘往返
  - inject_employee_journal 两段式 (distilled + journal 都注入)
  - maybe_run_llm_distillation 异步流程 (mock loopback LLM)
  - only_old_segments=True 跳过最后 INJECT_MAX_BYTES
  - 红线 / 触发条件复用规则版逻辑
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from catfish_gateway import memory_distill
from catfish_gateway.employee_journal import inject_employee_journal


# ── read/write distilled_facts 往返 ─────────────────────


def test_write_then_read_distilled_facts(tmp_path, monkeypatch):
    """write_distilled_facts → read_distilled_facts 文本一致."""
    fake_path = tmp_path / "distilled_facts.md"
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", fake_path)

    text = "- 老板: 张总, 偏好简短\n- 项目: EIS 资质管理"
    memory_distill.write_distilled_facts(text)

    assert fake_path.exists()
    got = memory_distill.read_distilled_facts()
    assert got == text  # write 自带 strip + 末尾 \n, read 再 strip 应该等于原 text


def test_read_distilled_facts_missing_returns_empty(tmp_path, monkeypatch):
    """文件不存在 → 返空字符串, 不抛."""
    monkeypatch.setattr(
        memory_distill, "DISTILLED_FACTS_PATH",
        tmp_path / "does_not_exist.md",
    )
    assert memory_distill.read_distilled_facts() == ""


def test_write_distilled_facts_creates_parent_dir(tmp_path, monkeypatch):
    """父目录不存在 → 自动建."""
    nested = tmp_path / "deep" / "nested" / "distilled_facts.md"
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", nested)
    memory_distill.write_distilled_facts("test")
    assert nested.exists()


# ── inject_employee_journal 两段式 ─────────────────────


def test_inject_with_both_distilled_and_journal(tmp_path, monkeypatch):
    """distilled + journal 都存在 → 都被注入, distilled 在前 (A 段), journal 在后 (B 段)."""
    journal_path = tmp_path / "employee_journal.md"
    journal_path.write_text("## 2026-05-15 14:00\n最近写的 journal 段")
    distilled_path = tmp_path / "distilled_facts.md"
    distilled_path.write_text("- 老板: 张总\n- 项目: EIS")

    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", distilled_path)

    out = inject_employee_journal([{"role": "system", "content": "原 system"}])
    content = out[0]["content"]
    # A 段在 B 段前
    a_idx = content.find("A. 长期事实")
    b_idx = content.find("B. 最近 journal")
    assert a_idx > 0, "A 段必须存在"
    assert b_idx > a_idx, "B 段必须在 A 段后"
    # 内容都到了
    assert "老板: 张总" in content
    assert "项目: EIS" in content
    assert "最近写的 journal 段" in content


def test_inject_with_only_journal_no_distilled(tmp_path, monkeypatch):
    """没 distilled 但有 journal → 只注入 B 段, 不报错."""
    journal_path = tmp_path / "employee_journal.md"
    journal_path.write_text("## 2026-05-15\njournal only")
    distilled_path = tmp_path / "distilled_facts.md"  # 不创建

    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", distilled_path)

    out = inject_employee_journal([{"role": "system", "content": "原"}])
    content = out[0]["content"]
    assert "B. 最近 journal" in content
    assert "A. 长期事实" not in content
    assert "journal only" in content


def test_inject_with_only_distilled_no_journal(tmp_path, monkeypatch):
    """有 distilled 但 journal 空 → 只注入 A 段."""
    journal_path = tmp_path / "employee_journal.md"  # 不创建
    distilled_path = tmp_path / "distilled_facts.md"
    distilled_path.write_text("- 关键事实")

    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", distilled_path)

    out = inject_employee_journal([{"role": "system", "content": "原"}])
    content = out[0]["content"]
    assert "A. 长期事实" in content
    assert "B. 最近 journal" not in content
    assert "关键事实" in content


def test_inject_both_empty_no_change(tmp_path, monkeypatch):
    """都没 → 原样返."""
    journal_path = tmp_path / "j.md"
    distilled_path = tmp_path / "d.md"
    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", distilled_path)
    msgs = [{"role": "system", "content": "原"}]
    assert inject_employee_journal(msgs) == msgs


def test_inject_idempotent(tmp_path, monkeypatch):
    """重复 inject → 第二次幂等不重复注入."""
    journal_path = tmp_path / "j.md"
    journal_path.write_text("## a\nb")
    distilled_path = tmp_path / "d.md"
    distilled_path.write_text("- c")
    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", distilled_path)

    msgs = [{"role": "system", "content": "原"}]
    once = inject_employee_journal(msgs)
    twice = inject_employee_journal(once)
    # 第二次不再追加
    assert once[0]["content"] == twice[0]["content"]


# ── maybe_run_llm_distillation 异步流程 ─────────────────


@pytest.mark.asyncio
async def test_llm_distillation_skips_when_journal_short(tmp_path, monkeypatch):
    """journal < INJECT_MAX_BYTES → 没"老段"可蒸馏, 直接 skip."""
    journal_path = tmp_path / "j.md"
    journal_path.write_text("## a\n短 journal")
    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", tmp_path / "d.md")
    monkeypatch.setattr(memory_distill, "DISTILL_STATE_PATH", tmp_path / "s.json")
    # 强制满足触发条件 (entry 数 ≥ DISTILL_THRESHOLD, monkeypatch 阈值降到 1)
    monkeypatch.setattr(memory_distill, "DISTILL_THRESHOLD", 1)

    result = await memory_distill.maybe_run_llm_distillation()
    # journal 太短没"老段", skip
    assert result is None


@pytest.mark.asyncio
async def test_llm_distillation_calls_loopback_and_writes_facts(tmp_path, monkeypatch):
    """有老段 → 调 _llm_distill_chunk → 写 distilled_facts.md."""
    # 造一个 30KB journal (> 5KB INJECT_MAX_BYTES) 让 only_old_segments 有内容蒸馏
    big_journal = "\n\n".join(
        f"## 2026-05-{i:02d} 10:00 - test\n这是第 {i} 段内容. " + "x" * 200
        for i in range(1, 50)
    )
    journal_path = tmp_path / "j.md"
    journal_path.write_text(big_journal)

    distilled_path = tmp_path / "d.md"
    state_path = tmp_path / "s.json"

    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", distilled_path)
    monkeypatch.setattr(memory_distill, "DISTILL_STATE_PATH", state_path)
    monkeypatch.setattr(memory_distill, "DISTILL_THRESHOLD", 1)

    # mock _llm_distill_chunk 返固定 markdown
    fake_chunk_result = "- 老板: 张总\n- 项目: EIS 资质管理"
    monkeypatch.setattr(
        memory_distill, "_llm_distill_chunk",
        AsyncMock(return_value=fake_chunk_result),
    )

    result = await memory_distill.maybe_run_llm_distillation()
    assert result is not None
    assert fake_chunk_result in result
    # 文件已落盘
    assert distilled_path.exists()
    on_disk = distilled_path.read_text(encoding="utf-8")
    assert fake_chunk_result in on_disk
    # state 已标 (24h cooldown)
    assert state_path.exists()


@pytest.mark.asyncio
async def test_llm_distillation_all_chunks_fail_no_write(tmp_path, monkeypatch):
    """所有 chunk LLM 都返 None → 不写 distilled_facts.md (不污染老数据)."""
    big_journal = "\n\n".join(
        f"## 2026-05-{i:02d} 10:00\n" + "x" * 500 for i in range(1, 30)
    )
    journal_path = tmp_path / "j.md"
    journal_path.write_text(big_journal)

    distilled_path = tmp_path / "d.md"
    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", distilled_path)
    monkeypatch.setattr(memory_distill, "DISTILL_STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(memory_distill, "DISTILL_THRESHOLD", 1)

    monkeypatch.setattr(
        memory_distill, "_llm_distill_chunk", AsyncMock(return_value=None),
    )

    result = await memory_distill.maybe_run_llm_distillation()
    assert result is None
    assert not distilled_path.exists()


# ── BL-MEMORY-DISTILL-DEDUP 后处理去重 ─────────────────


def test_dedup_exact_duplicate_line_truncates():
    """完全相同行第二次出现 → 截到首次."""
    from catfish_gateway.memory_distill import _dedup_distilled_lines

    text = """- 老板: 张总
- 项目: EIS
- 老板: 张总
- 决策: X"""  # "- 老板: 张总" 重复
    out = _dedup_distilled_lines(text)
    # 截到首次出现点 (第 1 行), 后面全丢
    assert "- 老板: 张总" in out
    assert "- 决策: X" not in out
    assert out.count("- 老板: 张总") == 1


def test_dedup_same_prefix_streak_truncates():
    """同前缀连续 ≥ 6 行 → 截到第一次该前缀."""
    from catfish_gateway.memory_distill import _dedup_distilled_lines

    lines = ["- 项目: A", "- 项目: B"] + [
        f"- 偏好: 使用 tool_{i}" for i in range(10)
    ]
    text = "\n".join(lines)
    out = _dedup_distilled_lines(text)
    # 截到 streak 第一行 ("- 偏好: 使用 tool_0")
    assert "- 项目: A" in out
    assert "- 项目: B" in out
    assert "- 偏好: 使用 tool_0" in out
    # 后面 5+ 个不该有
    assert "tool_9" not in out
    assert "tool_8" not in out


def test_dedup_normal_content_passes_through():
    """没重复模式 → 原样返."""
    from catfish_gateway.memory_distill import _dedup_distilled_lines

    text = """- 老板: 张总
- 同事: 周园
- 项目: EIS
- 偏好: 列表型
- 决策: X"""
    out = _dedup_distilled_lines(text)
    assert out.strip() == text.strip()


def test_dedup_handles_blank_lines():
    """空行不算同前缀, 不该误截."""
    from catfish_gateway.memory_distill import _dedup_distilled_lines

    text = """- 项目: A

### 子标题

- 偏好: B"""
    out = _dedup_distilled_lines(text)
    assert "- 项目: A" in out
    assert "- 偏好: B" in out


def test_dedup_empty_returns_empty():
    from catfish_gateway.memory_distill import _dedup_distilled_lines
    assert _dedup_distilled_lines("") == ""


# ── BL-MEMORY-POLISH cross-chunk dedup ─────────────────


def test_cross_chunk_dedup_removes_duplicate_bullets():
    """同 bullet 关键词前缀重复 → 第二次出现被 skip."""
    from catfish_gateway.memory_distill import _cross_chunk_dedup

    parts = [
        "### 段 1\n\n- 同事: 周园 (本地网对接人)\n- 项目: EIS",
        "### 段 2\n\n- 同事: 周园 (本地网对接人)\n- 决策: 投资策略",
    ]
    out = _cross_chunk_dedup(parts)
    # "同事: 周园" 只出现 1 次
    assert out.count("同事: 周园") == 1
    # 不重复的都在
    assert "项目: EIS" in out
    assert "决策: 投资策略" in out


def test_cross_chunk_dedup_filters_chatty_prefixes():
    """LLM 寒暄开场白被过滤."""
    from catfish_gateway.memory_distill import _cross_chunk_dedup

    parts = [
        "好的, 鸿波。根据 session 历史:\n- 项目: EIS",
        "整理如下\n- 决策: X",
    ]
    out = _cross_chunk_dedup(parts)
    assert "好的" not in out
    assert "根据 session" not in out
    assert "整理如下" not in out
    # bullet 仍在
    assert "项目: EIS" in out
    assert "决策: X" in out


def test_cross_chunk_dedup_preserves_section_headers():
    """### 标题保留, 不当寒暄滤."""
    from catfish_gateway.memory_distill import _cross_chunk_dedup

    parts = ["### 蒸馏段 1\n\n- A"]
    out = _cross_chunk_dedup(parts)
    assert "### 蒸馏段 1" in out


def test_cross_chunk_dedup_empty_returns_empty():
    from catfish_gateway.memory_distill import _cross_chunk_dedup
    assert _cross_chunk_dedup([]) == ""


# ── BL-MEMORY-POLISH Chunk 编号用 len 而非 i+1 ──────────


@pytest.mark.asyncio
async def test_llm_distillation_skipped_chunk_no_gap_in_numbering(tmp_path, monkeypatch):
    """LLM 某 chunk 返 None 失败 → output 编号不跳 (用 len 不用 i+1)."""
    big_journal = "\n\n".join(
        f"## 2026-05-{i:02d}\n" + "x" * 800 for i in range(1, 40)
    )
    journal_path = tmp_path / "j.md"
    journal_path.write_text(big_journal)

    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", tmp_path / "d.md")
    monkeypatch.setattr(memory_distill, "DISTILL_STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(memory_distill, "DISTILL_THRESHOLD", 1)

    # mock: 第 1 个 chunk 返 None 失败, 第 2/3 成功
    call_count = {"n": 0}

    async def fake_distill(chunk, *, timeout=60.0):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # 第 1 chunk 失败
        return f"- 事实 {call_count['n']}"

    monkeypatch.setattr(memory_distill, "_llm_distill_chunk", fake_distill)

    result = await memory_distill.maybe_run_llm_distillation()
    assert result is not None
    # 编号该是 1, 2 (不是 2, 3) — 跳过失败的不留空号
    assert "蒸馏段 1" in result
    assert "蒸馏段 2" in result
    # "蒸馏段 3" 可以有 (如果第 3 chunk 也成功); 关键是不跳号
    # 用 chunk 总数判断: 应该有 N-1 个段 (跳了 1 个失败)


@pytest.mark.asyncio
async def test_llm_distillation_24h_cooldown(tmp_path, monkeypatch):
    """24h 内重跑 → skip (复用 should_run_distillation 逻辑)."""
    big_journal = "\n\n".join(
        f"## 2026-05-{i:02d}\n" + "x" * 500 for i in range(1, 30)
    )
    journal_path = tmp_path / "j.md"
    journal_path.write_text(big_journal)

    state_path = tmp_path / "s.json"
    # 写一个 1 小时前的 state
    import json as _json
    state_path.write_text(_json.dumps({"last_run_ts": time.time() - 3600}))

    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: journal_path,
    )
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", tmp_path / "d.md")
    monkeypatch.setattr(memory_distill, "DISTILL_STATE_PATH", state_path)
    monkeypatch.setattr(memory_distill, "DISTILL_THRESHOLD", 1)

    mock_llm = AsyncMock(return_value="should-not-be-called")
    monkeypatch.setattr(memory_distill, "_llm_distill_chunk", mock_llm)

    result = await memory_distill.maybe_run_llm_distillation()
    assert result is None
    mock_llm.assert_not_called()  # cooldown 触发, LLM 没被调
