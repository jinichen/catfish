"""Word (.docx) 的合并入口 (接口见 doc_merge/__init__.py)。

合并的地方: 正文、每个页眉页脚 (按部件名对上)、每条脚注 / 尾注 (按编号对上)。细节:
  word_pkg   包 / 关系 / 把交回稿里的东西搬进合并版
  word_norm  先全部接受已有修订, 再算跟存盘细节无关的签名
  word_merge 对齐 + 合并 → 合并计划 (冲突在这里产生)
  word_track 合并计划 → XML (修订版 / 干净版)
"""
from __future__ import annotations

import io
from typing import Any

from lxml import etree

from . import Ctx, Return
from .core import Undecided
from .word_merge import M, Ver, blocks_of, merge_blocks
from .word_norm import accept_all, sig
from .word_pkg import DOC, NOTES, Pkg, w, wa
from .word_track import Writer

_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _load(data: bytes) -> Pkg:
    pkg = Pkg(data)
    names = [DOC] + [n for n in pkg.order if n.startswith("word/") and n.endswith(".xml")
                     and any(k in n for k in ("header", "footer", "footnotes", "endnotes", "comments"))]
    for n in names:
        if pkg.has(n):
            accept_all(pkg.xml(n))
    return pkg


def _body(pkg: Pkg) -> Any:
    return pkg.xml(DOC).find(w("body"))


def _hf_parts(pkg: Pkg) -> list[tuple[str, str, str]]:
    """(作用域, 部件名, 「页眉」/「页脚」)。按「第几节 · 哪种 (默认 / 首页 / 偶数页)」对上, 不按部件名 ——
    换个软件另存会给页眉页脚部件重新编号 (LibreOffice 实测: 原来的 header1 变成 header2)。"""
    body = _body(pkg)
    sects = [p.find(f"{w('pPr')}/{w('sectPr')}") for p in body.iter(w("p"))]
    sects = [x for x in sects if x is not None] + [x for x in body.findall(w("sectPr"))]
    out = []
    for k, sp in enumerate(sects):
        for ref in sp:
            if ref.tag not in (w("headerReference"), w("footerReference")):
                continue
            got = pkg.rel(DOC, ref.get(f"{{{_R}}}id") or "")
            kind = "页眉" if ref.tag == w("headerReference") else "页脚"
            typ = wa(ref, "type") or "default"
            if got and pkg.has(got[1]):
                out.append((f"hf{k}:{kind}:{typ}", got[1], kind + {"default": "", "first": " (首页)", "even": " (偶数页)"}.get(typ, "")))
    return out


def _notes(pkg: Pkg, kind: str) -> dict[str, Any]:
    part = pkg.rel_of_type(DOC, NOTES[kind][1])
    if not part or not pkg.has(part):
        return {}
    return {wa(n, "id"): n for n in pkg.xml(part) if n.tag == w(kind) and wa(n, "type") in (None, "normal")}


def _new_comments(base: Pkg, ed: Pkg) -> set[str]:
    def items(p: Pkg) -> dict[str, tuple]:
        part = p.rel_of_type(DOC, "comments")
        if not part or not p.has(part):
            return {}
        return {wa(c, "id"): ("".join(c.itertext()), wa(c, "author"), wa(c, "date")) for c in p.xml(part) if c.tag == w("comment")}
    b, e = items(base), items(ed)
    old = set(b.values())
    return {cid for cid, v in e.items() if v not in old}


def _containers(pkg: Pkg) -> list[tuple[str, str, str, Any]]:
    """(作用域, 部件, 位置前缀, 容器元素) —— 正文、页眉页脚、脚注尾注。"""
    out = [("body", DOC, "", _body(pkg))]
    for scope, part, label in _hf_parts(pkg):
        out.append((scope, part, f"{label}: ", pkg.xml(part)))
    for kind, label in (("footnote", "脚注"), ("endnote", "尾注")):
        part = pkg.rel_of_type(DOC, NOTES[kind][1])
        for nid, n in _notes(pkg, kind).items():
            out.append((f"{kind}{nid}", part, f"{label} {nid}: ", n))
    return out


def _find(pkg: Pkg, scope: str, part: str) -> Any:
    if scope == "body":
        return _body(pkg)
    if scope.startswith(("footnote", "endnote")):
        kind = "footnote" if scope.startswith("footnote") else "endnote"
        return _notes(pkg, kind).get(scope[len(kind):])
    got = next((p for sc, p, _l in _hf_parts(pkg) if sc == scope), None)
    return pkg.xml(got) if got else None


def _part_of(pkg: Pkg, scope: str, base_part: str) -> str:
    if scope.startswith("hf"):
        return next((p for sc, p, _l in _hf_parts(pkg) if sc == scope), base_part)
    if scope.startswith(("footnote", "endnote")):
        return pkg.rel_of_type(DOC, NOTES["footnote" if scope.startswith("footnote") else "endnote"][1]) or base_part
    return DOC


def _plan(base: bytes, returns: list[Return], ctx: Ctx) -> tuple[M, list[tuple[str, str, Any, list]], list[str]]:
    bp = _load(base)
    eds: dict[str, Ver] = {}
    for r in returns:
        p = _load(r.data)
        eds[r.who] = Ver(r.who, p, r.at, _new_comments(bp, p))
    m = M(Ver("", bp), eds, [r.who for r in returns], dict(ctx.names))
    plans = []
    same = []
    for scope, part, prefix, cont in _containers(bp):
        vers = {}
        for who, v in eds.items():
            other = _find(v.pkg, scope, part)
            if other is not None:
                vers[who] = blocks_of(other)
                m.part_map.setdefault(part, {})[who] = _part_of(v.pkg, scope, part)
        plans.append((scope, part, cont, merge_blocks(m, blocks_of(cont), vers, part, scope, prefix)))
    for who, v in eds.items():
        if v.new_comments:
            continue
        if all(_find(v.pkg, scope, part) is not None and
               [sig(b, v.pkg, _part_of(v.pkg, scope, part)) for b in blocks_of(_find(v.pkg, scope, part))] == [sig(b, bp, part) for b in blocks_of(cont)]
               for scope, part, _p, cont in _containers(bp)):
            same.append(who)
    return m, plans, same


def analyze(base: bytes, returns: list[Return], ctx: Ctx) -> dict[str, Any]:
    m, plans, same = _plan(base, returns, ctx)
    probe = Writer(m, _load(base), {}, ctx.owner, ctx.now, tracked=False)
    for _s, _p, _c, items in plans:
        probe.blocks(items)
    return {"conflicts": m.conflicts, "changes": probe.changes, "same": same, "warnings": m.warnings}


def _write(base: bytes, m: M, plans: list, decisions: dict[str, Any], ctx: Ctx, tracked: bool) -> tuple[bytes, Writer]:
    dst = _load(base)
    wr = Writer(m, dst, decisions, ctx.owner, ctx.now, tracked)
    for scope, part, _cont, items in plans:
        target = _find(dst, scope, part)
        blocks = wr.blocks(items)
        keep_tail = [c for c in target if not (c.tag in (w("p"), w("tbl"), w("sdt")))]
        for c in list(target):
            if c.tag in (w("p"), w("tbl"), w("sdt")):
                target.remove(c)
        sect = [c for c in keep_tail if c.tag == w("sectPr")]
        pos = target.index(sect[0]) if sect and sect[0].getparent() is target else len(target)
        for k, b in enumerate(blocks):
            target.insert(pos + k, b)
        if not blocks and scope != "body":
            target.insert(pos, etree.Element(w("p")))
    if wr.left:
        raise Undecided(f"还有 {wr.left} 处冲突没定")
    out = dst.save()
    import docx as _docx  # noqa: PLC0415  (自检: python-docx 打得开)
    _docx.Document(io.BytesIO(out))
    return out, wr


def render(base: bytes, returns: list[Return], decisions: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    m, plans, _same = _plan(base, returns, ctx)
    clean, wr = _write(base, m, plans, decisions, ctx, tracked=False)
    tracked, _ = _write(base, m, plans, decisions, ctx, tracked=True)
    return {"tracked": tracked, "clean": clean, "changes": wr.changes, "warnings": m.warnings}
