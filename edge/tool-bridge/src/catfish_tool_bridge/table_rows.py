"""表格文件搜到了, 给命中的那几行 + 表头 (11/28)。

多跳实测 (答案分在两份资料里) 里, 答案在 Excel / CSV 某一行的题 3 道全错: 文件搜得到, 但给模型的只是命中处
十几个字 (catfish_search_files 的 snippet) 或前后 700 字 (local_lookup) —— 一行 Excel 动辄几十列, 看不到
表头, 「115.0 | 数智化业务事业部 | 数智韧性产品部 | …」也不知道哪格是什么。

索引里表格是两种样子 (local-search 的 extractor / markitdown 产出):
  · Excel / Word / PDF 里的表: markdown 表格, 「## 工作表名」+「| 列 | 列 |」一行一行, 可能夹「| --- |」分隔行;
  · CSV / TSV: 第一行表头, 逗号 (制表符) 分隔。
→ 找含搜索词的行 (含的词越多越靠前), 跟它那张表的表头配成「列=值; 列=值」, 空格 / NaN 不列。
"""
from __future__ import annotations

import csv
import io
import re

MAX_ROWS = 3
ROW_CHARS = 900
CELL_CHARS = 360        # 含搜索词的格: 取词前后这么多字
OTHER_CELL_CHARS = 120  # 别的格最多这么多字
_EMPTY = {"", "nan", "none", "null", "-"}
_SEP = re.compile(r"^\|\s*:?-{2,}")


def _pipe_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip().replace("\\n", " ") for c in s.split("|")]


def _pair(header: list[str], cells: list[str], words: list[str] = ()) -> str:
    """「列=值; 列=值」, 空格 / NaN 不列。太长时先保含搜索词的格 (取词前后一段) 和前 3 格 (认得出是哪一行), 再按列顺序补别的 ——
    11/28 实测: 原来从头截 500 字, 一行几十列时答案那格 (一大段说明) 被截掉, 小鲶说「没找到」。"""
    items = []
    for i, v in enumerate(cells):
        v = v.strip()
        if v.lower() in _EMPTY:
            continue
        name = header[i].strip() if i < len(header) else ""
        low = v.lower()
        hit = next((w for w in words if w in low), None)
        if hit and len(v) > CELL_CHARS:
            k = low.find(hit)
            v = ("…" if k > CELL_CHARS // 3 else "") + v[max(0, k - CELL_CHARS // 3):k + CELL_CHARS * 2 // 3] + "…"
        elif not hit and len(v) > OTHER_CELL_CHARS:
            v = v[:OTHER_CELL_CHARS] + "…"
        items.append((i, f"{name}={v}" if name and name.lower() not in _EMPTY else v, bool(hit)))
    order = [x for x in items if x[2]] + items[:3] + items
    keep: set[int] = set()
    used = 0
    for i, text, _ in order:
        if i in keep:
            continue
        if used + len(text) + 2 > ROW_CHARS and keep:
            continue
        keep.add(i)
        used += len(text) + 2
    return "; ".join(text for i, text, _ in items if i in keep)


def _pipe_row(lines: list[str], i: int, words: list[str]) -> str | None:
    """第 i 行是 markdown 表格行 → 「[工作表] 列=值; …」; 不是表格行返回 None。"""
    if not lines[i].lstrip().startswith("|") or _SEP.match(lines[i].strip()):
        return None
    start = i
    while start > 0 and lines[start - 1].lstrip().startswith("|"):
        start -= 1
    if start == i:   # 命中的就是表头本身, 没有可配的
        return None
    sheet = ""
    for j in range(start - 1, max(-1, start - 4), -1):
        if lines[j].startswith("#"):
            sheet = lines[j].lstrip("# ").strip()
            break
    row = _pair(_pipe_cells(lines[start]), _pipe_cells(lines[i]), words)
    return (f"[{sheet}] " if sheet else "") + row if row else None


def _csv_row(lines: list[str], i: int, delim: str, words: list[str]) -> str | None:
    if i == 0:
        return None
    try:
        header = next(csv.reader(io.StringIO(lines[0]), delimiter=delim))
        cells = next(csv.reader(io.StringIO(lines[i]), delimiter=delim))
    except (StopIteration, csv.Error):
        return None
    return _pair(header, cells, words) or None


def table_rows(content: str, terms: list[str], file_type: str = "", max_rows: int = MAX_ROWS) -> list[str]:
    """content 里含 terms 的表格行 (配好表头), 含的词多的在前; 不是表格 / 没命中返回 []。"""
    words = [t.lower() for t in terms if t and t.strip()]
    if not content or not words:
        return []
    lines = content.splitlines()
    ft = (file_type or "").lower()
    delim = "\t" if ft == ".tsv" else ","
    hits = []
    for i, line in enumerate(lines):
        low = line.lower()
        n = sum(1 for w in words if w in low)
        if n:
            hits.append((-n, i))
    out: list[str] = []
    for _, i in sorted(hits):
        row = _csv_row(lines, i, delim, words) if ft in (".csv", ".tsv") else _pipe_row(lines, i, words)
        if row and row not in out:
            out.append(row)
        if len(out) >= max_rows:
            break
    return out
