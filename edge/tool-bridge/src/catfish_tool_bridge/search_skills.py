"""BL-SKILLS-RAG-TOOL (5/25 鸿波): catfish_search_skills native tool.

# 为啥

5/25 ship 的 BL-SKILLS-PROGRESSIVE-DISCLOSURE 在 gateway 把 skill catalog
压缩到 Tier 1 metadata (单 skill ~25 tokens), 但**100+ skill 时**只 inline top-K
(BM25 选), 其余按 namespace 折叠. 折叠区底部提示员工:

  > **找不到合适的? 调 catfish_search_skills(query='...') 在全部 skill 里搜.**

这工具就是那个钩子. 模型主动调它从折叠区捞 skill.

# 跟 sessions_search / email_search 同 pattern

直读员工本机 skill 目录 (~/.hermes/skills/ + ~/.catfish/skills/), 不走 gateway HTTP.
- 快 (无 HTTP 延迟)
- 鲁棒 (gateway 挂了仍能搜)
- 跟 BM25 算法解耦 (跟 gateway/skills_retrieval.py 同份代码, 但复制独立, 改 BM25
  时记得改两处 — trade-off 同 sessions_search 本机直读)

# 缓存

skill 集合不变 → 缓 BM25 index. fingerprint = sum of SKILL.md mtime + skill_path.
新装 skill / 改 SKILL.md → fingerprint 变 → 重建 index.

# 注意

不返完整 SKILL.md (按 Anthropic Progressive Disclosure Tier 2 思路: 详细参数靠
catfish_run_skill(params={'_help': True}) 拿). 只返 metadata (name/path/description
snippet/score), 让模型决定调哪个 skill 后自己 _help.
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("catfish.tool_bridge.search_skills")


# ── BM25 (复制自 gateway/skills_retrieval.py, 改 BM25 时记得同步) ─────

_CJK_RANGE = ('一', '鿿')
_ASCII_SPLIT_RE = re.compile(r"[\s,.;:!?'\"()\[\]{}<>/\\|@#$%^&*=+~`\-]+")


def _tokenize(text: str) -> list[str]:
    """ASCII 按词 + CJK 按 char + 大小写归一. 跟 gateway 同份算法."""
    tokens: list[str] = []
    for piece in _ASCII_SPLIT_RE.split(text.lower()):
        if not piece:
            continue
        if not any(_CJK_RANGE[0] <= c <= _CJK_RANGE[1] for c in piece):
            tokens.append(piece)
            continue
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


class _BM25:
    """Okapi BM25 (k1=1.5, b=0.75). 跟 gateway 同份."""

    def __init__(self, docs: list[list[str]]) -> None:
        self.docs = docs
        self.k1 = 1.5
        self.b = 0.75
        self.n = len(docs)
        self.avgdl = (sum(len(d) for d in docs) / self.n) if self.n else 0.0
        self.df: dict[str, int] = {}
        for d in docs:
            for term in set(d):
                self.df[term] = self.df.get(term, 0) + 1
        self.idf = {
            t: math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for t, df in self.df.items()
        }
        self._tf: list[dict[str, int]] = []
        for d in docs:
            tf: dict[str, int] = {}
            for w in d:
                tf[w] = tf.get(w, 0) + 1
            self._tf.append(tf)

    def rank(self, query: str, top_k: int) -> list[tuple[int, float]]:
        if not self.n:
            return []
        q_terms = list(dict.fromkeys(_tokenize(query)))
        if not q_terms:
            return []
        scores: list[tuple[int, float]] = []
        for i in range(self.n):
            tf = self._tf[i]
            doc_len = len(self.docs[i])
            score = 0.0
            for q in q_terms:
                if q not in self.idf:
                    continue
                t_freq = tf.get(q, 0)
                if t_freq == 0:
                    continue
                num = t_freq * (self.k1 + 1)
                den = t_freq + self.k1 * (1 - self.b + self.b * doc_len / max(self.avgdl, 1.0))
                score += self.idf[q] * (num / den)
            if score > 0:
                scores.append((i, score))
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]


# ── skill discovery ─────────────────────────────────────────────


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.+?)\n---\s*\n?", re.DOTALL)


def _hermes_skills_root() -> Path:
    return Path.home() / ".hermes" / "skills"


def _catfish_skills_root() -> Path | None:
    """env CATFISH_SKILLS_DIR 优先, 否则尝试 ~/.catfish/skills/."""
    env = os.environ.get("CATFISH_SKILLS_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    # 默认 ~/.catfish/skills/ (员工本机录屏存 skill 的地方)
    p = Path.home() / ".catfish" / "skills"
    return p if p.is_dir() else None


def _parse_skill_md(md_path: Path) -> dict[str, str] | None:
    """从 SKILL.md frontmatter 提 name + description. 失败返 None."""
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    name = str(front.get("name", "")).strip()
    desc = str(front.get("description", "")).strip()
    if not name or not desc:
        return None
    return {"name": name, "description": desc}


def _discover_skill_paths() -> list[tuple[Path, str, str]]:
    """扫两个 root, 返 [(SKILL.md path, namespace, skill_path)].

    namespace = 'catfish' | 'hermes' (粗粒度, 比 gateway 的细分简单).
    skill_path = 相对 root 的子路径, 例 'department/leadership-briefing'.
    """
    results: list[tuple[Path, str, str]] = []

    catfish_root = _catfish_skills_root()
    if catfish_root is not None:
        for md in catfish_root.rglob("SKILL.md"):
            try:
                rel = md.relative_to(catfish_root).parent
            except ValueError:
                continue
            results.append((md, "catfish", str(rel).replace(os.sep, "/")))

    hermes_root = _hermes_skills_root()
    if hermes_root.is_dir():
        for md in hermes_root.rglob("SKILL.md"):
            try:
                rel = md.relative_to(hermes_root).parent
            except ValueError:
                continue
            results.append((md, "hermes", str(rel).replace(os.sep, "/")))

    return results


# ── 缓存 (按 SKILL.md mtime fingerprint) ────────────────────────


_cache: dict[str, Any] = {"fingerprint": None, "index": None, "meta": []}


def _fingerprint(paths: list[tuple[Path, str, str]]) -> str:
    parts = []
    for p, _ns, _sp in paths:
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = 0
        parts.append(f"{p}:{mt}")
    return "|".join(parts)


def _build_or_get_index() -> tuple[_BM25 | None, list[dict[str, Any]]]:
    """返 (BM25 index, list of {skill_path, name, description, namespace, md_path}).

    cache hit (skill 集合不变) → 直接返. miss → 重扫 + 建 index.
    """
    paths = _discover_skill_paths()
    if not paths:
        return None, []

    fp = _fingerprint(paths)
    if _cache["fingerprint"] == fp and _cache["index"] is not None:
        return _cache["index"], _cache["meta"]

    docs: list[list[str]] = []
    meta: list[dict[str, Any]] = []
    for md, ns, skill_path in paths:
        parsed = _parse_skill_md(md)
        if not parsed:
            continue
        meta.append({
            "namespace": ns,
            "skill_path": skill_path,
            "name": parsed["name"],
            "description": parsed["description"],
            "md_path": str(md),
        })
        # BM25 doc = skill_path + name + description (member 加入索引提升匹配)
        docs.append(_tokenize(f"{skill_path} {parsed['name']} {parsed['description']}"))

    if not docs:
        return None, []

    idx = _BM25(docs)
    _cache["fingerprint"] = fp
    _cache["index"] = idx
    _cache["meta"] = meta
    logger.info("BL-SKILLS-RAG-TOOL: 缓存刷新, %d skill 进索引", len(docs))
    return idx, meta


# ── 公开 API ─────────────────────────────────────────────────────


_SKILL_DESC_PREVIEW_CHARS = 200


def search_skills(query: str, top_k: int = 10) -> dict[str, Any]:
    """跨 ~/.hermes/skills + ~/.catfish/skills BM25 搜 skill.

    Args:
        query: 自然语言或关键字 ("写汇报" / "leadership briefing" / "PPT 杂志风")
        top_k: 返多少个 (默认 10, 上限 30)

    Returns:
        {"matches": [{skill_path, name, description, namespace, score, md_path}],
         "count": N, "total_indexed": M}
    """
    top_k = max(1, min(int(top_k or 10), 30))

    idx, meta = _build_or_get_index()
    if idx is None:
        return {
            "matches": [],
            "count": 0,
            "total_indexed": 0,
            "summary": "员工本机没安装任何 skill (扫了 ~/.hermes/skills/ + ~/.catfish/skills/)",
        }

    ranked = idx.rank(query, top_k=top_k)
    matches = []
    for doc_idx, score in ranked:
        m = meta[doc_idx]
        desc = m["description"]
        if len(desc) > _SKILL_DESC_PREVIEW_CHARS:
            desc = desc[:_SKILL_DESC_PREVIEW_CHARS].rstrip() + "…"
        matches.append({
            "skill_path": m["skill_path"],
            "name": m["name"],
            "description": desc,
            "namespace": m["namespace"],
            "score": round(score, 3),
            "md_path": m["md_path"],
        })

    if not matches:
        summary = f"没找到匹配 '{query}' 的 skill (共扫 {len(meta)} 个 skill)"
    else:
        names = [m["skill_path"] for m in matches[:3]]
        summary = (
            f"找到 {len(matches)} 个匹配 skill (top 3: {', '.join(names)}). "
            f"想知道具体参数, 调 catfish_run_skill(skill_path='...', params={{'_help': True}})"
        )

    return {
        "matches": matches,
        "count": len(matches),
        "total_indexed": len(meta),
        "summary": summary,
    }


def tool_search_skills(args: dict) -> dict[str, Any]:
    """tool-bridge dispatch 入口. args = {"query": str, "top_k"?: int}."""
    query = str(args.get("query", "")).strip()
    if not query:
        return {
            "matches": [],
            "count": 0,
            "summary": "query 是必填. 给个关键字 (e.g. '汇报' / 'PPT' / '周报')",
        }
    top_k = args.get("top_k") or 10
    start = time.time()
    try:
        out = search_skills(query=query, top_k=int(top_k))
    except Exception as e:  # 永远不抛, 跟 sessions_search 同 pattern
        logger.warning("BL-SKILLS-RAG-TOOL: search 挂 (%s), 返友好空", e)
        return {
            "matches": [],
            "count": 0,
            "summary": f"搜出错 ({type(e).__name__}). 试调 search_skills (hermes 内置) 或直接 catfish_run_skill 名字猜.",
        }
    out["latency_ms"] = round((time.time() - start) * 1000, 1)
    return out


__all__ = ["search_skills", "tool_search_skills"]
