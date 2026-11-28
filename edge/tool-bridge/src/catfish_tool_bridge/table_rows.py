"""表格文件搜到了, 给命中的那几行 + 表头 (11/28)。

多跳实测 (答案分在两份资料里) 里, 答案在 Excel / CSV 某一行的题 3 道全错: 文件搜得到, 但给模型的只是命中处
十几个字 (catfish_search_files 的 snippet) 或前后 700 字 (local_lookup) —— 一行 Excel 动辄几十列, 看不到
表头, 「115.0 | 数智化业务事业部 | 数智韧性产品部 | …」也不知道哪格是什么。

索引里表格是两种样子 (local-search 的 extractor / markitdown 产出):
  · Excel / Word / PDF 里的表: markdown 表格, 「## 工作表名」+「| 列 | 列 |」一行一行, 可能夹「| --- |」分隔行;
  · CSV / TSV: 第一行表头, 逗号 (制表符) 分隔。
→ 找含搜索词的行 (含的词越多越靠前), 跟它那张表的表头配成「列=值; 列=值」, 空格 / NaN 不列。

11/28 鲁棒性复查 (本机索引 3400 张表): 实际比上面乱得多 ——
  · 老 extractor 的 Excel 行是「a | b | c」, 行首没有竖线; 第一格空的行又是「 | b | c」;
  · Word 表 94% 第一行是空表头 (markitdown 的习惯), Excel 55% 第一行是标题 / 「Unnamed: 3」, 真表头在下面几行;
  · 单元格里带换行的把一行拆成几行; CSV 带换行的引号格也一样。
配错列名比不配更糟 (把值安到别的列头上)。所以: 只有找得到「像表头」的一行 (格数跟命中行一样、多数格有字、都是短词) 才配列名,
否则只列这一行的值。
"""
from __future__ import annotations

import csv
import io
import re

MAX_ROWS = 3
ROW_CHARS = 900
CELL_CHARS = 360        # 含搜索词的格: 取词前后这么多字
OTHER_CELL_CHARS = 120  # 别的格最多这么多字
HEADER_SCAN = 8         # 表头在表块开头这么多行里找
HEADER_CELL_CHARS = 30  # 表头格是短词
BLOCK_BACK = 20000      # 往上找表块开头最多走这么多行
_EMPTY = {"", "nan", "none", "null", "-"}
_SEP = re.compile(r"^\|?\s*:?-{2,}")
_NUM = re.compile(r"^[\d.,%\-+:/ ]+$")


def _is_row(line: str) -> bool:
    return line.lstrip().startswith("|") or " | " in line


def _cells(line: str, md: bool) -> list[str]:
    """md = markitdown 的「| a | b |」; 否则是老 extractor 的「a | b」(第一格空 = 「 | b」, 不能先去空白)。"""
    if md:
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip().replace("\\n", " ") for c in s.split("|")]
    return [c.strip().replace("\\n", " ") for c in line.split(" | ")]


def _blank_name(x: str) -> bool:
    low = x.strip().lower()
    return low in _EMPTY or low.startswith("unnamed")


def _looks_header(cells: list[str], n: int) -> bool:
    """像表头: 格数跟数据行一样, 六成以上的格有字, 没有长句, 数字格不到三成。"""
    if len(cells) != n or n < 2:
        return False
    named = [c for c in cells if not _blank_name(c)]
    if len(named) < max(2, 0.6 * n):
        return False
    if any(len(c) > HEADER_CELL_CHARS for c in named):
        return False
    return sum(1 for c in named if _NUM.match(c)) <= 0.3 * len(named)


def _pair(header: list[str] | None, cells: list[str], words: list[str] = ()) -> str:
    """「列=值; 列=值」(header 为 None 时只列值), 空格 / NaN 不列。太长时先保含搜索词的格 (取词前后一段) 和前 3 格
    (认得出是哪一行), 再按列顺序补别的 —— 11/28 实测: 原来从头截 500 字, 一行几十列时答案那格被截掉, 小鲶说「没找到」。"""
    items = []
    for i, v in enumerate(cells):
        v = v.strip()
        if _blank_name(v):
            continue
        name = header[i].strip() if header and i < len(header) else ""
        low = v.lower()
        hit = next((w for w in words if w in low), None)
        if hit and len(v) > CELL_CHARS:
            k = low.find(hit)
            v = ("…" if k > CELL_CHARS // 3 else "") + v[max(0, k - CELL_CHARS // 3):k + CELL_CHARS * 2 // 3] + "…"
        elif not hit and len(v) > OTHER_CELL_CHARS:
            v = v[:OTHER_CELL_CHARS] + "…"
        items.append((i, f"{name}={v}" if name and not _blank_name(name) else v, bool(hit)))
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
    """第 i 行是表格行 → 「[工作表] 列=值; …」(找不到像样的表头就只列值); 不是表格行 / 是分隔行返回 None。"""
    if not _is_row(lines[i]) or _SEP.match(lines[i].strip()):
        return None
    start = i
    while start > 0 and i - start < BLOCK_BACK and _is_row(lines[start - 1]):
        start -= 1
    md = any(_SEP.match(lines[j].strip()) for j in range(start, min(start + 3, len(lines)))) \
        or (lines[i].startswith("|") and lines[i].rstrip().endswith("|"))
    cells = _cells(lines[i], md)
    if len(cells) < 2:
        return None
    header = None
    sep = next((j for j in range(start, min(start + 3, len(lines))) if _SEP.match(lines[j].strip())), None)
    if sep is not None and sep > start and all(not c.strip() for c in _cells(lines[sep - 1], md)):
        scan: range | list[int] = []            # markdown 表头整行是空的 (Word 表): 下面都是数据, 不拿数据行当表头
    else:
        scan = range(start, min(start + HEADER_SCAN, i))
    if i == start and _looks_header(cells, len(cells)):
        return None                              # 命中的就是表头那一行, 没有数据可给
    for j in scan:
        if _SEP.match(lines[j].strip()):
            continue
        cand = _cells(lines[j], md)
        if _looks_header(cand, len(cells)):
            header = cand
            break
    sheet = ""
    for j in range(start - 1, max(-1, start - 4), -1):
        if lines[j].startswith("#"):
            sheet = lines[j].lstrip("# ").strip()
            break
    row = _pair(header, cells, words)
    return (f"[{sheet}] " if sheet else "") + row if row else None


def _csv_row(lines: list[str], i: int, delim: str, words: list[str]) -> str | None:
    if i == 0:
        return None
    try:
        header = next(csv.reader(io.StringIO(lines[0]), delimiter=delim))
        cells = next(csv.reader(io.StringIO(lines[i]), delimiter=delim))
    except (StopIteration, csv.Error):
        return None
    # 带换行的引号格把一行拆成几行: 格数对不上就不配列名
    return _pair(header if len(header) == len(cells) else None, cells, words) or None


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
