"""pptx 同一页里按形状合并 (slides.py 用): 一页被几个人改成了不同结果时, 先试着在页内合, 合不了才整页二选一。

认形状靠形状编号 (p:cNvPr/@id)。编号不可信的 (LibreOffice / Keynote 另存会把编号重排) 这页不在页内合 ——
绝不靠猜把内容合到别的形状上。可信的标准 (保守): 编号不重复; 跟底稿至少有一个共同编号; 同一编号的形状类型 /
占位符类型没变; 语义签名跟底稿某个形状一模一样的, 编号也得一样。

单元 = 页里的一个顶层形状。每个形状单独交给 core.merge_seq (底稿 = [这个形状], 每人 = [他的] 或 [] (删了)):
  · 一个人动过 / 几个人改成一样 → 用他的; 一个删一个改 → 冲突 (kind="slide")
  · 几个人改得不一样 → 再拆成 位置 / 外框 (几何、图片、图表数据、表格行列) / 段落 三样各自合:
      位置、外框: 只有一个人动 → 用他的; 几个人动得不一样 → 这个形状整体冲突
      段落 (文本类): 按段再交给 core.merge_seq, 改到同一段才冲突 (kind="text", 可以自己写, 按原段落的格式写回)
      表格: 行列没变时按格、格里按段合
  · 新加的形状插在它在交回稿里的前一个形状后面; 同一处各加了形状, 都保留
备注按段 (行) 合; 版式几个人改得不一样用先交回的。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from lxml import etree

from . import core
from .slides_sig import A_NS, ShapeInfo, a, p, xfrm_of

Adopt = Callable[[Any, str], Any]          # (拷出来的元素, 谁那份的) → rId 改写成合并版这页的
_KIND = {"table": "表格", "pic": "图片", "chart": "图表", "group": "组合", "other": "对象"}
_PH = {"title": "标题", "ctrTitle": "标题", "subTitle": "副标题", "body": "正文", "obj": "正文"}


@dataclass
class PageSrc:
    who: str                 # 底稿 = ""
    shapes: list[ShapeInfo]
    notes: str
    layout: str
    part: Any                # 页部件 (搬形状时从它的 rels 找图片 / 图表)


@dataclass
class ShapeMerge:
    base: ShapeInfo
    segs: list[core.Seg]                      # 就一个: 形状级的 keep / change / conflict
    vers: dict[str, ShapeInfo] = field(default_factory=dict)
    mixed: bool = False                       # 拆成 位置 / 外框 / 段落 合的
    pos_who: list[str] = field(default_factory=list)
    frame_who: list[str] = field(default_factory=list)
    paras: list[core.Seg] | None = None
    cells: dict[tuple[int, int], list[core.Seg]] = field(default_factory=dict)


@dataclass
class NewRun:
    anchors: list[Any]       # 交回稿里它前面的底稿形状编号 (最近的在前, 最后 None = 最底下)
    who: list[str]
    shapes: list[ShapeInfo]


@dataclass
class Conf:
    info: dict[str, Any]
    holder: list[core.Seg]
    idx: int
    custom: Callable[[str, core.Seg], list[core.Unit]] | None = None


@dataclass
class PageMerge:
    where: str
    name: Callable[[str], str]
    order_who: list[str]
    parts: dict[str, Any]
    shapes: list[ShapeMerge] = field(default_factory=list)
    runs: list[NewRun] = field(default_factory=list)
    order: list[Any] = field(default_factory=list)
    notes: list[core.Seg] | None = None
    layout: tuple[str, Any] | None = None     # (用谁的, 他那页的版式部件)
    confs: list[Conf] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def label(s: ShapeInfo) -> str:
    """给人看的形状名: 标题 / 正文 / 表格 / 图片 / 图表, 文本框用它的前 12 个字。"""
    if s.ph in _PH:
        return _PH[s.ph]
    if s.kind != "text":
        return _KIND.get(s.kind, "对象")
    t = s.text.replace("\n", " ").strip()
    return f"「{t[:12]}」" if t else "形状"


def _trusted(base: list[ShapeInfo], mine: list[ShapeInfo]) -> bool:
    ids = [s.id for s in mine]
    if None in ids or len(set(ids)) != len(ids):
        return False
    bmap = {s.id: s for s in base}
    if base and not set(ids) & set(bmap):
        return False
    bsig: dict[str, list[ShapeInfo]] = {}
    for b in base:
        bsig.setdefault(b.sig, []).append(b)
    msig: dict[str, int] = {}
    for s in mine:
        msig[s.sig] = msig.get(s.sig, 0) + 1
    for s in mine:
        b = bmap.get(s.id)
        if b is not None and (b.tag != s.tag or b.kind != s.kind or _PH.get(b.ph, b.ph) != _PH.get(s.ph, s.ph)):
            return False
        same = bsig.get(s.sig, [])
        if len(same) == 1 and msig[s.sig] == 1 and same[0].id != s.id:
            return False
    return True


def _units(items: list[tuple[str, str, Any]], who: str | None) -> list[core.Unit]:
    return [core.Unit(sig, text, (who, el)) for sig, text, el in items]


def _lines(text: str, who: str | None) -> list[core.Unit]:
    return [core.Unit(t, t, (who, None)) for t in text.split("\n")] if text else []


def _para_like(tmpl: Any, text: str) -> Any:
    """发起人自己写的一段: 段落属性和字的格式照原来那段。"""
    new = etree.Element(a("p"), nsmap={"a": A_NS})
    rpr = None
    if tmpl is not None:
        ppr, r0 = tmpl.find(a("pPr")), tmpl.find(a("r"))
        if ppr is not None:
            new.append(copy.deepcopy(ppr))
        rpr = r0.find(a("rPr")) if r0 is not None else None
    r = etree.SubElement(new, a("r"))
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    etree.SubElement(r, a("t")).text = text
    return new


def _custom_paras(base_els: list[Any]) -> Callable[[str, core.Seg], list[core.Unit]]:
    def fn(text: str, seg: core.Seg) -> list[core.Unit]:
        out = []
        for i, line in enumerate(text.split("\n")):
            if seg.old:
                tmpl = seg.old[min(i, len(seg.old) - 1)].data[1]
            else:
                tmpl = base_els[min(max(seg.start - 1, 0), len(base_els) - 1)] if base_els else None
            out.append(core.Unit("custom:" + line, line, (None, _para_like(tmpl, line))))
        return out
    return fn


def _custom_lines(text: str, seg: core.Seg) -> list[core.Unit]:
    return _lines(text, None)


def _seg_where(s: core.Seg, total: int) -> str:
    if total <= 1:
        return ""
    if s.start == s.end:
        return f"第 {s.start} 段后" if s.start else "开头"
    return f"第 {s.start + 1} 段" if s.end - s.start == 1 else f"第 {s.start + 1}–{s.end} 段"


def _text_confs(pm: PageMerge, segs: list[core.Seg], what: str, total: int,
                custom: Callable[[str, core.Seg], list[core.Unit]]) -> None:
    for i, s in enumerate(segs):
        if s.kind != "conflict":
            continue
        base = "\n".join(u.text for u in s.old)
        texts = {o.who[0]: "\n".join(u.text for u in o.units) for o in s.options}
        combined = core.combine_text(base, texts, pm.order_who) if base and all(texts.values()) else None
        info = {"cid": s.cid, "where": f"{pm.where} · {what}{_seg_where(s, total)}", "kind": "text", "custom": True,
                "base": base or "(原来没有这段)", "combined": combined,
                "options": [{"key": o.key, "who": list(o.who), "text": texts[o.who[0]] or "(删掉了这段)"}
                            for o in s.options]}
        pm.confs.append(Conf(info, segs, i, custom))


def _describe(b: ShapeInfo, s: ShapeInfo | None) -> str:
    if s is None:
        return "删掉了这个" + (_KIND.get(b.kind) or "形状")
    out = []
    if s.frame != b.frame or s.tag != b.tag:
        out.append({"pic": "换了图片", "chart": "改了图表数据", "table": "改了表格的行列",
                    "group": "改了组合里的内容"}.get(b.kind, "改了形状"))
    if s.body != b.body:
        out.append("改成: " + (s.text or "(空)"))
    if s.pos != b.pos:
        out.append("挪了位置 / 改了大小")
    return ", ".join(out) or "改了"


def _shape_conf(pm: PageMerge, sm: ShapeMerge) -> None:
    s, b = sm.segs[0], sm.base
    options = []
    for o in s.options:
        desc = _describe(b, o.units[0].data[1] if o.units else None)
        options.append({"key": o.key, "who": list(o.who),
                        "text": f"{'、'.join(pm.name(w) for w in o.who)}: {desc}"})
    info = {"cid": s.cid, "where": f"{pm.where} · {label(b)}", "kind": "slide", "custom": False,
            "base": b.text or f"({_KIND.get(b.kind, '形状')})", "options": options, "combined": None}
    pm.confs.append(Conf(info, sm.segs, 0))


def _mix(pm: PageMerge, sm: ShapeMerge, scope: str) -> None:
    """几个人把同一个形状改得不一样: 位置 / 外框 / 段落分开合。合不了就留着形状级冲突。"""
    b, order = sm.base, pm.order_who
    ch = {w: s for w, s in sm.vers.items() if s.sig != b.sig}
    if any(s.tag != b.tag or s.kind != b.kind for s in ch.values()):
        return
    pos = {w: s.pos for w, s in ch.items() if s.pos != b.pos}
    frame = {w: s.frame for w, s in ch.items() if s.frame != b.frame}
    body = [w for w in order if w in ch and ch[w].body != b.body]
    if len(set(pos.values())) > 1 or len(set(frame.values())) > 1:
        return
    paras, cells = None, {}
    if b.kind == "text":
        paras = core.merge_seq(_units(b.paras, None), {w: _units(ch[w].paras, w) for w in body}, order,
                               scope=f"{scope}:{b.id}:p")
    elif b.kind == "table":
        if frame and any(w not in frame for w in body):
            return                              # 一个人改了行列, 另一个人改了格里的字: 对不上格
        if not frame:
            for key, (_, ps) in b.cells.items():
                segs = core.merge_seq(_units(ps, None), {w: _units(ch[w].cells[key][1], w) for w in body}, order,
                                      scope=f"{scope}:{b.id}:c{key[0]}.{key[1]}")
                if any(s.kind != "keep" for s in segs):
                    cells[key] = segs
    elif len({ch[w].body for w in body}) > 1:
        return
    sm.mixed, sm.paras, sm.cells = True, paras, cells
    sm.pos_who = [w for w in order if w in pos]
    sm.frame_who = [w for w in order if w in frame]
    if paras is not None:
        _text_confs(pm, paras, label(b), len(b.paras), _custom_paras([x[2] for x in b.paras]))
    for (r, c), segs in cells.items():
        ps = b.cells[(r, c)][1]
        _text_confs(pm, segs, f"表格第 {r + 1} 行第 {c + 1} 列", len(ps), _custom_paras([x[2] for x in ps]))


def plan_page(scope: str, where: str, base: PageSrc, others: list[PageSrc],
              name: Callable[[str], str]) -> PageMerge | str:
    """返 PageMerge; 形状对不上号、没法在页内合的返一句 warning (这页退回整页二选一)。"""
    bids = [s.id for s in base.shapes]
    if None in bids or len(set(bids)) != len(bids):
        return f"{where}: 原稿这页的形状编号有重复, 这页只能整页选用"
    for o in others:
        if not _trusted(base.shapes, o.shapes):
            return f"{where}: {name(o.who)}这份是用别的软件导出的, 页里的形状对不上号, 这页只能整页选用"
    order = [o.who for o in others]
    pm = PageMerge(where, name, order, {o.who: o.part for o in others})
    mine = {o.who: {s.id: s for s in o.shapes} for o in others}
    for b in base.shapes:
        vers = {w: mine[w][b.id] for w in order if b.id in mine[w]}
        versions = {w: ([core.Unit(vers[w].sig, vers[w].text, (w, vers[w]))] if w in vers else []) for w in order}
        segs = core.merge_seq([core.Unit(b.sig, b.text, (None, b))], versions, order, scope=f"{scope}:{b.id}")
        sm = ShapeMerge(b, segs, vers)
        if segs[0].kind == "conflict":
            if all(o.units for o in segs[0].options):
                _mix(pm, sm, scope)
            if not sm.mixed:
                _shape_conf(pm, sm)
        pm.shapes.append(sm)
    _plan_new(pm, base, others)
    if any(o.notes != base.notes for o in others):
        pm.notes = core.merge_seq(_lines(base.notes, None), {o.who: _lines(o.notes, o.who) for o in others}, order,
                                  scope=f"{scope}:notes")
        _text_confs(pm, pm.notes, "备注", 2, _custom_lines)
    lay = [o for o in others if o.layout != base.layout]
    if lay:
        pm.layout = (lay[0].who, lay[0].part)
        if len({o.layout for o in lay}) > 1:
            pm.warnings.append(f"{where}: {'、'.join(name(o.who) for o in lay)}把版式改得不一样, "
                               f"用的是先交回的{name(lay[0].who)}的")
    return pm


def _plan_new(pm: PageMerge, base: PageSrc, others: list[PageSrc]) -> None:
    bids = [s.id for s in base.shapes]
    seen: dict[tuple[Any, tuple[str, ...]], NewRun] = {}
    pm.order = list(bids)
    moved = False
    for o in others:
        prev: list[Any] = [None]
        cur: NewRun | None = None
        for s in o.shapes:
            if s.id in bids:
                prev, cur = [s.id] + prev, None
                continue
            if cur is None:
                cur = NewRun(list(prev), [o.who], [])
                pm.runs.append(cur)
            cur.shapes.append(s)
        kept = [s.id for s in o.shapes if s.id in bids]
        if not moved and kept != [i for i in bids if i in set(kept)]:      # 调了叠放顺序: 用先交回的
            moved, pm.order = True, kept
    out = []
    for r in pm.runs:
        k = (r.anchors[0], tuple(s.sig for s in r.shapes))
        if k in seen:
            seen[k].who += [w for w in r.who if w not in seen[k].who]
        else:
            seen[k] = r
            out.append(r)
    pm.runs = out


def resolve(pm: PageMerge, decisions: dict[str, Any], me: str) -> int:
    """按决定把冲突换掉; 返还剩几处没定。"""
    left = 0
    for c in pm.confs:
        s = c.holder[c.idx]
        if s.kind != "conflict":
            continue
        try:
            c.holder[c.idx] = core.resolve([s], decisions, me, c.custom)[0]
        except core.Undecided:
            left += 1
    return left


def credits(pm: PageMerge) -> dict[str, list[str]]:
    """每个人合进去的改动 (一处一条, 写的是改了哪个形状); 没定的冲突不算。"""
    out: dict[str, list[str]] = {}

    def add(ws: list[str], what: str) -> None:
        for w in ws:
            out.setdefault(w, []).append(what)
    for sm in pm.shapes:
        lb = label(sm.base)
        if sm.mixed:
            add(sm.pos_who, lb)
            add([w for w in sm.frame_who if w not in sm.pos_who], lb)
            for segs in ([sm.paras] if sm.paras is not None else []) + list(sm.cells.values()):
                for s in segs:
                    if s.kind == "change":
                        add(s.who, lb)
        elif sm.segs[0].kind == "change":
            add(sm.segs[0].who, lb)
    for r in pm.runs:
        for s in r.shapes:
            add(r.who, label(s))
    for s in pm.notes or []:
        if s.kind == "change":
            add(s.who, "备注")
    if pm.layout is not None:
        add([pm.layout[0]], "版式")
    return {w: out[w] for w in pm.order_who + [w for w in out if w not in pm.order_who] if w in out}


def moved_or_added(pm: PageMerge) -> bool:
    if pm.runs:
        return True
    for sm in pm.shapes:
        s = sm.segs[0]
        if sm.mixed and sm.pos_who:
            return True
        if not sm.mixed and s.kind == "change" and s.new and s.new[0].data[1].pos != sm.base.pos:
            return True
    return False


# ── 写回: 拼出合并后这页的形状 ───────────────────────────────────────────────

def _fill(body: Any, segs: list[core.Seg], adopt: Adopt) -> None:
    if body is None:
        return
    for old in body.findall(a("p")):
        body.remove(old)
    for s in segs:
        for u in (s.new if s.kind == "change" else s.old):
            who, el = u.data
            el = copy.deepcopy(el)
            body.append(adopt(el, who) if who else el)
    if body.find(a("p")) is None:
        etree.SubElement(body, a("p"))


def _set_xfrm(el: Any, src: Any) -> None:
    sx, dx = xfrm_of(src), xfrm_of(el)
    if sx is None:
        return
    new = copy.deepcopy(sx)
    if dx is not None:
        dx.getparent().replace(dx, new)
    elif el.find(p("spPr")) is not None:
        el.find(p("spPr")).insert(0, new)


def _build_mixed(sm: ShapeMerge, adopt: Adopt) -> Any:
    fw = sm.frame_who[0] if sm.frame_who else None
    el = adopt(copy.deepcopy(sm.vers[fw].el), fw) if fw else copy.deepcopy(sm.base.el)
    if sm.pos_who and fw not in sm.pos_who:
        _set_xfrm(el, sm.vers[sm.pos_who[0]].el)
    if sm.paras is not None:
        _fill(el.find(p("txBody")), sm.paras, adopt)
    if sm.cells:
        tcs = {(r, c): tc for r, tr in enumerate(el.iter(a("tr"))) for c, tc in enumerate(tr.findall(a("tc")))}
        for key, segs in sm.cells.items():
            _fill(tcs[key].find(a("txBody")), segs, adopt)
    return el


def build(pm: PageMerge, adopt: Adopt) -> list[Any]:
    """合并后这页的顶层形状 (按叠放顺序)。来自各人那页的元素都过一遍 adopt (搬图片 / 图表, 改写 rId)。"""
    made: dict[Any, Any] = {}
    for sm in pm.shapes:
        s = sm.segs[0]
        if sm.mixed:
            made[sm.base.id] = _build_mixed(sm, adopt)
        elif s.kind != "change":
            made[sm.base.id] = copy.deepcopy(sm.base.el)
        elif s.new:
            who, sh = s.new[0].data
            made[sm.base.id] = adopt(copy.deepcopy(sh.el), who)
    bids = [sm.base.id for sm in pm.shapes]
    res = [i for i in pm.order if i in made]
    for k, i in enumerate(bids):
        if i in made and i not in res:
            prev = next((bids[j] for j in range(k - 1, -1, -1) if bids[j] in res), None)
            res.insert(res.index(prev) + 1 if prev is not None else 0, i)
    after: dict[Any, list[Any]] = {}
    for r in pm.runs:
        anchor = next(x for x in r.anchors if x is None or x in made)
        after.setdefault(anchor, []).extend(adopt(copy.deepcopy(s.el), r.who[0]) for s in r.shapes)
    out, new = [], list(after.get(None, []))
    out += new
    for i in res:
        out.append(made[i])
        out += after.get(i, [])
        new += after.get(i, [])
    taken = {int(e.get("id")) for el in out if not any(el is n for n in new)
             for e in el.iter(p("cNvPr")) if (e.get("id") or "").isdigit()}
    nxt = max(taken | {1})
    for el in new:                         # 新形状的编号不能跟这页已有的撞
        for e in el.iter(p("cNvPr")):
            v = int(e.get("id")) if (e.get("id") or "").isdigit() else 0
            if v in taken or not v:
                nxt = max(nxt, max(taken | {1})) + 1
                e.set("id", str(nxt))
                v = nxt
            taken.add(v)
    return out


def notes_text(pm: PageMerge) -> str | None:
    if pm.notes is None:
        return None
    return "\n".join(u.text for s in pm.notes for u in (s.new if s.kind == "change" else s.old))
