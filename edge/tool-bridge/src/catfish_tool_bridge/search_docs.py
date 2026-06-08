"""BL-STRATEGIC-DOC-SYNC Phase 3 (6/7 鸿波 audit 后 ship).

catfish_search_docs native tool — 跨 ~/.catfish/strategic_docs/*.md BM25 搜.

# 为啥 (跟 catfish_search_skills 同思路, 不同对象)

Phase 2 _render_strategic_docs 在 prefetch 把 doc 关键段 (head ~1.2KB / doc)
注入 system prompt, 但 budget cap 8KB. 当员工 doc 增加 (e.g. 20+ 份) prefetch
塞不下, 或员工想看**完整 doc 内容** (非 head), 需要这个 tool 让 LLM 主动查.

# 跟 Phase 2 区别

Phase 2 (prefetch _render_strategic_docs):
  - 每轮 user msg 自动 inject head + frontmatter 去掉
  - cap 8KB (no query) / 5KB (query 触发 top-K)
  - 适合"快速一瞥 + 名字提示"

Phase 3 (本 tool):
  - LLM 主动调, 不进 prefetch budget
  - 返完整 head (2KB / doc) + 全文路径 (LLM 可后续读 wiki_read 拿全文, 但这步
    catfish 没暴露给 LLM, BL)
  - 适合"员工细问 manifesto 第几条说啥"

# 跟 catfish_search_skills 区别

search_skills 是 BM25 + skill metadata (name/desc), 跨 ~/.hermes/skills/ +
~/.catfish/skills/. 这里只搜 ~/.catfish/strategic_docs/, doc 没 metadata 概念,
title 取 frontmatter `title` (没就用 filename).

# 缓存策略

跟 search_skills 一样, fingerprint = sum of mtime, doc 不变 → cache hit.

# 跟 manifesto 公理一致

- 数据在 ~/.catfish/strategic_docs/ 员工本机, 不上传中央
- LLM 主动调, pull-based
- 不调用 BGE-M3 (Rust Tauri 跨进程 RPC 复杂, BM25 char-level 对 20 份内 doc 完全够用)
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from .search_skills import _BM25, _tokenize

logger = logging.getLogger("catfish.tool_bridge.search_docs")


# ── 扫 strategic_docs ────────────────────────────────────────────


def _strategic_docs_dir() -> Path:
    """~/.catfish/strategic_docs/. env CATFISH_HOME 覆盖给测试用."""
    import os
    if env := os.environ.get("CATFISH_HOME"):
        return Path(env) / "strategic_docs"
    return Path.home() / ".catfish" / "strategic_docs"


def _discover_doc_paths() -> list[Path]:
    """扫所有 .md, 字母序返."""
    docs_dir = _strategic_docs_dir()
    if not docs_dir.is_dir():
        return []
    try:
        return sorted(p for p in docs_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md")
    except OSError:
        return []


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_doc(path: Path) -> dict[str, Any] | None:
    """读 doc, 解 frontmatter + 拿 head 2KB body."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    title = path.stem  # default = filename without .md
    body = raw

    # 去 frontmatter
    m = _FRONTMATTER_RE.match(raw)
    if m:
        fm = m.group(1)
        # 简单 title 提取 (YAML key: value)
        for line in fm.splitlines():
            line = line.strip()
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip("\"'")
                break
        body = raw[m.end():]

    # 取 head 2KB
    head = body.strip()[:2000]

    return {
        "name": path.stem,
        "title": title,
        "head": head,
        "path": str(path),
    }


# ── BM25 cache (跟 search_skills 同模式) ─────────────────────────────

_cache: dict[str, Any] = {
    "fingerprint": "",
    "index": None,
    "meta": [],
}


def _fingerprint(paths: list[Path]) -> str:
    parts = []
    for p in paths:
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = 0
        parts.append(f"{p}:{mt}")
    return "|".join(parts)


def _build_or_get_index() -> tuple[_BM25 | None, list[dict[str, Any]]]:
    paths = _discover_doc_paths()
    if not paths:
        return None, []

    fp = _fingerprint(paths)
    if _cache["fingerprint"] == fp and _cache["index"] is not None:
        return _cache["index"], _cache["meta"]

    docs: list[list[str]] = []
    meta: list[dict[str, Any]] = []
    for path in paths:
        parsed = _parse_doc(path)
        if not parsed:
            continue
        meta.append(parsed)
        # BM25 doc = name + title + head (匹配文件名 / 标题 / 内容)
        docs.append(_tokenize(f"{parsed['name']} {parsed['title']} {parsed['head']}"))

    if not docs:
        return None, []

    idx = _BM25(docs)
    _cache["fingerprint"] = fp
    _cache["index"] = idx
    _cache["meta"] = meta
    logger.info("BL-STRATEGIC-DOC-SYNC Phase 3: 缓存刷新, %d doc 进索引", len(docs))
    return idx, meta


# ── 公开 API ──────────────────────────────────────────────────────


_DOC_HEAD_PREVIEW_CHARS = 1500


def search_docs(query: str, top_k: int = 5) -> dict[str, Any]:
    """BM25 搜战略 doc.

    Args:
        query: 自然语言 / 关键字 ("manifesto 公理" / "patent 方向" / "沙盒")
        top_k: 返多少个 (默认 5, 上限 15)

    Returns:
        {"matches": [{name, title, head, path, score}], "count", "total_indexed", "summary"}
    """
    top_k = max(1, min(int(top_k or 5), 15))

    idx, meta = _build_or_get_index()
    if idx is None:
        return {
            "matches": [],
            "count": 0,
            "total_indexed": 0,
            "summary": (
                "员工本机没装战略 doc (~/.catfish/strategic_docs/ 不存在或空). "
                "鸿波: 跑 catfish/scripts/sync_strategic_docs.sh 拷过去."
            ),
        }

    ranked = idx.rank(query, top_k=top_k)
    matches = []
    for doc_idx, score in ranked:
        m = meta[doc_idx]
        head = m["head"]
        if len(head) > _DOC_HEAD_PREVIEW_CHARS:
            head = head[:_DOC_HEAD_PREVIEW_CHARS].rstrip() + "…"
        matches.append({
            "name": m["name"],
            "title": m["title"],
            "head": head,
            "path": m["path"],
            "score": round(score, 3),
        })

    if not matches:
        summary = f"没找到匹配 '{query}' 的 doc (共扫 {len(meta)} 份战略 doc)"
    else:
        names = [m["name"] for m in matches[:3]]
        summary = (
            f"找到 {len(matches)} 份匹配 doc (top: {', '.join(names)}). "
            f"head 已含关键段; 想看全文用 wiki_read 调路径."
        )

    return {
        "matches": matches,
        "count": len(matches),
        "total_indexed": len(meta),
        "summary": summary,
    }


def tool_search_docs(args: dict) -> dict[str, Any]:
    """tool-bridge dispatch 入口. args = {"query": str, "top_k"?: int}."""
    query = str(args.get("query", "")).strip()
    if not query:
        return {
            "matches": [],
            "count": 0,
            "summary": "query 是必填. 给个关键字 (e.g. 'manifesto' / 'patent' / '沙盒 audit')",
        }
    top_k = args.get("top_k") or 5
    start = time.time()
    try:
        out = search_docs(query=query, top_k=int(top_k))
    except Exception as e:  # 永远不抛 (跟 search_skills 同)
        logger.warning("BL-STRATEGIC-DOC-SYNC Phase 3: search 挂 (%s), 返友好空", e)
        return {
            "matches": [],
            "count": 0,
            "summary": f"搜出错 ({type(e).__name__}). 让鸿波检查 ~/.catfish/strategic_docs/.",
        }
    out["latency_ms"] = round((time.time() - start) * 1000, 1)
    return out


__all__ = ["search_docs", "tool_search_docs"]
