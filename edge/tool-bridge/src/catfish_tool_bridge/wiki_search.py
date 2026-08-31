"""P3.5.35 (6/18 鸿波 catch 'chat 是不是已经接了 wiki_search? + 装到本机后部门
wiki 不就是自家了吗?') — catfish_wiki_search native tool.

# 为啥 (audit 实证)

鸿波 6/18 多轮 audit:
1. 现状 chat 路径 LLM 看不到 wiki — `catfish_tool_schemas.py` grep `wiki_search`
   只有一处提及 (在 catfish_search_docs description 引用), **没注册成 LLM tool**.
2. 现状的 Wiki tab 内部搜索 (`wiki_read.rs:416` wiki_search_text /
   `wiki_embed.rs:171` ensure_index) hardcode 只扫
   `["wiki/entities", "wiki/concepts", "wiki/queries"]`, **不扫 wiki-shared/dept/*** —
   部门装机 wiki 物理在 ~/.catfish/wiki-shared/dept/<部门>/ 已是员工本机, 跟自家边界
   相同, 没理由排除. 是 bug 不是设计.

本 tool ship 给 LLM 用 (chat 路径), Rust 端 wiki_search_text + ensure_index 加
wiki-shared/dept walk 由 P3.5.35 同 commit 另两改动 cover (Wiki tab UI 用).

# 设计

跟 search_docs.py 同模板, 不同点:
- 扫两套目录: ~/.catfish/wiki/{entities,concepts,queries} (自家) +
  ~/.catfish/wiki-shared/dept/<部门>/ (装机部门)
- frontmatter 多字段 (title / type / tags / related)
- 返 source 字段 ("own" | "dept/<部门>") 让 LLM 知道出处, 但不区分边界 (装机
  部门 wiki 已是员工本机, 跟自家同边界)
- snippet head 1500 chars (跟 search_docs 一致)

# 跟 manifesto 公理一致

- 数据在 ~/.catfish/wiki 员工本机, 不上传中央
- LLM 主动调, pull-based
- 不调 BGE-M3 (Rust Tauri 跨进程 RPC 复杂, BM25 char-level 对 100 份内 wiki 完全够用)
- 装机部门 wiki 已在员工本机 (鸿波 6/18 catch '装到本机后不就是自家'), 同边界搜
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from .search_skills import _BM25, _tokenize

logger = logging.getLogger("catfish.tool_bridge.wiki_search")


def _catfish_home() -> Path:
    """~/.catfish/. env CATFISH_HOME 覆盖给测试用 (跟 search_docs 同模式)."""
    if env := os.environ.get("CATFISH_HOME"):
        return Path(env)
    return Path.home() / ".catfish"


def _wiki_own_dirs() -> list[Path]:
    """自家 wiki 三子目录: entities / concepts / queries."""
    home = _catfish_home()
    return [home / "wiki" / sub for sub in ("entities", "concepts", "queries")]


def _wiki_shared_dept_dirs() -> list[tuple[Path, str]]:
    """装机部门 wiki 目录, 返 (dir_path, dept_name).

    路径结构: ~/.catfish/wiki-shared/dept/<部门>/<file_id>.md
    扫一层得到所有 <部门>/.
    """
    home = _catfish_home()
    shared_root = home / "wiki-shared" / "dept"
    if not shared_root.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    try:
        for entry in shared_root.iterdir():
            if entry.is_dir():
                dept_name = entry.name
                if dept_name:
                    out.append((entry, dept_name))
    except OSError:
        return []
    return out


def _discover_all_paths() -> list[tuple[Path, str]]:
    """扫全 wiki 文件, 返 (file_path, source) 列表.

    source: "own" | "dept/<部门>" 给 LLM 看出处.
    .md 文件 only, 字母序 (per-dir).
    """
    out: list[tuple[Path, str]] = []
    # 自家
    for d in _wiki_own_dirs():
        if not d.is_dir():
            continue
        try:
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix.lower() == ".md":
                    out.append((p, "own"))
        except OSError:
            continue
    # 装机部门
    for dept_dir, dept_name in _wiki_shared_dept_dirs():
        try:
            for p in sorted(dept_dir.iterdir()):
                if p.is_file() and p.suffix.lower() == ".md":
                    out.append((p, f"dept/{dept_name}"))
        except OSError:
            continue
    return out


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

# 历史/失效条目仍保留在 wiki 中供旧链接解析，但不能作为当前事实参与
# 默认检索。需要看历史时，应通过 wiki_read 打开明确的 rel_path。
_NON_CURRENT_STATUSES = frozenset({
    "expired",
    "historical",
    "archived",
    "superseded",
    "inactive",
})


def _parse_frontmatter_field(fm: str, key: str) -> str | None:
    """简单 YAML 字段抽 (跟 wiki_read.rs:78 parse_frontmatter_field 风格一致)."""
    prefix = f"{key}:"
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip().strip("\"'")
    return None


def _parse_wiki(path: Path, source: str) -> dict[str, Any] | None:
    """读一份当前有效 wiki MD, 解 frontmatter + head 2KB body.

    deprecated/status 是生命周期字段，不影响 wiki_resolve 对历史链接的解析，
    但必须阻止旧事实进入默认回答检索。
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    title = path.stem  # default
    kind = ""  # entity / concept / query, fallback 后面填
    body = raw

    m = _FRONTMATTER_RE.match(raw)
    if m:
        fm = m.group(1)
        deprecated = (_parse_frontmatter_field(fm, "deprecated") or "").lower()
        status = (_parse_frontmatter_field(fm, "status") or "").lower()
        if deprecated in {"true", "yes", "1"} or status in _NON_CURRENT_STATUSES:
            return None
        title = _parse_frontmatter_field(fm, "title") or title
        kind = _parse_frontmatter_field(fm, "type") or kind
        body = raw[m.end():]

    # kind 兜底: 从路径推 (自家 wiki/entities/X.md → entity 等)
    if not kind:
        parts = path.parts
        # 找 "entities" / "concepts" / "queries"
        for marker, k in (("entities", "entity"), ("concepts", "concept"), ("queries", "query")):
            if marker in parts:
                kind = k
                break
        if not kind:
            kind = "entity"  # 装机部门 wiki 没标 type 时默认

    head = body.strip()[:2000]

    # 算 rel_path (相对 ~/.catfish/)
    try:
        rel_path = str(path.relative_to(_catfish_home()))
    except ValueError:
        rel_path = str(path)

    return {
        "name": path.stem,
        "title": title,
        "kind": kind,
        "source": source,
        "head": head,
        "rel_path": rel_path,
    }


# ── BM25 cache (跟 search_docs 同模式) ─────────────────────────────

_cache: dict[str, Any] = {
    "fingerprint": "",
    "index": None,
    "meta": [],
}


def _fingerprint(paths: list[tuple[Path, str]]) -> str:
    parts = []
    for p, src in paths:
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = 0
        parts.append(f"{src}:{p}:{mt}")
    return "|".join(parts)


def _build_or_get_index() -> tuple[_BM25 | None, list[dict[str, Any]]]:
    paths = _discover_all_paths()
    if not paths:
        return None, []

    fp = _fingerprint(paths)
    if _cache["fingerprint"] == fp and _cache["index"] is not None:
        return _cache["index"], _cache["meta"]

    docs: list[list[str]] = []
    meta: list[dict[str, Any]] = []
    for path, source in paths:
        parsed = _parse_wiki(path, source)
        if not parsed:
            continue
        meta.append(parsed)
        # BM25 doc = name + title + kind + head (跟 search_docs 同思路)
        docs.append(
            _tokenize(
                f"{parsed['name']} {parsed['title']} {parsed['kind']} {parsed['head']}"
            )
        )

    if not docs:
        return None, []

    idx = _BM25(docs)
    _cache["fingerprint"] = fp
    _cache["index"] = idx
    _cache["meta"] = meta
    logger.info(
        "P3.5.35 wiki_search 缓存刷新: %d 份 wiki 进索引 (含装机部门)", len(docs),
    )
    return idx, meta


# ── 公开 API ──────────────────────────────────────────────────────


_WIKI_HEAD_PREVIEW_CHARS = 1500


def search_wiki(query: str, top_k: int = 5) -> dict[str, Any]:
    """BM25 搜员工本机 wiki (自家 + 装机部门).

    Args:
        query: 自然语言 / 关键字 ("openai 价格" / "资质评估流程" / "客户机房 IP")
        top_k: 返多少份 (默认 5, 上限 15)

    Returns:
        {"matches": [{name, title, kind, source, head, rel_path, score}],
         "count", "total_indexed", "summary"}
    """
    top_k = max(1, min(int(top_k or 5), 15))
    start_ts = time.monotonic()

    idx, meta = _build_or_get_index()
    if idx is None:
        return {
            "matches": [],
            "count": 0,
            "total_indexed": 0,
            "summary": (
                "员工本机 wiki 空 (~/.catfish/wiki/ 和 ~/.catfish/wiki-shared/dept/ 都不存在或空). "
                "员工可以: (1) 在 Wiki tab 自建 entity/concept (2) 从部门 hub install 装机部门 wiki."
            ),
            "latency_ms": int((time.monotonic() - start_ts) * 1000),
        }

    ranked = idx.rank(query, top_k=top_k)
    matches = []
    for doc_idx, score in ranked:
        m = meta[doc_idx]
        head = m["head"]
        if len(head) > _WIKI_HEAD_PREVIEW_CHARS:
            head = head[:_WIKI_HEAD_PREVIEW_CHARS].rstrip() + "…"
        matches.append({
            "name": m["name"],
            "title": m["title"],
            "kind": m["kind"],          # entity / concept / query
            "source": m["source"],      # "own" | "dept/<部门>"
            "head": head,
            "rel_path": m["rel_path"],  # ~/.catfish/ 相对路径
            "score": round(score, 3),
        })

    if not matches:
        summary = (
            f"没找到匹配 '{query}' 的 wiki "
            f"(扫了 {len(meta)} 份, 含自家 + 装机部门)"
        )
    else:
        titles = [m["title"] for m in matches[:3]]
        own_count = sum(1 for m in matches if m["source"] == "own")
        dept_count = len(matches) - own_count
        src_hint = []
        if own_count:
            src_hint.append(f"{own_count} 自家")
        if dept_count:
            src_hint.append(f"{dept_count} 部门")
        summary = (
            f"找到 {len(matches)} 份 wiki ({', '.join(src_hint)}, top: {', '.join(titles)}). "
            f"head 已含关键段; 想看全文用 wiki_read 调 rel_path."
        )

    return {
        "matches": matches,
        "count": len(matches),
        "total_indexed": len(meta),
        "summary": summary,
        "latency_ms": int((time.monotonic() - start_ts) * 1000),
    }


def tool_wiki_search(args: dict) -> dict[str, Any]:
    """tool-bridge dispatch 入口. args = {"query": str, "top_k"?: int}."""
    query = str(args.get("query", "")).strip()
    if not query:
        return {
            "matches": [],
            "count": 0,
            "total_indexed": 0,
            "summary": "query 空 — 调用 catfish_wiki_search 需要 query 参数 (关键字 / 自然语言).",
            "latency_ms": 0,
        }
    top_k = args.get("top_k")
    return search_wiki(query, top_k=top_k if top_k is not None else 5)
