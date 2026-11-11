"""Word 合并 · 把底稿和每份交回稿的内容对齐、合并, 得到一棵「合并计划」(还没写 XML, 写在 word_track)。

一层层合 (同一个函数递归用在正文、页眉页脚、脚注、表格单元格里):
  块 (段落 / 表格)   core.merge_seq, 段落按内容签名对齐; **表格按「首行文字 + 列数」对齐** —— 表里改了几格不影响对齐,
                     对上了再往里合:
  表格行             merge_seq, 行按内容签名; 两人改了同一行 (行级冲突) 且行数、格数都对得上 → 拆到单元格:
  单元格             单元格里又是一串块, 回到第一层。
所以两个人改同一张表的不同格子不算冲突, 改到同一格的同一段才是。

几条补充规则 (都在这里, analyze 和 render 走同一条路, 冲突编号一致):
  · 一个人改了表头 (表格整个换了) 而别人改了这张表里的格子 → 冲突, 不能悄悄丢掉别人的修改。
  · 冲突里只有一个人改了文字、其他人只改了格式 → 用改文字的, 警告里写「谁的格式调整没合进来」。
  · 批注: 只加批注没改字的段落签名不变 (锚点不进签名), 合并版里这段换成加了批注的那一版; 几个人都在这段加了批注,
    其余人的批注锚到整段。段落被别人删了, 批注没地方放 → 警告。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .core import Option, Seg, Unit, cid_of, merge_seq
from .word_norm import COMMENT_TAGS, heading_level, is_w, local, sig, text
from .word_pkg import Pkg, w, wa


@dataclass
class Ver:
    who: str      # "" = 底稿
    pkg: Pkg
    at: str = ""
    new_comments: set[str] = field(default_factory=set)


@dataclass
class M:
    base: Ver
    eds: dict[str, Ver]
    order: list[str]
    names: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    part_map: dict[str, dict[str, str]] = field(default_factory=dict)   # 底稿部件 → {谁: 他那份里对应的部件} (页眉页脚会改名)

    def ep(self, who: str, part: str) -> str:
        return self.part_map.get(part, {}).get(who, part) if who else part

    def name(self, who: str) -> str:
        return self.names.get(who) or who.split("@")[0]

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)


def _ident(el: Any) -> str:
    if is_w(el, "tbl"):
        rows = el.findall(w("tr"))
        first = text(rows[0]) if rows else ""
        cols = len(rows[0].findall(w("tc"))) if rows else 0
        return "tbl:" + hashlib.sha1(f"{cols}|{first}".encode("utf-8")).hexdigest()
    return ""


def _units(m: "M", els: list[Any], ver: Ver, part: str) -> list[Unit]:
    out = []
    p = m.ep(ver.who, part)
    for el in els:
        ident = _ident(el)
        out.append(Unit(ident or sig(el, ver.pkg, p), text(el), (el, ver.who, p)))
    return out


def _eqmap(base: list[Unit], ver: list[Unit]) -> dict[int, int]:
    sm = SequenceMatcher(None, [u.sig for u in base], [u.sig for u in ver], autojunk=False)
    out = {}
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            out[a + k] = b + k
    return out


def blocks_of(container: Any) -> list[Any]:
    return [c for c in container if is_w(c, "p", "tbl", "sdt")]


def _has_new_comment(el: Any, ver: Ver) -> bool:
    return bool(ver.new_comments) and any(local(n) in COMMENT_TAGS and wa(n, "id") in ver.new_comments for n in el.iter() if isinstance(n.tag, str))


class Labels:
    """底稿第 i 块给人看的位置。"""

    def __init__(self, units: list[Unit], pkg: Pkg, prefix: str):
        self.at: list[str] = []
        head, k = None, 0
        for u in units:
            el = u.data[0]
            if is_w(el, "p") and heading_level(el, pkg) and u.text.strip():
                head, k = u.text.strip()[:24], 0
                self.at.append(f"{prefix}标题「{head}」")
                continue
            k += 1
            if is_w(el, "tbl"):
                self.at.append(f"{prefix}{'「' + head + '」下' if head else ''}表格「{u.text.strip()[:12]}」")
            else:
                self.at.append(f"{prefix}{'「' + head + '」下' if head else ''}第 {k} 段")
        self.prefix = prefix

    def of(self, seg: Seg) -> str:
        if not self.at:
            return self.prefix or "开头"
        if seg.start == seg.end:
            return (self.at[seg.start - 1] + "后面") if seg.start else (self.prefix + "开头")
        tail = f" 起 {seg.end - seg.start} 块" if seg.end - seg.start > 1 else ""
        return self.at[seg.start] + tail


def merge_blocks(m: M, base_els: list[Any], vers: dict[str, list[Any]], part: str, scope: str, prefix: str) -> list[dict[str, Any]]:
    bu = _units(m, base_els, m.base, part)
    vu = {who: _units(m, els, m.eds[who], part) for who, els in vers.items()}
    order = [x for x in m.order if x in vu]
    segs = merge_seq(bu, vu, order, scope)
    maps = {who: _eqmap(bu, vu[who]) for who in order}
    labels = Labels(bu, m.base.pkg, prefix)
    # 表格内容被改了 (表头没动) 的人
    tchg: dict[int, list[str]] = {}
    for i, u in enumerate(bu):
        if u.sig.startswith("tbl:"):
            full = sig(u.data[0], m.base.pkg, part)
            tchg[i] = [x for x in order if i in maps[x] and sig(vu[x][maps[x][i]].data[0], m.eds[x].pkg, m.ep(x, part)) != full]
    segs = [_hidden_table_edits(s, bu, vu, maps, tchg, order, scope) for s in segs]
    segs = [_format_only(m, s, labels) for s in segs]
    items: list[dict[str, Any]] = []
    for s in segs:
        if s.kind == "keep":
            for i in range(s.start, s.end):
                el = bu[i].data[0]
                if tchg.get(i):
                    items.append(merge_table(m, el, {x: vu[x][maps[x][i]].data[0] for x in tchg[i]}, part,
                                             f"{scope}/t{i}", labels.at[i]))
                    continue
                donors = [(x, vu[x][maps[x][i]].data[0]) for x in order if i in maps[x] and _has_new_comment(vu[x][maps[x][i]].data[0], m.eds[x])]
                items.append({"t": "keep", "el": el, "part": part, "donors": donors})
            continue
        lost = [(x, vu[x][maps[x][i]].data[0]) for i in range(s.start, s.end) for x in order
                if i in maps[x] and _has_new_comment(vu[x][maps[x][i]].data[0], m.eds[x])]
        if s.kind == "change":
            items.append({"t": "change", "seg": s, "part": part, "donors": lost, "where": labels.of(s)})
        else:
            c = _conflict_view(m, s, labels.of(s))
            m.conflicts.append(c)
            items.append({"t": "conflict", "seg": s, "part": part, "donors": lost, "where": c["where"]})
    return items


def _region(bu: list[Unit], vu: list[Unit], mp: dict[int, int], start: int, end: int) -> list[Unit]:
    return [vu[mp[i]] if i in mp else bu[i] for i in range(start, end)]


def _hidden_table_edits(s: Seg, bu, vu, maps, tchg, order, scope) -> Seg:
    """一片被 A 整体改了 (换了表头 / 删了表), 而 B 改了这片里某张表的格子 → 变成冲突 (B 那一版也是选项)。"""
    if s.kind == "keep":
        return s
    covered = {x for o in s.options for x in o.who} if s.kind == "conflict" else set(s.who)
    extra = [x for i in range(s.start, s.end) for x in tchg.get(i, []) if x not in covered]
    if not extra:
        return s
    opts = list(s.options) if s.kind == "conflict" else [Option(list(s.who), s.new)]
    for x in dict.fromkeys(extra):
        opts.append(Option([x], _region(bu, vu[x], maps[x], s.start, s.end)))
    return Seg("conflict", s.start, s.end, s.old, options=opts, cid=cid_of(scope, s.start, s.end, s.old, opts))


def _texts(units: list[Unit]) -> list[str]:
    return [u.text for u in units]


def _format_only(m: M, s: Seg, labels: Labels) -> Seg:
    if s.kind != "conflict":
        return s
    base_t = _texts(s.old)
    wording = [o for o in s.options if _texts(o.units) != base_t]
    if len(wording) != 1 or any(_has_table(o.units) for o in s.options):
        return s
    pick = wording[0]
    rest = [x for o in s.options if o is not pick for x in o.who]
    m.warn(f"{labels.of(s)}: {'、'.join(m.name(x) for x in rest)} 只调了格式, {'、'.join(m.name(x) for x in pick.who)} 改了文字 —— 用了改文字的那一版, 格式调整没合进来")
    return Seg("change", s.start, s.end, s.old, list(pick.who), pick.units)


def _has_table(units: list[Unit]) -> bool:
    return any(is_w(u.data[0], "tbl") for u in units)


def _join(units: list[Unit]) -> str:
    return "\n".join(u.text for u in units)


def _conflict_view(m: M, s: Seg, where: str) -> dict[str, Any]:
    from .core import combine_text  # noqa: PLC0415
    base_t = _join(s.old)
    opts = [{"key": o.key, "who": o.who, "text": _join(o.units)} for o in s.options]
    simple = not _has_table(s.old) and not any(_has_table(o.units) for o in s.options)
    combined = combine_text(base_t, {o["key"]: o["text"] for o in opts}, [o["key"] for o in opts]) if simple else None
    return {"cid": s.cid, "where": where, "kind": "text", "base": base_t, "options": opts,
            "combined": combined if combined not in (None, base_t) else None, "custom": simple}


# ── 表格 ──────────────────────────────────────────────────────────

def _cells(tr: Any) -> list[Any]:
    return tr.findall(w("tc"))


def merge_table(m: M, base_tbl: Any, vers: dict[str, Any], part: str, scope: str, where: str) -> dict[str, Any]:
    order = [x for x in m.order if x in vers]
    bu = [Unit(sig(tr, m.base.pkg, part), text(tr), (tr, "", part)) for tr in base_tbl.findall(w("tr"))]
    vu = {x: [Unit(sig(tr, m.eds[x].pkg, m.ep(x, part)), text(tr), (tr, x, m.ep(x, part))) for tr in vers[x].findall(w("tr"))] for x in order}
    segs = merge_seq(bu, vu, order, scope)
    maps = {x: _eqmap(bu, vu[x]) for x in order}
    rows: list[dict[str, Any]] = []
    for s in segs:
        if s.kind == "keep":
            for i in range(s.start, s.end):
                tr = bu[i].data[0]
                donors = [x for x in order if i in maps[x] and _has_new_comment(vu[x][maps[x][i]].data[0], m.eds[x])]
                if donors:   # 只加了批注的行: 拆到单元格, 让批注跟着段落走
                    rows.append(_cells_item(m, tr, {x: vu[x][maps[x][i]].data[0] for x in donors}, part, f"{scope}/r{i}", f"{where}第 {i + 1} 行"))
                else:
                    rows.append({"t": "keep", "el": tr, "part": part, "donors": []})
            continue
        label = f"{where}第 {s.start + 1} 行" if s.end > s.start else f"{where}第 {s.start} 行后面"
        split = _split_rows(m, s, bu, part, scope, where)
        if split is not None:
            rows.extend(split)
        elif s.kind == "change":
            rows.append({"t": "change", "seg": s, "part": part, "donors": [], "where": label})
        else:
            c = {"cid": s.cid, "where": label, "kind": "text", "base": _join(s.old),
                 "options": [{"key": o.key, "who": o.who, "text": _join(o.units)} for o in s.options], "combined": None, "custom": False}
            m.conflicts.append(c)
            rows.append({"t": "conflict", "seg": s, "part": part, "donors": [], "where": label})
    return {"t": "table", "el": base_tbl, "part": part, "rows": rows}


def _split_rows(m: M, s: Seg, bu: list[Unit], part: str, scope: str, where: str) -> list[dict[str, Any]] | None:
    """行级冲突 → 拆到单元格: 这一片每种改法的行数都跟原来一样、每行格数也一样, 才拆。"""
    if s.kind != "conflict" or s.end == s.start:
        return None
    k = s.end - s.start
    if any(len(o.units) != k for o in s.options):
        return None
    out = []
    for r in range(k):
        base_tr = bu[s.start + r].data[0]
        n = len(_cells(base_tr))
        vers = {x: o.units[r].data[0] for o in s.options for x in o.who}
        if any(len(_cells(tr)) != n for tr in vers.values()):
            return None
        out.append(_cells_item(m, base_tr, vers, part, f"{scope}/r{s.start + r}", f"{where}第 {s.start + r + 1} 行"))
    return out


def _cells_item(m: M, base_tr: Any, vers: dict[str, Any], part: str, scope: str, where: str) -> dict[str, Any]:
    cells = []
    for c, tc in enumerate(_cells(base_tr)):
        cv = {x: blocks_of(_cells(tr)[c]) for x, tr in vers.items()}
        cells.append(merge_blocks(m, blocks_of(tc), cv, part, f"{scope}/c{c}", f"{where}第 {c + 1} 列"))
    return {"t": "cells", "el": base_tr, "part": part, "cells": cells}
