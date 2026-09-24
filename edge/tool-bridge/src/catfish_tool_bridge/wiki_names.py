"""同一个名字只能指一个条目 —— wiki 写入前的认领检查 (9/24 从 wiki_files 拆出)。

跟 catfish-memory 的 wiki_resolve.name_owners / catfish_memory_wiki_dedup 同口径:
三条写入产线 (后台蒸馏 / 本工具 / Companion 界面) 一个规则。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _wiki_files():
    # wiki_files 反过来也 import 本模块 —— 延迟到调用时, 避免循环导入
    from . import wiki_files  # noqa: PLC0415
    return wiki_files


def name_owners(name: str, *, exclude: str = "") -> list[dict[str, Any]]:
    """哪些条目已用 title 或 aliases 认领了这个名字 (跟 catfish-memory
    wiki_resolve.name_owners 同口径)。9/24「福富」事件: 两个条目认领同一个名字,
    所有写这个名字的关系都解析成"指向不明"。"""
    n = (name or "").strip().lower()
    if not n:
        return []
    return [
        item for item in _wiki_files().list_wiki_files(limit=100000).get("items", [])
        if item["kind"] in ("entity", "concept")
        and item["rel_path"] != exclude
        and (item.get("ontology_status") or "active").lower() not in ("rejected", "deprecated")
        and (item["title"].strip().lower() == n
             or any(a.strip().lower() == n for a in item.get("aliases", [])))
    ]


def drop_claimed_aliases(rel_path: str, content: str) -> tuple[str, list[str]]:
    """别名已被别的条目认领 → 去掉, 返回 (新内容, 被去掉的别名)。"""
    wf = _wiki_files()
    fm, _ = wf._split_frontmatter(content)
    m = re.search(r"^aliases:[ \t]*\[(.*)\][ \t]*$", fm, re.MULTILINE)
    if not m:
        return content, []
    aliases = [a.strip().strip('"').strip("'") for a in wf._split_top_level(m.group(1)) if a.strip()]
    dropped = [a for a in aliases if name_owners(a, exclude=rel_path)]
    if not dropped:
        return content, []
    keep = [a for a in aliases if a not in dropped]
    line = "aliases: [" + ", ".join('"' + a.replace('"', "") + '"' for a in keep) + "]"
    return content.replace(fm, fm[: m.start()] + line + fm[m.end():], 1), dropped


def ontology_status_for_create(
    home: Path,
    kind: str,
    subtype: str,
    related: list[str | dict[str, Any]],
) -> tuple[str, list[str]]:
    """让工具写入与蒸馏写入遵守同一条 pending 规则。"""
    # 9/24: 关系是可选的 —— 没写关系直接 active (以前判 pending, 员工只能硬配一条)。
    if not related:
        return "active", []
    wf = _wiki_files()
    existing = wf.list_wiki_files(limit=100000).get("items", [])
    reasons: list[str] = []
    # 9/17: 没 rel / rel 是兜底「关联」都算没类型 —— 跟蒸馏侧同口径
    if any(wf._relation_is_untyped(raw) for raw in related):
        reasons.append("untyped_relation")
    for raw in related:
        name = wf._relation_name(raw)
        if not name:
            continue
        matches = [
            item for item in existing
            if item["kind"] in ("entity", "concept")
            # 9/24: 目标「待确认」也算找得到 (否则同批互相引用的条目全体 pending);
            # 只排除明确废弃/驳回的。跟蒸馏侧 _classify_ontology_status 同口径。
            and (item.get("ontology_status") or "active").lower() not in ("rejected", "deprecated")
            and (item["title"].strip().lower() == name.lower()
            or item["slug"].strip().lower() == name.lower()
            or any(a.strip().lower() == name.lower() for a in item.get("aliases", [])))
        ]
        if len(matches) == 0:
            reasons.append("unresolved_relation")
        elif len(matches) > 1:
            reasons.append("ambiguous_relation")
    return ("pending", sorted(set(reasons))) if reasons else ("active", [])
