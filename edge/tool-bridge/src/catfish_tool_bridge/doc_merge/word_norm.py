"""Word 合并 · 比较前的整理: 先「全部接受」已有修订, 再把段落 / 表格变成一个跟存盘细节无关的签名。

为什么要签名而不是直接比 XML: Word / WPS 每存一次都会改一堆跟内容无关的东西 —— rsid、段落 paraId、把一段文字拆成几个 run
的方式、拼写检查标记、语言标记、图片的 docPr 编号、关系编号 rId; 换个软件另存 (LibreOffice 实测) 还会把默认格式全写出来
(每段多出 pStyle=Normal、字体、间距, 表格多出边框、边距)。直接比 XML, 一个人只改了第 5 段, 整篇都像被改了,
跟别人的修改全成冲突。签名只留: 段落样式 (按样式名, 默认样式不写) + 对齐 / 列表级别 + (文字, 算数的字符格式) 序列
(相邻同格式的合并) + 图片 / 脚注等对象的**内容**哈希; 表格只看行列结构 (合并单元格) 和格里的内容。
批注的锚点 (commentRangeStart / End / 引用) 不进签名: 只加了批注不算改了段落, 批注另外搬 (见 word_merge)。
"""
from __future__ import annotations

import hashlib
from typing import Any

from lxml import etree

from .word_pkg import NOTES, R, W, Pkg, w, wa

_UNWRAP = {"ins", "moveTo", "customXmlInsRangeStart"}
_DROP = {"del", "moveFrom", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
         "rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "tblGridChange", "tcPrChange", "trPrChange",
         "tblPrExChange", "numberingChange", "customXmlDelRangeStart", "customXmlDelRangeEnd",
         "customXmlInsRangeEnd"}
# 签名里不要的元素 (跟内容无关, 或批注锚点另算)
_SKIP = {"proofErr", "bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "permStart", "permEnd",
         "lastRenderedPageBreak", "noProof", "lang", "szCs", "bCs", "iCs", "webHidden", "rsid"}
COMMENT_TAGS = {"commentRangeStart", "commentRangeEnd", "commentReference"}


def local(el: Any) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def is_w(el: Any, *names: str) -> bool:
    return isinstance(el.tag, str) and el.tag.startswith(f"{{{W}}}") and local(el) in names


def accept_all(root: Any) -> None:
    """把文档里已有的修订全部接受 (跟在 Word 里点「接受所有修订」一样), 再拿来比。"""
    for el in list(root.iter()):
        if not isinstance(el.tag, str) or not el.tag.startswith(f"{{{W}}}"):
            continue
        name = local(el)
        parent = el.getparent()
        if parent is None:
            continue
        if name in _DROP and not (name in ("ins", "del") and local(parent) in ("rPr", "trPr")):
            parent.remove(el)
        elif name in _UNWRAP and local(parent) not in ("rPr", "trPr"):
            idx = parent.index(el)
            for k, child in enumerate(list(el)):
                parent.insert(idx + k, child)
            parent.remove(el)
    for tr in list(root.iter(w("tr"))):   # 删掉的表格行
        pr = tr.find(w("trPr"))
        if pr is not None and pr.find(w("del")) is not None:
            tr.getparent().remove(tr)
        elif pr is not None and pr.find(w("ins")) is not None:
            pr.remove(pr.find(w("ins")))
    for p in list(root.iter(w("p"))):     # 段落标记的修订: 删掉的段落标记 = 跟下一段合成一段
        rpr = p.find(f"{w('pPr')}/{w('rPr')}")
        if rpr is None:
            continue
        if rpr.find(w("ins")) is not None:
            rpr.remove(rpr.find(w("ins")))
        mark = rpr.find(w("del"))
        if mark is not None:
            rpr.remove(mark)
            nxt = p.getnext()
            if nxt is not None and is_w(nxt, "p"):
                pos = 1 if nxt.find(w("pPr")) is not None else 0
                for k, child in enumerate([c for c in p if not is_w(c, "pPr")]):
                    nxt.insert(pos + k, child)
                p.getparent().remove(p)


def _target_hash(pkg: Pkg, part: str, rid: str) -> str:
    got = pkg.rel(part, rid)
    if not got:
        return "?"
    _t, tgt, ext = got
    if ext:
        return "ext:" + tgt
    return hashlib.sha1(pkg.bytes_of(tgt)).hexdigest()[:12] if pkg.has(tgt) else "?"


def _note_hash(pkg: Pkg, kind: str, nid: str) -> str:
    part = pkg.rel_of_type("word/document.xml", NOTES[kind][1])
    if not part or not pkg.has(part):
        return "?"
    for n in pkg.xml(part):
        if wa(n, "id") == nid:
            return hashlib.sha1(text(n).encode("utf-8")).hexdigest()[:10]
    return "?"


# 字符格式里算数的几样 (加粗 / 斜体 / 下划线 / 删除线 / 颜色 / 突出显示 / 字号 / 上下标 / 字符样式 / 大小写)。
# 字体、间距、缩进这类不算: 不同软件另存时会把默认值全写出来 (LibreOffice 实测每段都多出 rFonts / spacing / jc),
# 算进来的话没改过的段落也像被改了。只改了这类格式的段落不合并; 段落有别的修改时整段用改的人的版本, 格式一起带过来。
_RUN_FMT = ("b", "i", "u", "strike", "dstrike", "color", "highlight", "sz", "vertAlign", "rStyle", "caps", "smallCaps")
_OFF = {"0", "false", "off", "none"}


def _style_names(pkg: Pkg) -> dict[str, str]:
    cache = getattr(pkg, "_style_names", None)
    if cache is not None:
        return cache
    out: dict[str, str] = {}
    if pkg.has("word/styles.xml"):
        for st in pkg.xml("word/styles.xml").findall(w("style")):
            sid = wa(st, "styleId") or ""
            nm = st.find(w("name"))
            name = (wa(nm, "val") if nm is not None else sid) or sid
            out[sid] = "" if wa(st, "default") in ("1", "true") else name.lower()
    pkg._style_names = out
    return out


def _style(pkg: Pkg, sid: str | None) -> str:
    if not sid:
        return ""
    return _style_names(pkg).get(sid, sid.lower())


def _target_tokens(el: Any, pkg: Pkg, part: str) -> str:
    """对象 (图片 / 图表 / 嵌入) 只认它引用的内容: 各软件存盘时位置、编号、效果参数写法都不一样。"""
    hs = [_target_hash(pkg, part, v) for n in el.iter() if isinstance(n.tag, str)
          for k, v in n.attrib.items() if etree.QName(k).namespace == R]
    return "obj:" + ",".join(hs)


def _props(rpr: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in (rpr if rpr is not None else []):
        n = local(c)
        if n in _RUN_FMT and n != "rStyle":
            v = (wa(c, "val") or "").lower()
            out[n] = ("0" if v in _OFF else "1") if n in ("b", "i", "strike", "dstrike", "caps", "smallCaps") else v
    return out


def _style_props(pkg: Pkg, sid: str | None, kind: str) -> dict[str, str]:
    """样式 (连同 basedOn 链) 给字符定的格式。sid 为空 = 这一类的默认样式。"""
    cache = pkg.__dict__.setdefault("_style_props", {})
    key = (sid, kind)
    if key in cache:
        return cache[key]
    out: dict[str, str] = {}
    if pkg.has("word/styles.xml"):
        styles = {wa(st, "styleId"): st for st in pkg.xml("word/styles.xml").findall(w("style"))}
        if not sid:
            sid = next((k for k, st in styles.items() if wa(st, "type") == kind and wa(st, "default") in ("1", "true")), None)
        chain, seen = [], set()
        while sid and sid in styles and sid not in seen and len(chain) < 12:
            seen.add(sid)
            chain.append(styles[sid])
            bo = styles[sid].find(w("basedOn"))
            sid = wa(bo, "val") if bo is not None else None
        for st in reversed(chain):
            out.update(_props(st.find(w("rPr"))))
    cache[key] = out
    return out


def _defaults(pkg: Pkg) -> dict[str, str]:
    if "_defaults" not in pkg.__dict__:
        d = pkg.xml("word/styles.xml").find(f"{w('docDefaults')}/{w('rPrDefault')}/{w('rPr')}") if pkg.has("word/styles.xml") else None
        pkg.__dict__["_defaults"] = _props(d)
    return pkg.__dict__["_defaults"]


def _run_fmt(r: Any, pkg: Pkg, pstyle: str | None = None) -> str:
    """字符的**实际**格式 (文档默认 → 段落样式 → 字符样式 → 直接格式): 另存时把默认字号 / 颜色显式写出来的, 跟没写一样。"""
    rpr = r.find(w("rPr"))
    rs = rpr.find(w("rStyle")) if rpr is not None else None
    eff = {**_defaults(pkg), **_style_props(pkg, pstyle, "paragraph"),
           **(_style_props(pkg, wa(rs, "val"), "character") if rs is not None else {}), **_props(rpr)}
    out = []
    for n, v in sorted(eff.items()):
        if v in ("0", "") and n in ("b", "i", "strike", "dstrike", "caps", "smallCaps"):
            continue
        if (n == "u" and v in _OFF) or (n == "color" and v in ("auto", "000000")) or (n == "highlight" and v in _OFF) \
                or (n == "vertAlign" and v == "baseline"):
            continue
        out.append(f"{n}={v}")
    return ",".join(out)


def _run_body(r: Any, pkg: Pkg, part: str) -> str:
    body = []
    for c in r:
        n = local(c)
        if n in ("rPr", "lastRenderedPageBreak") or n in _SKIP or n in COMMENT_TAGS:
            continue
        if n == "t":
            body.append(c.text or "")
        elif n == "tab":
            body.append("\t")
        elif n in ("br", "cr"):
            body.append("\n" if wa(c, "type") in (None, "textWrapping") else f"<br:{wa(c, 'type')}>")
        elif n in ("footnoteReference", "endnoteReference"):
            body.append(f"<{n}:{_note_hash(pkg, n[:-9], wa(c, 'id') or '')}>")
        elif n == "instrText":
            body.append(f"<fld:{(c.text or '').strip()}>")
        elif n == "fldChar":
            body.append(f"<{wa(c, 'fldCharType')}>")
        elif n in ("drawing", "pict", "object"):
            body.append(f"<{_target_tokens(c, pkg, part)}>")
        elif n == "sym":
            body.append(f"<sym:{wa(c, 'char')}>")
        elif n in ("noBreakHyphen", "softHyphen"):
            body.append("-")
        else:
            body.append(f"<{n}>")
    return "".join(body)


def _p_lite(p: Any, pkg: Pkg, part: str) -> str:
    head = []
    ppr = p.find(w("pPr"))
    pstyle = None
    if ppr is not None:
        st = ppr.find(w("pStyle"))
        pstyle = wa(st, "val") if st is not None else None
        head.append("s=" + _style(pkg, wa(st, "val") if st is not None else None))
        jc = ppr.find(w("jc"))
        if jc is not None and (wa(jc, "val") or "") not in ("left", "start", "both"):
            head.append("jc=" + (wa(jc, "val") or ""))
        num = ppr.find(w("numPr"))
        if num is not None and num.find(w("numId")) is not None and wa(num.find(w("numId")), "val") != "0":
            lvl = num.find(w("ilvl"))
            head.append("list=" + (wa(lvl, "val") if lvl is not None else "0"))
        ol = ppr.find(w("outlineLvl"))
        if ol is not None:
            head.append("ol=" + (wa(ol, "val") or ""))
    parts, last_fmt, buf = [";".join(h for h in head if h != "s=")], None, []
    for c in p.iter():
        if c is p or not isinstance(c.tag, str):
            continue
        n = local(c)
        if n == "r":
            body = _run_body(c, pkg, part)
            if not body:
                continue
            fmt = _run_fmt(c, pkg, pstyle)
            if fmt == last_fmt:
                buf.append(body)
            else:
                if buf:
                    parts.append(f"{last_fmt}|{''.join(buf)}")
                last_fmt, buf = fmt, [body]
        elif n in ("hyperlink", "fldSimple", "oMath"):
            if buf:
                parts.append(f"{last_fmt}|{''.join(buf)}")
                last_fmt, buf = None, []
            tgt = c.get(f"{{{R}}}id")
            parts.append(f"<{n}:{_target_hash(pkg, part, tgt) if tgt else (wa(c, 'anchor') or wa(c, 'instr') or '')}>")
    if buf:
        parts.append(f"{last_fmt}|{''.join(buf)}")
    return "\x1e".join(parts)


def _lite(el: Any, pkg: Pkg, part: str) -> str:
    n = local(el)
    if n == "p":
        return "P[" + _p_lite(el, pkg, part) + "]"
    if n == "tbl":
        return "T[" + "".join(_lite(tr, pkg, part) for tr in el.findall(w("tr"))) + "]"
    if n == "tr":
        return "R[" + "".join(_lite(tc, pkg, part) for tc in el.findall(w("tc"))) + "]"
    if n == "tc":
        pr = el.find(w("tcPr"))
        span = vm = ""
        if pr is not None:
            gs, v = pr.find(w("gridSpan")), pr.find(w("vMerge"))
            span = wa(gs, "val") or "" if gs is not None else ""
            vm = (wa(v, "val") or "continue") if v is not None else ""
        return f"C{span}{vm}[" + "".join(_lite(b, pkg, part) for b in el if local(b) in ("p", "tbl", "sdt")) + "]"
    if n == "sdt":
        content = el.find(w("sdtContent"))
        inner = "".join(_lite(b, pkg, part) for b in (content if content is not None else []) if local(b) in ("p", "tbl", "sdt"))
        return "S[" + inner + "]"
    return f"<{n}>"


def sig(el: Any, pkg: Pkg, part: str = "word/document.xml") -> str:
    """段落 / 表格 / 表格行 / 单元格 的内容签名 (相同 ⇔ 文字和算数的格式都相同)。"""
    return hashlib.sha1(_lite(el, pkg, part).encode("utf-8")).hexdigest()


def text(el: Any) -> str:
    """给人看的文字: 段落按换行分开, 单元格之间 Tab, 图片写「[图片]」。"""
    out: list[str] = []

    def walk(e: Any) -> None:
        for c in e:
            if not isinstance(c.tag, str):
                continue
            n = local(c)
            if n == "t":
                out.append(c.text or "")
            elif n == "tab":
                out.append("\t")
            elif n in ("br", "cr"):
                out.append("\n")
            elif n in ("drawing", "pict", "object"):
                out.append("[图片]")
            elif n in ("footnoteReference", "endnoteReference"):
                out.append("[注]")
            elif n in ("del", "delText", "instrText", "rPr", "pPr"):
                continue
            else:
                walk(c)
                if n == "p":
                    out.append("\n")
                elif n == "tc":
                    out.append("\t")
    walk(el)
    return "".join(out).replace("\t\n", "\n").strip("\n")


def heading_level(p: Any, pkg: Pkg) -> int | None:
    """是不是标题 (用来写冲突位置「「3.2 报价」下第 2 段」): 样式名里有 Heading / 标题, 或段落带大纲级别。"""
    ppr = p.find(w("pPr"))
    if ppr is None:
        return None
    ol = ppr.find(w("outlineLvl"))
    if ol is not None and (wa(ol, "val") or "").isdigit() and int(wa(ol, "val")) < 9:
        return int(wa(ol, "val")) + 1
    ps = ppr.find(w("pStyle"))
    if ps is None:
        return None
    sid = wa(ps, "val") or ""
    names = {sid.lower()}
    if pkg.has("word/styles.xml"):
        for st in pkg.xml("word/styles.xml").findall(w("style")):
            if wa(st, "styleId") == sid:
                nm = st.find(w("name"))
                if nm is not None:
                    names.add((wa(nm, "val") or "").lower())
                o = st.find(f"{w('pPr')}/{w('outlineLvl')}")
                if o is not None and (wa(o, "val") or "").isdigit() and int(wa(o, "val")) < 9:
                    return int(wa(o, "val")) + 1
    for nm in names:
        for key in ("heading", "标题"):
            if nm.startswith(key):
                tail = nm[len(key):].strip()
                return int(tail) if tail.isdigit() else 1
    return None
