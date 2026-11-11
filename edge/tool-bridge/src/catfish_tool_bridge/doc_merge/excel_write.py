"""xlsx 合并版写回 (docs/DOC-REVISE-MERGE-PLAN.md §4.4 第 3 条)。

以底稿 workbook 为起点: 按合并计划把每张表的格子搬到合并版坐标 (删掉的行 / 列丢掉、插进来的行 / 列空出位置),
再把改了的格子写进去。openpyxl 的 insert_rows / delete_rows 不管合并单元格、公式、行高, 所以这里不用它们,
自己搬, 下面这些都跟着行列映射平移:
  公式 (所有表, 跨表引用按被引用那张表的映射)、行高 / 列宽、合并单元格、数据验证、条件格式、表格 (ListObject)、
  自动筛选、打印区域 / 打印标题、冻结窗格、超链接、定义名称、图表的数据区域和锚点、图片锚点、数据透视表的
  源区域和位置 (另设打开时刷新)。
新插入的行 / 列从交回稿复制值和样式 (font / fill / border / number_format / alignment / protection 都 copy())。

tracked 版 = clean 版 + 每个改过的格子一条批注「王五: 原值 48 → 52」+ 最后一张「修改记录」表。
"""
from __future__ import annotations

import io
from copy import copy
from typing import Any

from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.comments import Comment
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string, range_boundaries
from openpyxl.worksheet.cell_range import MultiCellRange
from openpyxl.worksheet.formula import ArrayFormula
from openpyxl.worksheet.table import TableColumn

from .formula_refs import rewrite
from .grid import int_of, blank, display

LOG_SHEET = "修改记录"


class Maps:
    """合并版里每张表的行 / 列位置; 给每份稿子 (k=-1 底稿) 造「它的坐标 → 合并版坐标」的映射函数。"""

    def __init__(self, run):
        self.run = run
        self.pos: dict[str, tuple[dict[str, int], dict[str, int]]] = {}
        for name, src in run.order:
            plan = run.plans.get(name) if src < 0 else None
            if plan is not None:
                self.pos[name] = ({x: i + 1 for i, (x, _, _) in enumerate(plan.rows)},
                                  {x: i + 1 for i, (x, _, _) in enumerate(plan.cols)})
        self.added = {name for name, src in run.order if src >= 0}
        self.gone = {ws.title for ws in run.bwb.worksheets} - {n for n, _ in run.order}

    def one(self, k: int, name: str, axis: str, idx: int, mode: str) -> int | None:
        if name in self.added or name not in self.pos:
            return None if name in self.gone else idx
        s = self.run.side(k, name)
        if s is None:
            return idx
        ids = s.rows if axis == "r" else s.cols
        pos = self.pos[name][0 if axis == "r" else 1]
        if idx > len(ids):
            return len(pos) + idx - len(ids)
        x = ids[idx - 1]
        if x in pos:
            return pos[x]
        if mode == "lo":
            return next((pos[ids[i]] for i in range(idx, len(ids)) if ids[i] in pos), None)
        if mode == "hi":
            return next((pos[ids[i]] for i in range(idx - 2, -1, -1) if ids[i] in pos), None)
        return None

    def mapper(self, k: int, cur: str):
        return lambda sheet, axis, idx, mode: self.one(k, sheet or cur, axis, idx, mode)

    def ref(self, k: int, cur: str, ref: str) -> str | None:
        """区域 (A1:B5 / A:C / 1:3) → 合并版坐标; 整个没了返回 None。"""
        out = rewrite("=" + ref, self.mapper(k, cur))[1:]
        return None if "#REF!" in out else out


def _kg(run, name: str, k: int) -> int:
    """合并计划里的稿子下标 (只数有这张表的交回稿) → 全局下标。"""
    return k if k < 0 else run.present[name][k]


def copy_style(dst, src) -> None:
    if src is None or not src.has_style:
        return
    dst.font, dst.fill, dst.border = copy(src.font), copy(src.fill), copy(src.border)
    dst.number_format = src.number_format
    dst.alignment, dst.protection = copy(src.alignment), copy(src.protection)


def _value(v: Any, maps: Maps, k: int, cur: str) -> Any:
    if isinstance(v, ArrayFormula):
        ref = maps.ref(k, cur, v.ref) or v.ref
        return ArrayFormula(ref, rewrite(v.text, maps.mapper(k, cur)))
    if isinstance(v, str) and v.startswith("=") and len(v) > 1:
        return rewrite(v, maps.mapper(k, cur))
    return v


def _plain(ws, r: int, c: int) -> Cell:
    cell = ws._cells.get((r, c))
    if cell is None or isinstance(cell, MergedCell):
        cell = Cell(ws, row=r, column=c)
        ws._cells[(r, c)] = cell
    return cell


def _relocate(ws, side, rpos: dict[str, int], cpos: dict[str, int]):
    nr, nc = len(side.rows), len(side.cols)

    def rm(r: int) -> int | None:
        return rpos.get(side.rows[r - 1]) if r <= nr else len(rpos) + r - nr

    def cm(c: int) -> int | None:
        return cpos.get(side.cols[c - 1]) if c <= nc else len(cpos) + c - nc
    new = {}
    for (r, c), cell in ws._cells.items():
        R, C = rm(r), cm(c)
        if R is None or C is None:
            continue
        cell.row, cell.column = R, C
        new[(R, C)] = cell
    ws._cells = new
    old_rows = dict(ws.row_dimensions)
    ws.row_dimensions.clear()
    for r, dim in old_rows.items():
        R = rm(r)
        if R:
            dim.index = R
            ws.row_dimensions[R] = dim
    old_cols = dict(ws.column_dimensions)
    ws.column_dimensions.clear()
    for letter, dim in old_cols.items():
        lo = dim.min or column_index_from_string(letter)
        hi = dim.max or lo
        if hi - lo > 200:       # 整片列 (比如 A:XFD 统一样式): 只挪两头
            a, b = cm(lo) or 1, cm(hi) or hi
            dim.min, dim.max, dim.index = a, b, get_column_letter(a)
            ws.column_dimensions[dim.index] = dim
            continue
        for c in range(lo, hi + 1):
            C = cm(c)
            if C:
                nd = copy(dim)
                nd.index, nd.min, nd.max = get_column_letter(C), C, C
                ws.column_dimensions[nd.index] = nd
    return rm, cm


def _fix_merged(ws, maps: Maps, k: int, extra: list[str]) -> None:
    ranges = [str(r) for r in ws.merged_cells.ranges]
    ws.merged_cells = MultiCellRange()
    for (r, c), cell in list(ws._cells.items()):
        if isinstance(cell, MergedCell):
            ws._cells[(r, c)] = Cell(ws, row=r, column=c)
    for ref in ranges:
        nref = maps.ref(k, ws.title, ref)
        if nref and ":" in nref and nref.split(":")[0] != nref.split(":")[1]:
            ws.merge_cells(nref)
    for nref in extra:
        ws.merge_cells(nref)


def _fix_sheet_objects(ws, maps: Maps, rm, cm) -> None:
    name = ws.title
    mp = maps.mapper(-1, name)
    for dv in list(ws.data_validations.dataValidation):
        refs = [maps.ref(-1, name, str(r)) for r in dv.sqref.ranges]
        refs = [r for r in refs if r]
        if not refs:
            ws.data_validations.dataValidation.remove(dv)
            continue
        dv.sqref = MultiCellRange(" ".join(refs))
        for attr in ("formula1", "formula2"):
            f = getattr(dv, attr, None)
            if f:
                setattr(dv, attr, rewrite("=" + f, mp)[1:])
    old_cf = ws.conditional_formatting
    ws.conditional_formatting = ConditionalFormattingList()
    for cf in old_cf:
        refs = [r for r in (maps.ref(-1, name, str(x)) for x in cf.sqref.ranges) if r]
        if not refs:
            continue
        for rule in cf.rules:
            rule.formula = [rewrite("=" + f, mp)[1:] for f in (rule.formula or [])]
            ws.conditional_formatting.add(" ".join(refs), rule)
    for t in list(ws.tables.values()):
        nref = maps.ref(-1, name, t.ref)
        if not nref:
            del ws.tables[t.name]
            continue
        t.ref = nref
        if t.autoFilter is not None:
            t.autoFilter.ref = nref
        c1, r1, c2, _ = range_boundaries(nref)
        by_name = {tc.name: tc for tc in t.tableColumns}
        cols = []
        for i, c in enumerate(range(c1, c2 + 1), 1):
            h = display(ws.cell(r1, c).value) or f"列{i}"
            tc = by_name.pop(h, None) or TableColumn(id=i, name=h)
            tc.id = i
            cols.append(tc)
        t.tableColumns = cols
    if ws.auto_filter.ref:
        ws.auto_filter.ref = maps.ref(-1, name, ws.auto_filter.ref)
    areas = [r for r in (maps.ref(-1, name, str(x)) for x in ws._print_area.ranges) if r]
    ws.print_area = areas or None
    if ws._print_rows:
        rr = maps.ref(-1, name, f"{ws._print_rows.min_row}:{ws._print_rows.max_row}")
        ws._print_rows = None
        ws.print_title_rows = rr
    if ws._print_cols:
        cc = maps.ref(-1, name, f"{ws._print_cols.min_col}:{ws._print_cols.max_col}")
        ws._print_cols = None
        ws.print_title_cols = cc
    for cell in ws._cells.values():
        link = getattr(cell, "hyperlink", None)
        if link is not None:
            link.ref = cell.coordinate
    if ws.freeze_panes:
        c, r = range_boundaries(ws.freeze_panes)[:2]
        R, C = maps.one(-1, name, "r", r, "lo"), maps.one(-1, name, "c", c, "lo")
        ws.freeze_panes = f"{get_column_letter(C or c)}{R or r}"
    for obj in list(ws._images) + list(ws._charts):
        _move_anchor(getattr(obj, "anchor", None), rm, cm)
    for p in getattr(ws, "_pivots", []) or []:
        loc = maps.ref(-1, name, p.location.ref)
        if loc:
            p.location.ref = loc


def _move_anchor(anchor, rm, cm) -> None:
    for marker in (getattr(anchor, "_from", None), getattr(anchor, "to", None)):
        if marker is None:
            continue
        r, c = marker.row + 1, marker.col + 1
        R, C = rm(r), cm(c)
        if R is None:     # 锚的那行被删了: 落到它前面最近的一行
            R = next((rm(i) for i in range(r - 1, 0, -1) if rm(i)), 1)
        if C is None:
            C = next((cm(i) for i in range(c - 1, 0, -1) if cm(i)), 1)
        marker.row, marker.col = R - 1, C - 1


def _fix_chart_refs(chart, mp) -> None:
    for ch in {id(c): c for c in [chart] + list(getattr(chart, "_charts", []) or [])}.values():
        for s in getattr(ch, "series", []) or getattr(ch, "ser", []) or []:
            for attr in ("tx", "cat", "val", "xVal", "yVal", "bubbleSize"):
                obj = getattr(s, attr, None)
                for sub in ("strRef", "numRef", "multiLvlStrRef"):
                    ref = getattr(obj, sub, None) if obj is not None else None
                    if ref is not None and getattr(ref, "f", None):
                        ref.f = rewrite("=" + ref.f, mp)[1:]


def _write_sheet(run, maps: Maps, ws, name: str) -> None:
    plan = run.plans[name]
    al = run.aligned[name]
    rpos, cpos = maps.pos[name]
    rm, cm = _relocate(ws, al.base, rpos, cpos)
    for cell in ws._cells.values():
        if not isinstance(cell, MergedCell) and cell.value is not None:
            cell.value = _value(cell.value, maps, -1, name)
    extra_merges: list[str] = []
    # 新插入的行 / 列: 从交回稿复制样式、行高 / 列宽
    for R, (rid, k, r) in enumerate(plan.rows, 1):
        if k < 0:
            continue
        kg = _kg(run, name, k)
        vws, vside = run.vwbs[kg][name], al.vers[k]
        if r in vws.row_dimensions and vws.row_dimensions[r].height:
            ws.row_dimensions[R].height = vws.row_dimensions[r].height
        for C, (cid, _, _) in enumerate(plan.cols, 1):
            vc = vside.cpos().get(cid)
            if vc:
                copy_style(_plain(ws, R, C), vws._cells.get((r, vc)))
        for mr in vws.merged_cells.ranges:
            if all(vside.rows[i - 1] in rpos and vside.rows[i - 1] not in al.base.rpos()
                   for i in range(mr.min_row, mr.max_row + 1) if i <= len(vside.rows)) and mr.min_row == r:
                nref = maps.ref(kg, name, mr.coord)
                if nref:
                    extra_merges.append(nref)
    for C, (cid, k, c) in enumerate(plan.cols, 1):
        if k < 0:
            continue
        kg = _kg(run, name, k)
        vws, vside = run.vwbs[kg][name], al.vers[k]
        vd = vws.column_dimensions.get(get_column_letter(c))
        if vd is not None and vd.width:
            ws.column_dimensions[get_column_letter(C)].width = vd.width
        for R, (rid, _, _) in enumerate(plan.rows, 1):
            vr = vside.rpos().get(rid)
            if vr:
                copy_style(_plain(ws, R, C), vws._cells.get((vr, c)))
    # 改了的格子
    for (rid, cid), pick in plan.cells.items():
        R, C = rpos[rid], cpos[cid]
        cell = _plain(ws, R, C)
        if pick[0] == "text":
            v = _value(pick[1], maps, -1, name)
            cell.value = v
            if hasattr(v, "year") and cell.number_format == "General":
                cell.number_format = "yyyy-mm-dd"
            continue
        k, r, c = pick
        kg = _kg(run, name, k)
        src = run.vwbs[kg][name]._cells.get((r, c))
        v = None if src is None or isinstance(src, MergedCell) else src.value
        link = getattr(src, "hyperlink", None)
        cell.hyperlink = copy(link) if link else None      # 超链接跟着改的人走
        cell.value = _value(v, maps, kg, name)
        copy_style(cell, src)
    renumber(plan, al, rpos, cpos, lambda R, C: ws._cells.get((R, C)))
    _fix_merged(ws, maps, -1, extra_merges)
    _fix_sheet_objects(ws, maps, rm, cm)


def renumber(plan, al, rpos: dict[str, int], cpos: dict[str, int], cell_at) -> None:
    """序号列按合并后的行重新编号 (原来空着的格子还空着)。cell_at(行, 列) → 有 .value 的对象。"""
    for cid in plan.serial:
        bc = al.base.cpos()[cid]
        g = al.base.grid
        start = next((int_of(g.get(r, bc)) for r in range(al.base.hdr + 1, g.nrows + 1) if not blank(g.get(r, bc))), 1)
        C, n = cpos.get(cid), 0
        if C is None:
            continue
        for R in range(rpos.get("h", 0) + 1, len(plan.rows) + 1):
            cell = cell_at(R, C)
            if cell is not None and int_of(cell.value) is not None:
                cell.value = (start or 1) + n if not isinstance(cell.value, str) else str((start or 1) + n)
                n += 1


def _copy_sheet(run, maps: Maps, wb, name: str, kg: int):
    """有人新增的整张表: 从他的稿子整张抄过来。"""
    src = run.vwbs[kg][name]
    ws = wb.create_sheet(name)
    for (r, c), cell in src._cells.items():
        if isinstance(cell, MergedCell):
            continue
        dst = ws.cell(r, c)
        dst.value = _value(cell.value, maps, kg, name)
        copy_style(dst, cell)
        if cell.hyperlink:
            dst.hyperlink = copy(cell.hyperlink)
        if cell.comment:
            dst.comment = Comment(cell.comment.text, cell.comment.author)
    for mr in src.merged_cells.ranges:
        ws.merge_cells(mr.coord)
    for r, d in src.row_dimensions.items():
        if d.height:
            ws.row_dimensions[r].height = d.height
    for letter, d in src.column_dimensions.items():
        nd = copy(d)
        nd.parent = ws
        ws.column_dimensions[letter] = nd
    ws.freeze_panes = src.freeze_panes
    for dv in src.data_validations.dataValidation:
        ws.add_data_validation(copy(dv))
    for cf in src.conditional_formatting:
        for rule in cf.rules:
            ws.conditional_formatting.add(str(cf.sqref), copy(rule))
    for img in src._images:
        ws.add_image(img)
    mp = maps.mapper(kg, name)
    for chart in src._charts:
        _fix_chart_refs(chart, mp)
        ws.add_chart(chart)
    if getattr(src, "_pivots", None):
        run.warnings.append(f"{run.ctx.name(run.whos[kg])} 新增的工作表「{name}」里的数据透视表没有带进合并版, "
                            f"请在他的稿子上手动处理")
    ws.sheet_properties.tabColor = src.sheet_properties.tabColor
    return ws


def write_clean(run) -> bytes:
    from .excel import load  # noqa: PLC0415  (避免循环 import)
    wb = load(run.base_bytes)
    maps = Maps(run)
    for name in maps.gone:
        wb.remove(wb[name])
    kept = {}
    for name, src in run.order:
        if src < 0:
            ws = wb[name]
            _write_sheet(run, maps, ws, name)
            kept[name] = ws
    for ws in list(wb.worksheets) + list(wb.chartsheets):
        mp = maps.mapper(-1, ws.title)
        for chart in getattr(ws, "_charts", []):
            _fix_chart_refs(chart, mp)
        for p in getattr(ws, "_pivots", []) or []:
            wsrc = getattr(getattr(p.cache, "cacheSource", None), "worksheetSource", None)
            if wsrc is not None and wsrc.ref and wsrc.sheet:
                wsrc.ref = maps.ref(-1, wsrc.sheet, wsrc.ref) or wsrc.ref
            p.cache.refreshOnLoad = True
    for dn in list(wb.defined_names.values()) + [d for ws in wb.worksheets for d in ws.defined_names.values()]:
        if dn.attr_text and not dn.attr_text.startswith("["):
            dn.attr_text = rewrite("=" + dn.attr_text, maps.mapper(-1, ""))[1:]
    for name, src in run.order:
        if src >= 0:
            kept[name] = _copy_sheet(run, maps, wb, name, src)
    others = [s for s in wb._sheets if s.title not in kept]
    wb._sheets = [kept[n] for n, _ in run.order] + others
    wb.active = 0
    for ws in wb.worksheets:
        ws.sheet_view.tabSelected = ws is wb.worksheets[0]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _note(cell, text: str, author: str) -> None:
    if cell.comment and cell.comment.text:
        text = cell.comment.text.rstrip() + "\n" + text
        author = cell.comment.author or author
    c = Comment(text, author)
    c.width, c.height = 260, 90
    cell.comment = c


def write_tracked(run, clean: bytes) -> bytes:
    """clean 版上给每个改过的格子加批注, 最后加一张「修改记录」。"""
    from .excel import load  # noqa: PLC0415
    wb = load(clean)
    maps = Maps(run)
    ctx = run.ctx
    names = lambda who: "、".join(ctx.name(w) for w in who)   # noqa: E731
    log: list[list[str]] = []
    for name, src in run.order:
        if src >= 0 or name not in run.plans:
            continue
        ws, plan, al = wb[name], run.plans[name], run.aligned[name]
        rpos, cpos = maps.pos[name]
        key = lambda rid: rid[2:] if rid.startswith("k:") else ""   # noqa: E731
        for e in plan.events:
            if e.kind == "cell" and e.rid in rpos and e.cid in cpos:
                cell = _plain(ws, rpos[e.rid], cpos[e.cid])
                _note(cell, f"{names(e.who)}: 原值 {e.old or '(空)'} → {e.new or '(空)'}", names(e.who))
                log.append([name, cell.coordinate, key(e.rid), names(e.who), e.old, e.new])
            elif e.kind == "row+" and e.rid in rpos:
                R = rpos[e.rid]
                C = next((c for c in range(1, ws.max_column + 1) if not blank(ws.cell(R, c).value)), 1)
                _note(_plain(ws, R, C), f"{names(e.who)}: 新增这一行", names(e.who))
                text = " | ".join(display(ws.cell(R, c).value) for c in range(1, ws.max_column + 1)
                                  if not blank(ws.cell(R, c).value))
                log.append([name, f"第 {R} 行", key(e.rid), names(e.who), "", f"新增行: {text}"])
            elif e.kind == "row-":
                r = al.base.rpos()[e.rid]
                g = al.base.grid
                text = " | ".join(display(g.get(r, c)) for c in range(1, g.ncols + 1) if not blank(g.get(r, c)))
                log.append([name, f"底稿第 {r} 行", key(e.rid), names(e.who), text, "(删除这一行)"])
            elif e.kind == "col+" and e.cid in cpos:
                C = cpos[e.cid]
                hr = rpos.get("h", 1)
                _note(_plain(ws, hr, C), f"{names(e.who)}: 新增这一列", names(e.who))
                log.append([name, f"{get_column_letter(C)} 列", "", names(e.who), "",
                            f"新增列「{display(ws.cell(hr, C).value)}」"])
            elif e.kind == "col-":
                c = al.base.cpos()[e.cid]
                log.append([name, f"底稿 {get_column_letter(c)} 列", "", names(e.who),
                            al.base.names[c - 1] or get_column_letter(c), "(删除这一列)"])
            elif e.kind == "order":
                log.append([name, "整张表", "", names(e.who), "", "调整了列的顺序" if e.cid else "调整了行的顺序"])
    for name, kind, who in run.sheet_events:
        log.append([name, "整张表", "", names(who), "", "新增工作表" if kind == "sheet+" else "删除工作表"])
    title = LOG_SHEET
    n = 2
    while title in wb.sheetnames:
        title, n = f"{LOG_SHEET} ({n})", n + 1
    ws = wb.create_sheet(title)
    ws.append(["表名", "单元格", "行主键", "谁", "原值", "新值"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in log:
        ws.append(row)
    for letter, w in zip("ABCDEF", (14, 12, 16, 12, 30, 30)):
        ws.column_dimensions[letter].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = ["write_clean", "write_tracked", "Maps", "copy_style"]
