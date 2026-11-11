"""csv 的三方合并 (docs/DOC-REVISE-MERGE-PLAN.md §5 csv 行)。接口见 doc_merge/__init__.py。

跟 xlsx 同一套规则 (grid.py / grid_merge.py): 单元是单元格, 认表头、找主键、按主键或整行内容对齐行、列按表头名对齐;
没有公式和样式, 值都按文字比。

编码自动认: utf-8 (带不带 BOM) / gb18030。写回时用底稿的编码、分隔符、换行风格 (和是不是每格都加引号);
没人改动的行原样写回 —— 一个人都没改时输出跟底稿逐字节一样。
csv 加不了批注, tracked 跟 clean 一样。
"""
from __future__ import annotations

import codecs
import csv
import io
from dataclasses import dataclass
from typing import Any

from . import Ctx, Return, core
from .grid import Grid, align, value_sig
from .grid_merge import Plan, merge, stats

LABEL = "表格"


@dataclass
class Dialect:
    encoding: str = "utf-8"
    delimiter: str = ","
    newline: str = "\r\n"
    quote_all: bool = False
    trailing: bool = True       # 最后一行后面有没有换行


def decode(data: bytes) -> tuple[str, str]:
    """→ (文本, 编码)。utf-8-sig / utf-8 / gb18030 依次试。"""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8", "replace"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("gb18030", "replace"), "gb18030"


def sniff(text: str, encoding: str) -> Dialect:
    nl = "\r\n" if "\r\n" in text else ("\r" if "\r" in text and "\n" not in text else "\n")
    sample = text[:20000]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    lines = [ln for ln in text.splitlines() if ln.strip()]
    quote_all = bool(lines) and all(ln.startswith('"') and ln.endswith('"') for ln in lines)
    return Dialect(encoding, delim, nl, quote_all, text.endswith(("\n", "\r")))


def parse(data: bytes) -> tuple[list[list[str]], Dialect]:
    text, enc = decode(data)
    d = sniff(text, enc)
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=d.delimiter))
    return rows, d


def to_grid(rows: list[list[str]]) -> Grid:
    cells = {(r, c): v for r, row in enumerate(rows, 1) for c, v in enumerate(row, 1) if v != ""}
    return Grid(LABEL, cells, max((r for r, _ in cells), default=0), max((c for _, c in cells), default=0))


def _asig(g: Grid, r: int, c: int) -> str:
    return value_sig(g.get(r, c))


def _parse_text(text: str) -> str:
    return text.strip()


class Run:
    def __init__(self, base: bytes, returns: list[Return], ctx: Ctx, decisions: dict[str, Any] | None):
        self.rows, self.dialect = parse(base)
        self.vrows = [parse(r.data)[0] for r in returns]
        self.whos = [r.who for r in returns]
        grids = [to_grid(v) for v in self.vrows]
        self.al = align(to_grid(self.rows), list(zip(self.whos, grids)), _asig)
        sides = [self.al.base] + self.al.vers

        def sig(k: int, r: int, c: int) -> str:
            return value_sig(sides[k + 1].grid.get(r, c))
        self.plan: Plan = merge(self.al, sig, label=LABEL, owner=ctx.owner, decisions=decisions, parse=_parse_text)

    def same(self) -> list[str]:
        return [w for w in dict.fromkeys(self.whos) if w not in self.plan.changed]


def _cell(run: Run, rid: str, cid: str) -> str:
    plan, al = run.plan, run.al
    pick = plan.cells.get((rid, cid))
    if pick is not None:
        if pick[0] == "text":
            return "" if pick[1] is None else str(pick[1])
        k, r, c = pick
        return al.vers[k].grid.get(r, c) or ""
    r, c = al.base.rpos().get(rid), al.base.cpos().get(cid)
    return (al.base.grid.get(r, c) or "") if r and c else ""


def write(run: Run) -> bytes:
    plan, al, d = run.plan, run.al, run.dialect
    cols = [cid for cid, _, _ in plan.cols]
    same_cols = cols == al.base.cols
    out_rows: list[list[str]] = []
    for rid, k, r in plan.rows:
        row = [_cell(run, rid, cid) for cid in cols]
        last = max((i + 1 for i, v in enumerate(row) if v != ""), default=0)
        width = max(last, len(run.rows[r - 1])) if k < 0 and same_cols else len(cols)
        out_rows.append(row[:width] + [""] * (width - len(row)))
    out_rows += [list(r) for r in run.rows[al.base.grid.nrows:]]     # 底稿末尾的空行原样带着
    # 序号列重新编号
    rpos = {rid: i for i, (rid, _, _) in enumerate(plan.rows)}
    for cid in plan.serial:
        if cid not in cols:
            continue
        c = cols.index(cid)
        bc = al.base.cpos()[cid]
        g = al.base.grid
        start = next((int(str(g.get(r, bc)).strip()) for r in range(al.base.hdr + 1, g.nrows + 1)
                      if g.get(r, bc)), 1)
        n = 0
        for i in range(rpos.get("h", -1) + 1, len(out_rows)):
            if c < len(out_rows[i]) and out_rows[i][c].strip().isdigit():
                out_rows[i][c] = str(start + n)
                n += 1
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=d.delimiter, lineterminator=d.newline,
                   quoting=csv.QUOTE_ALL if d.quote_all else csv.QUOTE_MINIMAL)
    for row in out_rows:
        w.writerow(row)
    text = buf.getvalue()
    if not d.trailing and text.endswith(d.newline):
        text = text[:-len(d.newline)]
    enc = d.encoding
    if enc == "utf-8-sig":
        return codecs.BOM_UTF8 + text.encode("utf-8")
    return text.encode(enc, "replace")


def analyze(base: bytes, returns: list[Return], ctx: Ctx) -> dict[str, Any]:
    run = Run(base, returns, ctx, None)
    return {"conflicts": run.plan.conflicts, "changes": stats(run.plan), "same": run.same(),
            "warnings": list(run.plan.warnings)}


def render(base: bytes, returns: list[Return], decisions: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    run = Run(base, returns, ctx, decisions or {})
    if run.plan.undecided:
        raise core.Undecided(f"还有 {run.plan.undecided} 处冲突没定")
    data = write(run)
    parse(data)    # 自检
    return {"tracked": data, "clean": data, "changes": stats(run.plan), "warnings": list(run.plan.warnings)}
