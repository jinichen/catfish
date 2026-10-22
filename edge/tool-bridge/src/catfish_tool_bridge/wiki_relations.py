"""知识库条目的关系 (出边 + 入边) 和按名字找条目 —— 给 catfish_wiki_read 用 (10/23 P2)。

条目自己的 frontmatter 里只有**出边** (`related: [{name, rel}]`, 读作「本条目 rel 对方」)。
「谁隶属于企发部」「哪些项目用了这个系统」是**入边**, 只看本条目永远看不到 —— 原来模型
得把整个知识库挨个读一遍。Companion 已经把全部关系同步进 ~/.catfish/wiki_graph.db
(commands/wiki_graph.rs), 这里只读它。

读不到图库 (Windows 上知识库页没开过 / 新装机器) 就返 None, 调用方照旧只给 frontmatter。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

_UNTYPED = "关联"


def _db(home: Path) -> Optional[sqlite3.Connection]:
    db = home / "wiki_graph.db"
    if not db.exists():
        return None
    try:
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None


def relations_of(home: Path, rel_path: str) -> Optional[dict[str, Any]]:
    """{"out": [{rel, target, rel_path}], "in": [{rel, source, rel_path}], "untyped": n}; 图库没有 → None。"""
    con = _db(home)
    if con is None:
        return None
    try:
        out = con.execute(
            "SELECT e.relation_type, n.title, e.target_path FROM wiki_graph_edge e "
            "JOIN wiki_graph_node n ON n.rel_path = e.target_path WHERE e.source_path = ?",
            (rel_path,),
        ).fetchall()
        inc = con.execute(
            "SELECT e.relation_type, n.title, e.source_path FROM wiki_graph_edge e "
            "JOIN wiki_graph_node n ON n.rel_path = e.source_path WHERE e.target_path = ?",
            (rel_path,),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return {
        "reading": "out: 本条目 rel target; in: source rel 本条目。rel=关联 表示还没标类型",
        "out": [{"rel": r, "target": t, "rel_path": p} for r, t, p in out],
        "in": [{"rel": r, "source": t, "rel_path": p} for r, t, p in inc],
        "untyped": sum(1 for r, *_ in out + inc if r == _UNTYPED),
    }


def find_by_name(home: Path, name: str) -> list[dict[str, str]]:
    """标题或别名完全相同 (忽略大小写和首尾空白) 的条目。没有图库 → 空列表。"""
    con = _db(home)
    if con is None:
        return []
    want = name.strip().lower()
    try:
        rows = con.execute("SELECT rel_path, title, aliases FROM wiki_graph_node").fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    hits = []
    for rel_path, title, aliases in rows:
        names = {title}
        try:
            names.update(a for a in json.loads(aliases or "[]") if isinstance(a, str))
        except ValueError:
            pass
        if any(n.strip().lower() == want for n in names):
            hits.append({"rel_path": rel_path, "title": title})
    return hits
