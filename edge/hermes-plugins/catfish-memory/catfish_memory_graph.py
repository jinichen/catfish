"""问题里点到名字的实体 → 把它在知识库里的关系一并给主聊天 (10/23 P2)。

# 为什么

知识库有类型化关系 (`related: [{name, rel}]`, Companion 同步进 ~/.catfish/wiki_graph.db),
但原来只在知识库页的关系图和早安里用。主聊天只拿到一串实体标题, 员工问「工业互联网
事业部归谁」「金智达参与过哪些认证」, 模型得自己想起来去读条目、再翻对方条目。

这里: 员工这句话里直接出现的实体名 (标题或别名), 把它的出边 + 入边一起列出来, 统一
写成「A 关系 B」(关系的读法见 edge/contracts/wiki_relation_vocab.json: 本条目 rel 对方)。

# 规则

  · 只认问题里**原样出现**的名字 (含别名), 长的优先 (「中电福富信息科技」命中时不再算「中电福富」)
  · 按关系分组, 一种关系一行; 「关联」(没标类型) 放最后、标明「未标关系类型」—— 它不说明是什么关系
  · 只读 wiki_graph.db (Companion 写), 读不到就整段不出; 按文件 mtime 缓存
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_UNTYPED = "关联"
#: 10/23: Companion 把正文 [[链接]] 记成「提及」(commands/wiki_graph.rs BODY_MENTION), 不是关系
_MENTION = "提及"
_MAX_NODES = 3
_MAX_EDGES_PER_NODE = 10
_MAX_NAMES = 8
#: 对称关系: A 同类 B 等于 B 同类 A, 两头只列一次
_SYMMETRIC = {"同类", "协作", "对标", "配套", "兄弟"}
_MIN_NAME_LEN = 2


class _Graph:
    def __init__(self, db: Path):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            nodes = con.execute("SELECT rel_path, title, aliases FROM wiki_graph_node").fetchall()
            edges = con.execute(
                "SELECT e.source_path, e.target_path, e.relation_type FROM wiki_graph_edge e"
            ).fetchall()
        finally:
            con.close()
        self.title: Dict[str, str] = {}
        self.names: List[Tuple[str, str]] = []  # (小写名字, rel_path), 长的在前
        for rel, title, aliases in nodes:
            self.title[rel] = title
            names = {title}
            try:
                names.update(a for a in json.loads(aliases or "[]") if isinstance(a, str))
            except ValueError:
                pass
            for n in names:
                n = n.strip()
                if len(n) >= _MIN_NAME_LEN:
                    self.names.append((n.lower(), rel))
        self.names.sort(key=lambda t: -len(t[0]))
        self.out: Dict[str, List[Tuple[str, str]]] = {}
        self.inc: Dict[str, List[Tuple[str, str]]] = {}
        for src, dst, rel in edges:
            self.out.setdefault(src, []).append((rel, dst))
            self.inc.setdefault(dst, []).append((rel, src))

    def mentioned(self, query: str) -> List[str]:
        q = query.lower()
        found: List[str] = []
        taken: List[Tuple[int, int]] = []
        for name, rel in self.names:
            start = q.find(name)
            if start < 0 or rel in found:
                continue
            end = start + len(name)
            # 已被更长的名字覆盖的位置不再算 (「中电福富信息科技」里的「中电福富」)
            if any(s <= start and end <= e for s, e in taken):
                continue
            taken.append((start, end))
            found.append(rel)
            if len(found) >= _MAX_NODES:
                break
        return found

    def lines_for(self, rel: str) -> List[str]:
        """按关系分组: 「本条目 rel: 对方们」/「rel 本条目 的: 来源们」。

        一条关系一行时, 中电福富这种枢纽 (几十条关系) 前 10 行全被「参与」占了, 员工问
        「有哪些部门」要的「隶属 中电福富」根本排不进来。分组后每种关系一行, 全都露得出来。
        """
        me = self.title.get(rel, rel)
        groups: Dict[Tuple[str, str], List[str]] = {}  # (方向, 关系) -> 对方
        for r, other in self.out.get(rel, []):
            groups.setdefault(("sym" if r in _SYMMETRIC else "out", r), []).append(self.title.get(other, other))
        for r, other in self.inc.get(rel, []):
            groups.setdefault(("sym" if r in _SYMMETRIC else "in", r), []).append(self.title.get(other, other))
        lines: List[str] = []
        untyped = None
        mentioned = None
        for (direction, r), names in groups.items():
            names = list(dict.fromkeys(n for n in names if n != me))  # 去重 (对称关系两头都记了)
            if not names:
                continue
            shown = "、".join(names[:_MAX_NAMES]) + (f" 等 {len(names)} 个" if len(names) > _MAX_NAMES else "")
            if r == _MENTION:
                # 正文 [[链接]]: 不是关系, 只说明正文里提到了 (两个方向合在一起)
                mentioned = (mentioned or []) + names
            elif r == _UNTYPED:
                untyped = f"  - 相关 (未标关系类型): {shown}"
            elif direction == "in":
                lines.append(f"  - {r} {me} 的: {shown}")
            else:
                lines.append(f"  - {me} {r}: {shown}")
        if untyped and len(lines) < _MAX_EDGES_PER_NODE:
            lines.append(untyped)
        if mentioned and len(lines) < _MAX_EDGES_PER_NODE:
            names = list(dict.fromkeys(mentioned))
            shown = "、".join(names[:_MAX_NAMES]) + (f" 等 {len(names)} 个" if len(names) > _MAX_NAMES else "")
            lines.append(f"  - 正文里提到 (不是关系): {shown}")
        return lines[:_MAX_EDGES_PER_NODE]


_cache: Dict[str, Tuple[Tuple[float, int], _Graph]] = {}
_lock = threading.Lock()


def _graph(db: Path) -> Optional[_Graph]:
    try:
        st = db.stat()
    except OSError:
        return None
    stamp = (st.st_mtime, st.st_size)
    with _lock:
        hit = _cache.get(str(db))
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        g = _Graph(db)
    except sqlite3.Error:
        return None
    with _lock:
        _cache[str(db)] = (stamp, g)
    return g


def render_graph(catfish_home: Path, query: str, budget: int = 1200) -> str:
    """问题里点名的实体及其关系; 没点名 / 没有关系 / 图库读不到 → 空串。"""
    if not query or not query.strip():
        return ""
    g = _graph(catfish_home / "wiki_graph.db")
    if g is None:
        return ""
    blocks: List[str] = []
    used = 0
    for rel in g.mentioned(query):
        lines = g.lines_for(rel)
        if not lines:
            continue
        block = f"- {g.title.get(rel, rel)} (`{rel}`):\n" + "\n".join(lines)
        if used + len(block) > budget:
            break
        blocks.append(block)
        used += len(block) + 1
    if not blocks:
        return ""
    return (
        "## 🕸 问题里提到的实体在知识库里的关系 (读作「A 关系 B」, 如「市场部 隶属 中电福富」= 市场部属于中电福富;"
        " 要看条目全文用 catfish_wiki_read)\n\n" + "\n".join(blocks)
    )
