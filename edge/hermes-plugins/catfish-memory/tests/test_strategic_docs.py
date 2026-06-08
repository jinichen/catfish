"""Test _render_strategic_docs — 6/7 BL-STRATEGIC-DOC-SYNC.

跟 _render_skills_catalog 同 pattern. 测:
- 目录不存在 → 空
- 空目录 → 空
- 多个 doc, query 空 → 全注入字母序
- query 触发 → top-K Jaccard 排序 + cap 5KB
- frontmatter 去掉
- budget 截
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 路径 hack: catfish-memory plugin 不是常规 package, 走 conftest 相同模式
_PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_DIR))

from catfish_memory import CatfishMemoryProvider  # noqa: E402


@pytest.fixture
def provider():
    return CatfishMemoryProvider()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_no_docs_dir_returns_empty(tmp_path, provider):
    """~/.catfish/strategic_docs/ 不存在 → 空 str."""
    result = provider._render_strategic_docs(tmp_path, query="")
    assert result == ""


def test_empty_docs_dir_returns_empty(tmp_path, provider):
    """目录空 → 空."""
    (tmp_path / "strategic_docs").mkdir()
    result = provider._render_strategic_docs(tmp_path, query="")
    assert result == ""


def test_single_doc_renders(tmp_path, provider):
    """单 doc, query 空 → 注入."""
    _write(
        tmp_path / "strategic_docs" / "manifesto.md",
        "# Catfish Manifesto\n\n4 公理: 员工主权 / 数据零出端 / 中央 0 控制 / API 物理无能.\n",
    )
    result = provider._render_strategic_docs(tmp_path, query="")
    assert "📘 战略 / 设计 doc" in result
    assert "manifesto" in result
    assert "员工主权" in result
    assert "API 物理无能" in result


def test_frontmatter_stripped(tmp_path, provider):
    """markdown YAML frontmatter 去掉, 不进 LLM context."""
    _write(
        tmp_path / "strategic_docs" / "patent.md",
        "---\ntype: concept\ntitle: Patent Landscape\ncreated: 2026-06-07\n---\n\n核心 5 方向研究. catfish 主推方向 1+2.\n",
    )
    result = provider._render_strategic_docs(tmp_path, query="")
    # frontmatter 字段不该出现
    assert "type: concept" not in result
    assert "created: 2026-06-07" not in result
    # body 真内容该出现
    assert "核心 5 方向研究" in result


def test_multiple_docs_alphabetical_when_no_query(tmp_path, provider):
    """query 空 → 字母序全注入."""
    docs = tmp_path / "strategic_docs"
    _write(docs / "moat.md", "moat content")
    _write(docs / "advisory.md", "advisory content")
    _write(docs / "patent.md", "patent content")

    result = provider._render_strategic_docs(tmp_path, query="")
    # 验证 3 个都在
    assert "moat" in result
    assert "advisory" in result
    assert "patent" in result
    # 验证字母序 (advisory 在 moat 前, moat 在 patent 前)
    a_idx = result.index("### advisory")
    m_idx = result.index("### moat")
    p_idx = result.index("### patent")
    assert a_idx < m_idx < p_idx


def test_query_triggers_topk_sort(tmp_path, provider):
    """query 提到 manifesto → manifesto 排第一. 小 doc 全 fit budget, 不触发 cap."""
    docs = tmp_path / "strategic_docs"
    _write(docs / "patent.md", "patent landscape 5 方向 prior art 检索")
    _write(docs / "manifesto.md", "manifesto 4 公理 员工主权 数据零出端")
    _write(docs / "moat.md", "moat 7 Powers 战略分析")

    result = provider._render_strategic_docs(tmp_path, query="manifesto 公理")
    # manifesto 排第一 (name match + content match 都最高)
    m_idx = result.index("### manifesto")
    other_idx = min(
        result.index("### patent") if "### patent" in result else 10**9,
        result.index("### moat") if "### moat" in result else 10**9,
    )
    assert m_idx < other_idx, f"manifesto 应该排第一, 实际: {result[:200]}"
    # 3 份小 doc 都 fit budget, title 没 top-K 提示 (entries == candidates)
    # 想测 top-K hint 看 test_query_cap_triggers_topk_label


def test_query_cap_smaller_budget(tmp_path, provider):
    """query 触发 → cap 5000 (比无 query 的 8000 严)."""
    docs = tmp_path / "strategic_docs"
    # 写 3 份 each 2KB, 总 6KB > 5KB cap
    for name in ["a", "b", "c"]:
        _write(docs / f"{name}.md", "X" * 2000)

    result = provider._render_strategic_docs(tmp_path, query="something")
    # 应该 cap 5KB → 只 ~2 个进入 (2KB + 头部 + 标题)
    assert len(result) < 6000


def test_query_cap_triggers_topk_label(tmp_path, provider):
    """budget 截断 → title 加 'top N/M' 提示.

    数学 (修正): _read_text_safe(1500 byte) 走 truncate path 返
    `text[:max_bytes//3] + "\\n...[truncated]"` ≈ 516 char (不是 1500).
    entry = `### name\\n\\n{head}\\n` ≈ 525 char. query 触发 budget 5000 →
    9-10 份 fit. 写 12 份让最后 2-3 份 break (触发 top-K hint).
    """
    docs = tmp_path / "strategic_docs"
    # 写 12 份: budget 5000 / ~525 = ~9.5 fit → 9 entries + top 9/12 label
    for i in range(12):
        _write(docs / f"doc_{i:02d}.md", "X" * 2000)

    result = provider._render_strategic_docs(tmp_path, query="something")
    # entries < candidates → title 加 "top N/M"
    assert "top" in result, f"title 应有 top-K 提示: {result[:300]}"
    assert "/12" in result, f"应有 '/12' 总数提示: {result[:300]}"


def test_non_md_files_ignored(tmp_path, provider):
    """非 .md 文件忽略 (e.g. .pdf .txt 不进)."""
    docs = tmp_path / "strategic_docs"
    _write(docs / "real.md", "real markdown doc")
    _write(docs / "junk.txt", "should be ignored")
    _write(docs / "subdir" / "nested.md", "nested also ignored")

    result = provider._render_strategic_docs(tmp_path, query="")
    assert "real markdown doc" in result
    assert "should be ignored" not in result
    assert "nested also ignored" not in result  # subdirectories not scanned


def test_empty_file_skipped(tmp_path, provider):
    """空 .md 文件 → 跳过 (head 空), 不抛."""
    docs = tmp_path / "strategic_docs"
    _write(docs / "empty.md", "")
    _write(docs / "real.md", "real content")

    result = provider._render_strategic_docs(tmp_path, query="")
    assert "real content" in result
    # empty.md 不该出现 ### empty 段
    assert "### empty" not in result
