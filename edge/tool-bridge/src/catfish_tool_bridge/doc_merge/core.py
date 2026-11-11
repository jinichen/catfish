"""N 份修改一起跟同一份底稿三方合并 (docs/DOC-REVISE-MERGE-PLAN.md §4.2)。各格式 (text / docx / xlsx / pptx)
先把文档拆成一串「单元」(行 / 段落 / 表格行 / 页), 交给这里; 这里不懂格式, 只认单元的 sig (内容相同 ⇔ sig 相同)。

做法: 每份修改稿各自跟底稿对齐 (SequenceMatcher), 得到它改了底稿的哪些区间 (hunk)。**所有人都跟同一份底稿比**,
不是两两依次合 —— 依次合时第三个人的改动是跟「前两人的合并结果」比, 会把没冲突的判成冲突、或把冲突漏掉。

区间互相压到的 hunk 归成一簇:
  · 一簇只有一个人                    → 直接用他的
  · 一簇里几个人改成一模一样          → 用这个, 记上这几个人
  · 几个人在同一处各插了新内容 (插入) → 都保留, 按交回先后排 (相同的只留一份)
  · 其它 (改法不同 / 一个删一个改)    → 冲突: 原文 + 每个人在这一片的版本, 由人定
两个区间只是首尾相接 (一个改第 5 段, 一个改第 6 段 / 在第 5 段后面插) 不算压到。

冲突编号 (cid) 由区间和内容算出来: 同样的输入重算一遍编号不变 —— 合并面板记下的「这处用谁的」, 出合并版时重算后照样对得上。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable

BASE = "base"   # 冲突里「保留原文」的选项键


@dataclass
class Unit:
    sig: str          # 比较 / 对齐用
    text: str         # 给人看的文字
    data: Any = None  # 格式自己的东西 (段落 XML / 一行单元格 / 一页)


@dataclass
class Hunk:
    who: str
    start: int
    end: int
    new: list[Unit]


@dataclass
class Option:
    who: list[str]
    units: list[Unit]

    @property
    def key(self) -> str:
        return "+".join(self.who)


@dataclass
class Seg:
    kind: str                       # keep / change / conflict
    start: int
    end: int
    old: list[Unit]
    who: list[str] = field(default_factory=list)
    new: list[Unit] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)
    cid: str = ""


def hunks(base: list[Unit], ver: list[Unit], who: str) -> list[Hunk]:
    sm = SequenceMatcher(None, [u.sig for u in base], [u.sig for u in ver], autojunk=False)
    return [Hunk(who, i1, i2, ver[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]


def _touch(a: Hunk, b: Hunk) -> bool:
    if a.start == a.end and b.start == b.end:
        return a.start == b.start                     # 同一处都插: 归一簇 (排先后, 不算冲突)
    if a.start == a.end:
        return b.start < a.start < b.end              # 插在别人改的区间正中间
    if b.start == b.end:
        return a.start < b.start < a.end
    return a.start < b.end and b.start < a.end


def _clusters(hs: list[Hunk]) -> list[list[Hunk]]:
    parent = list(range(len(hs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(hs)):
        for j in range(i + 1, len(hs)):
            if hs[i].who != hs[j].who and _touch(hs[i], hs[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[Hunk]] = {}
    for i, h in enumerate(hs):
        groups.setdefault(find(i), []).append(h)
    out = list(groups.values())
    out.sort(key=lambda g: (min(h.start for h in g), max(h.end for h in g)))
    return out


def _sigs(units: list[Unit]) -> tuple[str, ...]:
    return tuple(u.sig for u in units)


def _apply(base: list[Unit], start: int, end: int, own: list[Hunk]) -> list[Unit]:
    """这个人在底稿 [start, end) 这一片上的版本。"""
    out, pos = [], start
    for h in sorted(own, key=lambda h: (h.start, h.end)):
        out.extend(base[pos:h.start])
        out.extend(h.new)
        pos = max(pos, h.end)
    out.extend(base[pos:end])
    return out


def cid_of(scope: str, start: int, end: int, base_units: list[Unit], options: list[Option]) -> str:
    raw = repr((scope, start, end, _sigs(base_units), [(o.key, _sigs(o.units)) for o in options]))
    return "c_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def merge_seq(base: list[Unit], versions: dict[str, list[Unit]], order: list[str] | None = None,
              scope: str = "") -> list[Seg]:
    """versions: {谁: 他那一版的单元}。order: 交回先后 (同一处都插时按这个排; 冲突选项也按这个排)。"""
    order = list(order or versions)
    rank = {w: i for i, w in enumerate(order)}
    hs = [h for w in order if w in versions for h in hunks(base, versions[w], w)]
    segs: list[Seg] = []
    pos = 0
    for group in _clusters(hs):
        start, end = min(h.start for h in group), max(h.end for h in group)
        if start > pos:
            segs.append(Seg("keep", pos, start, base[pos:start]))
        pos = max(pos, end)
        whos = sorted({h.who for h in group}, key=lambda w: rank.get(w, 99))
        if start == end:   # 都是插在同一处: 都保留, 一样的只留一份
            seen: dict[tuple[str, ...], Seg] = {}
            for w in whos:
                new = [u for h in group if h.who == w for u in h.new]
                s = seen.get(_sigs(new))
                if s:
                    s.who.append(w)
                else:
                    seen[_sigs(new)] = Seg("change", start, end, [], [w], new)
            segs.extend(seen.values())
            continue
        opts: dict[tuple[str, ...], Option] = {}
        for w in whos:
            units = _apply(base, start, end, [h for h in group if h.who == w])
            if _sigs(units) in opts:
                opts[_sigs(units)].who.append(w)
            else:
                opts[_sigs(units)] = Option([w], units)
        old = base[start:end]
        if len(opts) == 1:
            o = next(iter(opts.values()))
            segs.append(Seg("change", start, end, old, o.who, o.units))
        else:
            options = list(opts.values())
            segs.append(Seg("conflict", start, end, old, options=options, cid=cid_of(scope, start, end, old, options)))
    if pos < len(base):
        segs.append(Seg("keep", pos, len(base), base[pos:]))
    return segs


class Undecided(ValueError):
    pass


def resolve(segs: list[Seg], decisions: dict[str, dict[str, Any]], me: str,
            custom: Callable[[str, Seg], list[Unit]] | None = None) -> list[Seg]:
    """冲突按决定换成 keep / change。decisions: {cid: {"pick": 选项键 | "base" | "text", "text": ...}}。
    pick=text: 发起人自己写的 (或采用的建议稿), 由格式的 custom() 变成单元, 算发起人改的。没定完 → Undecided。"""
    out, left = [], 0
    for s in segs:
        if s.kind != "conflict":
            out.append(s)
            continue
        d = decisions.get(s.cid) or {}
        pick = d.get("pick")
        if pick == BASE:
            out.append(Seg("keep", s.start, s.end, s.old))
        elif pick == "text" and custom is not None and isinstance(d.get("text"), str):
            out.append(Seg("change", s.start, s.end, s.old, [me], custom(d["text"], s)))
        else:
            o = next((o for o in s.options if o.key == pick), None)
            if o is None:
                left += 1
                continue
            out.append(Seg("change", s.start, s.end, s.old, list(o.who), o.units))
    if left:
        raise Undecided(f"还有 {left} 处冲突没定")
    return out


# ── 冲突的「拼合建议」: 两人改的是同一段里不同的地方时, 按字拼起来 ─────────────

_TOKEN = re.compile(r"[A-Za-z0-9_.%]+|\s+|.", re.S)


def combine_text(base: str, texts: dict[str, str], order: list[str] | None = None) -> str | None:
    """同一段文字的几种改法按字 / 词三方合并; 改到同一处 (真冲突) 返 None。数字串整体算一个词 (48 改 52 不会拼成 4852)。"""
    def toks(s: str) -> list[Unit]:
        return [Unit(t, t) for t in _TOKEN.findall(s)]
    segs = merge_seq(toks(base), {w: toks(t) for w, t in texts.items()}, order)
    if any(s.kind == "conflict" for s in segs):
        return None
    return "".join(u.text for s in segs for u in (s.new if s.kind == "change" else s.old))


def stats(segs: list[Seg]) -> dict[str, int]:
    """每个人合进去几处 (同一处几个人改得一样, 每人都算一处)。"""
    n: dict[str, int] = {}
    for s in segs:
        if s.kind == "change":
            for w in s.who:
                n[w] = n.get(w, 0) + 1
    return n
