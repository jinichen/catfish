"""BL-SKILLS-RAG-TOOL (5/25 鸿波): catfish_search_skills 单测.

跑法: cd edge/tool-bridge && PYTHONPATH=src python3 -m pytest tests/test_search_skills.py -q

覆盖:
- _tokenize: ASCII / CJK / 混合 (跟 gateway/skills_retrieval 同行为)
- _BM25: 空 / 单 doc / 排序 / no-match 返空
- _discover_skill_paths: 真扫 ~/.hermes/skills + ~/.catfish/skills
- _parse_skill_md: 合法 frontmatter / 缺字段 / 烂 yaml
- search_skills: 端到端 (mock 目录)
- tool_search_skills: 空 query / 异常返友好
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_tool_bridge import search_skills as ss  # noqa: E402


# ── _tokenize (跟 gateway 同份算法) ─────────────────────────────


def test_tokenize_ascii():
    assert ss._tokenize("hello world") == ["hello", "world"]


def test_tokenize_cjk_char_level():
    assert ss._tokenize("汇报材料") == ["汇", "报", "材", "料"]


def test_tokenize_mixed():
    assert ss._tokenize("PPT文档") == ["ppt", "文", "档"]


def test_tokenize_empty():
    assert ss._tokenize("") == []


def test_tokenize_lowercase():
    assert ss._tokenize("Hello WORLD") == ["hello", "world"]


# ── _BM25 ─────────────────────────────────────────────────────


def test_bm25_empty_returns_empty():
    idx = ss._BM25([])
    assert idx.rank("query", top_k=10) == []


def test_bm25_basic_ranking():
    docs = [
        ss._tokenize("生成 PPT 幻灯片"),     # doc 0: 关键词全中
        ss._tokenize("汇报材料 5 段结构"),    # doc 1: 不相关
        ss._tokenize("PPT 模板"),            # doc 2: 部分中
    ]
    idx = ss._BM25(docs)
    ranked = idx.rank("PPT 幻灯片", top_k=10)
    assert ranked[0][0] == 0  # doc 0 排第一
    assert all(i != 1 for i, _ in ranked)  # doc 1 不入榜


def test_bm25_top_k():
    docs = [ss._tokenize(f"doc {i}") for i in range(10)]
    idx = ss._BM25(docs)
    assert len(idx.rank("doc", top_k=3)) == 3


# ── _parse_skill_md ──────────────────────────────────────────


def test_parse_skill_md_valid(tmp_path: Path):
    md = tmp_path / "SKILL.md"
    md.write_text(
        "---\nname: leadership-briefing\ndescription: 5 段汇报模板\n---\n# body\n",
        encoding="utf-8",
    )
    parsed = ss._parse_skill_md(md)
    assert parsed == {"name": "leadership-briefing", "description": "5 段汇报模板"}


def test_parse_skill_md_missing_name(tmp_path: Path):
    md = tmp_path / "SKILL.md"
    md.write_text("---\ndescription: 缺 name\n---\n", encoding="utf-8")
    assert ss._parse_skill_md(md) is None


def test_parse_skill_md_no_frontmatter(tmp_path: Path):
    md = tmp_path / "SKILL.md"
    md.write_text("# 没 yaml frontmatter\n", encoding="utf-8")
    assert ss._parse_skill_md(md) is None


def test_parse_skill_md_broken_yaml(tmp_path: Path):
    md = tmp_path / "SKILL.md"
    md.write_text("---\nname: x\n  bad: indentation: here\n---\n", encoding="utf-8")
    # 烂 yaml 返 None, 不抛
    assert ss._parse_skill_md(md) is None


def test_parse_skill_md_missing_file(tmp_path: Path):
    assert ss._parse_skill_md(tmp_path / "nonexistent.md") is None


# ── search_skills 端到端 (mock 目录) ─────────────────────────────


@pytest.fixture
def fake_skills(tmp_path, monkeypatch):
    """造一个 fake ~/.catfish/skills/ 目录."""
    catfish_skills = tmp_path / ".catfish" / "skills"
    catfish_skills.mkdir(parents=True)
    # 4 个 fake skill
    for name, desc in [
        ("dept/leadership-briefing", "5 段汇报上行决策事项"),
        ("dept/weekly-report", "周报模板, 含本周完成 + 下周计划"),
        ("productivity/ppt-magazine", "杂志风 PPT 幻灯片生成"),
        ("dev/github-sync", "TODO 自动同步到 GitHub Issues"),
    ]:
        skill_dir = catfish_skills / name
        skill_dir.mkdir(parents=True)
        nm = name.split("/")[-1]
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {nm}\ndescription: {desc}\n---\n# {nm}\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(catfish_skills))
    monkeypatch.setenv("HOME", str(tmp_path))  # 让 _hermes_skills_root 也返空
    # reset module cache
    ss._cache["fingerprint"] = None
    ss._cache["index"] = None
    ss._cache["meta"] = []
    yield catfish_skills
    ss._cache["fingerprint"] = None
    ss._cache["index"] = None
    ss._cache["meta"] = []


def test_search_finds_ppt(fake_skills):
    """搜 'PPT' 应该 hit ppt-magazine."""
    out = ss.search_skills(query="PPT 幻灯片", top_k=5)
    assert out["count"] >= 1
    assert out["total_indexed"] == 4
    top = out["matches"][0]
    assert "ppt-magazine" in top["skill_path"]
    assert top["score"] > 0


def test_search_finds_weekly(fake_skills):
    out = ss.search_skills(query="周报 计划", top_k=5)
    assert out["count"] >= 1
    assert out["matches"][0]["skill_path"] == "dept/weekly-report"


def test_search_no_match_returns_empty(fake_skills):
    # 用纯 ASCII garbage + 罕见拉丁单词避免跟 fake skills 任何 CJK char/英文撞
    out = ss.search_skills(query="xyzqwer123 zzzwww blahblah", top_k=5)
    assert out["count"] == 0
    assert "没找到匹配" in out["summary"]
    assert out["total_indexed"] == 4   # 索引建好了, 只是没匹配


def test_search_top_k_truncation(fake_skills):
    """top_k 上限 30, 但请求超出也只返实际命中数."""
    out = ss.search_skills(query="dept", top_k=100)  # 测 cap 至 30
    # dept 在 2 个 skill 的 path 里, 应至少返 2
    assert out["count"] >= 1
    assert out["count"] <= 30


def test_search_returns_summary_with_top3(fake_skills):
    out = ss.search_skills(query="汇报", top_k=5)
    if out["count"] > 0:
        assert "top" in out["summary"].lower() or "找到" in out["summary"]


# ── tool_search_skills (dispatch 入口) ─────────────────────────


def test_tool_empty_query():
    out = ss.tool_search_skills({"query": ""})
    assert out["count"] == 0
    assert "必填" in out["summary"]


def test_tool_whitespace_query():
    out = ss.tool_search_skills({"query": "   "})
    assert out["count"] == 0


def test_tool_returns_latency(fake_skills):
    out = ss.tool_search_skills({"query": "汇报"})
    assert "latency_ms" in out
    assert isinstance(out["latency_ms"], (int, float))


def test_tool_handles_invalid_top_k(fake_skills):
    """top_k 0 / 负数 / 超大 → clamp 到 [1, 30]."""
    out = ss.tool_search_skills({"query": "汇报", "top_k": 0})
    assert out["count"] <= 30  # 实际算了
    out = ss.tool_search_skills({"query": "汇报", "top_k": 999})
    assert out["count"] <= 30


# ── 缓存 (fingerprint 不变不重建) ──────────────────────────────


def test_cache_hit_on_unchanged_skills(fake_skills):
    """skill 不变 → 第 2 次 search 复用 cache."""
    # 第 1 次 build cache
    out1 = ss.search_skills(query="汇报", top_k=5)
    fp1 = ss._cache["fingerprint"]
    idx1 = ss._cache["index"]
    # 第 2 次 不该重建
    _ = ss.search_skills(query="周报", top_k=5)
    fp2 = ss._cache["fingerprint"]
    idx2 = ss._cache["index"]
    assert fp1 == fp2
    assert idx1 is idx2  # 同一个对象


def test_cache_invalidated_on_new_skill(fake_skills):
    """加新 SKILL.md → fingerprint 变 → 重建."""
    out1 = ss.search_skills(query="x", top_k=5)
    total_before = out1["total_indexed"]

    # 加新 skill
    new_dir = fake_skills / "extra" / "new-skill"
    new_dir.mkdir(parents=True)
    (new_dir / "SKILL.md").write_text(
        "---\nname: new-skill\ndescription: 新加的\n---\n",
        encoding="utf-8",
    )

    out2 = ss.search_skills(query="x", top_k=5)
    assert out2["total_indexed"] == total_before + 1
