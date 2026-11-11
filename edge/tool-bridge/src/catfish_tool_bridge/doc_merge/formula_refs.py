"""Excel 公式里的单元格引用: 拆出来、按行列映射改写 (docs/DOC-REVISE-MERGE-PLAN.md §5 xlsx)。

合并时公式要换两次坐标:
  · 比较前: 交回稿的公式映射回底稿坐标 —— 交回人在第 5 行上面插了一行, 他的 =C6*D6 (原来 =C5*D5)、
    SUM(C2:C11) (原来 SUM(C2:C10)) 映射回去跟底稿一模一样, 不算改了公式;
  · 写合并版: 底稿 / 交回稿坐标映射到合并版坐标 —— 插了两行后 SUM(C2:C10) 变成 SUM(C2:C12)。

引用用 openpyxl.formula.Tokenizer 切 (只改 OPERAND/RANGE 记号), 认 A1、$A$1、A1:B5、A:C、1:3、Sheet1!A1、
'表 1'!A1:B2; 外部工作簿 ([1]Sheet1!A1)、表格结构化引用 (表1[列])、定义名称原样不动。

映射函数 mapper(sheet, axis, idx, mode) → 新序号 (int) | 记号 (str, 只在比较时用) | None (这一行 / 列没了):
  sheet = 引用里写的表名 (没写 = None, 即公式所在的表); axis = "r" / "c"; idx 1 起;
  mode = "one" (单格) / "lo" (区域起点) / "hi" (区域终点) —— 区域端点那行被删了, 起点往下找、终点往上找。
"""
from __future__ import annotations

import re
from typing import Callable, Union

from openpyxl.formula import Tokenizer
from openpyxl.formula.tokenizer import Token
from openpyxl.utils import column_index_from_string, get_column_letter

Mapped = Union[int, str, None]
Mapper = Callable[[Union[str, None], str, int, str], Mapped]

_SHEET = r"(?:'((?:[^']|'')+)'|([^'!:\[\](){},;=+\-*/^&<> ]+))!"
_CELL = r"(\$?)([A-Za-z]{1,3})(\$?)([0-9]+)"
_COL = r"(\$?)([A-Za-z]{1,3})"
_ROW = r"(\$?)([0-9]+)"
_RE_CELLS = re.compile(rf"^(?:{_SHEET})?{_CELL}(?::{_CELL})?$")
_RE_COLS = re.compile(rf"^(?:{_SHEET})?{_COL}:{_COL}$")
_RE_ROWS = re.compile(rf"^(?:{_SHEET})?{_ROW}:{_ROW}$")
MAX_COL, MAX_ROW = 16384, 1048576


def _sheet_of(m: re.Match) -> str | None:
    if m.group(1) is not None:
        return m.group(1).replace("''", "'")
    return m.group(2)


def _prefix(m: re.Match, value: str) -> str:
    """原样保留引用前面的表名部分 ('表 1'! / Sheet1!)。"""
    if m.group(1) is None and m.group(2) is None:
        return ""
    return value[:value.index("!") + 1] if m.group(1) is None else value[:value.rindex("'!") + 2]


def _col_txt(v: Mapped) -> str:
    return get_column_letter(v) if isinstance(v, int) else str(v)


def _ok(v: Mapped, limit: int) -> bool:
    return v is not None and (not isinstance(v, int) or 1 <= v <= limit)


def _rewrite_operand(value: str, mapper: Mapper, keep_abs: bool = False) -> str:
    """一个引用记号 → 映射后的写法; 不认识的 (名称 / 结构化引用 / 外部引用) 原样返回。
    keep_abs: 带 $ 的部分不映射 (只给 relative() 用)。"""
    def call(sheet: str | None, axis: str, idx: int, mode: str, dollar: str) -> Mapped:
        return idx if keep_abs and dollar else mapper(sheet, axis, idx, mode)
    if value.startswith("["):
        return value
    m = _RE_CELLS.match(value)
    if m:
        sheet, pre = _sheet_of(m), _prefix(m, value)
        g = m.groups()[2:]
        c1, r1 = column_index_from_string(g[1].upper()), int(g[3])
        if c1 > MAX_COL or r1 > MAX_ROW or r1 < 1:
            return value
        if g[5] is None:   # 单格
            nc, nr = call(sheet, "c", c1, "one", g[0]), call(sheet, "r", r1, "one", g[2])
            if not (_ok(nc, MAX_COL) and _ok(nr, MAX_ROW)):
                return "#REF!"
            return f"{pre}{g[0]}{_col_txt(nc)}{g[2]}{nr}"
        c2, r2 = column_index_from_string(g[5].upper()), int(g[7])
        if c2 > MAX_COL or r2 > MAX_ROW:
            return value
        lo_c, hi_c = min(c1, c2), max(c1, c2)
        lo_r, hi_r = min(r1, r2), max(r1, r2)
        a = (call(sheet, "c", lo_c, "lo", g[0]), call(sheet, "r", lo_r, "lo", g[2]))
        b = (call(sheet, "c", hi_c, "hi", g[4]), call(sheet, "r", hi_r, "hi", g[6]))
        if not all(_ok(v, MAX_COL if i % 2 == 0 else MAX_ROW) for i, v in enumerate(a + b)):
            return "#REF!"
        if all(isinstance(v, int) for v in a + b) and (a[0] > b[0] or a[1] > b[1]):
            return "#REF!"
        return f"{pre}{g[0]}{_col_txt(a[0])}{g[2]}{a[1]}:{g[4]}{_col_txt(b[0])}{g[6]}{b[1]}"
    m = _RE_COLS.match(value)
    if m:
        sheet, pre = _sheet_of(m), _prefix(m, value)
        g = m.groups()[2:]
        c1, c2 = sorted((column_index_from_string(g[1].upper()), column_index_from_string(g[3].upper())))
        if c2 > MAX_COL:
            return value
        a, b = call(sheet, "c", c1, "lo", g[0]), call(sheet, "c", c2, "hi", g[2])
        if not (_ok(a, MAX_COL) and _ok(b, MAX_COL)) or (isinstance(a, int) and isinstance(b, int) and a > b):
            return "#REF!"
        return f"{pre}{g[0]}{_col_txt(a)}:{g[2]}{_col_txt(b)}"
    m = _RE_ROWS.match(value)
    if m:
        sheet, pre = _sheet_of(m), _prefix(m, value)
        g = m.groups()[2:]
        r1, r2 = sorted((int(g[1]), int(g[3])))
        if r1 < 1 or r2 > MAX_ROW:
            return value
        a, b = call(sheet, "r", r1, "lo", g[0]), call(sheet, "r", r2, "hi", g[2])
        if not (_ok(a, MAX_ROW) and _ok(b, MAX_ROW)) or (isinstance(a, int) and isinstance(b, int) and a > b):
            return "#REF!"
        return f"{pre}{g[0]}{a}:{g[2]}{b}"
    return value


def rewrite(formula: str, mapper: Mapper, keep_abs: bool = False) -> str:
    """公式 (带不带开头的 = 都行, 原样保留) 里的每个引用按 mapper 改写。切不开的公式原样返回。"""
    if not isinstance(formula, str) or not formula:
        return formula
    eq = formula.startswith("=")
    try:
        tok = Tokenizer(formula if eq else "=" + formula)
    except Exception:   # noqa: BLE001  (切不开的公式不动)
        return formula
    changed = False
    for t in tok.items:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
            nv = _rewrite_operand(t.value, mapper, keep_abs)
            if nv != t.value:
                t.value = nv
                changed = True
    if not changed:
        return formula
    out = tok.render()
    return out if eq else out[1:]


def relative(formula: str, row: int, col: int) -> str:
    """对齐用的签名: 相对引用写成相对公式所在格的偏移 (R1C1 的意思), 绝对引用 ($) 原样。
    这样别人插了行, 跟着行走的 =C6*D6 和底稿的 =C5*D5 签名一样, 行对齐认得出是同一行。"""
    def mp(_sheet: str | None, axis: str, idx: int, _mode: str) -> Mapped:
        return f"[{idx - (row if axis == 'r' else col)}]"
    return rewrite(formula, mp, keep_abs=True)

