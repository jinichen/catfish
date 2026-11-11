"""pptx 页的语义签名 (slides.py 用): 只看「有意义的内容」, 不看 XML 写法。

WPS / LibreOffice 另存时会把整份 XML 重写: 形状改名重新编号、把从版式 / 母版继承来的位置、字号、颜色、项目符号
显式写到每一页上、run 拆碎、补上各种 extLst。按 XML 比的话, 原样另存一次就成了「每页都改了」。
所以按形状顺序, 每个形状只取:
  · 类型 (文本 / 图片 / 表格 / 图表 / 组合 / 连接线 …) 和几何形状 (矩形不算)
  · 位置和尺寸: 实际生效的 (占位符没写就沿用版式 / 母版的; 组合里的换算成页面坐标), 四舍五入到约 1mm
  · 文字: 每段的级别、有没有项目符号; 每个 run 的文字 + 实际生效的 b / i / u / strike / 颜色 / 字号 / 上下标
    (run → 形状 lstStyle → 版式占位符 → 母版占位符 → 母版 txStyles / 演示文稿默认; false / 0 / none 和默认值都不算;
    主题色按母版 clrMap + 主题换算成 RGB 再比, tx1 和 dk1 两种写法一样)
  · 图片 / 媒体 / 链接: 引用内容的哈希 (外链按地址)
  · 表格: 行列结构 (合并单元格) 和格里的文字
  · 图表: 图表类型 + 每个系列的名字、分类、数值
形状 id / name、占位符序号、ext / extLst、字体名、段落对齐 / 间距这类一律不算。空的占位符 (放映时什么都不显示) 跳过。
"""
from __future__ import annotations

from typing import Any

from lxml import etree
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

from .slides_opc import R_NS, Fingerprint

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
GRID = 36000                       # 位置 / 尺寸按约 1mm 取整
_BULLETS = ("buNone", "buChar", "buAutoNum", "buBlip")
_TITLE = ("title", "ctrTitle")
_OTHER = ("dt", "ftr", "sldNum", "hdr")
_SHAPES = ("sp", "pic", "graphicFrame", "grpSp", "cxnSp", "contentPart")


def a(t: str) -> str:
    return f"{{{A_NS}}}{t}"


def p(t: str) -> str:
    return f"{{{P_NS}}}{t}"


def _local(el: Any) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _ph(sp: Any) -> tuple[str, str | None] | None:
    """占位符 (类型, 序号); 不是占位符返 None。类型没写 = body。"""
    for nv in sp:
        nvpr = nv.find(p("nvPr"))
        if nvpr is not None:
            ph = nvpr.find(p("ph"))
            return None if ph is None else (ph.get("type") or "body", ph.get("idx"))
    return None


def _kind(typ: str) -> str:
    return "title" if typ in _TITLE else typ if typ in _OTHER else "body"


def _find_ph(tree: Any, ph: tuple[str, str | None], by_idx: bool) -> Any:
    """版式里按序号找对应的占位符 (找不到再按类型), 母版里按类型找。"""
    sps = [(s, _ph(s)) for s in tree.iter(p("sp"))]
    sps = [(s, q) for s, q in sps if q is not None]
    if by_idx and ph[1] is not None:
        for s, q in sps:
            if q[1] == ph[1]:
                return s
    for s, q in sps:
        if q[0] == ph[0]:
            return s
    for s, q in sps:
        if _kind(q[0]) == _kind(ph[0]):
            return s
    return None


class _Theme:
    def __init__(self, master_part: Any):
        el = master_part._element
        cm = el.find(p("clrMap"))
        self.map = dict(cm.attrib) if cm is not None else {}
        self.colors: dict[str, str] = {}
        try:
            theme = etree.fromstring(master_part.part_related_by(RT.THEME).blob)
        except (KeyError, ValueError):
            return
        scheme = theme.find(f".//{a('clrScheme')}")
        for c in (scheme if scheme is not None else []):
            v = c.find(a("srgbClr"))
            s = c.find(a("sysClr"))
            if v is not None:
                self.colors[_local(c)] = (v.get("val") or "").upper()
            elif s is not None:
                self.colors[_local(c)] = (s.get("lastClr") or s.get("val") or "").upper()

    def color(self, fill: Any) -> str | None:
        """solidFill → 'RRGGBB(+变换)'; 主题色先按 clrMap 再按主题换成 RGB。"""
        if fill is None:
            return None
        if _local(fill) != "solidFill":
            return _local(fill)                      # noFill / gradFill …
        c = next((x for x in fill if isinstance(x.tag, str)), None)
        return None if c is None else self.color_el(c)

    def color_el(self, c: Any) -> str:
        n = _local(c)
        if n == "srgbClr":
            v = (c.get("val") or "").upper()
        elif n == "schemeClr":
            k = c.get("val") or ""
            v = self.colors.get(self.map.get(k, k), k)
        elif n == "sysClr":
            v = (c.get("lastClr") or c.get("val") or "").upper()
        else:
            v = f"{n}:{c.get('val')}"
        mods = "".join(f"/{_local(m)}={m.get('val')}" for m in c if isinstance(m.tag, str))
        return v + mods


class _Ctx:
    """一页的继承链: 版式 / 母版的占位符、母版 txStyles、演示文稿默认文字样式、主题色。"""

    def __init__(self, slide_part: Any, fp: Fingerprint):
        self.part, self.fp = slide_part, fp
        self.layout = slide_part.part_related_by(RT.SLIDE_LAYOUT)
        self.master = self.layout.part_related_by(RT.SLIDE_MASTER)
        self.theme = _Theme(self.master)
        tx = self.master._element.find(p("txStyles"))
        self.styles = {"title": None, "body": None, "other": None}
        if tx is not None:
            self.styles = {"title": tx.find(p("titleStyle")), "body": tx.find(p("bodyStyle")),
                           "other": tx.find(p("otherStyle"))}
        prs = slide_part.package.main_document_part._element
        self.default = prs.find(p("defaultTextStyle"))

    def bases(self, sp: Any) -> list[Any]:
        """这个占位符继承自的: [版式里的, 母版里的]。"""
        ph = _ph(sp) if _local(sp) == "sp" else None
        if ph is None:
            return []
        out = []
        lay = _find_ph(self.layout._element, ph, True)
        if lay is not None:
            out.append(lay)
        mas = _find_ph(self.master._element, ph, False)
        if mas is not None:
            out.append(mas)
        return out

    def lvl_chain(self, sp: Any, lvl: int) -> tuple[list[Any], int]:
        """第 lvl 级 (0 起) 段落的样式链 (a:lvlNpPr), 由近到远; 第二项 = 其中几个是形状自己 lstStyle 的。"""
        tag = a(f"lvl{lvl + 1}pPr")
        out = []
        own = sp.find(f"{p('txBody')}/{a('lstStyle')}/{tag}")
        if own is not None:
            out.append(own)
        for s in self.bases(sp):
            ls = s.find(f"{p('txBody')}/{a('lstStyle')}")
            if ls is not None and ls.find(tag) is not None:
                out.append(ls.find(tag))
        ph = _ph(sp) if _local(sp) == "sp" else None
        style = self.styles.get("other" if ph is None or _kind(ph[0]) in _OTHER else _kind(ph[0]))
        if ph is None and self.default is not None and self.default.find(tag) is not None:
            out.append(self.default.find(tag))
        if style is not None and style.find(tag) is not None:
            out.append(style.find(tag))
        return out, int(own is not None)


# ── 文字 ───────────────────────────────────────────────────────────────────

def _eff(attr: str, rpr: Any, chain: list[Any]) -> str | None:
    if rpr is not None and rpr.get(attr) is not None:
        return rpr.get(attr)
    for lv in chain:
        d = lv.find(a("defRPr"))
        if d is not None and d.get(attr) is not None:
            return d.get(attr)
    return None


def _eff_color(rpr: Any, chain: list[Any], theme: _Theme, font_ref: Any = None, own: int = 0) -> str | None:
    """字的颜色: run → 形状自己的 lstStyle → 形状样式 (p:style 的 fontRef) → 版式 / 母版 / 默认。"""
    defs = [lv.find(a("defRPr")) for lv in chain]
    els = ([rpr] if rpr is not None else []) + defs[:own] + ([font_ref] if font_ref is not None else []) + defs[own:]
    for el in els:
        for c in (el if el is not None else []):
            if _local(c) in ("solidFill", "noFill", "gradFill", "pattFill"):
                return theme.color(c)
            if el is font_ref and _local(c).endswith("Clr"):     # fontRef 里直接是颜色
                return theme.color_el(c)
    return None


def _run_fmt(rpr: Any, chain: list[Any], ctx: _Ctx, font_ref: Any = None, own: int = 0) -> str:
    out = []
    for k in ("b", "i"):
        if _eff(k, rpr, chain) in ("1", "true", "on"):
            out.append(k)
    u = _eff("u", rpr, chain)
    if u not in (None, "none"):
        out.append(f"u={u}")
    st = _eff("strike", rpr, chain)
    if st not in (None, "noStrike"):
        out.append(f"strike={st}")
    base = _eff("baseline", rpr, chain)
    if base not in (None, "0"):
        out.append(f"base={base}")
    out.append(f"sz={_eff('sz', rpr, chain) or '1800'}")
    clr = _eff_color(rpr, chain, ctx.theme, font_ref, own)
    if clr is not None:
        out.append(f"clr={clr}")
    return ",".join(out)


def _bullet(ppr: Any, chain: list[Any]) -> bool:
    for el in ([ppr] if ppr is not None else []) + chain:
        for c in el:
            if _local(c) in _BULLETS:
                return _local(c) != "buNone"
    return False


def _paras(sp: Any, ctx: _Ctx | None, body: Any = None) -> list[str]:
    """一个 txBody 的段落签名; ctx=None (表格格子) 只取文字。末尾的空段落不算。"""
    body = body if body is not None else sp.find(p("txBody"))
    out: list[str] = []
    for para in (body.findall(a("p")) if body is not None else []):
        ppr = para.find(a("pPr"))
        lvl = int(ppr.get("lvl") or 0) if ppr is not None else 0
        chain, own = ctx.lvl_chain(sp, lvl) if ctx is not None else ([], 0)
        font_ref = sp.find(f"{p('style')}/{a('fontRef')}") if ctx is not None else None
        runs: list[list[str]] = []
        for r in para:
            n = _local(r)
            if n not in ("r", "fld", "br"):
                continue
            t = "\n" if n == "br" else "".join(x.text or "" for x in r.findall(a("t")))
            fmt = _run_fmt(r.find(a("rPr")), chain, ctx, font_ref, own) if ctx is not None and n != "br" else ""
            refs = _refs(r, ctx.fp, ctx.part) if ctx is not None else ""
            if not t:
                continue
            if runs and runs[-1][1] == fmt and runs[-1][2] == refs:
                runs[-1][0] += t
            else:
                runs.append([t, fmt, refs])
        if not runs:
            out.append("")
            continue
        bu = _bullet(ppr, chain) if ctx is not None else False
        out.append(f"L{lvl}{'•' if bu else ''}:" + "|".join(f"{t!r}<{f}>{x}" for t, f, x in runs))
    while out and not out[-1]:
        out.pop()
    return out


def plain_text(body: Any) -> str:
    return "\n".join("".join(x.text or "" for x in para.iter(a("t"))) for para in body.findall(a("p")))


# ── 形状 ───────────────────────────────────────────────────────────────────

def _refs(el: Any, fp: Fingerprint, part: Any, skip: tuple[str, ...] = ()) -> str:
    """元素里 r:id / r:embed / r:link 指向的东西的指纹 (图片按内容哈希, 外链按地址); skip 的子树不看。"""
    ref = fp.ref_fn(part)
    out: list[str] = []

    def walk(e: Any) -> None:
        if not isinstance(e.tag, str) or (e is not el and _local(e) in skip):
            return
        out.extend(ref(v)[:16] for k, v in e.attrib.items() if k.startswith(f"{{{R_NS}}}"))
        for c in e:
            walk(c)
    walk(el)
    return ",".join(out)


def _xfrm(el: Any) -> Any:
    for path in (f"{p('spPr')}/{a('xfrm')}", f"{p('grpSpPr')}/{a('xfrm')}", p("xfrm")):
        x = el.find(path)
        if x is not None:
            return x
    return None


def _box(x: Any) -> tuple[int, int, int, int] | None:
    off, ext = x.find(a("off")), x.find(a("ext"))
    if off is None or ext is None:
        return None
    return int(off.get("x") or 0), int(off.get("y") or 0), int(ext.get("cx") or 0), int(ext.get("cy") or 0)


def _pos(el: Any, ctx: _Ctx, tf: tuple[float, float, float, float]) -> str:
    x = _xfrm(el)
    box = _box(x) if x is not None else None
    if box is None:
        for b in ctx.bases(el):
            bx = _xfrm(b)
            box = _box(bx) if bx is not None else None
            if box is not None:
                break
    if box is None:
        return "@?"
    ox, oy, sx, sy = tf
    v = (ox + box[0] * sx, oy + box[1] * sy, box[2] * sx, box[3] * sy)
    return "@" + ",".join(str(round(n / GRID)) for n in v)


def _child_tf(grp: Any, tf: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """组合里子形状的坐标 → 页面坐标。"""
    x = _xfrm(grp)
    if x is None:
        return tf
    box = _box(x)
    choff, chext = x.find(a("chOff")), x.find(a("chExt"))
    if box is None or choff is None or chext is None:
        return tf
    cx, cy = int(chext.get("cx") or 0) or box[2] or 1, int(chext.get("cy") or 0) or box[3] or 1
    kx, ky = (box[2] / cx if cx else 1.0), (box[3] / cy if cy else 1.0)
    ox, oy, sx, sy = tf
    gx = box[0] - int(choff.get("x") or 0) * kx
    gy = box[1] - int(choff.get("y") or 0) * ky
    return ox + gx * sx, oy + gy * sy, sx * kx, sy * ky


def _geom(el: Any) -> str:
    g = el.find(f"{p('spPr')}/{a('prstGeom')}")
    if g is not None:
        return "" if g.get("prst") in (None, "rect") else g.get("prst") or ""
    return "cust" if el.find(f"{p('spPr')}/{a('custGeom')}") is not None else ""


def _table(tbl: Any) -> str:
    cols = len(tbl.findall(f"{a('tblGrid')}/{a('gridCol')}"))
    rows = []
    for tr in tbl.findall(a("tr")):
        cells = []
        for tc in tr.findall(a("tc")):
            span = "".join(f"{k}={tc.get(k)}" for k in ("gridSpan", "rowSpan", "hMerge", "vMerge")
                           if tc.get(k) not in (None, "0", "1", "false"))
            body = tc.find(a("txBody"))
            cells.append(span + repr(plain_text(body).strip() if body is not None else ""))
        rows.append("[" + ",".join(cells) + "]")
    return f"T{cols}[" + "".join(rows) + "]"


def _chart(part: Any) -> str:
    """图表: 类型 + 每个系列的名字 / 分类 / 数值 (取缓存值, 跟谁存的无关)。"""
    try:
        el = etree.fromstring(part.blob)
    except (etree.XMLSyntaxError, ValueError):
        return "chart?"
    out = []
    plot = el.find(f".//{{{C_NS}}}plotArea")
    for kind in (plot if plot is not None else []):
        n = _local(kind)
        if not n.endswith("Chart"):
            continue
        bd = kind.find(f"{{{C_NS}}}barDir")
        out.append(n + (f"/{bd.get('val')}" if bd is not None else ""))
        for ser in kind.findall(f"{{{C_NS}}}ser"):
            vals = []
            for tag in ("tx", "cat", "val", "xVal", "yVal"):
                sub = ser.find(f"{{{C_NS}}}{tag}")
                if sub is not None:
                    vals.append(tag + "=" + "|".join(v.text or "" for v in sub.iter(f"{{{C_NS}}}v")))
            out.append("S(" + ";".join(vals) + ")")
    return "C[" + ",".join(out) + "]"


def _graphic(el: Any, ctx: _Ctx) -> str:
    gd = el.find(f"{a('graphic')}/{a('graphicData')}")
    if gd is None:
        return "frame"
    tbl = gd.find(a("tbl"))
    if tbl is not None:
        return _table(tbl)
    ch = gd.find(f"{{{C_NS}}}chart")
    if ch is not None:
        rid = ch.get(f"{{{R_NS}}}id") or ""
        rel = ctx.part.rels.get(rid)
        return _chart(rel.target_part) if rel is not None and not rel.is_external else "chart?"
    return "obj:" + (gd.get("uri") or "").rsplit("/", 1)[-1] + ":" + _refs(gd, ctx.fp, ctx.part)


def _shape(el: Any, ctx: _Ctx, tf: tuple[float, float, float, float]) -> list[str]:
    n = _local(el)
    if n == "AlternateContent":            # 新功能的形状: 取 Choice 里的 (没有就 Fallback)
        alt = next((c for c in el if _local(c) in ("Choice", "Fallback")), None)
        return [s for c in (alt if alt is not None else []) for s in _shape(c, ctx, tf)]
    if n not in _SHAPES:
        return []
    if n == "grpSp":
        ctf = _child_tf(el, tf)
        inner = [s for c in el for s in _shape(c, ctx, ctf)]
        return ["G{" + ";".join(inner) + "}"]      # 组合自己的框不算 (子形状已换算成页面坐标)
    if n == "sp":
        paras = _paras(el, ctx)
        if _ph(el) is not None and not paras:
            return []                       # 空占位符放映时不显示
        refs = _refs(el, ctx.fp, ctx.part, skip=("txBody",))
        return [f"sp{_geom(el)}{_pos(el, ctx, tf)}#{refs}[" + "/".join(paras) + "]"]
    if n == "pic":
        return [f"pic{_pos(el, ctx, tf)}#{_refs(el, ctx.fp, ctx.part)}"]
    if n == "graphicFrame":
        return [f"gf{_pos(el, ctx, tf)}{_graphic(el, ctx)}"]
    return [f"{n}{_pos(el, ctx, tf)}#{_refs(el, ctx.fp, ctx.part)}"]


def slide_lite(slide_part: Any, fp: Fingerprint) -> str:
    """一页的语义签名原文 (相同 ⇔ 内容和算数的格式都相同)。"""
    ctx = _Ctx(slide_part, fp)
    tree = slide_part._element.find(f"{p('cSld')}/{p('spTree')}")
    shapes = [s for c in (tree if tree is not None else []) for s in _shape(c, ctx, (0.0, 0.0, 1.0, 1.0))]
    return "\n".join(shapes)
