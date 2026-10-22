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


def _home() -> Path:
    """~/.catfish (env CATFISH_HOME 覆盖, 跟 wiki_files._catfish_home 同口径)。"""
    import os  # noqa: PLC0415
    override = os.environ.get("CATFISH_HOME")
    return Path(override) if override else Path.home() / ".catfish"


# 9/17: related[].rel 受控词表 —— 跟蒸馏侧 / Companion UI 同一份
# (edge/contracts/wiki_relation_vocab.json)。表外 → 兜底「关联」, 而「关联」不算
# 有类型 → pending。三条写入产线一个口径, 见 catfish_memory_wiki._normalize_relations。
_RELATION_FALLBACK = "关联"


def _custom_relations() -> frozenset[str]:
    """10/23: 员工在知识体系「关系类型」里登记的自定义关系 (Companion 写 ~/.catfish/wiki_relation_vocab.custom.json)。"""
    try:
        data = json.loads((_home() / "wiki_relation_vocab.custom.json").read_text(encoding="utf-8"))
        rel = data.get("relations") or {}
        return frozenset(str(k) for k in rel) if isinstance(rel, dict) else frozenset()
    except (OSError, json.JSONDecodeError, AttributeError):
        return frozenset()


def _load_relation_vocab() -> tuple[frozenset[str], dict[str, str], str]:
    contract = Path(__file__).resolve().parents[3] / "contracts" / "wiki_relation_vocab.json"
    try:
        data = json.loads(contract.read_text(encoding="utf-8"))
        return frozenset(data["relations"]) | _custom_relations(), dict(data["aliases"]), str(data["fallback"])
    except (OSError, json.JSONDecodeError, KeyError):
        return frozenset(), {}, _RELATION_FALLBACK


def _canonical_relation(rel: str) -> str:
    value = rel.strip().strip('"').strip("'")
    relations, aliases, fallback = _load_relation_vocab()
    if not value:
        return fallback
    if not relations:
        return value
    candidate = aliases.get(value, value)
    return candidate if candidate in relations else fallback



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
        "reading": "out: 本条目 rel target; in: source rel 本条目。rel=关联 表示还没标类型; rel=提及 只是正文里提到, 不是关系",
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
