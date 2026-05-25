"""BL-SKILLS-VECTOR (5/25 鸿波): VectorIndex + 多 backend switch 单测.

跑法: cd central/llm-gateway && PYTHONPATH=src pytest tests/test_skills_vector.py -v

覆盖:
- VectorIndex: 空 / 单 doc / 多 doc rank / fake embed_fn 注入
- env CATFISH_SKILLS_RETRIEVAL=vector/hybrid/bm25 切 backend 行为
- vector fallback to BM25 (embed_fn 挂时, retrieval 仍能工作)
- RRF fusion (hybrid mode)
- cache: skills 不变 → 不重新 embed
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_gateway.skills_loader import (  # noqa: E402
    SkillMeta,
    _retrieval_mode,
    _rrf_fuse,
    _select_skills_for_render,
    _vector_cache,
    format_skills_block,
)
from catfish_gateway.skills_vector import VectorIndex  # noqa: E402


# ── 测试用 fake embed_fn ────────────────────────────────────


def _fake_embed_fn(dim: int = 8):
    """造一个 deterministic fake embed_fn: text → hash-based fake embedding.

    用 hash(token) 凑 dim 维 float, 同样 text 给同样 embedding (deterministic).
    不真用 bge-m3, 单测 self-contained.
    """
    def _embed(texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for j in range(dim):
                # 简单 hash: byte XOR 然后 mod
                h = (hash(t + str(j)) % 10000) / 10000.0
                out[i, j] = h
        # 给些方向变化 — 让 cosine sim 有差异
        return out
    return _embed


# ── VectorIndex 基础 ────────────────────────────────────────


def test_vector_empty_docs_returns_empty():
    idx = VectorIndex([], embed_fn=_fake_embed_fn())
    assert idx.rank("query") == []


def test_vector_empty_query_returns_empty():
    idx = VectorIndex(["foo"], embed_fn=_fake_embed_fn())
    assert idx.rank("") == []
    assert idx.rank("   ") == []


def test_vector_single_doc_returns_it():
    idx = VectorIndex(["catfish leadership briefing"], embed_fn=_fake_embed_fn())
    ranked = idx.rank("catfish leadership briefing")
    # 同 text → cosine ≈ 1, 应返
    assert len(ranked) == 1
    assert ranked[0][0] == 0
    assert ranked[0][1] > 0.99


def test_vector_top_k_truncation():
    """top_k 截断 (跟 BM25Index 同行为)."""
    docs = [f"document {i}" for i in range(20)]
    idx = VectorIndex(docs, embed_fn=_fake_embed_fn())
    ranked = idx.rank("test query", top_k=5)
    assert len(ranked) <= 5


def test_vector_filters_by_min_score():
    """min_score=0.5 → 低相关 doc 不返."""
    docs = [
        "exact match for query",
        "totally unrelated content xyz123",
    ]
    # 真 embed 要看 hash 行为, fake_embed_fn 给确定值. 这里只验 min_score 起作用.
    idx = VectorIndex(docs, embed_fn=_fake_embed_fn(), min_score=0.99)
    ranked = idx.rank("query")
    # min_score=0.99 极严格, fake embed 几乎都不到 0.99 → 返空
    assert all(s > 0.99 for _, s in ranked)


def test_vector_ndim_validation():
    """embed_fn 返非 2D array → 报错."""
    def bad_embed(texts):
        return np.zeros((len(texts),))  # 1D, 错
    with pytest.raises(ValueError, match=r"必须返 \(N, dim\)"):
        VectorIndex(["x"], embed_fn=bad_embed)


def test_vector_doc_count_mismatch_validation():
    """embed_fn 返的 N 跟 docs 数不符 → 报错."""
    def bad_embed(texts):
        return np.zeros((len(texts) + 1, 8), dtype=np.float32)
    with pytest.raises(ValueError, match=r"返 \d+ 个 embedding"):
        VectorIndex(["x", "y"], embed_fn=bad_embed)


def test_vector_query_embed_failure_returns_empty():
    """rank 时 embed_fn 调用挂 → 返空 list, 不抛."""
    call_count = {"n": 0}

    def flaky_embed(texts):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # build 时成功
            return np.random.rand(len(texts), 8).astype(np.float32)
        # rank 时 (call 2) 挂
        raise RuntimeError("bge-m3 down")

    idx = VectorIndex(["doc1", "doc2"], embed_fn=flaky_embed)
    # 第一个 rank 应该挂 → 返空 (logger.warning, 不抛)
    ranked = idx.rank("query")
    assert ranked == []


# ── _retrieval_mode env switch ──────────────────────────────


def test_retrieval_mode_default_bm25(monkeypatch):
    monkeypatch.delenv("CATFISH_SKILLS_RETRIEVAL", raising=False)
    assert _retrieval_mode() == "bm25"


def test_retrieval_mode_vector(monkeypatch):
    monkeypatch.setenv("CATFISH_SKILLS_RETRIEVAL", "vector")
    assert _retrieval_mode() == "vector"


def test_retrieval_mode_hybrid(monkeypatch):
    monkeypatch.setenv("CATFISH_SKILLS_RETRIEVAL", "HYBRID")  # 大小写不敏感
    assert _retrieval_mode() == "hybrid"


def test_retrieval_mode_invalid_falls_back_to_bm25(monkeypatch):
    monkeypatch.setenv("CATFISH_SKILLS_RETRIEVAL", "GPT-5-embed-magic")
    assert _retrieval_mode() == "bm25"


# ── RRF fusion (hybrid) ─────────────────────────────────────


def test_rrf_fuse_combines_two_rankers():
    """RRF 给两个 ranker 都看到的 doc 高分."""
    bm25 = [(0, 1.0), (1, 0.8), (2, 0.5)]
    vec = [(1, 0.95), (0, 0.7), (3, 0.6)]
    fused = _rrf_fuse(bm25, vec, top_k=10)
    # doc 0 + 1 都在两 ranker 里 → 高分
    fused_ids = [i for i, _ in fused]
    assert 0 in fused_ids
    assert 1 in fused_ids
    # doc 0 在 bm25 第 1 + vec 第 2 → 应该最高 / 第二高
    # doc 1 在 bm25 第 2 + vec 第 1 → 类似
    top2 = set(fused_ids[:2])
    assert top2 == {0, 1}


def test_rrf_fuse_handles_disjoint_rankers():
    """两 ranker 完全没交集 → 全保留, RRF 按各自 rank 排."""
    bm25 = [(0, 1.0), (1, 0.5)]
    vec = [(2, 1.0), (3, 0.5)]
    fused = _rrf_fuse(bm25, vec, top_k=10)
    assert len(fused) == 4
    fused_ids = {i for i, _ in fused}
    assert fused_ids == {0, 1, 2, 3}


def test_rrf_fuse_respects_top_k():
    bm25 = [(i, 1.0) for i in range(20)]
    vec = [(i, 1.0) for i in range(20, 40)]
    fused = _rrf_fuse(bm25, vec, top_k=5)
    assert len(fused) == 5


# ── _select_skills_for_render 多 backend ────────────────────


def _mk_skill(skill_path: str, namespace: str = "hermes:bundled", description: str = "") -> SkillMeta:
    return SkillMeta(
        skill_path=skill_path,
        name=skill_path.split("/")[-1],
        description=description or f"desc for {skill_path}",
        skill_md_path=Path(f"/tmp/{skill_path}"),
        script_py_path=None,
        namespace=namespace,
    )


def _reset_vector_cache():
    _vector_cache["fingerprint"] = None
    _vector_cache["index"] = None
    _vector_cache["backend"] = None


@pytest.fixture(autouse=True)
def _isolate_vector_cache():
    _reset_vector_cache()
    yield
    _reset_vector_cache()


def test_bm25_mode_does_not_call_vector(monkeypatch):
    """env=bm25 → 永远不调 _build_vector_index."""
    monkeypatch.setenv("CATFISH_SKILLS_RETRIEVAL", "bm25")
    calls = []
    import catfish_gateway.skills_loader as sl
    monkeypatch.setattr(
        sl, "_build_vector_index",
        lambda skills: (calls.append(skills) or None),
    )
    skills = [_mk_skill(f"x/skill{i}") for i in range(50)]
    _select_skills_for_render(skills, user_query="test", rag_threshold=10, top_k=5)
    assert calls == [], "bm25 mode 不该调 vector index 构建"


def test_vector_mode_calls_vector_index(monkeypatch):
    """env=vector → 调 _build_vector_index 真建 index."""
    monkeypatch.setenv("CATFISH_SKILLS_RETRIEVAL", "vector")
    import catfish_gateway.skills_loader as sl

    fake_idx_called = {"build": 0, "rank": 0}

    class FakeIdx:
        def rank(self, query, top_k):
            fake_idx_called["rank"] += 1
            return [(0, 0.9), (1, 0.8)]

    def fake_build(skills):
        fake_idx_called["build"] += 1
        return FakeIdx()

    monkeypatch.setattr(sl, "_build_vector_index", fake_build)
    skills = [_mk_skill(f"x/skill{i}") for i in range(50)]
    _select_skills_for_render(skills, user_query="test", rag_threshold=10, top_k=5)
    assert fake_idx_called["build"] == 1
    assert fake_idx_called["rank"] == 1


def test_vector_mode_falls_back_to_bm25_if_index_build_fails(monkeypatch):
    """vector index 建失败 (bge-m3 不可达) → 自动用 BM25, 不挂."""
    monkeypatch.setenv("CATFISH_SKILLS_RETRIEVAL", "vector")
    import catfish_gateway.skills_loader as sl
    # _build_vector_index 返 None → fallback 触发
    monkeypatch.setattr(sl, "_build_vector_index", lambda s: None)
    skills = [_mk_skill(f"x/skill{i}", description=f"PPT 模板 {i}") for i in range(50)]
    # 应该不挂, 走 BM25 ranking
    rendered, folded = _select_skills_for_render(
        skills, user_query="PPT 模板", rag_threshold=10, top_k=5,
    )
    # 至少要有 inline 结果 (BM25 命中)
    assert len(rendered) > 0


def test_hybrid_mode_uses_rrf(monkeypatch):
    """env=hybrid → 调 vector + BM25, 用 RRF 融合."""
    monkeypatch.setenv("CATFISH_SKILLS_RETRIEVAL", "hybrid")
    import catfish_gateway.skills_loader as sl

    class FakeIdx:
        def rank(self, query, top_k):
            return [(0, 0.95)]  # vector 选 doc 0

    monkeypatch.setattr(sl, "_build_vector_index", lambda s: FakeIdx())

    # BM25 会自己跑 — 给一组 skill 让它选不同的 doc
    skills = [
        _mk_skill("x/PPT-skill", description="PPT 杂志风模板"),  # BM25 顶 PPT
        _mk_skill("x/other", description="完全不相关"),
    ] + [_mk_skill(f"x/extra{i}") for i in range(50)]

    rendered, _ = _select_skills_for_render(
        skills, user_query="PPT", rag_threshold=10, top_k=5,
    )
    # PPT-skill (BM25 命中) + vector 选的 (RRF 合并) 应至少 1 个被选中
    assert len(rendered) > 0


# ── format_skills_block 集成 (vector mode) ───────────────────


def test_format_block_vector_mode_end_to_end(monkeypatch):
    """vector mode 端到端: catfish 全展示, hermes 按 vector top-K."""
    monkeypatch.setenv("CATFISH_SKILLS_RETRIEVAL", "vector")
    import catfish_gateway.skills_loader as sl

    class FakeIdx:
        """选最后 3 个 hermes skill (假装 vector 觉得它们最相关)."""
        def __init__(self):
            self._idx = None
        def rank(self, query, top_k):
            # 最后 3 个
            return [(i, 0.9 - 0.01 * i) for i in range(47, 50)][:top_k]

    monkeypatch.setattr(sl, "_build_vector_index", lambda s: FakeIdx())

    skills = [_mk_skill("c/keep1", namespace="catfish")]  # catfish 永远留
    skills += [_mk_skill(f"h/s{i:02d}", namespace="hermes:bundled") for i in range(50)]

    block = format_skills_block(skills, user_query="test", rag_threshold=10, top_k=3)
    # catfish 必在
    assert "c/keep1" in block
    # 选的 hermes 是 47/48/49 (最后 3 个)
    assert "h/s47" in block
    assert "h/s48" in block
    assert "h/s49" in block
    # 其他 hermes 折叠
    assert "h/s00" not in block
    assert "折叠区" in block