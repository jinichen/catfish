"""Word 合并 · 把合并计划写成 XML: 「修订版」每处修改都是 Word 的修订 (w:ins / w:del, 作者 = 改的人), 「干净版」直接是结果。

一段话被改了: 新旧两段按词比 (中文按字, 数字串整体), 没变的照抄新的那段 (格式用改的人的), 删掉的字写成 w:del,
加的字写成 w:ins; 同样的字只改了格式 → w:rPrChange; 段落格式变了 → w:pPrChange。在 Word 里就是「接受 / 拒绝」一条条的修订。
段落里有逐字比不了的东西 (域、内容控件、公式) → 整段旧的标删除、整段新的标插入。
新加 / 删掉的整段: 段里的字标插入 / 删除, 段落标记也标上 (这样接受之后段落真的没了 / 有了)。
表格行: 行标插入 / 删除 (w:trPr 里的 w:ins / w:del), 两边行数、格数一样时逐格逐段比。
"""
from __future__ import annotations

import copy
import re
from difflib import SequenceMatcher
from typing import Any

from lxml import etree

from .core import BASE, Option, Seg, Unit
from .word_merge import M, blocks_of
from .word_norm import COMMENT_TAGS, is_w, local
from .word_pkg import DOC, W, Pkg, w, wa

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_TOKEN = re.compile(r"[A-Za-z0-9_.%]+|\s+|.", re.S)
_MARKS = {"bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "permStart", "permEnd"}
_SPECIAL = {"tab": "\t", "br": "\n", "cr": "\n", "noBreakHyphen": "-", "softHyphen": "", "sym": ""}
_COMPLEX = {"fldSimple", "smartTag", "sdt", "oMath", "oMathPara", "customXml", "ins", "del", "moveTo", "moveFrom", "dir", "bdo"}


class Undecided(Exception):
    pass


class Writer:
    def __init__(self, m: M, dst: Pkg, decisions: dict[str, Any], owner: str, now: str, tracked: bool):
        self.m, self.dst, self.dec, self.owner, self.now, self.tracked = m, dst, decisions, owner, now, tracked
        self.left = 0
        self.next_id = 900000
        self.changes: dict[str, int] = {}

    # ── 公用 ──
    def _rid(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    def _src(self, who: str) -> Pkg:
        return self.m.eds[who].pkg if who else self.m.base.pkg

    def _adopt(self, u: Unit, part: str) -> Any:
        el, who, src_part = u.data
        return self.dst.adopt(el, self._src(who), src_part, part)

    def _author(self, who: list[str]) -> tuple[str, str]:
        name = "、".join(self.m.name(x) for x in who) if who else "鲶鱼"
        at = next((self.m.eds[x].at for x in who if x in self.m.eds and self.m.eds[x].at), "") or self.now
        return name, at

    def _mark(self, tag: str, who: list[str]) -> Any:
        name, at = self._author(who)
        el = etree.Element(w(tag))
        el.set(w("id"), self._rid())
        el.set(w("author"), name)
        if at:
            el.set(w("date"), at[:19] + "Z" if len(at) >= 19 and not at.endswith("Z") else at)
        return el

    def _count(self, who: list[str]) -> None:
        for x in who:
            self.changes[x] = self.changes.get(x, 0) + 1

    # ── 块 ──
    def blocks(self, items: list[dict[str, Any]]) -> list[Any]:
        out: list[Any] = []
        for it in items:
            t, part = it["t"], it["part"]
            if t == "keep":
                out.extend(self._keep(it, part))
            elif t == "table":
                out.append(self._table(it))
            elif t == "change":
                s: Seg = it["seg"]
                self._count(s.who)
                out.extend(self._donate(self.change(s.who, s.old, s.new, part), it["donors"], it.get("where", "")))
            else:
                out.extend(self._donate(self._conflict(it["seg"], part), it["donors"], it.get("where", "")))
        return out

    def _keep(self, it: dict[str, Any], part: str) -> list[Any]:
        donors = it["donors"]
        if not donors:
            return [self.dst.adopt(it["el"], self.m.base.pkg, part, part)]
        x, el = donors[0]
        got = self.dst.adopt(el, self.m.eds[x].pkg, part, part)
        return self._donate([got], donors[1:], "")

    def _donate(self, els: list[Any], donors: list[tuple[str, Any]], where: str) -> list[Any]:
        """别人在这段加的批注: 锚到整段 (那段被改了 / 换成了别人的版本)。"""
        if not donors:
            return els
        target = next((e for e in els if is_w(e, "p") and not _deleted_para(e)), None)
        for x, el in donors:
            got = self.dst.adopt(el, self.m.eds[x].pkg, DOC, DOC)
            ids = [wa(n, "id") for n in got.iter() if isinstance(n.tag, str) and local(n) == "commentRangeStart"]
            ids += [wa(n, "id") for n in got.iter() if isinstance(n.tag, str) and local(n) == "commentReference" and wa(n, "id") not in ids]
            ids = [i for i in dict.fromkeys(ids) if i is not None]
            if target is None:
                if ids:
                    self.m.warn(f"{where or '有一段'}: {self.m.name(x)} 加的批注所在的段落被删了, 批注没合进来")
                continue
            have = {wa(n, "id") for n in target.iter() if isinstance(n.tag, str) and local(n) in COMMENT_TAGS}
            for cid in ids:
                if cid in have:
                    continue
                start = etree.Element(w("commentRangeStart"))
                start.set(w("id"), cid)
                ppr = target.find(w("pPr"))
                (ppr.addnext(start) if ppr is not None else target.insert(0, start))
                end = etree.SubElement(target, w("commentRangeEnd"))
                end.set(w("id"), cid)
                r = etree.SubElement(target, w("r"))
                ref = etree.SubElement(r, w("commentReference"))
                ref.set(w("id"), cid)
        return els

    def _conflict(self, s: Seg, part: str) -> list[Any]:
        d = self.dec.get(s.cid) or {}
        pick = d.get("pick")
        if pick == BASE:
            return [self.dst.adopt(u.data[0], self.m.base.pkg, part, part) for u in s.old]
        if pick == "text" and isinstance(d.get("text"), str):
            self._count([self.owner])
            return self.change([self.owner], s.old, custom_units(d["text"], s), part)
        o = next((o for o in s.options if o.key == pick), None)
        if o is None:
            self.left += 1
            return [self.dst.adopt(u.data[0], self.m.base.pkg, part, part) for u in s.old]
        self._count(o.who)
        return self.change(o.who, s.old, o.units, part)

    def change(self, who: list[str], old: list[Unit], new: list[Unit], part: str) -> list[Any]:
        news = [self._adopt(u, part) for u in new]
        if not self.tracked:
            return news
        olds = [self.dst.adopt(u.data[0], self.m.base.pkg, part, part) for u in old]
        out: list[Any] = []
        for op, o, n in _pair(olds, news):
            if op == "del":
                out.append(self.mark_all(o, "del", who))
            elif op == "ins":
                out.append(self.mark_all(n, "ins", who))
            elif is_w(o, "p") and is_w(n, "p"):
                out.extend(self.diff_para(o, n, who))
            elif is_w(o, "tbl") and is_w(n, "tbl"):
                out.append(self.diff_table(o, n, who))
            else:
                out.extend([self.mark_all(o, "del", who), self.mark_all(n, "ins", who)])
        return out

    # ── 整段 / 整表标插入、删除 ──
    def mark_all(self, el: Any, mode: str, who: list[str]) -> Any:
        for r in [r for r in el.iter(w("r"))]:
            if local(r.getparent()) in ("ins", "del"):
                continue
            if mode == "del":
                _to_deltext(r)
            wrap = self._mark(mode, who)
            r.addprevious(wrap)
            wrap.append(r)
        for p in el.iter(w("p")):
            _para_mark(p, self._mark(mode, who))
        for tr in el.iter(w("tr")):
            pr = tr.find(w("trPr"))
            if pr is None:
                pr = etree.Element(w("trPr"))
                pex = tr.find(w("tblPrEx"))
                (pex.addnext(pr) if pex is not None else tr.insert(0, pr))
            pr.append(self._mark(mode, who))
        return el

    def diff_table(self, o: Any, n: Any, who: list[str]) -> Any:
        orows, nrows = o.findall(w("tr")), n.findall(w("tr"))
        same_shape = len(orows) == len(nrows) and all(len(a.findall(w("tc"))) == len(b.findall(w("tc"))) for a, b in zip(orows, nrows))
        if not same_shape:
            box = etree.Element(w("tbl"))
            for c in n:
                if not is_w(c, "tr"):
                    box.append(copy.deepcopy(c))
            for tr in orows:
                box.append(self.mark_all(copy.deepcopy(tr), "del", who))
            for tr in nrows:
                box.append(self.mark_all(tr, "ins", who))
            return box
        for a, b in zip(orows, nrows):
            for ta, tb in zip(a.findall(w("tc")), b.findall(w("tc"))):
                ob, nb = blocks_of(ta), blocks_of(tb)
                merged: list[Any] = []
                for op, x, y in _pair(ob, nb):
                    if op == "del":
                        merged.append(self.mark_all(copy.deepcopy(x), "del", who))
                    elif op == "ins":
                        merged.append(self.mark_all(y, "ins", who))
                    elif is_w(x, "p") and is_w(y, "p"):
                        merged.extend(self.diff_para(copy.deepcopy(x), y, who))
                    else:
                        merged.append(y)
                for blk in nb:
                    if blk.getparent() is tb:
                        tb.remove(blk)
                for blk in merged:
                    tb.append(blk)
        return n

    # ── 一段按词比 ──
    def diff_para(self, o: Any, n: Any, who: list[str]) -> list[Any]:
        ao, an = atoms(o), atoms(n)
        if ao is None or an is None:
            return [self.mark_all(o, "del", who), self.mark_all(n, "ins", who)]
        so, sn = "".join(a[0] for a in ao if a[1] != "m"), "".join(a[0] for a in an if a[1] != "m")
        to, tn = _TOKEN.findall(so), _TOKEN.findall(sn)
        po, pn = _offsets(to), _offsets(tn)
        co, cn = [a for a in ao if a[1] != "m"], [a for a in an if a[1] != "m"]
        marks = _marks_at(an)
        p = etree.Element(n.tag, attrib=dict(n.attrib))
        nppr = n.find(w("pPr"))
        if nppr is not None:
            p.append(nppr)
            oppr = o.find(w("pPr"))
            if _ppr_key(oppr) != _ppr_key(nppr):
                ch = self._mark("pPrChange", who)
                inner = etree.SubElement(ch, w("pPr"))
                for c in (oppr if oppr is not None else []):
                    if local(c) not in ("rPr", "sectPr", "pPrChange"):
                        inner.append(copy.deepcopy(c))
                nppr.append(ch)
        pieces: list[tuple[str, list[tuple], list[tuple] | None]] = []
        sm = SequenceMatcher(None, to, tn, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                pieces.append(("keep", cn[pn[j1]:pn[j2]], co[po[i1]:po[i2]]))
            else:
                if i2 > i1:
                    pieces.append(("del", co[po[i1]:po[i2]], None))
                if j2 > j1:
                    pieces.append(("ins", cn[pn[j1]:pn[j2]], None))
        # 新段里每个字在新段的位置 (标记按位置插回去)
        pos_of = {id(a): k for k, a in enumerate(cn)}
        emitted = 0
        for mode, run_atoms, old_atoms in pieces:
            for k, a in enumerate(run_atoms):
                if mode != "del":
                    idx = pos_of[id(a)]
                    while emitted <= idx:
                        for mk in marks.get(emitted, []):
                            p.append(copy.deepcopy(mk))
                        emitted += 1
                self._emit(p, a, mode, who, old_atoms[k] if old_atoms is not None and k < len(old_atoms) else None)
        while emitted <= len(cn):
            for mk in marks.get(emitted, []):
                p.append(copy.deepcopy(mk))
            emitted += 1
        _coalesce(p)
        return [p]

    def _emit(self, p: Any, a: tuple, mode: str, who: list[str], old: tuple | None) -> None:
        ch, kind, el, run, wrap = a
        holder = p
        if wrap is not None:
            last = p[-1] if len(p) else None
            if last is not None and last.get("_w") == str(id(wrap)):
                holder = last
            else:
                holder = etree.SubElement(p, wrap.tag, attrib=dict(wrap.attrib))
                holder.set("_w", str(id(wrap)))
        if mode in ("ins", "del"):
            last = holder[-1] if len(holder) else None
            if last is not None and local(last) == mode and last.get("_m") == "1":
                box = last
            else:
                box = self._mark(mode, who)
                box.set("_m", "1")
                holder.append(box)
        else:
            box = holder
        rpr = run.find(w("rPr"))
        fmt_changed = mode == "keep" and old is not None and _rpr_key(old[3].find(w("rPr"))) != _rpr_key(rpr)
        key = f"{id(run)}|{mode}|{fmt_changed}"
        last = box[-1] if len(box) else None
        if last is not None and local(last) == "r" and last.get("_k") == key:
            r = last
        else:
            r = etree.SubElement(box, w("r"))
            r.set("_k", key)
            if rpr is not None:
                nr = copy.deepcopy(rpr)
                if fmt_changed:
                    chg = self._mark("rPrChange", who)
                    old_rpr = old[3].find(w("rPr"))
                    inner = etree.SubElement(chg, w("rPr"))
                    for c in (old_rpr if old_rpr is not None else []):
                        if local(c) != "rPrChange":
                            inner.append(copy.deepcopy(c))
                    nr.append(chg)
                r.append(nr)
            elif fmt_changed:
                nr = etree.SubElement(r, w("rPr"))
                chg = self._mark("rPrChange", who)
                inner = etree.SubElement(chg, w("rPr"))
                for c in old[3].find(w("rPr")):
                    inner.append(copy.deepcopy(c))
                nr.append(chg)
        if kind == "c":
            tag = "delText" if mode == "del" else "t"
            last = r[-1] if len(r) else None
            if last is not None and local(last) == tag:
                last.text = (last.text or "") + ch
            else:
                t = etree.SubElement(r, w(tag))
                t.text = ch
                t.set(XML_SPACE, "preserve")
        else:
            c = copy.deepcopy(el)
            if mode == "del" and local(c) == "instrText":
                c.tag = w("delInstrText")
            r.append(c)

    # ── 表格 ──
    def _table(self, it: dict[str, Any]) -> Any:
        base_tbl, part = it["el"], it["part"]
        tbl = etree.Element(base_tbl.tag, attrib=dict(base_tbl.attrib))
        for c in base_tbl:
            if not is_w(c, "tr"):
                tbl.append(self.dst.adopt(c, self.m.base.pkg, part, part))
        for row in it["rows"]:
            t = row["t"]
            if t == "keep":
                tbl.append(self.dst.adopt(row["el"], self.m.base.pkg, part, part))
            elif t == "cells":
                tbl.append(self._cells(row))
            elif t == "change":
                s = row["seg"]
                self._count(s.who)
                tbl.extend(self._rows(s.who, s.old, s.new, part))
            else:
                s = row["seg"]
                d = self.dec.get(s.cid) or {}
                o = next((o for o in s.options if o.key == d.get("pick")), None)
                if d.get("pick") == BASE:
                    tbl.extend(self.dst.adopt(u.data[0], self.m.base.pkg, part, part) for u in s.old)
                elif o is None:
                    self.left += 1
                    tbl.extend(self.dst.adopt(u.data[0], self.m.base.pkg, part, part) for u in s.old)
                else:
                    self._count(o.who)
                    tbl.extend(self._rows(o.who, s.old, o.units, part))
        return tbl

    def _rows(self, who: list[str], old: list[Unit], new: list[Unit], part: str) -> list[Any]:
        news = [self._adopt(u, part) for u in new]
        if not self.tracked:
            return news
        olds = [self.dst.adopt(u.data[0], self.m.base.pkg, part, part) for u in old]
        if len(olds) == len(news) and all(len(a.findall(w("tc"))) == len(b.findall(w("tc"))) for a, b in zip(olds, news)):
            box = etree.Element(w("tbl"))
            for r in news:
                box.append(r)
            fake_old = etree.Element(w("tbl"))
            for r in olds:
                fake_old.append(r)
            return list(self.diff_table(fake_old, box, who).findall(w("tr")))
        return [self.mark_all(r, "del", who) for r in olds] + [self.mark_all(r, "ins", who) for r in news]

    def _cells(self, row: dict[str, Any]) -> Any:
        base_tr, part = row["el"], row["part"]
        tr = etree.Element(base_tr.tag, attrib=dict(base_tr.attrib))
        cells = base_tr.findall(w("tc"))
        k = 0
        for c in base_tr:
            if not is_w(c, "tc"):
                tr.append(self.dst.adopt(c, self.m.base.pkg, part, part))
                continue
            tc = etree.SubElement(tr, c.tag, attrib=dict(c.attrib))
            pr = c.find(w("tcPr"))
            if pr is not None:
                tc.append(copy.deepcopy(pr))
            got = self.blocks(row["cells"][k])
            for b in got:
                tc.append(b)
            if not got or not is_w(got[-1], "p"):
                etree.SubElement(tc, w("p"))
            k += 1
        assert k == len(cells)
        return tr


def custom_units(text: str, s: Seg) -> list[Unit]:
    """发起人自己写的 → 段落 (格式照原文第一段; 原文没有就照第一种改法)。"""
    tmpl_u = next((u for u in s.old if is_w(u.data[0], "p")), None) or next(
        (u for o in s.options for u in o.units if is_w(u.data[0], "p")), None)
    tmpl, who, part = (tmpl_u.data if tmpl_u else (None, "", DOC))
    out = []
    for line in text.split("\n"):
        p = etree.Element(w("p"))
        if tmpl is not None and tmpl.find(w("pPr")) is not None:
            p.append(copy.deepcopy(tmpl.find(w("pPr"))))
        r = etree.SubElement(p, w("r"))
        fr = tmpl.find(f".//{w('r')}/{w('rPr')}") if tmpl is not None else None
        if fr is not None:
            r.append(copy.deepcopy(fr))
        t = etree.SubElement(r, w("t"))
        t.text = line
        t.set(XML_SPACE, "preserve")
        out.append(Unit("custom:" + line, line, (p, who, part)))
    return out


def _pair(olds: list[Any], news: list[Any]) -> list[tuple[str, Any, Any]]:
    """新旧两串块怎么一一对上: 内容像的配成「改了这段」, 剩下的算删 / 加。"""
    from .word_norm import text  # noqa: PLC0415
    ot, nt = [text(e) for e in olds], [text(e) for e in news]
    sm = SequenceMatcher(None, ot, nt, autojunk=False)
    out: list[tuple[str, Any, Any]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.extend(("pair", olds[i], news[j]) for i, j in zip(range(i1, i2), range(j1, j2)))
            continue
        i, j = i1, j1
        while i < i2 and j < j2:
            if local(olds[i]) == local(news[j]) and (SequenceMatcher(None, ot[i], nt[j], autojunk=False).ratio() >= 0.3 or (i2 - i1 == j2 - j1)):
                out.append(("pair", olds[i], news[j]))
                i += 1
                j += 1
            elif i2 - i > j2 - j:
                out.append(("del", olds[i], None))
                i += 1
            else:
                out.append(("ins", None, news[j]))
                j += 1
        out.extend(("del", olds[k], None) for k in range(i, i2))
        out.extend(("ins", None, news[k]) for k in range(j, j2))
    return out


def atoms(p: Any) -> list[tuple] | None:
    """段落 → 一串「字」: (字符, 种类 c 字 / e 制表换行等 / o 对象 / m 零宽标记, 元素, 所在 run, 外层超链接)。
    有逐字比不了的东西返 None。"""
    out: list[tuple] = []
    obj = [0]

    def walk(parent: Any, wrap: Any) -> bool:
        for c in parent:
            if not isinstance(c.tag, str):
                continue
            n = local(c)
            if n == "pPr" or n == "proofErr":
                continue
            if n in _MARKS:
                out.append(("", "m", c, None, wrap))
            elif n == "hyperlink" and wrap is None:
                if not walk(c, c):
                    return False
            elif n == "r":
                for x in c:
                    xn = local(x)
                    if xn in ("rPr", "lastRenderedPageBreak"):
                        continue
                    if xn == "t":
                        out.extend((ch, "c", None, c, wrap) for ch in (x.text or ""))
                    elif xn in _SPECIAL:
                        out.append((_SPECIAL[xn] or "­", "e", x, c, wrap))
                    elif xn in ("commentReference", "annotationRef"):
                        out.append(("", "m", _solo_run(c, x), None, wrap))
                    else:
                        obj[0] += 1
                        out.append((chr(0xE100 + obj[0] % 0x1000), "o", x, c, wrap))
            elif n in _COMPLEX or n == "hyperlink":
                return False
        return True
    return out if walk(p, None) else None


def _solo_run(r: Any, child: Any) -> Any:
    nr = etree.Element(w("r"))
    if r.find(w("rPr")) is not None:
        nr.append(copy.deepcopy(r.find(w("rPr"))))
    nr.append(copy.deepcopy(child))
    return nr


def _offsets(tokens: list[str]) -> list[int]:
    out, k = [0], 0
    for t in tokens:
        k += len(t)
        out.append(k)
    return out


def _marks_at(an: list[tuple]) -> dict[int, list[Any]]:
    out: dict[int, list[Any]] = {}
    k = 0
    for a in an:
        if a[1] == "m":
            out.setdefault(k, []).append(a[2])
        else:
            k += 1
    return out


def _rpr_key(rpr: Any) -> str:
    if rpr is None:
        return ""
    keep = [c for c in rpr if local(c) not in ("lang", "noProof", "rPrChange", "szCs", "bCs", "iCs", "webHidden")]
    return "".join(sorted(etree.tostring(c).decode() for c in keep))


def _ppr_key(ppr: Any) -> str:
    if ppr is None:
        return ""
    return "".join(etree.tostring(c).decode() for c in ppr if local(c) not in ("rPr", "sectPr", "pPrChange"))


def _to_deltext(r: Any) -> None:
    for t in r.iter(w("t")):
        t.tag = w("delText")
    for t in r.iter(w("instrText")):
        t.tag = w("delInstrText")


def _para_mark(p: Any, mark: Any) -> None:
    ppr = p.find(w("pPr"))
    if ppr is None:
        ppr = etree.Element(w("pPr"))
        p.insert(0, ppr)
    rpr = ppr.find(w("rPr"))
    if rpr is None:
        rpr = etree.Element(w("rPr"))
        tail = next((c for c in ppr if local(c) in ("sectPr", "pPrChange")), None)
        (tail.addprevious(rpr) if tail is not None else ppr.append(rpr))
    rpr.insert(0, mark)


def _deleted_para(p: Any) -> bool:
    return p.find(f"{w('pPr')}/{w('rPr')}/{w('del')}") is not None


def _coalesce(p: Any) -> None:
    """去掉写的时候用的临时属性。"""
    for el in p.iter():
        if isinstance(el.tag, str):
            for k in ("_w", "_m", "_k"):
                if k in el.attrib:
                    del el.attrib[k]


__all__ = ["Writer", "custom_units", "Undecided", "Option", "W"]
