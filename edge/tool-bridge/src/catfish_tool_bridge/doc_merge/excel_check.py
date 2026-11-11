"""xlsx 合并里合不了、只能提示的东西 (都写进 warnings, 不静默丢)。

1. openpyxl 3.1.5 读写一遍就会丢的 (实测): 形状 / 文本框、迷你图、新版扩展的条件格式 / 数据验证、表单控件、
   切片器、日程表、嵌入对象、新版批注的回复、图表里的新版扩展设置。底稿里有 → 合并版里没有;
   交回稿里新加的 → 合不进来。这些从 xlsx 的 zip 里直接认。
2. 只改了格式没改值的格子 (按实际生效的格式比, 见 xlsx_fmt.py; 另外批注、行高 / 列宽、合并单元格):
   不算改动, 提示「张三对 B5 的格式调整没合进来」。LibreOffice / WPS 另存时显式写出的默认值不算改。
3. 图表、图片、数据透视表整个取底稿 (区域随插删行平移); 交回稿里改了 / 加了的提示手动处理。
"""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from typing import Any

from openpyxl.utils import get_column_letter

from .formula_refs import rewrite
from . import xlsx_fmt
from .grid_merge import matched

# (说明, 在哪找, 正则)
_LOST = [
    ("形状 / 文本框", "drawing", re.compile(r"<(?:\w+:)?(?:sp|grpSp|cxnSp)[ >]")),
    ("迷你图", "sheet", re.compile(r"<(?:\w+:)?sparklineGroup[ >]")),
    ("新版条件格式 (数据条 / 图标集等扩展设置)", "sheet", re.compile(r"<x14:conditionalFormatting[ >]")),
    ("引用其他表的数据验证 (扩展设置)", "sheet", re.compile(r"<x14:dataValidation[ >]")),
    ("表单控件", "sheet", re.compile(r"<(?:\w+:)?control[ >]")),
    ("嵌入对象", "sheet", re.compile(r"<(?:\w+:)?oleObject[ >]")),
    ("切片器", "sheet", re.compile(r"<(?:\w+:)?slicerList[ >]")),
    ("日程表", "sheet", re.compile(r"<(?:\w+:)?timelineRef[ >]")),
    ("新版批注的回复", "rels", re.compile(r"threadedComment")),
    ("图表里的新版扩展设置 (如数据标签取自单元格)", "chart", re.compile(r"<c15:\w+")),
]


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    d, f = posixpath.split(part)
    path = posixpath.join(d, "_rels", f + ".rels")
    if path not in z.namelist():
        return {}
    xml = z.read(path).decode("utf-8", "replace")
    out = {}
    for m in re.finditer(r"<Relationship\b([^>]*)/?>", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        tgt = attrs.get("Target", "")
        if attrs.get("TargetMode") == "External":
            continue
        full = posixpath.normpath(tgt.lstrip("/") if tgt.startswith("/") else posixpath.join(d, tgt))
        out[attrs.get("Id", "")] = (attrs.get("Type", ""), full)
    return out


def lost_features(data: bytes) -> dict[str, dict[str, int]]:
    """每张表 openpyxl 会丢的东西: {表名: {说明: 个数}}。"""
    out: dict[str, dict[str, int]] = {}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        wb_xml = z.read("xl/workbook.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        return out
    wb_rels = _rels(z, "xl/workbook.xml")
    names = set(z.namelist())
    for m in re.finditer(r"<(?:\w+:)?sheet\b([^>]*)/?>", wb_xml):
        attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
        name = attrs.get("name", "").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") \
            .replace("&quot;", '"').replace("&apos;", "'")
        rid = next((v for k, v in attrs.items() if k.endswith(":id")), "")
        part = wb_rels.get(rid, ("", ""))[1]
        if part not in names:
            continue
        found: dict[str, int] = {}
        texts = {"sheet": [z.read(part).decode("utf-8", "replace")], "drawing": [], "chart": [], "rels": []}
        for typ, tgt in _rels(z, part).values():
            texts["rels"].append(typ + " " + tgt)
            if tgt in names and typ.endswith("/drawing"):
                texts["drawing"].append(z.read(tgt).decode("utf-8", "replace"))
                for t2, c in _rels(z, tgt).values():
                    if t2.endswith("/chart") and c in names:
                        texts["chart"].append(z.read(c).decode("utf-8", "replace"))
        for label, where, rx in _LOST:
            n = sum(len(rx.findall(t)) for t in texts[where])
            if n:
                found[label] = n
        if found:
            out[name] = found
    return out



def _comment(cell) -> str:
    return (cell.comment.text or "").strip() if cell is not None and cell.comment else ""


def _list(cells: list[str]) -> str:
    return "、".join(cells[:8]) + (f" 等 {len(cells)} 处" if len(cells) > 8 else "")


def _format_changes(run, name: str, k: int) -> list[str]:
    al = run.aligned[name]
    side = run.side(k, name)
    bws, vws = run.bwb[name], run.vwbs[k][name]
    who = run.ctx.name(run.whos[k])
    fmt, notes = [], []
    bbook, vbook = xlsx_fmt.Book(run.bwb), xlsx_fmt.Book(run.vwbs[k])
    for rb, cb, rv, cv in matched(al.base, side):
        b, v = bws._cells.get((rb, cb)), vws._cells.get((rv, cv))
        if b is None and v is None:
            continue
        if run.sig(-1, name, rb, cb) != run.sig(k, name, rv, cv):
            continue       # 值改了: 格式跟着改的人走, 不在这里提示
        addr = f"{get_column_letter(cb)}{rb}"
        if not xlsx_fmt.same_fmt(b, bbook, v, vbook):
            fmt.append(addr)
        if _comment(b) != _comment(v):
            notes.append(addr)
    out = []
    if fmt:
        out.append(f"{who}对「{name}」{_list(fmt)} 的格式调整没合进来")
    if notes:
        out.append(f"{who}对「{name}」{_list(notes)} 的批注修改没合进来")
    dims = []
    for rid in al.base.rows:
        rv = side.rpos().get(rid)
        rb = al.base.rpos()[rid]
        if rv and not xlsx_fmt.same_row_height(bws, rb, vws, rv):
            dims.append(f"第 {rb} 行行高")
    for cid in al.base.cols:
        cv = side.cpos().get(cid)
        cb = al.base.cpos()[cid]
        if cv and not xlsx_fmt.same_col_width(bws, cb, vws, cv):
            dims.append(f"{get_column_letter(cb)} 列列宽")
    if dims:
        out.append(f"{who}对「{name}」{_list(dims)} 的调整没合进来")
    mp = run._norm_mapper(k, name)
    base_m = {str(r) for r in bws.merged_cells.ranges}
    ver_m = {rewrite("=" + str(r), mp)[1:] for r in vws.merged_cells.ranges}
    if base_m != ver_m:
        out.append(f"{who}对「{name}」合并单元格的调整没合进来 ({_list(sorted(ver_m ^ base_m))})")
    if len(bws.data_validations.dataValidation) != len(vws.data_validations.dataValidation):
        out.append(f"{who}对「{name}」数据验证 (下拉选项等) 的调整没合进来")
    if len(bws.conditional_formatting) != len(vws.conditional_formatting):
        out.append(f"{who}对「{name}」条件格式的调整没合进来")
    return out


def _chart_sig(ws, mp) -> list[Any]:
    out = []
    for ch in getattr(ws, "_charts", []):
        refs = []
        for c in {id(c): c for c in [ch] + list(getattr(ch, "_charts", []) or [])}.values():
            for s in getattr(c, "series", []) or []:
                for attr in ("tx", "cat", "val", "xVal", "yVal", "bubbleSize"):
                    obj = getattr(s, attr, None)
                    for sub in ("strRef", "numRef", "multiLvlStrRef"):
                        ref = getattr(obj, sub, None) if obj is not None else None
                        if ref is not None and getattr(ref, "f", None):
                            refs.append(rewrite("=" + ref.f, mp))
        out.append((type(ch).__name__, tuple(refs)))
    return sorted(out, key=repr)


def _pivot_sig(ws, mp) -> list[Any]:
    out = []
    for p in getattr(ws, "_pivots", []) or []:
        src = getattr(getattr(p.cache, "cacheSource", None), "worksheetSource", None)
        sref = rewrite("=" + src.ref, lambda s, a, i, m: mp(src.sheet, a, i, m)) if src is not None and src.ref else ""
        out.append((p.name, len(p.rowFields or []), len(p.colFields or []), len(p.dataFields or []),
                    len(p.pageFields or []), sref))
    return sorted(out, key=repr)


def check(run) -> list[str]:
    warns: list[str] = []
    lost_b = lost_features(run.base_bytes)
    kept = {n for n, src in run.order if src < 0}
    for name, found in lost_b.items():
        if name in kept:
            for label, n in found.items():
                warns.append(f"工作表「{name}」里的{label} ({n} 处) 没有带进合并版 (openpyxl 读写会丢), "
                             f"请在原表上手动处理")
    for k, r in enumerate(run.returns):
        who = run.ctx.name(r.who)
        for name, found in lost_features(r.data).items():
            for label, n in found.items():
                if n > lost_b.get(name, {}).get(label, 0):
                    warns.append(f"{who}在工作表「{name}」里加的{label}没有带进合并版, 请手动处理")
    for name in run.plans:
        bws = run.bwb[name]
        base_charts = _chart_sig(bws, lambda s, a, i, m: i)
        base_pivots = _pivot_sig(bws, lambda s, a, i, m: i)
        for k in run.present[name]:
            who = run.ctx.name(run.whos[k])
            vws = run.vwbs[k][name]
            mp = run._norm_mapper(k, name)
            warns += _format_changes(run, name, k)
            if _chart_sig(vws, mp) != base_charts:
                warns.append(f"{who}改了 / 加了工作表「{name}」里的图表, 合并版用的是底稿的图表 "
                             f"(数据区域已随插删行调整), 请手动看")
            if len(vws._images) != len(bws._images):
                warns.append(f"{who}在工作表「{name}」里加 / 删了图片, 合并版用的是底稿的图片, 请手动看")
            if _pivot_sig(vws, mp) != base_pivots:
                warns.append(f"{who}改了工作表「{name}」里的数据透视表, 合并版用的是底稿的透视表设置 "
                             f"(源区域已随插删行调整, 打开时刷新), 请手动看")
    for name in run.plans:
        if getattr(run.bwb[name], "_pivots", None):
            warns.append(f"工作表「{name}」里的数据透视表保留了底稿的设置, 合并版打开时会自动刷新; "
                         f"请确认透视结果")
    return warns


__all__ = ["check", "lost_features"]
