#!/usr/bin/env python3
"""BL-L26 (5/7): 大文件 (≥50KB) 段落检索 — TF + 长度归一化打分.

# 设计

用户上传 ≥ 50KB 文件后, parse_file.py 写 sidecar `<keptPath>.parsed.txt`
(完整提取文本). 员工发消息时 frontend 调这个 helper:

    python attachment_bm25.py --text-path <kept_path>.parsed.txt --query "<问题>" --top-k 5

替代之前"把 5K 字 preview 塞 user message"的做法 — 现在塞跟问题相关的 top-K 段落,
LLM 上下文密度高, 答得更准.

# 为什么不用 SQLite FTS5

最初想直接复用 edge/local-search 的 FTS5 trigram tokenizer.
但 trigram 对 **2-char 中文词无效** (合同/解除/违约/终止/报销/请假/加班 都不命中,
央企公文场景常用 2-char 词):

    >>> CREATE VIRTUAL TABLE t USING fts5(c, tokenize='trigram')
    >>> SELECT * FROM t WHERE t MATCH '"合同"'  -- 0 hits 即使段里有"合同"

unicode61 tokenizer 中文不切词 (整段当一个 token), 也不行.

所以走自研轻量打分: **TF + 长度归一化** (BM25 简化版, 同一文档内不需要 IDF).
对中英都 work, 0 依赖, ~50 行代码.

5/1 鸿波拍板的"BM25 单一方案 cover 95%, 不做 embedding RAG" 仍然成立 —
我们用的是 **BM25 思想** (TF 加权 + 长度归一化), 不是字面 BM25 公式.

# 输出 schema

成功:
    {
        "passages": [
            {"text": "...", "score": 1.234, "ord": 3},
            ...
        ],
        "total_passages": 47,
        "query_strategy": "tf_score" | "empty"
    }

失败:
    {"error": "..."}

# 段落切分

- 双换行 → 段
- 单段 > PARAGRAPH_MAX_CHARS (1500) → 按句号 / 中文句号 / 分号 切
- 不强制并合短段 (1-字噪音段就让它低分自然沉底)

# Query 切词

- 空白切英文词
- 中文按 2-char window 切 (公文最常见的就是 2 字词)
- 空 query → 返开头 top_k (员工只上传文件没说话给个梗概兜底)
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

# 切段参数
PARAGRAPH_MAX_CHARS = 1500    # 超这个的强行按句号切
DEFAULT_TOP_K = 5

# Query 参数
QUERY_MAX_TOKENS = 30         # 防员工塞 200 字段当 query
MIN_TOKEN_LEN = 1             # 中文 2-char window, 单字也保留 (LIKE)


# ============================================================
# 段落切分
# ============================================================

def split_into_passages(text: str) -> list[str]:
    """双换行切段, 超长段按句号再切.

    返回保持原顺序的段落列表. 段落间不强制合并 (短段也独立成行,
    自然由 score 低排到后面).
    """
    if not text:
        return []
    raw = re.split(r"\n\s*\n", text)
    raw = [p.strip() for p in raw if p.strip()]

    final: list[str] = []
    for p in raw:
        if len(p) <= PARAGRAPH_MAX_CHARS:
            final.append(p)
            continue
        # 超长段按句号切
        sentences = re.split(r"(?<=[。.；;!?！？])\s*", p)
        cur = ""
        for s in sentences:
            if not s:
                continue
            if cur and len(cur) + len(s) > PARAGRAPH_MAX_CHARS:
                final.append(cur.strip())
                cur = s
            else:
                cur = cur + s
        if cur.strip():
            final.append(cur.strip())
    return final


# ============================================================
# Query 切词 (中英混合)
# ============================================================

# 中文 (CJK Unified Ideographs + Extensions A/B 主部分) + 日韩 hiragana/katakana
_CHINESE_CHAR_RE = re.compile(r"[㐀-鿿豈-﫿]")


def _is_chinese(ch: str) -> bool:
    return bool(_CHINESE_CHAR_RE.match(ch))


def tokenize_query(query: str) -> list[str]:
    """切词:
       - 英文 / 数字: 按空白 + 标点切, 单 token 保留
       - 中文: 按相邻 2-char window 切 (再加单字, 兼容人名 / 缩写)
       - 去重保持顺序

    例:
       "termination 终止条件" → ["termination", "终止", "止条", "条件", "终", "止", "条", "件"]
       "KPI 考核"            → ["KPI", "考核", "考", "核"]
    """
    if not query.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(tok: str) -> None:
        if tok and len(tok) >= MIN_TOKEN_LEN and tok not in seen:
            seen.add(tok)
            out.append(tok)

    # 拆 ASCII 词 + 中文连续段
    # 把非 (字母/数字/中文) 当分隔符
    parts = re.split(r"[^\w㐀-鿿豈-﫿]+", query)
    for part in parts:
        if not part:
            continue
        if _is_chinese(part[0]):
            # 中文段, 按 2-char window 切 (双字词覆盖率最高)
            for i in range(len(part) - 1):
                add(part[i : i + 2])
            # 再加单字兜底
            for ch in part:
                add(ch)
        else:
            # 英文 / 数字段, 整体 + 转小写
            add(part.lower())
    return out[:QUERY_MAX_TOKENS]


# ============================================================
# 打分: TF + 长度归一化
# ============================================================

def score_passage(passage: str, tokens: list[str]) -> float:
    """对一段文本, 计算分数:
       score = (sum log(1+tf) for matched tokens) * coverage_bonus / length_penalty

       - tf:  passage 里 token 出现次数 (大小写敏感为 False — 中文无大小写)
       - coverage_bonus: 命中 token 数 / 总 token 数 (0-1, 鼓励多 token 命中)
       - length_penalty: sqrt(len/300) — 段越长越低 (但别太狠)
    """
    if not tokens or not passage:
        return 0.0
    body = passage.lower()
    matched = 0
    tf_sum = 0.0
    for t in tokens:
        # 单字中文已经在 tokens 里了, 大写英文已 .lower(), 这里直接 substring
        c = body.count(t.lower())
        if c > 0:
            matched += 1
            tf_sum += math.log1p(c)
    if matched == 0:
        return 0.0
    coverage = matched / max(1, len(tokens))
    length_penalty = 1.0 + math.sqrt(len(passage) / 300.0)
    return (tf_sum * coverage) / length_penalty


def query_top_k(passages: list[str], query: str, top_k: int = DEFAULT_TOP_K) -> tuple[list[dict[str, Any]], str]:
    """对所有段落打分, 返 top_k 跟用了什么 strategy.

    strategy:
       "tf_score"  正常打分
       "empty"     query 空 / passages 空 → 返开头 top_k 段
    """
    if not passages:
        return [], "empty"
    tokens = tokenize_query(query)
    if not tokens:
        # 空 query 给个开头梗概兜底
        return (
            [{"text": p, "score": 0.0, "ord": i} for i, p in enumerate(passages[:top_k])],
            "empty",
        )

    scored: list[tuple[float, int, str]] = []
    for i, p in enumerate(passages):
        s = score_passage(p, tokens)
        if s > 0:
            scored.append((s, i, p))

    if not scored:
        # 一个也没命中: 返开头几段当兜底 (PDF 的 cover page 常含目的)
        return (
            [{"text": p, "score": 0.0, "ord": i} for i, p in enumerate(passages[:top_k])],
            "empty",
        )

    # 按 score 降序, 同分按 ord 升序 (保持顺序稳定 — 长文档里靠前段更重要)
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = [
        {"text": p, "score": round(s, 4), "ord": i}
        for s, i, p in scored[:top_k]
    ]
    return out, "tf_score"


# ============================================================
# CLI
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-path", required=True, help="解析后的全文文件路径")
    parser.add_argument("--query", default="", help="员工的问题, 用作打分 query")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args()

    text_path = Path(args.text_path)
    if not text_path.exists():
        print(json.dumps({"error": f"sidecar 不存在: {text_path}"}, ensure_ascii=False))
        return 1
    try:
        text = text_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(json.dumps({"error": f"读 sidecar 失败: {e}"}, ensure_ascii=False))
        return 2

    passages = split_into_passages(text)
    hits, strategy = query_top_k(passages, args.query, top_k=max(1, args.top_k))

    print(json.dumps({
        "passages": hits,
        "total_passages": len(passages),
        "query_strategy": strategy,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
