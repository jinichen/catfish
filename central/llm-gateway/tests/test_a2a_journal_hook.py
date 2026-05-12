"""测试 BL-FED2.4 (5/12) — A2A 反馈环 journal hook.

主要保护的不变量:
1. 写出的条目带 [a2a-help] 标记 (后续 expertise extract 识别加权)
2. 答案截断到 100 字符 (隐私: 不留完整答案落盘)
3. 问题截断到 200 字符
4. 失败静默 (不抛异常 — a2a 主流程不能因 journal 失败而 500)
5. 缺 from_sub / question 跳过
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from catfish_gateway.a2a_journal_hook import append_a2a_help_entry


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch):
    """每个测试独立 employee_journal.md.

    employee_journal.py 把 JOURNAL_PATH 写在模块顶层 (Path.home() 那一刻就算),
    所以必须 monkeypatch journal_path() 函数本身 — 那是给测试准备的钩子.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    fake_journal = fake_home / ".catfish" / "employee_journal.md"
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.journal_path",
        lambda: fake_journal,
    )
    yield fake_home


def _journal_path(home: Path) -> Path:
    return home / ".catfish" / "employee_journal.md"


# ── 基本写入 ──────────────────────────────────────────────


def test_basic_append(isolated_home: Path):
    ok = append_a2a_help_entry(
        from_sub="alice@ffcs.cn",
        question="资质审核怎么搞?",
        purpose="expert_consult:资质审核",
        answer_preview="走 OA 工单, 类目选合规.",
        chunks_count=3,
        duration_ms=1500,
    )
    assert ok is True
    p = _journal_path(isolated_home)
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "[a2a-help]" in content
    assert "alice@ffcs.cn" in content
    assert "expert_consult:资质审核" in content
    assert "资质审核怎么搞?" in content
    assert "走 OA 工单" in content
    assert "chunks: 3" in content
    assert "1500ms" in content


def test_question_truncated_to_200(isolated_home: Path):
    long_q = "x" * 500
    ok = append_a2a_help_entry(
        from_sub="alice@ffcs.cn",
        question=long_q,
    )
    assert ok is True
    content = _journal_path(isolated_home).read_text(encoding="utf-8")
    # 200 char + "..."
    assert "x" * 200 in content
    assert "x" * 201 not in content
    assert "..." in content


def test_answer_truncated_to_100(isolated_home: Path):
    long_ans = "y" * 300
    append_a2a_help_entry(
        from_sub="alice@ffcs.cn",
        question="q",
        answer_preview=long_ans,
    )
    content = _journal_path(isolated_home).read_text(encoding="utf-8")
    assert "y" * 100 in content
    assert "y" * 101 not in content


def test_no_answer_preview_shows_placeholder(isolated_home: Path):
    """无 answer_preview 显示 (流式, 未保留)."""
    append_a2a_help_entry(
        from_sub="alice@ffcs.cn",
        question="q",
        answer_preview="",
    )
    content = _journal_path(isolated_home).read_text(encoding="utf-8")
    assert "(流式, 未保留)" in content


def test_no_purpose_shows_placeholder(isolated_home: Path):
    append_a2a_help_entry(
        from_sub="alice@ffcs.cn",
        question="q",
    )
    content = _journal_path(isolated_home).read_text(encoding="utf-8")
    assert "(未指定)" in content


def test_skip_when_from_sub_empty(isolated_home: Path):
    """没 from_sub → 不写, 返 False."""
    ok = append_a2a_help_entry(from_sub="", question="q")
    assert ok is False
    assert not _journal_path(isolated_home).exists()


def test_skip_when_question_empty(isolated_home: Path):
    ok = append_a2a_help_entry(from_sub="alice@ffcs.cn", question="")
    assert ok is False
    assert not _journal_path(isolated_home).exists()


def test_multiple_appends_accumulate(isolated_home: Path):
    """连写两条, journal 应该有两条 ## 标题."""
    append_a2a_help_entry(from_sub="alice@ffcs.cn", question="q1", chunks_count=1)
    append_a2a_help_entry(from_sub="bob@ffcs.cn", question="q2", chunks_count=2)
    content = _journal_path(isolated_home).read_text(encoding="utf-8")
    assert content.count("[a2a-help]") == 2
    assert "alice@ffcs.cn" in content
    assert "bob@ffcs.cn" in content


def test_explicit_timestamp(isolated_home: Path):
    """传 timestamp 必须出现在条目里."""
    ts = datetime(2026, 5, 12, 14, 30)
    append_a2a_help_entry(
        from_sub="alice@ffcs.cn",
        question="q",
        timestamp=ts,
    )
    content = _journal_path(isolated_home).read_text(encoding="utf-8")
    assert "2026-05-12 14:30" in content


def test_silently_swallows_journal_io_failure(monkeypatch, isolated_home: Path):
    """append_to_journal 抛异常时, 函数返 False 不传染 a2a 主流程."""
    def boom(entry: str) -> None:
        raise IOError("disk full")
    monkeypatch.setattr(
        "catfish_gateway.employee_journal.append_to_journal",
        boom,
    )
    ok = append_a2a_help_entry(
        from_sub="alice@ffcs.cn",
        question="q",
    )
    assert ok is False


def test_format_has_required_fields(isolated_home: Path):
    """journal entry 应该有所有结构化字段, 让 expertise extract 能解析."""
    append_a2a_help_entry(
        from_sub="bob@ffcs.cn",
        question="发票怎么开",
        purpose="expert_consult:财务",
        answer_preview="走 OA",
        chunks_count=5,
        duration_ms=900,
    )
    content = _journal_path(isolated_home).read_text(encoding="utf-8")
    # 关键标签 + 字段
    assert "[a2a-help]" in content
    assert "- 主题:" in content
    assert "- 问题:" in content
    assert "- 答案摘要:" in content
    assert "- chunks:" in content
    # ## 段落 prefix (跟 employee_journal 其他段一致)
    assert content.lstrip().startswith("## ")
