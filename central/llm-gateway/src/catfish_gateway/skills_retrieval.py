"""BM25 skill retrieval — Phase 3 BL-SKILLS-RAG (5/25 鸿波).

# 为啥要

5/25 鸿波看 Claude Skills "Progressive Disclosure" 文章后, 提出 "skill 越来越多
会爆" 的合理担忧. Phase 1 (BL-SKILLS-TIER1-SHRINK) + Phase 2 (BL-SKILLS-TIER1-FOLD)
解决了 single-skill 开销 + 分组渲染, 但 200+ skill 时 system prompt 还是会臃肿.

Anthropic 推荐 "Claude reads SKILL.md only when the Skill becomes relevant" —
但 "怎么判断 relevant" 没说. 我们用 BM25 (经典 lexical retrieval) 实现:

  1. 启动时给所有 SKILL.md description 建 BM25 index
  2. 每条 chat 提取 user query, 算 BM25 top-K
  3. system prompt 只露 top-K (15-20 个), 其余折成 namespace count
  4. 模型想找更多 → 调 `catfish_search_skills(query)` 工具 (tool-bridge, 后续 ship)

# 为啥 BM25 不是 vector

  1. 零外部依赖 (rank-bm25 / FAISS / bge-m3 都是大 dep)
  2. 中英混合好处理 (vector 需要 multilingual model)
  3. ~500 skill 量级 BM25 准确率够用 — 99% 时候 user query 含 skill 名/关键词
  4. 升级到 vector 时这个模块可以直接换 backend, 接口稳定

# 中文分词

不引 jieba — char-level tokenize CJK (每字一 token) + ASCII 按词. 短文本 (description
~80 chars) BM25 对 char-level 也够用. 后续要更高准确率再加 jieba.
"""
from __future__ import annotations

import math
import re
from typing import Iterable


# CJK Unicode range (中日韩统一表意文字, 基本块). 韩文 hangul 不在此, 但 catfish
# 客户群体没韩文场景, 先不管.
_CJK_RANGE = ('一', '鿿')

# 按这些字符切 ASCII (标点 / 引号 / 括号 / 通用符号). 不切 CJK char (它们各自一 token).
_ASCII_SPLIT_RE = re.compile(r"[\s,.;:!?'\"()\[\]{}<>/\\|@#$%^&*=+~`\-]+")


def tokenize(text: str) -> list[str]:
    """切词 — ASCII 按空格/标点, CJK 按 char.

    例:
      'leadership-briefing 上行汇报 5 段'
        → ['leadership', 'briefing', '上', '行', '汇', '报', '5', '段']

    特性:
      - 大小写归一 (lower)
      - 跳空 token
      - ASCII 跟 CJK 交错时正确拆 (e.g. 'PPT文档' → ['ppt', '文', '档'])
    """
    tokens: list[str] = []
    for piece in _ASCII_SPLIT_RE.split(text.lower()):
        if not piece:
            continue
        # 没 CJK 的 piece 整体作为一个 token
        if not any(_CJK_RANGE[0] <= c <= _CJK_RANGE[1] for c in piece):
            tokens.append(piece)
            continue
        # 含 CJK: 切, CJK 每字一 token, ASCII 段一 token
        ascii_buf: list[str] = []
        for c in piece:
            if _CJK_RANGE[0] <= c <= _CJK_RANGE[1]:
                if ascii_buf:
                    tokens.append("".join(ascii_buf))
                    ascii_buf = []
                tokens.append(c)
            else:
                ascii_buf.append(c)
        if ascii_buf:
            tokens.append("".join(ascii_buf))
    return [t for t in tokens if t]


class BM25Index:
    """简化 Okapi BM25 (k1=1.5, b=0.75, 行业默认).

    用法:
        idx = BM25Index([tokenize(skill1_text), tokenize(skill2_text), ...])
        ranked = idx.rank('汇报', top_k=10)   # [(doc_idx, score), ...]

    时间复杂度:
      - build: O(N * avg_doc_len)
      - rank:  O(N * |query|)  线性扫所有 doc, N ~500 时单次 < 5ms
    """

    def __init__(
        self,
        docs: list[list[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.docs = docs
        self.k1 = k1
        self.b = b
        self.n_docs = len(docs)
        self.avgdl = (
            sum(len(d) for d in docs) / self.n_docs
            if self.n_docs else 0.0
        )

        # df: 含每个 term 的 doc 数
        self.df: dict[str, int] = {}
        for d in docs:
            for term in set(d):
                self.df[term] = self.df.get(term, 0) + 1

        # idf: +0.5 平滑 (BM25 标准做法, 防 df=0 时 log 负值)
        self.idf: dict[str, float] = {
            term: math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
            for term, df in self.df.items()
        }

        # 预算每 doc 的 term frequency, 加速 rank
        self._tf_per_doc: list[dict[str, int]] = []
        for d in docs:
            tf: dict[str, int] = {}
            for t in d:
                tf[t] = tf.get(t, 0) + 1
            self._tf_per_doc.append(tf)

    def _score_doc(self, query_terms: Iterable[str], doc_idx: int) -> float:
        if not self.n_docs:
            return 0.0
        tf = self._tf_per_doc[doc_idx]
        doc_len = len(self.docs[doc_idx])
        score = 0.0
        for q in query_terms:
            if q not in self.idf:
                continue
            t_freq = tf.get(q, 0)
            if t_freq == 0:
                continue
            num = t_freq * (self.k1 + 1)
            den = t_freq + self.k1 * (1 - self.b + self.b * doc_len / max(self.avgdl, 1.0))
            score += self.idf[q] * (num / den)
        return score

    def rank(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """返 top_k 个 (doc_idx, score), score > 0 才返 — 没匹配的不返."""
        if not self.n_docs:
            return []
        q_terms = tokenize(query)
        if not q_terms:
            return []
        # 去重 query terms (不影响 score 正确性, 但跳 IDF=0 的 stop word 重复)
        q_terms_unique = list(dict.fromkeys(q_terms))
        scored: list[tuple[int, float]] = []
        for i in range(self.n_docs):
            s = self._score_doc(q_terms_unique, i)
            if s > 0:
                scored.append((i, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


__all__ = ["tokenize", "BM25Index"]
