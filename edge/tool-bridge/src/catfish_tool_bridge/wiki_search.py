"""catfish_wiki_search —— 小鲶在对话里搜员工本机知识库 (自家 + 装机部门)。

P3.5.35 (6/18) 第一版只有 BM25、只索引正文前 2000 字、不看别名 / 属性 / 关系, 跟界面上的知识库搜索
(Rust wiki_search_hybrid: 词法 + BGE-M3 向量 + 关系扩一跳) 是两套。11/08 鸿波机器上实测:
「我们公司有哪些证书快到期了」搜出两个人名, 证书编号搜不到 (分词 bug, 见 search_skills._tokenize)。

11/08 起跟界面同一套思路, 在 Python 里再做一遍 (tool-bridge 跟 Companion 不同进程, 调不到 Rust):
  1. 词法: BM25, 一条知识 = 标题 + 别名 + 标签 + 类型 + 属性值 + 关系对象名 + 全文
  2. 语义: 问题的向量借 Companion 算 (services/embed_socket.rs, 跟界面同一个模型), 跟 Companion 已经
     建好的分块向量 (~/.catfish/wiki_embeddings.db, wiki_embed_chunk) 算余弦; 门槛跟 Rust 同一条
     (比全库平均高出 SEMANTIC_MARGIN 才算命中)。Companion 没开 / 没向量 → 只用词法, 结果里说明。
  3. 融合: RRF 按排名融合 (两个分数空间不直接相加)
  4. 关系扩一跳: 从「按意思也相关」的结果顺着 related (出边 + 入边, 都从 frontmatter 算, 不依赖图库)
     拉进关联条目; 已经命中的只加分, 不盖过直接命中; 关系太多的枢纽 (> HUB_DEGREE) 不扩
  5. 过滤: 废弃 / 历史状态的不进索引; 待确认 (ontology_status=pending/rejected) 和按类型定义算出
     「已过期 / 已结束」的默认不列, 结果里说有几条没列、怎么看
结果每条多带: 类型、属性 (已填的)、算出来的状态、关系数、命中方式。

数据在 ~/.catfish 员工本机, 不上传中央; 小鲶主动调, pull-based。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import socket
import sqlite3
import time
from array import array
from pathlib import Path
from typing import Any

from .search_skills import _BM25, _tokenize

logger = logging.getLogger("catfish.tool_bridge.wiki_search")

#: 语义命中的门槛: 比这个问题跟全部分块的平均相似度高出这么多 (跟 wiki_embed.rs SEMANTIC_MARGIN 同一条)
SEMANTIC_MARGIN = 0.11
RRF_K = 60.0
#: 关系太多的条目 (「中电福富」挂着一百多条) 不往外扩, 不然什么都搜出它的邻居
HUB_DEGREE = 60
GRAPH_BONUS_FOR_DIRECT_HITS = 0.25
_WIKI_HEAD_PREVIEW_CHARS = 1500
_EMBED_TIMEOUT = 6.0
_EMBED_COOLDOWN = 60.0


def _catfish_home() -> Path:
    env = os.environ.get("CATFISH_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".catfish"


# ── 扫目录 ──────────────────────────────────────────────────────


def _wiki_own_dirs() -> list[Path]:
    """自家 wiki 三子目录: entities / concepts / queries。"""
    home = _catfish_home()
    return [home / "wiki" / sub for sub in ("entities", "concepts", "queries")]


def _wiki_shared_dept_dirs() -> list[tuple[Path, str]]:
    """装机部门 wiki 目录, 返 (dir_path, dept_name)。路径 ~/.catfish/wiki-shared/dept/<部门>/<file_id>.md。"""
    shared_root = _catfish_home() / "wiki-shared" / "dept"
    if not shared_root.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    try:
        for entry in shared_root.iterdir():
            if entry.is_dir() and entry.name:
                out.append((entry, entry.name))
    except OSError:
        return []
    return out


def _discover_all_paths() -> list[tuple[Path, str]]:
    """全部 wiki 文件, 返 (file_path, source)。source: "own" | "dept/<部门>"。"""
    out: list[tuple[Path, str]] = []
    for d in [*_wiki_own_dirs(), _catfish_home() / "wiki" / "raw" / "sources"]:   # 11/08: 原始资料也进索引 (默认不列)
        if not d.is_dir():
            continue
        try:
            out += [(p, "own") for p in sorted(d.iterdir()) if p.is_file() and p.suffix.lower() == ".md"]
        except OSError:
            continue
    for dept_dir, dept_name in _wiki_shared_dept_dirs():
        try:
            out += [(p, f"dept/{dept_name}") for p in sorted(dept_dir.iterdir())
                    if p.is_file() and p.suffix.lower() == ".md"]
        except OSError:
            continue
    return out


# ── 解析一条知识 ───────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_NON_CURRENT_STATUSES = frozenset({"expired", "historical", "archived", "superseded", "inactive"})
#: 不算属性的 frontmatter 键 (日期 / 出处 / 标志位); 其余顶层 `键: 值` 都当属性, 值进索引、结果里带出来
_SKIP_KEYS = frozenset({"type", "title", "created", "updated", "sources", "source", "related", "tags", "aliases",
                        "ontology_status", "deprecated", "status", "authored_by", "evidence", "unverified",
                        "kept_path", "uploaded", "bytes", "filename", "conflicts", "action_refs",
                        "entity_type", "concept_type",
                        # 11/08 分享治理: 部门共享页的审核字段 (wiki_install 写的), 不是属性
                        "review_status", "reviewed_by", "reviewed_at", "review_note", "withdrawn_by", "withdrawn_at"})
_MD_NOISE = re.compile(r"[#*>`|_\[\]()]+")


def _parse_frontmatter_field(fm: str, key: str) -> str | None:
    prefix = f"{key}:"
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip().strip("\"'")
    return None


def _parse_wiki(path: Path, source: str) -> dict[str, Any] | None:
    """读一条当前有效的知识: frontmatter 里的名字 / 类型 / 属性 / 关系 + 正文。废弃的、历史状态的返 None。"""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    title = path.stem
    kind = ""
    body = raw
    fm = ""
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
    if "raw" in path.parts and "sources" in path.parts:
        kind = "source"   # 员工上传 / 入库的原件转存 (wiki/raw/sources), 不是整理过的知识
    if not kind:
        for marker, k in (("entities", "entity"), ("concepts", "concept"), ("queries", "query")):
            if marker in path.parts:
                kind = k
                break
        kind = kind or "entity"
    aliases: list[str] = []
    tags: list[str] = []
    subtype = ""
    props: dict[str, str] = {}
    related: list[str] = []
    if fm:
        from .wiki_files import _parse_list, _parse_related  # noqa: PLC0415  (同一份 frontmatter 口径)
        aliases = [x for x in _parse_list(fm, "aliases") if x]
        tags = [x for x in _parse_list(fm, "tags") if x]
        subtype = _parse_frontmatter_field(fm, "entity_type") or _parse_frontmatter_field(fm, "concept_type") or ""
        related = [r["name"] for r in _parse_related(fm) if r.get("name")]
        for line in fm.splitlines():
            if ":" not in line or line.startswith((" ", "\t", "-")):
                continue
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip().strip("\"'")
            if k and v and k not in _SKIP_KEYS and not v.startswith("["):
                props[k] = v
    ontology_status = (_parse_frontmatter_field(fm, "ontology_status") or "active").lower() if fm else "active"
    # 11/08 分享治理: 装机部门页带 review_status (pending 待审 / approved / withdrawn 已撤回); 自家的和老副本都算 approved
    review_status = (_parse_frontmatter_field(fm, "review_status") or "approved").lower() if fm else "approved"
    try:
        rel_path = str(path.relative_to(_catfish_home()))
    except ValueError:
        rel_path = str(path)
    return {
        "name": path.stem, "title": title, "kind": kind, "source": source, "rel_path": rel_path,
        "head": body.strip()[:_WIKI_HEAD_PREVIEW_CHARS + 500],
        "aliases": aliases, "tags": tags, "subtype": subtype, "props": props, "related": related,
        "ontology_status": ontology_status, "review_status": review_status, "fm": fm, "body": body,
    }


def _index_text(p: dict[str, Any]) -> str:
    """BM25 看的文本: 名字类的写两遍 (标题命中比正文命中更该靠前), 再加属性值、关系对象名、全文。"""
    names = " ".join([p["title"], p["name"], *p["aliases"]])
    return " ".join([names, names, " ".join(p["tags"]), p["subtype"], " ".join(p["props"].values()),
                     " ".join(p["related"]), _MD_NOISE.sub(" ", p["body"])])


# ── 索引 (进程内缓存, 按文件 mtime 指纹刷新) ──────────────────────

_cache: dict[str, Any] = {"fingerprint": "", "index": None, "meta": []}


def _fingerprint(paths: list[tuple[Path, str]]) -> str:
    parts = []
    for p, _ in paths:
        try:
            parts.append(f"{p}:{p.stat().st_mtime_ns}")
        except OSError:
            continue
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
        docs.append(_tokenize(_index_text(parsed)))
        parsed["type_status"] = _type_status(parsed)
        meta.append(parsed)
    if not docs:
        return None, []
    idx = _BM25(docs)
    _attach_graph(meta)
    _cache.update(fingerprint=fp, index=idx, meta=meta)
    logger.info("wiki_search 索引刷新: %d 条 (含装机部门)", len(docs))
    return idx, meta


def _type_status(p: dict[str, Any]) -> dict[str, str] | None:
    """按类型定义算出来的状态 (证书: 有效 / 即将到期 / 已过期; 项目: 进行中 / 已结束 …), 没类型定义的 None。"""
    if not p["fm"]:
        return None
    try:
        from . import wiki_types  # noqa: PLC0415
        schema = wiki_types.load_schema()
        tk = wiki_types.type_of(p["fm"], schema)
        if not tk:
            return None
        return wiki_types.status_of(wiki_types.read_props(p["fm"], schema[tk]), schema[tk])
    except Exception:  # noqa: BLE001  (类型定义文件坏了不能让搜索挂掉)
        return None


def _attach_graph(meta: list[dict[str, Any]]) -> None:
    """按标题 / 别名把 related 解析成下标, 出边 + 入边都挂到每条上 (不依赖 Companion 的图库)。"""
    by_name: dict[str, list[int]] = {}
    for i, p in enumerate(meta):
        for n in [p["title"], *p["aliases"]]:
            by_name.setdefault(n.strip().lower(), []).append(i)
    for p in meta:
        p["out"] = []
        p["in"] = []
    for i, p in enumerate(meta):
        for name in p["related"]:
            hits = by_name.get(name.strip().lower()) or []
            if len(hits) != 1 or hits[0] == i:
                continue
            p["out"].append(hits[0])
            meta[hits[0]]["in"].append(i)
    for p in meta:
        p["degree"] = len(set(p["out"]) | set(p["in"]))


# ── 语义 (借 Companion 的向量) ─────────────────────────────────────

_embed_down_until = 0.0
_vec_cache: dict[str, Any] = {"mtime": None, "rows": []}


def _companion_port() -> int | None:
    try:
        return int((_catfish_home() / "companion-embed.port").read_text().strip())
    except (OSError, ValueError):
        return None


def _normalized(vec: Any) -> array:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array("f", (x / n for x in vec))


def _embed_query(text: str) -> array | None:
    """问题 → 归一化向量; Companion 没开 / 算不了 → None (一分钟内不再试)。"""
    global _embed_down_until
    if time.time() < _embed_down_until:
        return None
    port = _companion_port()
    if port is None:
        return None
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_EMBED_TIMEOUT) as sock:
            sock.settimeout(_EMBED_TIMEOUT)
            sock.sendall((json.dumps({"texts": [text]}, ensure_ascii=False) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(1 << 20)
                if not chunk:
                    break
                buf += chunk
        data = json.loads(buf.decode("utf-8") or "{}")
        vec = (data.get("vectors") or [None])[0]
        if data.get("error") or not vec:
            raise RuntimeError(data.get("error") or "没返回向量")
    except Exception as e:  # noqa: BLE001
        _embed_down_until = time.time() + _EMBED_COOLDOWN
        logger.info("wiki_search: Companion 那边算不了向量 (%s), %d 秒内只用词法", e, int(_EMBED_COOLDOWN))
        return None
    return _normalized(vec)


def _chunk_vectors() -> list[tuple[str, str, array]]:
    """Companion 建好的分块向量 (rel_path, 那段的摘录, 归一化向量), 按库文件 mtime 缓存。"""
    db = _catfish_home() / "wiki_embeddings.db"
    try:
        mtime = db.stat().st_mtime_ns
    except OSError:
        return []
    if _vec_cache["mtime"] == mtime:
        return _vec_cache["rows"]
    rows: list[tuple[str, str, array]] = []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            for rel, snippet, blob in con.execute("SELECT rel_path, snippet, vector FROM wiki_embed_chunk"):
                v = array("f")
                v.frombytes(blob)
                rows.append((rel, snippet or "", _normalized(v)))
        finally:
            con.close()
    except sqlite3.Error as e:
        logger.info("wiki_search: 读不了向量库 (%s), 只用词法", e)
        return []
    _vec_cache.update(mtime=mtime, rows=rows)
    return rows


def _dot(a: array, b: array) -> float:
    return sum(x * y for x, y in zip(a, b))


def _semantic(query: str, by_rel: dict[str, int], limit: int = 50) -> list[tuple[int, float, str]] | None:
    """按意思找: [(meta 下标, 相似度, 最像的那段)], 按相似度降序。向量用不了 → None。"""
    rows = _chunk_vectors()
    if not rows:
        return None
    q = _embed_query(query)
    if q is None:
        return None
    sims = [(_dot(q, v), rel, snippet) for rel, snippet, v in rows if len(v) == len(q)]
    if not sims:
        return None
    mean = sum(s for s, _, _ in sims) / len(sims)
    best: dict[str, tuple[float, str]] = {}
    for s, rel, snippet in sims:
        if s < mean + SEMANTIC_MARGIN or rel not in by_rel:
            continue
        if rel not in best or s > best[rel][0]:
            best[rel] = (s, snippet)
    ranked = sorted(((by_rel[rel], s, sn) for rel, (s, sn) in best.items()), key=lambda t: -t[1])
    return ranked[:limit]


# ── 融合 + 关系扩一跳 ─────────────────────────────────────────────


def _rrf(lexical: list[tuple[int, float]], semantic: list[tuple[int, float, str]] | None) -> dict[int, dict[str, Any]]:
    fused: dict[int, dict[str, Any]] = {}
    for rank, (i, _s) in enumerate(lexical):
        f = fused.setdefault(i, {"score": 0.0, "matched_in": [], "snippet": None})
        f["score"] += 1.0 / (RRF_K + rank + 1)
        f["matched_in"].append("lexical")
    for rank, (i, _s, snippet) in enumerate(semantic or []):
        f = fused.setdefault(i, {"score": 0.0, "matched_in": [], "snippet": None})
        f["score"] += 1.0 / (RRF_K + rank + 1)
        f["matched_in"].append("semantic")
        f["snippet"] = f["snippet"] or snippet
    return fused


def _expand(fused: dict[int, dict[str, Any]], meta: list[dict[str, Any]], seeds: list[int]) -> None:
    """从种子顺着关系扩一跳: 邻居没命中的加进来 (分数按种子名次递减), 已命中的只加一点分。"""
    for rank, i in enumerate(seeds):
        p = meta[i]
        if p["degree"] > HUB_DEGREE:
            continue
        bonus = 1.0 / (RRF_K + rank + 1) * 0.6
        for j in set(p["out"]) | set(p["in"]):
            if meta[j]["degree"] > HUB_DEGREE:
                continue
            if j in fused:
                if "graph-1hop" not in fused[j]["matched_in"]:
                    fused[j]["score"] += bonus * GRAPH_BONUS_FOR_DIRECT_HITS
                    fused[j]["matched_in"].append("graph-1hop")
            else:
                fused[j] = {"score": bonus, "matched_in": ["graph-1hop"], "snippet": None, "via": p["title"]}


def _name_hits(query: str, meta: list[dict[str, Any]], fused: dict[int, dict[str, Any]]) -> None:
    """问题里原文写着某条的标题 / 别名 → 那条排最前 (11/08 金标: 「北京福富…福州分公司 的全称」被母公司和「北京分公司」挤出前五,
    单字 BM25 分不清前缀)。越长的名字越靠前; 两字以下的别名不算 (太容易误中)。"""
    q = query.lower()
    for i, p in enumerate(meta):
        names = [n for n in [p["title"], *p["aliases"]] if len(n.strip()) >= 3]
        hit = max((len(n) for n in names if n.strip().lower() in q), default=0)
        if not hit:
            continue
        f = fused.setdefault(i, {"score": 0.0, "matched_in": [], "snippet": None})
        f["score"] += 1.0 + hit / 100.0
        f["matched_in"].insert(0, "name")


# ── 公开 API ──────────────────────────────────────────────────────


def search_wiki(query: str, top_k: int = 5, *, include_expired: bool = False,
                include_pending: bool = False, include_sources: bool = False,
                include_withdrawn: bool = False) -> dict[str, Any]:
    """混合搜员工本机 wiki (自家 + 装机部门)。

    Returns: {"matches": [{name, title, kind, type, source, head, rel_path, score, matched_in, status, props,
              relations, aliases?, via?, review? ("待审": 部门页主管还没审)}], "count", "total_indexed",
              "semantic" (向量用上了没), "hidden" (没列的条数; withdrawn = 部门里撤回的共享页), "summary"}
    """
    top_k = max(1, min(int(top_k or 5), 15))
    start_ts = time.monotonic()
    idx, meta = _build_or_get_index()
    if idx is None:
        return {"matches": [], "count": 0, "total_indexed": 0, "semantic": False, "hidden": {},
                "summary": ("员工本机 wiki 空 (~/.catfish/wiki/ 和 ~/.catfish/wiki-shared/dept/ 都不存在或空). "
                            "员工可以: (1) 在知识体系页自建条目 (2) 从部门装机部门 wiki."),
                "latency_ms": int((time.monotonic() - start_ts) * 1000)}
    by_rel = {p["rel_path"]: i for i, p in enumerate(meta)}
    lexical = idx.rank(query, top_k=50)
    semantic = _semantic(query, by_rel)
    fused = _rrf(lexical, semantic)
    _name_hits(query, meta, fused)
    seeds = [i for i, _s, _sn in semantic] if semantic else [i for i, _s in lexical]
    _expand(fused, meta, seeds[:10])

    hidden = {"expired": 0, "pending": 0, "sources": 0, "withdrawn": 0}
    matches = []
    unreviewed = 0
    for i, f in sorted(fused.items(), key=lambda kv: -kv[1]["score"]):
        p = meta[i]
        if p["kind"] == "source" and not include_sources:
            hidden["sources"] += 1
            continue
        if p.get("review_status") == "withdrawn" and not include_withdrawn:
            hidden["withdrawn"] += 1
            continue
        st = p.get("type_status")
        if st and st.get("code") in ("expired", "done") and not include_expired:
            hidden["expired"] += 1
            continue
        if p["ontology_status"] in ("pending", "rejected") and not include_pending:
            hidden["pending"] += 1
            continue
        if len(matches) >= top_k:
            continue
        head = f["snippet"] or p["head"]
        if len(head) > _WIKI_HEAD_PREVIEW_CHARS:
            head = head[:_WIKI_HEAD_PREVIEW_CHARS].rstrip() + "…"
        row: dict[str, Any] = {
            "name": p["name"], "title": p["title"], "kind": p["kind"], "type": p["subtype"] or None,
            "source": p["source"], "head": head, "rel_path": p["rel_path"], "score": round(f["score"], 4),
            "matched_in": f["matched_in"], "status": st["value"] if st else None,
            "props": p["props"], "relations": p["degree"],
        }
        if p["aliases"]:
            row["aliases"] = p["aliases"]
        if f.get("via"):
            row["via"] = f["via"]
        if p.get("review_status") == "pending":
            row["review"] = "待审"   # 部门共享页, 主管还没审过: 照常列, 但标出来
            unreviewed += 1
        elif p.get("review_status") == "withdrawn":
            row["review"] = "已撤回"
        matches.append(row)

    hidden = {k: v for k, v in hidden.items() if v}
    if not matches:
        summary = f"没找到匹配 '{query}' 的 wiki (扫了 {len(meta)} 份, 含自家 + 装机部门)"
    else:
        titles = [m["title"] for m in matches[:3]]
        own = sum(1 for m in matches if m["source"] == "own")
        src = ", ".join(x for x in [f"{own} 自家" if own else "", f"{len(matches) - own} 部门" if len(matches) > own else ""] if x)
        summary = (f"找到 {len(matches)} 份 wiki ({src}, top: {', '.join(titles)}). "
                   "head 是最相关的一段; 想看全文和全部关系用 catfish_wiki_read 调 rel_path; "
                   "按属性筛 (到期 / 某部门 / 没填) 用 catfish_wiki_query.")
    if unreviewed:
        summary += f" 其中 {unreviewed} 份是部门同事分享、主管还没审过的页 (review=待审), 引用时说明一下。"
    if semantic is None:
        summary += " (这次只按字面匹配, 没用上向量: Companion 没开或还没建向量)"
    if hidden:
        labels = {"expired": "条已过期 / 已结束", "pending": "条待确认", "sources": "份原始资料 (上传的原件)",
                  "withdrawn": "份部门里已撤回的共享页"}
        parts = [f"{n} {labels[k]}" for k, n in hidden.items()]
        summary += (f" 另有 {', '.join(parts)} 没列 (要看就传 include_expired / include_pending / include_sources / "
                    "include_withdrawn=true)。")
    return {"matches": matches, "count": len(matches), "total_indexed": len(meta), "semantic": semantic is not None,
            "hidden": hidden, "summary": summary, "latency_ms": int((time.monotonic() - start_ts) * 1000)}


def tool_wiki_search(args: dict) -> dict[str, Any]:
    """tool-bridge dispatch 入口. args = {"query": str, "top_k"?: int, "include_expired"?, "include_pending"?}。"""
    query = str(args.get("query", "")).strip()
    if not query:
        return {"matches": [], "count": 0, "total_indexed": 0, "summary": "query 空: 给关键词或问题", "error": "query 空"}
    try:
        return search_wiki(query, top_k=int(args.get("top_k") or 5), include_expired=bool(args.get("include_expired")),
                           include_pending=bool(args.get("include_pending")), include_sources=bool(args.get("include_sources")),
                           include_withdrawn=bool(args.get("include_withdrawn")))
    except Exception as e:  # noqa: BLE001
        logger.exception("wiki_search 失败")
        return {"matches": [], "count": 0, "total_indexed": 0, "summary": f"wiki 搜索出错: {e}", "error": str(e)}
