"""P3.5.35 (6/18 鸿波 catch 'chat 没接 wiki_search + 装到本机后部门 wiki 不就是自家了吗')
catfish_wiki_search 单测.

跑法: cd edge/tool-bridge && PYTHONPATH=src python3 -m pytest tests/test_wiki_search.py -q

覆盖:
- _wiki_own_dirs / _wiki_shared_dept_dirs / _discover_all_paths 扫两套目录
- _parse_wiki frontmatter title/type 抽
- search_wiki 自家命中 / 部门命中 / 跨源联合
- source 字段区分 ("own" vs "dept/<部门>")
- top_k clamp
- 空 query / 空目录 fallback
- tool_wiki_search 异常 path
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_tool_bridge import wiki_search as ws  # noqa: E402


# ── 测试用 fixture: tmp ~/.catfish ──────────────────────────────


@pytest.fixture
def tmp_catfish(tmp_path: Path, monkeypatch):
    """建临时 ~/.catfish 目录, 用 env CATFISH_HOME 重定向."""
    catfish = tmp_path / ".catfish"
    catfish.mkdir()
    (catfish / "wiki" / "entities").mkdir(parents=True)
    (catfish / "wiki" / "concepts").mkdir(parents=True)
    (catfish / "wiki" / "queries").mkdir(parents=True)
    (catfish / "wiki-shared" / "dept" / "finance").mkdir(parents=True)
    (catfish / "wiki-shared" / "dept" / "sales").mkdir(parents=True)

    monkeypatch.setenv("CATFISH_HOME", str(catfish))
    # 重置 BM25 cache 防上一 test 污染
    ws._cache.update({"fingerprint": "", "index": None, "meta": []})
    return catfish


def _write_wiki(path: Path, title: str, kind: str, body: str, tags: list[str] | None = None) -> None:
    """写一个 wiki MD 含 frontmatter."""
    tags_yaml = ""
    if tags:
        tags_yaml = "tags: [" + ", ".join(tags) + "]\n"
    content = f"""---
title: {title}
type: {kind}
{tags_yaml}---

{body}
"""
    path.write_text(content, encoding="utf-8")


# ── _wiki_own_dirs / _wiki_shared_dept_dirs ─────────────────────


def test_own_dirs_returns_three(tmp_catfish):
    dirs = ws._wiki_own_dirs()
    assert len(dirs) == 3
    names = [d.name for d in dirs]
    assert "entities" in names
    assert "concepts" in names
    assert "queries" in names


def test_shared_dept_dirs_returns_each_dept(tmp_catfish):
    dept_dirs = ws._wiki_shared_dept_dirs()
    assert len(dept_dirs) == 2
    names = sorted(d[1] for d in dept_dirs)
    assert names == ["finance", "sales"]


def test_shared_dept_dirs_handles_missing(tmp_path, monkeypatch):
    """wiki-shared/dept 不存在不挂, 返 []."""
    catfish = tmp_path / "no-shared"
    catfish.mkdir()
    (catfish / "wiki" / "entities").mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(catfish))
    assert ws._wiki_shared_dept_dirs() == []


# ── _discover_all_paths 扫两套 ────────────────────────────────


def test_discover_all_paths_own_only(tmp_catfish):
    _write_wiki(tmp_catfish / "wiki" / "entities" / "openai.md", "OpenAI", "entity", "OpenAI 是 LLM 公司.")
    paths = ws._discover_all_paths()
    assert len(paths) == 1
    assert paths[0][1] == "own"


def test_discover_all_paths_dept_only(tmp_catfish):
    _write_wiki(
        tmp_catfish / "wiki-shared" / "dept" / "finance" / "abc.md",
        "财务流程", "concept", "财务报销规则.",
    )
    paths = ws._discover_all_paths()
    assert len(paths) == 1
    assert paths[0][1] == "dept/finance"


def test_discover_all_paths_mixed(tmp_catfish):
    """自家 + 多部门混合."""
    _write_wiki(tmp_catfish / "wiki" / "entities" / "alice.md", "Alice", "entity", "同事")
    _write_wiki(tmp_catfish / "wiki" / "concepts" / "agile.md", "Agile", "concept", "敏捷")
    _write_wiki(
        tmp_catfish / "wiki-shared" / "dept" / "finance" / "tax.md",
        "税务", "concept", "增值税",
    )
    _write_wiki(
        tmp_catfish / "wiki-shared" / "dept" / "sales" / "crm.md",
        "CRM", "entity", "客户关系管理",
    )
    paths = ws._discover_all_paths()
    sources = sorted(s for _, s in paths)
    assert sources == ["dept/finance", "dept/sales", "own", "own"]


# ── _parse_wiki ─────────────────────────────────────────────────


def test_parse_wiki_extracts_frontmatter_title(tmp_catfish):
    p = tmp_catfish / "wiki" / "entities" / "openai.md"
    _write_wiki(p, "OpenAI 公司", "entity", "美国 LLM 公司")
    parsed = ws._parse_wiki(p, "own")
    assert parsed is not None
    assert parsed["title"] == "OpenAI 公司"
    assert parsed["kind"] == "entity"
    assert parsed["source"] == "own"
    assert "美国 LLM" in parsed["head"]
    assert parsed["rel_path"] == "wiki/entities/openai.md"


def test_parse_wiki_fallback_kind_from_path(tmp_catfish):
    """没 type field 时, 从路径推 kind (wiki/concepts/* → concept)."""
    p = tmp_catfish / "wiki" / "concepts" / "agile.md"
    p.write_text("# Agile\n\nNo frontmatter at all.", encoding="utf-8")
    parsed = ws._parse_wiki(p, "own")
    assert parsed is not None
    assert parsed["kind"] == "concept"


def test_parse_wiki_dept_path_default_entity(tmp_catfish):
    """装机部门 wiki 无 frontmatter type, 不在自家路径里, fallback entity."""
    p = tmp_catfish / "wiki-shared" / "dept" / "finance" / "raw.md"
    p.write_text("没有 frontmatter", encoding="utf-8")
    parsed = ws._parse_wiki(p, "dept/finance")
    assert parsed is not None
    assert parsed["kind"] == "entity"  # fallback


# ── search_wiki 端到端 ─────────────────────────────────────────


def test_search_wiki_empty_dir(tmp_catfish):
    """目录全空 → matches=0 + summary 提示."""
    res = ws.search_wiki("anything")
    assert res["count"] == 0
    assert res["total_indexed"] == 0
    assert "空" in res["summary"]


def test_search_wiki_own_hit(tmp_catfish):
    _write_wiki(
        tmp_catfish / "wiki" / "entities" / "openai.md",
        "OpenAI", "entity",
        "OpenAI 是美国 LLM 公司, 创立于 2015 年.",
    )
    res = ws.search_wiki("openai")
    assert res["count"] >= 1
    m = res["matches"][0]
    assert m["title"] == "OpenAI"
    assert m["source"] == "own"
    assert m["kind"] == "entity"


def test_search_wiki_dept_hit(tmp_catfish):
    _write_wiki(
        tmp_catfish / "wiki-shared" / "dept" / "finance" / "tax.md",
        "税务规则", "concept",
        "增值税申报每月 15 号前完成.",
    )
    res = ws.search_wiki("增值税")
    assert res["count"] >= 1
    m = res["matches"][0]
    assert m["source"] == "dept/finance"
    assert m["kind"] == "concept"


def test_search_wiki_cross_source(tmp_catfish):
    """自家 + 部门同时命中, 返一起."""
    _write_wiki(
        tmp_catfish / "wiki" / "entities" / "alice.md",
        "Alice", "entity", "Alice 处理财务.",
    )
    _write_wiki(
        tmp_catfish / "wiki-shared" / "dept" / "finance" / "rule.md",
        "财务规则", "concept", "财务报销三联单制度",
    )
    res = ws.search_wiki("财务")
    assert res["count"] >= 2
    sources = {m["source"] for m in res["matches"]}
    assert "own" in sources
    assert "dept/finance" in sources


def test_search_wiki_top_k_clamp(tmp_catfish):
    """top_k 上限 15."""
    for i in range(20):
        _write_wiki(
            tmp_catfish / "wiki" / "entities" / f"e{i}.md",
            f"E{i}", "entity", f"entity 测试 {i}",
        )
    res = ws.search_wiki("entity", top_k=100)
    assert res["count"] <= 15


def test_search_wiki_summary_mentions_dept_count(tmp_catfish):
    """summary 区分自家 / 部门 wiki 命中数."""
    _write_wiki(
        tmp_catfish / "wiki" / "entities" / "a.md",
        "A", "entity", "common word",
    )
    _write_wiki(
        tmp_catfish / "wiki-shared" / "dept" / "finance" / "b.md",
        "B", "entity", "common word",
    )
    res = ws.search_wiki("common")
    assert "自家" in res["summary"]
    assert "部门" in res["summary"]


# ── tool_wiki_search ────────────────────────────────────────────


def test_tool_wiki_search_empty_query():
    """空 query 返友好 message."""
    res = ws.tool_wiki_search({"query": ""})
    assert res["count"] == 0
    assert "query 空" in res["summary"]


def test_tool_wiki_search_passes_top_k(tmp_catfish):
    """args.top_k 传给 search_wiki."""
    for i in range(5):
        _write_wiki(
            tmp_catfish / "wiki" / "entities" / f"x{i}.md",
            f"X{i}", "entity", "xword",
        )
    res = ws.tool_wiki_search({"query": "xword", "top_k": 2})
    assert res["count"] <= 2
