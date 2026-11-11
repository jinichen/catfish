"""PowerPoint (.pptx) 的 N 份三方合并 (docs/DOC-REVISE-MERGE-PLAN.md §4.2 / §4.4 第 4 条 / §5)。单元是页, 页里不再拆。

对齐: 按 PowerPoint 的页编号 (sldId, 改动 / 挪动后不变) 认页, 不按位置 —— 删页、插页、调顺序都认得出来。
LibreOffice 另存会把页编号按顺序重排: 看出编号不可信 (内容一样的页编号不同) 时改按内容认页 (_realign)。
页的内容签名按语义算 (slides_sig: 文字 + 生效的格式 + 位置 + 图片 / 表格 / 图表内容), WPS / LibreOffice 原样另存不算改动。
每一页对每个交回人只有三种情况: 没动 / 改了 (内容签名变了) / 删了。每页单独交给 core.merge_seq
(底稿 = 这一页, 每人的版本 = [他的这页] 或 [] (删了), scope = 页编号), 于是:
  · 一个人改了 → 用他的;几个人改成一样 → 用这个
  · 改法不同 → 先在页内按形状合 (slides_shapes: 形状 → 位置 / 外框 / 段落), 改到同一个形状的同一段才冲突
  · 一个删一个改、或者有人那份页里的形状对不上号 → 整页冲突 (kind="slide", 整页二选一, 没有「自己写」)
页序单独算 (挪页在序列 diff 里会变成「删 + 插」, 跟别人改这页撞成假冲突):
  · 只有一个人调过顺序 → 用他的;几个人调得不一样 → 用先交回的人的, warnings 里写明
  · 新页插在它在交回稿里的前一页后面;同一处几个人各加了页, 都保留, 按交回先后排 (内容一样的只留一份)
写回在底稿的包上做 (slides_opc): 换进来的页连同图片 / 媒体 / 图表 / 嵌入对象 / 备注页一起搬, 底稿里不再用的页
保存时自然不写。docProps/ (含我们埋的 catfish_rev) 跟页无关, 不参与比较, 合并版沿用底稿的。
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.util import Emu

from . import core
from . import slides_opc as opc
from . import slides_shapes as shapes
from .slides_sig import is_shape, shape_infos, shape_tree, slide_lite

if TYPE_CHECKING:
    from . import Ctx, Return

_NO_TEXT = "(这页没有文字)"
_DELETED = "(删掉了这页)"
_SAME_TEXT = "(文字没变, 改的是图片、格式、版式或备注)"


@dataclass
class Page:
    sid: int
    idx: int          # 在它那份里的位置 (0 起)
    part: Any
    sig: str          # 内容签名 (不含页编号)
    text: str
    title: str
    layout: str
    has_notes: bool
    comments: str
    lite: str = ""    # 语义签名原文 (按内容认页时比相似度)
    notes: str = ""   # 备注文字


@dataclass
class Deck:
    who: str          # 底稿 = ""
    prs: Any
    pages: list[Page]
    design: str       # 页面大小 (母版 / 主题不合并, 也不比)
    secs: list[tuple[str, str, list[int]]]
    fp: Any = None

    @property
    def by_sid(self) -> dict[int, Page]:
        return {p.sid: p for p in self.pages}


@dataclass
class Run:
    """一个人在同一处连着新加的几页。anchors: 交回稿里它前面的底稿页 (最近的在前, 最后是 None = 最前面)。"""
    anchors: list[Any]
    who: list[str]
    pages: list[Page]


@dataclass
class Plan:
    base: Deck
    decks: list[Deck]
    segs: list[core.Seg]          # 跟 base.pages 一一对应
    runs: list[Run]
    order: list[int]              # 底稿页的页序 (用谁的就是谁的; 没人调就是底稿的)
    order_who: list[str]          # 页序算谁的改动
    touched: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    inpage: dict[int, shapes.PageMerge] = field(default_factory=dict)   # 页内按形状合的页 (页编号 → 合法)


# ── 读一份 pptx ──────────────────────────────────────────────────────────────

def _texts(shapes: Any) -> list[str]:
    out: list[str] = []
    for sh in shapes:
        try:
            if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
                out += _texts(sh.shapes)
                continue
        except NotImplementedError:
            pass
        if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
            t = sh.text_frame.text.replace("\v", "\n").strip()
            if t:
                out.append(t)
        elif getattr(sh, "has_table", False) and sh.has_table:
            for row in sh.table.rows:
                cells = [c.text.replace("\v", " ").replace("\n", " ").strip() for c in row.cells]
                if any(cells):
                    out.append(" | ".join(cells))
    return out


def _title(slide: Any, text: str) -> str:
    t = slide.shapes.title
    s = (t.text_frame.text if t is not None and t.has_text_frame else "").replace("\v", " ").replace("\n", " ").strip()
    return s or text.replace("\n", " ")[:20]


def _open(data: bytes, who: str) -> Deck:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError("包里有重名的部件")
        prs = Presentation(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001  (坏文件 / 不是 pptx)
        raise ValueError(f"{who or '原稿'} 这份不是能打开的 pptx: {e}") from e
    fp = opc.Fingerprint({id(s.part): s.slide_id for s in prs.slides})
    pages = []
    for i, s in enumerate(prs.slides):
        text = "\n".join(_texts(s.shapes))
        pages.append(Page(s.slide_id, i, s.part, "", text, _title(s, text), s.slide_layout.name or "",
                          s.has_notes_slide, opc.comments_fp(s.part)))
    d = Deck(who, prs, pages, f"{prs.slide_width}x{prs.slide_height}", opc.sections(prs.part), fp)
    _sign(d)
    return d


def _sign(d: Deck) -> None:
    """页的内容签名 = 语义签名 (slides_sig) + 版式名 + 备注文字; 不含页编号。"""
    d.fp.memo.clear()
    for pg, s in zip(d.pages, d.prs.slides):
        notes = ""
        if s.has_notes_slide and s.notes_slide.notes_text_frame is not None:
            notes = s.notes_slide.notes_text_frame.text
        pg.notes = notes
        pg.lite = slide_lite(s.part, d.fp) + "\0" + pg.layout + "\0" + notes
        pg.sig = hashlib.sha1(pg.lite.encode("utf-8")).hexdigest()[:20]


def _similar(q: Page, b: Page) -> float:
    a, c = (q.text, b.text) if (q.text or b.text) else (q.lite, b.lite)
    return SequenceMatcher(None, a, c, autojunk=False).ratio() + (0.3 if q.title and q.title == b.title else 0.0)


def _realign(base: Deck, d: Deck) -> None:
    """有的软件另存时把页编号按顺序重排 (LibreOffice 实测会; 原来不是顺序编号的就对不上了)。
    看出来了 (内容一模一样的页编号却不同) 就不信编号, 改按内容认页: 先认内容完全一样的,
    再按文字相似度认改过的 (够像才认, 认不上的算新页 / 删页); 认好后把这份的页编号换成底稿的。"""
    bsig: dict[str, list[int]] = {}
    for b in base.pages:
        bsig.setdefault(b.sig, []).append(b.sid)
    if not any(len(bsig.get(q.sig, [])) == 1 and bsig[q.sig][0] != q.sid for q in d.pages):
        return
    free = {b.sid: b for b in base.pages}
    got: dict[int, int] = {}
    for q in d.pages:
        hit = next((sid for sid in bsig.get(q.sig, []) if sid in free), None)
        if hit is not None:
            got[q.idx] = hit
            free.pop(hit)
    pairs = sorted(((_similar(q, b), q.idx, b.sid) for q in d.pages if q.idx not in got for b in free.values()),
                   key=lambda t: (-t[0], t[1], t[2]))
    for score, qi, sid in pairs:
        if score < 0.5:
            break
        if qi not in got and sid in free:
            got[qi] = sid
            free.pop(sid)
    nxt = max([b.sid for b in base.pages] + [q.sid for q in d.pages] + [255])
    old_new: dict[int, int] = {}
    for q in d.pages:
        if q.idx not in got:
            nxt += 1
        old_new[q.sid] = got.get(q.idx, nxt)
    for q in d.pages:
        q.sid = old_new[q.sid]
    d.fp.sid_of = {k: old_new.get(v, v) for k, v in d.fp.sid_of.items()}
    d.secs = [(n, i, [old_new.get(x, x) for x in ids]) for n, i, ids in d.secs]
    _sign(d)               # 页里链到别的页时签名带页编号, 换号后重算


def _where(p: Page) -> str:
    return f"第 {p.idx + 1} 页「{p.title}」" if p.title else f"第 {p.idx + 1} 页"


def _unit(p: Page, who: str | None) -> core.Unit:
    return core.Unit(f"{p.sid}:{p.sig}", p.text, (who, p))


# ── 算: 每页谁改了、页序、新页 ───────────────────────────────────────────────

def _plan(base_data: bytes, returns: list[Return], ctx: Ctx) -> Plan:
    base = _open(base_data, "")
    latest: dict[str, bytes] = {}
    for r in returns:                      # 同一人交了几次: 后交的覆盖先交的
        latest.pop(r.who, None)
        latest[r.who] = r.data
    decks = [_open(data, who) for who, data in latest.items()]
    for d in decks:
        _realign(base, d)
    whos = [d.who for d in decks]
    segs = []
    for p in base.pages:
        versions = {}
        for d in decks:
            q = d.by_sid.get(p.sid)
            versions[d.who] = [_unit(q, d.who)] if q is not None else []
        segs.extend(core.merge_seq([_unit(p, None)], versions, whos, scope=f"slide:{p.sid}"))
    plan = Plan(base, decks, segs, [], [p.sid for p in base.pages], [])
    _plan_inpage(plan, ctx)
    for d in decks:
        mine = d.by_sid
        if any(p.sid not in mine or mine[p.sid].sig != p.sig for p in base.pages):
            plan.touched.add(d.who)
    _plan_runs(plan)
    _plan_order(plan, ctx)
    _plan_warnings(plan, ctx)
    return plan


def _src(d: Deck, q: Page) -> shapes.PageSrc:
    return shapes.PageSrc(d.who, shape_infos(q.part, d.fp), q.notes, q.layout, q.part)


def _plan_inpage(plan: Plan, ctx: Ctx) -> None:
    """一页被几个人改成了不同结果 (没人删): 先试页内按形状合; 形状对不上号的留着整页冲突, 写进 warnings。"""
    for i, (p, s) in enumerate(zip(plan.base.pages, plan.segs)):
        if s.kind != "conflict" or not all(o.units for o in s.options):
            continue
        others = [_src(d, d.by_sid[p.sid]) for d in plan.decks
                  if p.sid in d.by_sid and d.by_sid[p.sid].sig != p.sig]
        got = shapes.plan_page(f"slide:{p.sid}:shape", _where(p), _src(plan.base, p), others, ctx.name)
        if isinstance(got, str):
            plan.warnings.append(got)
        else:
            plan.inpage[p.sid] = got
            plan.segs[i] = core.Seg("keep", s.start, s.end, s.old)


def _inpage_warnings(pm: shapes.PageMerge, ctx: Ctx) -> list[str]:
    who = list(shapes.credits(pm))
    out = list(pm.warnings)
    if len(who) > 1 and shapes.moved_or_added(pm):
        out.append(f"{pm.where}合并了{'、'.join(ctx.name(w) for w in who)}的修改, 请看一眼版式有没有重叠")
    return out


def _plan_runs(plan: Plan) -> None:
    bmap = plan.base.by_sid
    seen: dict[tuple[Any, tuple[str, ...]], Run] = {}
    for d in plan.decks:
        prev: list[Any] = [None]
        cur: Run | None = None
        for p in d.pages:
            if p.sid in bmap:
                prev = [p.sid] + prev
                cur = None
                continue
            plan.touched.add(d.who)
            if cur is None:
                cur = Run(list(prev), [d.who], [])
                plan.runs.append(cur)
            cur.pages.append(p)
    out = []
    for r in plan.runs:                    # 同一处加了内容一样的页: 只留一份, 算几个人的
        k = (r.anchors[0], tuple(p.sig for p in r.pages))
        if k in seen:
            seen[k].who += [w for w in r.who if w not in seen[k].who]
        else:
            seen[k] = r
            out.append(r)
    plan.runs = out


def _kept(d: Deck, bmap: dict[int, Page]) -> list[int]:
    return [p.sid for p in d.pages if p.sid in bmap]


def _same_order(a: list[int], b: list[int]) -> bool:
    common = set(a) & set(b)
    return [s for s in a if s in common] == [s for s in b if s in common]


def _plan_order(plan: Plan, ctx: Ctx) -> None:
    bmap = plan.base.by_sid
    base_ids = [p.sid for p in plan.base.pages]
    movers = [d for d in plan.decks if not _same_order(_kept(d, bmap), base_ids)]
    if not movers:
        return
    first = _kept(movers[0], bmap)
    plan.order = first
    plan.order_who = [d.who for d in movers if _same_order(_kept(d, bmap), first)]
    for d in movers:
        plan.touched.add(d.who)
    others = [d.who for d in movers if d.who not in plan.order_who]
    if others:
        plan.warnings.append(f"{'、'.join(ctx.name(w) for w in [movers[0].who] + others)}都调了页顺序, 调得不一样, "
                             f"用的是先交回的{ctx.name(movers[0].who)}的顺序")


def _plan_warnings(plan: Plan, ctx: Ctx) -> None:
    base, bmap = plan.base, plan.base.by_sid
    for d in plan.decks:
        n = ctx.name(d.who)
        if d.design != base.design:
            plan.touched.add(d.who)
            plan.warnings.append(f"{n}改了页面大小, 页面大小不合并 (合并版用原稿的)")
        if base.secs and _sec_view(d, bmap) != _sec_view(base, bmap):
            plan.touched.add(d.who)
            plan.warnings.append(f"{n}调了分节, 分节不合并 (合并版按原稿的分节, 新页跟它前一页同节)")
        for p in d.pages:
            b = bmap.get(p.sid)
            if p.comments and (b is None or b.comments != p.comments):
                plan.touched.add(d.who)
                plan.warnings.append(f"{n}在他那份{_where(p)}上的批注没合进来 (批注不参与合并)")


def _sec_view(d: Deck, bmap: dict[int, Page]) -> tuple[Any, ...]:
    names = [name for name, _, _ in d.secs]
    of = {sid: name for name, _, ids in d.secs for sid in ids if sid in bmap}
    return tuple(names), tuple(sorted(of.items()))


def _take_warnings(plan: Plan, base_p: Page | None, p: Page, who: str, ctx: Ctx, prefix: str = "") -> list[str]:
    """用某人的这页 (改过的 / 新加的) 时要提示的。"""
    out = []
    names = {opc.layout_name(lay) for m in opc.masters(plan.base.prs.part) for lay in opc.layouts_of(m)}
    if p.layout not in names:
        out.append(f"{prefix}{ctx.name(who)}那份{_where(p)}用的版式「{p.layout}」原稿里没有, "
                   f"把他那份里这个版式所在的母版一起带进合并版了")
    if base_p is not None and base_p.comments:
        out.append(f"{prefix}原稿{_where(base_p)}上的批注随旧版本去掉了 (换成了{ctx.name(who)}的版本)")
    return out


def _option_text(units: list[core.Unit], base_text: str) -> str:
    if not units:
        return _DELETED
    t = units[0].text
    if t == base_text:
        return (t + "\n" + _SAME_TEXT) if t else _SAME_TEXT
    return t or _NO_TEXT


def _changes(plan: Plan, segs: list[core.Seg]) -> dict[str, int]:
    n = core.stats(segs)
    for r in plan.runs:
        for w in r.who:
            n[w] = n.get(w, 0) + len(r.pages)
    for w in plan.order_who:
        n[w] = n.get(w, 0) + 1
    for pm in plan.inpage.values():
        for w, what in shapes.credits(pm).items():
            n[w] = n.get(w, 0) + len(what)
    return n


def _same(plan: Plan) -> list[str]:
    return [d.who for d in plan.decks if d.who not in plan.touched]


# ── 对外 ───────────────────────────────────────────────────────────────────

def analyze(base: bytes, returns: list[Return], ctx: Ctx) -> dict[str, Any]:
    plan = _plan(base, returns, ctx)
    conflicts, warnings = [], list(plan.warnings)
    for p, s in zip(plan.base.pages, plan.segs):
        if p.sid in plan.inpage:
            conflicts += [c.info for c in plan.inpage[p.sid].confs]
            warnings += _inpage_warnings(plan.inpage[p.sid], ctx)
        if s.kind == "change" and s.new:
            warnings += _take_warnings(plan, p, s.new[0].data[1], s.who[0], ctx)
        if s.kind != "conflict":
            continue
        options = []
        for o in s.options:
            options.append({"key": o.key, "who": list(o.who), "text": _option_text(o.units, p.text)})
            if o.units:
                pre = f"如果{_where(p)}选{ctx.name(o.who[0])}的: "
                warnings += _take_warnings(plan, p, o.units[0].data[1], o.who[0], ctx, pre)
        conflicts.append({"cid": s.cid, "where": _where(p), "kind": "slide", "custom": False,
                          "base": p.text or _NO_TEXT, "options": options, "combined": None})
    for r in plan.runs:
        for q in r.pages:
            warnings += _take_warnings(plan, None, q, r.who[0], ctx)
    return {"conflicts": conflicts, "changes": _changes(plan, [s for s in plan.segs if s.kind != "conflict"]),
            "same": _same(plan), "warnings": warnings}


@dataclass
class _Entry:
    sid: int                # 合并版里的页编号
    page: Page              # 内容从哪一页来 (底稿的或某人的)
    who: list[str]          # 用谁的 (底稿 = [])
    note: str = ""          # tracked 版备注里加的那一行
    src: str = ""           # page 属于谁那份 (底稿 = "")
    merge: Any = None       # 页内按形状合的 (shapes.PageMerge): 在底稿这页上换 / 加形状
    nshapes: int = -1       # 页内合并后这页该有几个顶层形状 (自检用)


def render(base: bytes, returns: list[Return], decisions: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    plan = _plan(base, returns, ctx)
    segs, left = [], 0
    for s in plan.segs:
        try:
            segs += core.resolve([s], decisions, ctx.owner)
        except core.Undecided:
            left += 1
    left += sum(shapes.resolve(pm, decisions, ctx.owner) for pm in plan.inpage.values())
    if left:
        raise core.Undecided(f"还有 {left} 处冲突没定")
    warnings = list(plan.warnings)

    def names(ws: list[str]) -> str:
        return "、".join(ctx.name(w) for w in ws)
    take: dict[int, _Entry | None] = {}
    for p, s, old in zip(plan.base.pages, segs, plan.segs):
        decided = old.kind == "conflict"
        pm = plan.inpage.get(p.sid)
        if pm is not None:
            cr = shapes.credits(pm)
            note = "这页合并了" + "、".join(
                f"{ctx.name(w)} ({'、'.join(dict.fromkeys(what))})" for w, what in cr.items()) if cr else ""
            take[p.sid] = _Entry(p.sid, p, list(cr), note, merge=pm)
            warnings += _inpage_warnings(pm, ctx)
        elif s.kind == "keep":
            note = ""
            if decided:
                note = f"这页保留原稿 ({names([w for o in old.options for w in o.who])} 的改法没有采用)"
            take[p.sid] = _Entry(p.sid, p, [], note)
        elif not s.new:
            take[p.sid] = None                                  # 删了
        else:
            q = s.new[0].data[1]
            note = f"这页用的是{names(s.who)}的版本" + (" (几个人改得一样)" if len(s.who) > 1 else "")
            take[p.sid] = _Entry(p.sid, q, list(s.who), note, s.who[0])
            pre = f"{_where(p)}选了{ctx.name(s.who[0])}的: " if decided else ""
            warnings += _take_warnings(plan, p, q, s.who[0], ctx, pre)
    for r in plan.runs:
        for q in r.pages:
            warnings += _take_warnings(plan, None, q, r.who[0], ctx)
    entries = _final_order(plan, take, ctx)
    clean, more = _write(base, plan, entries)
    warnings += more
    _check(clean, entries)
    tracked = _annotate(clean, entries)
    _check(tracked, entries)
    return {"tracked": tracked, "clean": clean, "changes": _changes(plan, segs), "warnings": warnings}


def _final_order(plan: Plan, take: dict[int, _Entry | None], ctx: Ctx) -> list[_Entry]:
    base_ids = [p.sid for p in plan.base.pages]
    alive = {s for s in base_ids if take.get(s) is not None}
    res = [s for s in plan.order if s in alive]
    for i, s in enumerate(base_ids):          # 调顺序的人删掉、但合并版里还在的页: 跟在底稿里它前一页后面
        if s in alive and s not in res:
            prev = next((base_ids[j] for j in range(i - 1, -1, -1) if base_ids[j] in res), None)
            res.insert(res.index(prev) + 1 if prev is not None else 0, s)
    after: dict[Any, list[Run]] = {}
    for r in plan.runs:
        anchor = next(a for a in r.anchors if a is None or a in alive)
        after.setdefault(anchor, []).append(r)
    used = set(base_ids) | {q.sid for d in plan.decks for q in d.pages}
    next_id = [max(list(used) + [255])]

    def new_entries(anchor: Any) -> list[_Entry]:
        out = []
        for r in after.get(anchor, []):
            for q in r.pages:
                next_id[0] += 1
                out.append(_Entry(next_id[0], q, list(r.who), f"这页是{'、'.join(ctx.name(w) for w in r.who)}新加的",
                                  r.who[0]))
        return out
    entries = new_entries(None)
    for s in res:
        e = take[s]
        assert e is not None
        entries.append(e)
        entries += new_entries(s)
    return entries


def _write(base: bytes, plan: Plan, entries: list[_Entry]) -> tuple[bytes, list[str]]:
    prs = Presentation(io.BytesIO(base))   # 底稿另开一份, 在它上面改
    dest = opc.Dest(prs)
    base_part = {s.slide_id: s.part for s in prs.slides}
    slide_of = {s.slide_id: s for s in prs.slides}
    if any((e.src and e.page.has_notes) or (e.merge is not None and e.merge.notes is not None) for e in entries):
        dest.notes_master()     # 先建好备注母版, 免得它的部件名跟后面搬进来的撞
    importers: dict[str, opc.Importer] = {}
    key: dict[int, tuple[str, int]] = {id(p): ("", sid) for sid, p in base_part.items()}
    for d in plan.decks:
        key.update({id(q.part): (d.who, q.sid) for q in d.pages})
    final: list[tuple[int, Any]] = []
    by_base: dict[int, Any] = {}
    by_src: dict[tuple[str, int], Any] = {}
    for e in entries:
        if not e.src:
            part = base_part[e.page.sid]
            if e.merge is not None:
                e.nshapes = _apply_merge(dest, slide_of[e.page.sid], e.merge, importers)
        else:
            part = importers.setdefault(e.src, opc.Importer(dest)).slide(e.page.part)
            by_src[(e.src, e.page.sid)] = part
        final.append((e.sid, part))
        if e.sid in plan.base.by_sid:
            by_base[e.sid] = part
    finals = {id(p) for _, p in final}

    def resolve(tgt: Any) -> Any:
        k = key.get(id(tgt))
        if k is None:
            return tgt if id(tgt) in finals else None
        if k[1] in by_base:
            return by_base[k[1]]
        return by_src.get(k)
    opc.set_slide_list(dest, final)
    warnings = []
    for i, (_, part) in enumerate(final):
        if opc.relink(part, resolve):
            warnings.append(f"合并版第 {i + 1} 页里有链接指向的页合并版里没有了, 这些链接去掉了")
    new_ids = {e.sid for e in entries if e.sid not in plan.base.by_sid}
    opc.set_sections(dest, plan.base.secs, [sid for sid, _ in final], new_ids)
    return _save(prs), warnings


def _apply_merge(dest: opc.Dest, slide: Any, pm: shapes.PageMerge, importers: dict[str, opc.Importer]) -> int:
    """页内合并的结果写到底稿这页上: 换 / 加形状 (各人的图片、图表、链接跟着搬, rId 改写), 备注, 版式。"""
    part = slide.part

    def adopt(el: Any, who: str) -> Any:
        return importers.setdefault(who, opc.Importer(dest)).adopt(el, pm.parts[who], part)
    els = shapes.build(pm, adopt)
    tree = shape_tree(part)
    old = [c for c in tree if is_shape(c)]
    at = tree.index(old[0]) if old else min(2, len(tree))
    for c in old:
        tree.remove(c)
    for i, el in enumerate(els):
        tree.insert(at + i, el)
    text = shapes.notes_text(pm)
    if text is not None:
        fresh = not slide.has_notes_slide
        ns = slide.notes_slide
        if fresh:               # python-pptx 起的名字可能跟搬进来还没挂上的备注页撞
            ns.part.partname = dest.fresh(str(ns.part.partname))
        if ns.notes_text_frame is not None:
            ns.notes_text_frame.text = text
    if pm.layout is not None:
        who, src = pm.layout
        lay = importers.setdefault(who, opc.Importer(dest)).layout(src.part_related_by(RT.SLIDE_LAYOUT))
        for rid, rel in list(part.rels.items()):
            if rel.reltype == RT.SLIDE_LAYOUT:
                opc.set_rel(part, rid, RT.SLIDE_LAYOUT, lay)
    opc.prune_rels(part)
    return len(els)


def _save(prs: Any) -> bytes:
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _annotate(clean: bytes, entries: list[_Entry]) -> bytes:
    """tracked 版: 被改过 / 新加的页, 备注里追加一行说明 (没有备注页的新建)。"""
    prs = Presentation(io.BytesIO(clean))
    for slide, e in zip(prs.slides, entries):
        if not e.note:
            continue
        ns = slide.notes_slide
        tf = ns.notes_text_frame
        if tf is None:     # 备注页里没有正文占位符: 加一个文本框
            tf = ns.shapes.add_textbox(Emu(457200), Emu(4572000), Emu(5943600), Emu(914400)).text_frame
        if tf.text.strip():
            tf.add_paragraph().text = "【合并说明】" + e.note
        else:
            tf.text = "【合并说明】" + e.note
    return _save(prs)


def _check(data: bytes, entries: list[_Entry]) -> None:
    """用 python-pptx 重新打开自检: 页数、每页编号、标题都要对得上, 不对就不出这一版。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError("包里有重名的部件")
        prs = Presentation(io.BytesIO(data))
        got = [(s.slide_id, _title(s, "\n".join(_texts(s.shapes)))) for s in prs.slides]
        counts = [len([c for c in shape_tree(s.part) if is_shape(c)]) for s in prs.slides]
        for s in prs.slides:
            s.slide_layout.name   # noqa: B018  (版式要能找到)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"合并版自检没通过, 打不开: {e}") from e
    want = [(e.sid, e.page.title) for e in entries]
    if len(got) != len(want) or any(g[0] != w[0] or (e.merge is None and g[1] != w[1])
                                    for g, w, e in zip(got, want, entries)):
        raise RuntimeError(f"合并版自检没通过: 页对不上 {got} != {want}")
    if any(e.nshapes >= 0 and e.nshapes != n for e, n in zip(entries, counts)):
        raise RuntimeError("合并版自检没通过: 页内合并后形状数对不上")


__all__ = ["analyze", "render"]
