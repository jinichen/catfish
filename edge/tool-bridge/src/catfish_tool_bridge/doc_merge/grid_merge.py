"""表格合并的第二步: 对齐好的底稿 + N 份交回稿 → 合并计划 (grid.py 是第一步对齐)。

单元格: 一个人改的用他的; 几个人改成一样的用这个; 改得不一样 → 冲突 (kind=value), 选项是各人的值。
一个人删了一行 (列)、别人改了这行 (列) 的格子 → 冲突, 选项是整行 (列) 的: 「删除这一行」/ 各人改过的这一行 /
几个人改的格子互不冲突时多一个「都要」。没人改过的行 (列) 有人删就删。
新行 (列) 的格子底稿当空的比, 两个人加了同一个主键的行、某格填得不一样也是冲突。

计划只记身份和「值从哪份稿子的哪一格来」, 不碰文件格式; xlsx / csv 各自照计划写回。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from openpyxl.utils import get_column_letter

from . import core
from .grid import Aligned, Side, blank, display, order_ids

DEL = "del"          # 删除行 / 列那个选项的 payload


@dataclass
class Event:
    kind: str            # cell / row+ / row- / col+ / col- / order
    rid: str
    cid: str
    who: list[str]
    old: str = ""
    new: str = ""


@dataclass
class Plan:
    rows: list[tuple[str, int, int]] = field(default_factory=list)   # 合并后的行: (身份, 来源稿下标 -1=底稿, 那份里的行号)
    cols: list[tuple[str, int, int]] = field(default_factory=list)
    cells: dict[tuple[str, str], Any] = field(default_factory=dict)  # 改了的格: (稿下标, 行, 列) 或 ("text", 值)
    events: list[Event] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    changed: set[str] = field(default_factory=set)   # 相对底稿有改动的人
    undecided: int = 0
    warnings: list[str] = field(default_factory=list)
    serial: list[str] = field(default_factory=list)   # 序号列: 写合并版时按行重新编号


class _Ctx:
    def __init__(self, al: Aligned, sig: Callable[[int, int, int], str], label: str, owner: str,
                 decisions: dict[str, Any] | None, parse: Callable[[str], Any]):
        self.al, self.sig, self.label, self.owner = al, sig, label, owner
        self.decisions, self.parse = decisions, parse
        self.sides = al.vers
        self._sig: dict[tuple[int, str, str], str | None] = {}

    def side(self, k: int) -> Side:
        return self.al.base if k < 0 else self.sides[k]

    def val(self, k: int, rid: str, cid: str) -> str | None:
        """格子的比较签名; None = 这份稿子里没有这一格 (删了 / 本来就没有)。"""
        key = (k, rid, cid)
        if key not in self._sig:
            s = self.side(k)
            r, c = s.rpos().get(rid), s.cpos().get(cid)
            if not r or not c:
                self._sig[key] = None
            elif (r, c) in s.grid.skip:
                self._sig[key] = "skip"
            else:
                self._sig[key] = self.sig(k, r, c)
        return self._sig[key]

    def raw(self, k: int, rid: str, cid: str) -> Any:
        s = self.side(k)
        r, c = s.rpos().get(rid), s.cpos().get(cid)
        return s.grid.get(r, c) if r and c else None

    def addr(self, rid: str, cid: str) -> str:
        for k in [-1] + list(range(len(self.sides))):
            s = self.side(k)
            r, c = s.rpos().get(rid), s.cpos().get(cid)
            if r and c:
                return f"{get_column_letter(c)}{r}"
        return "?"

    def colname(self, cid: str) -> str:
        for k in [-1] + list(range(len(self.sides))):
            s = self.side(k)
            c = s.cpos().get(cid)
            if c and s.names[c - 1]:
                return s.names[c - 1]
        return ""

    def keyval(self, rid: str) -> str:
        key = self.al.key
        if not key or not rid.startswith("k:"):
            return ""
        return f"{self.colname(key)}={rid[2:]}"

    def where(self, rid: str, cid: str) -> str:
        bits = [b for b in (self.keyval(rid), f"列「{self.colname(cid)}」" if self.colname(cid) else "") if b]
        return f"{self.label} · {self.addr(rid, cid)}" + (f"（{'，'.join(bits)}）" if bits else "")

    def row_text(self, k: int, rid: str, cols: list[str]) -> str:
        parts = []
        for cid in cols:
            v = self.raw(k, rid, cid)
            if not blank(v):
                n = self.colname(cid)
                parts.append(f"{n}：{display(v)}" if n else display(v))
        return " | ".join(parts)

    def cid(self, scope: str, base_sig: str, opts: list[tuple[list[str], str]]) -> str:
        return core.cid_of(f"{self.label}\x1f{scope}", 0, 0, [core.Unit(base_sig, "")],
                           [core.Option(list(w), [core.Unit(s, "")]) for w, s in opts])

    def decide(self, cid: str) -> dict[str, Any] | None:
        """None = 分析阶段 (先按底稿) 或没定 (记一笔)。"""
        if self.decisions is None:
            return None
        d = self.decisions.get(cid)
        if not d or not d.get("pick"):
            return None
        return d


def merge(al: Aligned, sig: Callable[[int, int, int], str], *, label: str, owner: str,
          decisions: dict[str, Any] | None, parse: Callable[[str], Any]) -> Plan:
    """sig(k, 行, 列): 第 k 份稿子 (-1 = 底稿) 那一格的比较签名。decisions=None 是分析 (冲突先按底稿)。"""
    x = _Ctx(al, sig, label, owner, decisions, parse)
    plan = Plan()
    base, vers = al.base, al.vers
    n = len(vers)
    whos = [s.who for s in vers]
    brows, bcols = set(base.rows), set(base.cols)
    row_order, reorder, ignored = order_ids(base.rows, [s.rows for s in vers])
    col_order, creorder, cignored = order_ids(base.cols, [s.cols for s in vers])
    all_cols = col_order

    # 谁动了这张表 (进不进 same 用)
    for k, s in enumerate(vers):
        if set(s.rows) != brows or set(s.cols) != bcols or k == reorder or k in ignored \
                or k == creorder or k in cignored:
            plan.changed.add(s.who)
    for k in ignored:
        plan.warnings.append(f"{label}: {whos[k]} 也调整了行的顺序, 跟 {whos[reorder]} 的不一样, "
                             f"合并版用的是 {whos[reorder]} 的顺序")
    for k in cignored:
        plan.warnings.append(f"{label}: {whos[k]} 也调整了列的顺序, 合并版用的是 {whos[creorder]} 的顺序")

    serial = set(al.serial)
    plan.serial = list(al.serial)

    def changers(rid: str, cid: str) -> dict[int, str]:
        if cid in serial and rid in brows and rid != "h" and not (rid[0] == "t" and rid[1:].isdigit()):
            return {}      # 序号列的数据行: 重新编号不算改动
        b = x.val(-1, rid, cid) or ""
        out = {}
        for k in range(n):
            v = x.val(k, rid, cid)
            if v is not None and v != "skip" and b != "skip" and v != b:
                out[k] = v
        return out

    # 先把所有格子的改动算出来
    ch: dict[tuple[str, str], dict[int, str]] = {}
    for rid in row_order:
        for cid in all_cols:
            c = changers(rid, cid)
            if c:
                ch[(rid, cid)] = c
                for k in c:
                    plan.changed.add(whos[k])

    dead_rows: set[str] = set()
    dead_cols: set[str] = set()
    ruled: dict[tuple[str, str], tuple[list[int], Any]] = {}    # 整行 / 整列冲突定下来的格子

    def structural(axis: str, xid: str, lines: list[str]) -> None:
        """一个人删了行 (列)、别人改了这行 (列)。lines: 这一行 (列) 上的另一个方向的身份。"""
        def pick_raw(ks: list[int], cc: tuple[str, str]) -> Any:
            k = next((k for k in ks if k in ch.get(cc, {})), -1)
            return x.raw(k, *cc)
        bset = brows if axis == "row" else bcols
        if xid not in bset:
            return
        has = (lambda s: xid in s.rpos()) if axis == "row" else (lambda s: xid in s.cpos())
        deleters = [k for k in range(n) if not has(vers[k])]
        if not deleters:
            return
        cells = [((xid, o) if axis == "row" else (o, xid)) for o in lines]
        mods = sorted({k for cc in cells for k in ch.get(cc, {}) if k not in deleters})
        dead = dead_rows if axis == "row" else dead_cols
        if not mods:
            dead.add(xid)
            plan.events.append(Event(axis + "-", xid if axis == "row" else "", xid if axis == "col" else "",
                                     [whos[k] for k in deleters]))
            return
        # 每个改了的人一个版本; 几个人改的格子互不冲突时再给一个「都要」
        versions: dict[tuple[str, ...], list[int]] = {}
        for k in mods:
            versions.setdefault(tuple(ch.get(cc, {}).get(k, "") for cc in cells), []).append(k)
        opts: list[tuple[list[int], Any]] = [(deleters, DEL)] + [(ks, ks) for ks in versions.values()]
        if len(versions) > 1 and all(len({v for k, v in ch.get(cc, {}).items() if k in mods}) <= 1 for cc in cells):
            opts.append((mods, mods))
        if axis == "row":
            base_txt = x.row_text(-1, xid, base.cols)
            where = f"{label} · 第 {base.rpos()[xid]} 行" + (f"（{x.keyval(xid)}）" if x.keyval(xid) else "")

            def txt(ks: list[int]) -> str:
                return " | ".join(f"{x.colname(c) or x.addr(xid, c)}：{display(pick_raw(ks, (xid, c)))}"
                                  for c in all_cols if not blank(pick_raw(ks, (xid, c))))
        else:
            name = x.colname(xid)
            base_txt = ", ".join(display(x.raw(-1, r, xid)) for r in base.rows if not blank(x.raw(-1, r, xid)))[:300]
            where = f"{label} · {get_column_letter(base.cpos()[xid])} 列" + (f"「{name}」" if name else "")

            def txt(ks: list[int]) -> str:
                return "; ".join(f"{x.addr(r, xid)}: {display(x.raw(-1, r, xid))} → {display(pick_raw(ks, (r, xid)))}"
                                 for r, _ in cells if any(k in ch.get((r, xid), {}) for k in ks))[:300]
        sig_of = repr([tuple(ch.get(cc, {}).get(k, "") for cc in cells) for k in mods])
        scope = f"{axis}\x1f{xid}"
        cid = x.cid(scope, repr([x.val(-1, *cc) for cc in cells]),
                    [([whos[k] for k in ks], DEL if p == DEL else sig_of + repr(ks)) for ks, p in opts])
        conflict = {"cid": cid, "where": where, "kind": "value", "base": base_txt, "combined": None,
                    "options": [{"key": "+".join(whos[k] for k in ks), "who": [whos[k] for k in ks],
                                 "text": "删除这一行" if p == DEL and axis == "row" else
                                 "删除这一列" if p == DEL else txt(ks)} for ks, p in opts]}
        plan.conflicts.append(conflict)
        d = x.decide(cid)
        if d is None:
            if decisions is not None:
                plan.undecided += 1
            for cc in cells:
                ruled[cc] = ([], None)
            return
        pick = d["pick"]
        chosen = next((o for o in opts if "+".join(whos[k] for k in o[0]) == pick), None)
        if pick == core.BASE:
            for cc in cells:
                ruled[cc] = ([], None)
        elif pick == "text" and isinstance(d.get("text"), str):
            vals = _split_text(d["text"], [(x.colname(o) if axis == "row" else "") for o in lines])
            for i, cc in enumerate(cells):
                ruled[cc] = ([-2], parse(vals[i])) if i < len(vals) and vals[i] is not None else ([], None)
        elif chosen is None:
            plan.undecided += 1
            for cc in cells:
                ruled[cc] = ([], None)
        elif chosen[1] == DEL:
            dead.add(xid)
            plan.events.append(Event(axis + "-", xid if axis == "row" else "", xid if axis == "col" else "",
                                     [whos[k] for k in chosen[0]]))
        else:
            for cc in cells:
                ks = [k for k in chosen[0] if k in ch.get(cc, {})]
                ruled[cc] = (ks, None)

    for rid in base.rows:
        structural("row", rid, all_cols)
    for cid in base.cols:
        structural("col", cid, [r for r in row_order if r not in dead_rows])

    # 合并后的行 / 列 (身份, 来源)
    def src(xid: str, axis: str) -> tuple[str, int, int]:
        bp = (base.rpos() if axis == "r" else base.cpos()).get(xid)
        if bp:
            return (xid, -1, bp)
        for k, s in enumerate(vers):
            p = (s.rpos() if axis == "r" else s.cpos()).get(xid)
            if p:
                return (xid, k, p)
        raise KeyError(xid)
    plan.rows = [src(r, "r") for r in row_order if r not in dead_rows and _alive(r, brows, vers, "r")]
    plan.cols = [src(c, "c") for c in col_order if c not in dead_cols and _alive(c, bcols, vers, "c")]
    new_rows = {r for r, _, _ in plan.rows if r not in brows}
    new_cols = {c for c, _, _ in plan.cols if c not in bcols}
    for r in new_rows:
        plan.events.append(Event("row+", r, "", [s.who for s in vers if r in s.rpos()]))
    for c in new_cols:
        plan.events.append(Event("col+", "", c, [s.who for s in vers if c in s.cpos()]))
    if reorder is not None:
        plan.events.append(Event("order", "", "", [whos[reorder]]))
    if creorder is not None:
        plan.events.append(Event("order", "", "#cols", [whos[creorder]]))

    live_r, live_c = {r for r, _, _ in plan.rows}, {c for c, _, _ in plan.cols}
    typed = {cc: {} for cc, (ks, _) in ruled.items() if ks == [-2] and cc not in ch}   # 发起人整行 (列) 自己写的
    for (rid, cid), c in list(ch.items()) + list(typed.items()):
        if rid not in live_r or cid not in live_c:
            continue
        in_new = rid in new_rows or cid in new_cols
        old = display(x.raw(-1, rid, cid))
        if (rid, cid) in ruled:
            ks, tv = ruled[(rid, cid)]
            if ks == [-2]:
                if display(tv) != old:
                    _put(plan, x, rid, cid, ("text", tv), [owner], old, display(tv), in_new)
            elif ks:
                _put(plan, x, rid, cid, _at(x, ks[0], rid, cid), [whos[k] for k in ks], old,
                     display(x.raw(ks[0], rid, cid)), in_new)
            continue
        groups: dict[str, list[int]] = {}
        for k, v in c.items():
            groups.setdefault(v, []).append(k)
        if len(groups) == 1:
            ks = next(iter(groups.values()))
            _put(plan, x, rid, cid, _at(x, ks[0], rid, cid), [whos[k] for k in ks], old,
                 display(x.raw(ks[0], rid, cid)), in_new)
            continue
        opts = list(groups.items())
        cidv = x.cid(f"cell\x1f{rid}\x1f{cid}", x.val(-1, rid, cid) or "", [([whos[k] for k in ks], v) for v, ks in opts])
        plan.conflicts.append({
            "cid": cidv, "where": x.where(rid, cid), "kind": "value", "base": old, "combined": None,
            "options": [{"key": "+".join(whos[k] for k in ks), "who": [whos[k] for k in ks],
                         "text": display(x.raw(ks[0], rid, cid))} for _, ks in opts]})
        d = x.decide(cidv)
        if d is None:
            if decisions is not None:
                plan.undecided += 1
            continue
        pick = d["pick"]
        if pick == core.BASE:
            continue
        if pick == "text" and isinstance(d.get("text"), str):
            tv = parse(d["text"])
            _put(plan, x, rid, cid, ("text", tv), [owner], old, display(tv), in_new)
            continue
        ks = next((ks for _, ks in opts if "+".join(whos[k] for k in ks) == pick), None)
        if ks is None:
            plan.undecided += 1
            continue
        _put(plan, x, rid, cid, _at(x, ks[0], rid, cid), [whos[k] for k in ks], old,
             display(x.raw(ks[0], rid, cid)), in_new)
    return plan


def _alive(xid: str, bset: set[str], vers: list[Side], axis: str) -> bool:
    """底稿里的都在 (删不删由上面定); 新的只要有人有。"""
    if xid in bset:
        return True
    return any(xid in (s.rpos() if axis == "r" else s.cpos()) for s in vers)


def _at(x: _Ctx, k: int, rid: str, cid: str) -> tuple[int, int, int]:
    s = x.side(k)
    return (k, s.rpos()[rid], s.cpos()[cid])


def _put(plan: Plan, x: _Ctx, rid: str, cid: str, pick: Any, who: list[str], old: str, new: str, in_new: bool) -> None:
    plan.cells[(rid, cid)] = pick
    if not in_new:
        plan.events.append(Event("cell", rid, cid, who, old, new))


def _split_text(text: str, names: list[str]) -> list[str | None]:
    """发起人给整行 (列) 写的内容: 「列名：值 | 列名：值」或按顺序的「值 | 值」。"""
    parts = [p.strip() for p in text.replace("\t", "|").split("|")]
    out: list[str | None] = [None] * len(names)
    pos = 0
    for p in parts:
        k, eq, v = p.replace(":", "：", 1).partition("：") if not p.startswith("=") else ("", "", p)
        if eq and k.strip() in names and k.strip():
            out[names.index(k.strip())] = v.strip()
        elif pos < len(out):
            out[pos] = p
            pos += 1
    return out


def stats(plan: Plan) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in plan.events:
        for w in e.who:
            out[w] = out.get(w, 0) + 1
    return out


def matched(base: Side, side: Side):
    """两份稿子里身份相同的格子: (底稿行, 底稿列, 这份行, 这份列)。"""
    bc = [(base.cpos()[c], side.cpos()[c]) for c in base.cols if c in side.cpos()]
    for rid in base.rows:
        rv = side.rpos().get(rid)
        if rv:
            rb = base.rpos()[rid]
            for cb, cv in bc:
                yield rb, cb, rv, cv
