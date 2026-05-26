"""Vector retrieval for skill catalog — BL-SKILLS-VECTOR (5/25 鸿波).

# 跟 BM25 的关系

`skills_retrieval.BM25Index` 是 Phase 3 默认 backend (零依赖, char-level CJK, 短文本
准确率好). 这个 `VectorIndex` 是 Phase 3 升级版 — 走 bge-m3 embedding model 算
真•语义相似 ("汇报" 找到 "述职报告" 这种 BM25 抓不到的 case).

# 接口稳定

`VectorIndex.rank(query, top_k) → [(doc_idx, score), ...]` 跟 BM25Index 完全一致.
caller (`skills_loader._select_skills_for_render`) 按 env 切 backend, 上层代码不动.

# 设计 — Dependency Injection

`embed_fn: Callable[[list[str]], np.ndarray]` 由 caller 注入:
  - 生产: 走 litellm.embedding(model='openai/bge-m3', input=texts, ...)
  - 测试: mock 函数返 fake embeddings (随机或预定义)
  - 离线: 跑本地 sentence-transformers (员工无 internal LLM 接入时)

skills_vector.py 自己**不 import litellm** — gateway 这一层 deps 已经够臃肿,
embed_fn 来源跟 vector 算法分开, 单测 + 替换 backend 都干净.

# 持久化 (P2, 留 task)

当前 build_index 启动时 embed 全 skill (10 个 skill ~500ms, 100 ~5s — gateway
启动延迟可接受). 真上规模 (200+) 时该写 ~/.catfish/skills_index.npz, 按 skill_path  # noqa: BOUNDARY (将来扩展讨论, 非当前路径)
+ SKILL.md mtime fingerprint 增量更新, 只 embed 变化的.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np

logger = logging.getLogger("catfish.gateway.skills_vector")


class VectorIndex:
    """Cosine-similarity vector retrieval.

    Use:
        idx = VectorIndex(['汇报模板', 'PPT 杂志风', '周报'], embed_fn=my_embed)
        ranked = idx.rank('述职', top_k=5)   # [(doc_idx, score), ...]

    score range: [-1, 1] (cosine), 但我们过滤 < 0 (无关), 实际返 [0, 1].

    时间复杂度:
        - build: O(N * embedding_call_latency)  N=100 skill 时 ~5s 启动
        - rank:  O(1 embed + N * dim)           dim=1024 (bge-m3), N=500 → ~3ms
    """

    def __init__(
        self,
        docs: list[str],
        embed_fn: Callable[[list[str]], np.ndarray],
        min_score: float = 0.0,
    ) -> None:
        """
        Args:
            docs: skill 描述文本列表 (e.g. f"{skill_path} {name} {description}")
            embed_fn: 输入 list[str] → 输出 (N, dim) numpy array
            min_score: 低于此 cosine 分数不返 (避免完全无关结果). 0 = 只过滤负相关.
        """
        self.docs = docs
        self.embed_fn = embed_fn
        self.min_score = min_score
        self._dim: int | None = None
        self._doc_embeddings: np.ndarray | None = None
        self._doc_norms: np.ndarray | None = None
        self._build()

    def _build(self) -> None:
        """启动时一次性 embed 所有 doc, cache norm."""
        if not self.docs:
            self._doc_embeddings = np.zeros((0, 1), dtype=np.float32)
            self._doc_norms = np.zeros((0,), dtype=np.float32)
            return
        try:
            embs = self.embed_fn(self.docs)
        except Exception as e:
            # 上游 embedding 服务挂时, 让 caller 知道 → fallback BM25
            logger.warning(
                "VectorIndex build 失败 (embed_fn 调用挂): %s. Caller 应 fallback BM25.",
                e,
            )
            raise
        if not isinstance(embs, np.ndarray):
            embs = np.asarray(embs, dtype=np.float32)
        if embs.ndim != 2:
            raise ValueError(
                f"embed_fn 必须返 (N, dim) 二维数组, 实际 shape={embs.shape}"
            )
        if embs.shape[0] != len(self.docs):
            raise ValueError(
                f"embed_fn 返 {embs.shape[0]} 个 embedding, 期望 {len(self.docs)} 个"
            )
        self._doc_embeddings = embs.astype(np.float32, copy=False)
        self._dim = int(embs.shape[1])
        # 预算 norm 加速 rank (cosine 分母里的 |doc|)
        self._doc_norms = np.linalg.norm(self._doc_embeddings, axis=1)
        # 防 0 vector (理论不该出现, 但 embed 服务边界条件)
        self._doc_norms = np.where(self._doc_norms < 1e-8, 1e-8, self._doc_norms)

    def rank(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """返 top_k 个 (doc_idx, score), score 在 [min_score, 1] 范围内.

        实现: q_emb · doc_embs / (|q_emb| * |doc_embs|)  全 cosine sim 一把.
        """
        if not self.docs or self._doc_embeddings is None:
            return []
        if not query or not query.strip():
            return []
        try:
            q_embs = self.embed_fn([query])
        except Exception as e:
            logger.warning("VectorIndex rank 失败 (embed_fn query 调用挂): %s", e)
            return []
        if not isinstance(q_embs, np.ndarray):
            q_embs = np.asarray(q_embs, dtype=np.float32)
        if q_embs.shape != (1, self._dim):
            logger.warning(
                "embed_fn(query) 返 shape=%s, 期望 (1, %d), 跳过 rank",
                q_embs.shape, self._dim,
            )
            return []
        q_emb = q_embs[0].astype(np.float32, copy=False)  # (dim,)
        q_norm = float(np.linalg.norm(q_emb))
        if q_norm < 1e-8:
            return []

        # cosine sim 一把: doc_embs @ q_emb / (doc_norms * q_norm)
        dots = self._doc_embeddings @ q_emb  # (N,)
        sims = dots / (self._doc_norms * q_norm)  # (N,)

        # 取 top_k, 过滤 min_score
        # argsort 是 ascending, 取最后 top_k 个再 reverse
        n = len(self.docs)
        k = min(top_k, n)
        # 用 argpartition 比全排序快 — N=500 时 ~3x 加速
        if k < n:
            top_indices = np.argpartition(-sims, k)[:k]
            # 这 k 个再内部排序
            top_indices = top_indices[np.argsort(-sims[top_indices])]
        else:
            top_indices = np.argsort(-sims)
        ranked: list[tuple[int, float]] = []
        for i in top_indices:
            s = float(sims[i])
            if s <= self.min_score:
                continue
            ranked.append((int(i), s))
        return ranked


# ── Embed function factory (生产用 litellm) ─────────────────────


def make_litellm_embed_fn(
    model_name: str,
    api_base: str | None = None,
    api_key: str | None = None,
    timeout: float = 30.0,
) -> Callable[[list[str]], np.ndarray]:
    """生产用 embed_fn — 走 litellm.embedding (gateway 自己内部调).

    用法:
        from .skills_vector import make_litellm_embed_fn, VectorIndex

        embed = make_litellm_embed_fn(
            model_name="openai/bge-m3",
            api_base=config.get_model("catfish-private-embed").upstream.api_base,
            api_key=config.get_model("catfish-private-embed").upstream.api_key,
        )
        idx = VectorIndex(['skill desc 1', 'skill desc 2'], embed_fn=embed)
    """
    def _embed(texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        # 懒 import litellm — 模块加载时不 import, 测试不 mock 也能跑
        import litellm  # noqa: PLC0415
        params: dict = {
            "model": model_name,
            "input": texts,
            "num_retries": 0,
            "timeout": timeout,
        }
        if api_base:
            params["api_base"] = api_base
        if api_key:
            params["api_key"] = api_key
        resp = litellm.embedding(**params)
        # resp.data 是 list of {"embedding": [...]}
        data = getattr(resp, "data", None) or resp.get("data", [])
        embs = np.array(
            [d["embedding"] if isinstance(d, dict) else d.embedding for d in data],
            dtype=np.float32,
        )
        return embs

    return _embed


__all__ = ["VectorIndex", "make_litellm_embed_fn"]
