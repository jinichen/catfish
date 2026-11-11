"""xlsx 单元格「实际生效的格式」, 判断交回稿是不是真改了格式 (excel_check 用)。

LibreOffice / WPS 另存时会把默认字体、字号、行高之类全部显式写出来; 按写法比, 每个人交回都会刷一屏
「格式调整没合进来」的假提醒。这里按效果比 (跟 word_norm._run_fmt 一个思路):
  · 显式写出的默认值跟不写一样 (字号没写 = 这个工作簿的默认字号; 字体颜色没写 / auto = 主题的 dk1);
  · 主题色 / 索引色 / RGB 都换算成 RGB 再比, 各通道差 ≤ 2 算同一个颜色 (各家软件换算 tint 有舍入);
  · 加粗 / 斜体 / 删除线的 false 跟不写一样, 下划线 none 跟不写一样;
  · 数字格式 General 不分大小写; 对齐 general / bottom / 不换行跟不写一样; 边框只看每条边有没有。
只比: 加粗、斜体、下划线、删除线、字号、字体颜色、填充色、数字格式、对齐、边框有无。字体名不比。
行高 / 列宽: 没设 = 这张表的默认值; 写成默认值 (底稿或交回稿的默认值都算) 跟没设一样。
"""
from __future__ import annotations

import colorsys
import re
from typing import Any

from openpyxl.cell.cell import MergedCell
from openpyxl.styles.colors import COLOR_INDEX
from openpyxl.utils import get_column_letter

# 没有主题时用 Office 默认主题; 顺序是 Excel 的主题色序号: lt1 dk1 lt2 dk2 accent1~6 hlink folHlink
_OFFICE = ["FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47",
           "0563C1", "954F72"]
_SLOTS = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6",
          "hlink", "folHlink"]
_TOL = 2


class Book:
    """一个工作簿的换算环境: 主题色表 + 默认字号。"""

    def __init__(self, wb):
        self.palette = _palette(getattr(wb, "loaded_theme", None))
        fonts = getattr(wb, "_fonts", None)
        sz = getattr(fonts[0], "sz", None) if fonts else None
        self.size = float(sz) if sz else 11.0

    def rgb(self, color: Any, default: str) -> tuple[int, int, int]:
        """openpyxl 的 Color → (r, g, b); 没写 / auto / 认不出 → default。"""
        hexv = default
        if color is not None and not getattr(color, "auto", False):
            typ = getattr(color, "type", None)
            if typ == "rgb" and isinstance(color.rgb, str) and re.fullmatch(r"[0-9A-Fa-f]{6,8}", color.rgb):
                hexv = color.rgb[-6:]
            elif typ == "theme" and isinstance(color.theme, int) and 0 <= color.theme < len(self.palette):
                hexv = self.palette[color.theme]
            elif typ == "indexed" and isinstance(color.indexed, int):
                if color.indexed < len(COLOR_INDEX):
                    hexv = COLOR_INDEX[color.indexed][-6:]
                elif color.indexed == 64:      # 系统前景色
                    hexv = self.palette[1]
        rgb = tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))
        tint = getattr(color, "tint", 0) or 0
        return _tint(rgb, tint) if tint else rgb   # type: ignore[arg-type]


def _palette(theme: Any) -> list[str]:
    if not theme:
        return list(_OFFICE)
    xml = theme.decode("utf-8", "replace") if isinstance(theme, bytes) else str(theme)
    out = list(_OFFICE)
    for i, slot in enumerate(_SLOTS):
        m = re.search(rf"<(?:\w+:)?{slot}>(.*?)</(?:\w+:)?{slot}>", xml, re.S)
        if not m:
            continue
        v = re.search(r'srgbClr\s+val="([0-9A-Fa-f]{6})"', m.group(1)) or \
            re.search(r'lastClr="([0-9A-Fa-f]{6})"', m.group(1))
        if v:
            out[i] = v.group(1).upper()
    return out


def _tint(rgb: tuple[int, int, int], tint: float) -> tuple[int, int, int]:
    h, l, s = colorsys.rgb_to_hls(*(x / 255 for x in rgb))
    l = l * (1 + tint) if tint < 0 else l * (1 - tint) + tint   # noqa: E741
    return tuple(round(x * 255) for x in colorsys.hls_to_rgb(h, l, s))   # type: ignore[return-value]


def fmt(cell: Any, book: Book) -> tuple:
    """格子的实际格式; 没有这个格子 / 没设样式 = 默认格式。"""
    if cell is None or isinstance(cell, MergedCell) or not cell.has_style:
        return default(book)
    f, fill, al, bd = cell.font, cell.fill, cell.alignment, cell.border
    u = getattr(f, "u", None)
    ptype = getattr(fill, "fill_type", None) or getattr(fill, "patternType", None)
    if ptype in (None, "none"):
        fillk: tuple = ("none",)
    elif ptype == "solid":
        fillk = ("solid", book.rgb(fill.fgColor, "000000"))
    else:
        fillk = (ptype, book.rgb(fill.fgColor, "000000"), book.rgb(fill.bgColor, "FFFFFF"))
    nf = (cell.number_format or "General")
    return (bool(f.b), bool(f.i), u not in (None, "none"), bool(f.strike),
            float(f.sz) if f.sz else book.size, book.rgb(f.color, book.palette[1]), fillk,
            "general" if nf.lower() == "general" else nf,
            al.horizontal or "general", al.vertical or "bottom", bool(al.wrap_text),
            tuple(getattr(getattr(bd, s, None), "style", None) not in (None, "none")
                  for s in ("left", "right", "top", "bottom")))


def default(book: Book) -> tuple:
    return (False, False, False, False, book.size, book.rgb(None, book.palette[1]), ("none",), "general",
            "general", "bottom", False, (False, False, False, False))


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        if len(a) == 3 and all(isinstance(x, int) for x in a + b):     # RGB
            return all(abs(x - y) <= _TOL for x, y in zip(a, b))
        return all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 0.05
    return a == b


def same_fmt(a: Any, ab: Book, b: Any, bb: Book) -> bool:
    return _close(fmt(a, ab), fmt(b, bb))


def _row_default(ws) -> float:
    return float(getattr(ws.sheet_format, "defaultRowHeight", None) or 15.0)


def _col_default(ws) -> float:
    sf = ws.sheet_format
    if getattr(sf, "defaultColWidth", None):
        return float(sf.defaultColWidth)
    return float(getattr(sf, "baseColWidth", None) or 8) + 0.71


def row_height(ws, r: int) -> float | None:
    d = ws.row_dimensions.get(r) if hasattr(ws.row_dimensions, "get") else None
    return float(d.height) if d is not None and d.height else None


def col_width(ws, c: int) -> float | None:
    letter = get_column_letter(c)
    for key, d in ws.column_dimensions.items():
        lo, hi = d.min or 0, d.max or 0
        if key == letter or (lo and hi and lo <= c <= hi):
            return float(d.width) if d.width else None
    return None


def _dim_same(a: float | None, b: float | None, defaults: tuple[float, float], tol: float) -> bool:
    """没设 = 默认; 底稿 / 交回稿任一方的默认值都算「没设」。"""
    def norm(v: float | None) -> float | None:
        if v is None or any(abs(v - d) <= tol for d in defaults):
            return None
        return v
    na, nb = norm(a), norm(b)
    if na is None or nb is None:
        return na is None and nb is None
    return abs(na - nb) <= tol


def same_row_height(bws, rb: int, vws, rv: int) -> bool:
    return _dim_same(row_height(bws, rb), row_height(vws, rv), (_row_default(bws), _row_default(vws)), 0.3)


def same_col_width(bws, cb: int, vws, cv: int) -> bool:
    return _dim_same(col_width(bws, cb), col_width(vws, cv), (_col_default(bws), _col_default(vws)), 0.3)
