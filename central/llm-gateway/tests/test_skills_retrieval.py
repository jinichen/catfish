"""BL-SKILLS-RAG (5/25 鸿波): Phase 3 BM25 retrieval 单测.

跑法: cd central/llm-gateway && PYTHONPATH=src pytest tests/test_skills_retrieval.py -v

覆盖:
- tokenize: ASCII / CJK / 混合 / 标点 / 空 / 大小写归一
- BM25Index: 空 / 单 doc / 多 doc rank / 完全无匹配返空 / top_k 截断
- format_skills_block with user_query: 30+ skill 时走 RAG, 30 内全展示
- _extract_last_user_query: str content / multimodal list / 没 user
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_gateway.skills_loader import SkillMeta, format_skills_block  # noqa: E402
from catfish_gateway.skills_retrieval import BM25Index, tokenize  # noqa: E402


# ── tokenize ──────────────────────────────────────────────────


def test_tokenize_ascii_simple():
    assert tokenize("hello world") == ["hello", "world"]


def test_tokenize_lowercase():
    assert tokenize("Hello WORLD") == ["hello", "world"]


def test_tokenize_punctuation_split():
    # 标点切, 不当 token
    toks = tokenize("foo, bar; baz!")
    assert toks == ["foo", "bar", "baz"]


def test_tokenize_cjk_char_level():
    """中文 char-level — 每字一 token."""
    toks = tokenize("汇报材料")
    assert toks == ["汇", "报", "材", "料"]


def test_tokenize_mixed_ascii_cjk():
    """'PPT 文档' → ['ppt', '文', '档']."""
    toks = tokenize("PPT文档")
    assert toks == ["ppt", "文", "档"]


def test_tokenize_intermixed_within_piece():
    """单 piece 内交错也正确拆: 'foo中文bar' → ['foo', '中', '文', 'bar']."""
    toks = tokenize("foo中文bar")
    assert toks == ["foo", "中", "文", "bar"]


def test_tokenize_empty():
    assert tokenize("") == []
    assert tokenize("   ") == []


def test_tokenize_only_punctuation():
    assert tokenize(",.;:!?") == []


def test_tokenize_skill_path_like():
    """skill_path 含 / - 等应该切."""
    toks = tokenize("department/leadership-briefing 上行汇报")
    assert "department" in toks
    assert "leadership" in toks
    assert "briefing" in toks
    assert "上" in toks and "行" in toks and "汇" in toks and "报" in toks


# ── BM25Index ─────────────────────────────────────────────────


def test_bm25_empty_corpus_returns_empty():
    idx = BM25Index([])
    assert idx.rank("query") == []


def test_bm25_empty_query_returns_empty():
    idx = BM25Index([tokenize("汇报材料")])
    assert idx.rank("") == []
    assert idx.rank("   ") == []


def test_bm25_no_match_returns_empty():
    """query 词全不在 corpus → 返空, 不返 0 分项."""
    idx = BM25Index([tokenize("汇报材料")])
    ranked = idx.rank("PPT 幻灯片")
    assert ranked == []


def test_bm25_ranks_relevant_first():
    """相关度高的 doc 应排在前面."""
    docs = [
        tokenize("生成 PPT 幻灯片"),         # doc 0: 关键词全中
        tokenize("汇报材料 5 段结构"),        # doc 1: 不相关
        tokenize("PPT 模板"),                # doc 2: 部分中
    ]
    idx = BM25Index(docs)
    ranked = idx.rank("PPT 幻灯片")
    assert len(ranked) >= 1
    top_idx, _ = ranked[0]
    assert top_idx == 0, f"doc 0 ('PPT 幻灯片') 应排第一, 实际 {ranked}"
    # doc 1 不该入榜 (没匹配关键词)
    assert all(i != 1 for i, _ in ranked)


def test_bm25_top_k_truncation():
    """top_k 截断."""
    docs = [tokenize(f"测试 doc {i}") for i in range(10)]
    idx = BM25Index(docs)
    ranked = idx.rank("测试", top_k=3)
    assert len(ranked) <= 3


def test_bm25_idf_penalizes_common_terms():
    """常见词 (出现多 doc) IDF 低 — 罕见词权重更高."""
    docs = [
        tokenize("催办 上报 立项"),         # 罕见词 '催办'
        tokenize("立项 上报"),
        tokenize("立项 上报"),
        tokenize("立项 上报"),
    ]
    idx = BM25Index(docs)
    # query 同时含罕见 '催办' 和常见 '上报' → 含催办的 doc 0 应排第一
    ranked = idx.rank("催办 上报")
    assert ranked[0][0] == 0


def test_bm25_handles_duplicate_query_terms():
    """query 重复词 — 不该破坏排序."""
    docs = [tokenize("PPT 文档"), tokenize("汇报")]
    idx = BM25Index(docs)
    ranked = idx.rank("PPT PPT 文档")
    assert ranked[0][0] == 0


# ── format_skills_block with user_query (RAG 集成) ───────────


def _mk_skill(skill_path: str, namespace: str = "catfish", description: str = "") -> SkillMeta:
    return SkillMeta(
        skill_path=skill_path,
        name=skill_path.split("/")[-1],
        description=description or f"desc for {skill_path}",
        skill_md_path=Path(f"/tmp/{skill_path}"),
        script_py_path=None,
        namespace=namespace,
    )


def test_format_block_no_rag_under_threshold():
    """skill 数 <= rag_threshold (30) → 全部展示, 不走 BM25."""
    skills = [_mk_skill(f"x/s{i}", namespace="hermes:bundled") for i in range(10)]
    block = format_skills_block(skills, user_query="任意 query", rag_threshold=30)
    # 10 个 skill 全在 (没折叠)
    for i in range(10):
        assert f"x/s{i}" in block
    # 没"折叠区"
    assert "折叠区" not in block


def test_format_block_no_rag_when_no_query():
    """没 user_query → 不走 BM25, 全展示 (即使超阈值)."""
    skills = [_mk_skill(f"x/s{i:03d}", namespace="hermes:bundled") for i in range(50)]
    block = format_skills_block(skills, user_query=None, rag_threshold=30)
    # 50 skill 全展示
    for i in range(50):
        assert f"x/s{i:03d}" in block
    assert "折叠区" not in block


def test_format_block_rag_kicks_in_above_threshold():
    """skill 数 > rag_threshold + 有 query → BM25 top-K + 折叠区."""
    # 50 个 hermes skill, 只 5 个跟 'PPT' 相关
    skills = []
    for i in range(5):
        skills.append(_mk_skill(
            f"ppt-skill-{i}", namespace="hermes:bundled",
            description=f"生成 PPT 幻灯片模板 {i}",
        ))
    for i in range(45):
        skills.append(_mk_skill(
            f"other-skill-{i:02d}", namespace="hermes:bundled",
            description=f"其他不相关功能 {i}",
        ))
    block = format_skills_block(
        skills, user_query="生成 PPT", rag_threshold=30, top_k=10,
    )
    # PPT 相关的 5 个应该都在 inline (top-10 里)
    for i in range(5):
        assert f"ppt-skill-{i}" in block, f"ppt-skill-{i} 应被 BM25 选中"
    # 折叠区出现
    assert "折叠区" in block
    assert "catfish_search_skills" in block


def test_format_block_catfish_always_inline_in_rag_mode():
    """RAG 模式下 catfish skill 永远 inline (工程审定优先级最高)."""
    skills = []
    # 3 个 catfish skill 跟 query 完全无关
    for i in range(3):
        skills.append(_mk_skill(
            f"catfish-only/s{i}", namespace="catfish",
            description="完全无关功能 zzzzz",
        ))
    # 50 个 hermes 跟 query 也无关
    for i in range(50):
        skills.append(_mk_skill(
            f"hermes/s{i:02d}", namespace="hermes:bundled",
            description="hermes 完全无关 yyyyy",
        ))
    block = format_skills_block(
        skills, user_query="完全无匹配的 query 词", rag_threshold=30,
    )
    # catfish 3 个全在
    for i in range(3):
        assert f"catfish-only/s{i}" in block, "catfish skill 应永远 inline"


def test_format_block_folded_summary_shows_namespace_count():
    """折叠区按 namespace 统计 count."""
    skills = [
        _mk_skill("ppt-a", namespace="hermes:bundled", description="生成 PPT 幻灯片"),
    ]
    # 30 个 hermes:bundled 不相关
    for i in range(30):
        skills.append(_mk_skill(
            f"bun-x{i:02d}", namespace="hermes:bundled",
            description=f"无关 {i}",
        ))
    # 5 个 hermes:local 不相关
    for i in range(5):
        skills.append(_mk_skill(
            f"local-x{i}", namespace="hermes:local:foo",
            description="本机自学",
        ))
    block = format_skills_block(
        skills, user_query="PPT", rag_threshold=20, top_k=5,
    )
    # 折叠区按 ns 数 count, 应看到 hermes:bundled + hermes:local 各自数字
    assert "折叠区" in block
    assert "hermes:bundled" in block.split("折叠区")[-1]
    # local 5 个全没匹配 → 5 折叠
    fold_section = block.split("折叠区")[-1]
    # 至少有数字 (count)
    import re as _re
    counts = _re.findall(r"还有 (\d+) 个", fold_section)
    assert counts, "折叠区应有'还有 N 个' 行"


# ── _extract_last_user_query ─────────────────────────────────


def test_extract_user_query_str_content():
    from catfish_gateway.skills_inject import _extract_last_user_query
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "  搜上海新闻  "},
    ]
    assert _extract_last_user_query(msgs) == "搜上海新闻"


def test_extract_user_query_last_user_wins():
    from catfish_gateway.skills_inject import _extract_last_user_query
    msgs = [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "第二句 (该用这条)"},
    ]
    assert _extract_last_user_query(msgs) == "第二句 (该用这条)"


def test_extract_user_query_multimodal_list():
    from catfish_gateway.skills_inject import _extract_last_user_query
    msgs = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": "..."},
            {"type": "text", "text": "看这个图"},
            {"type": "text", "text": "并搜相关新闻"},
        ]},
    ]
    out = _extract_last_user_query(msgs)
    assert "看这个图" in out
    assert "并搜相关新闻" in out


def test_extract_user_query_no_user_message_returns_none():
    from catfish_gateway.skills_inject import _extract_last_user_query
    msgs = [{"role": "system", "content": "sys"}]
    assert _extract_last_user_query(msgs) is None


def test_extract_user_query_empty_content_returns_none():
    from catfish_gateway.skills_inject import _extract_last_user_query
    msgs = [{"role": "user", "content": "   "}]
    assert _extract_last_user_query(msgs) is None
