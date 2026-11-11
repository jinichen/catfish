"""表格的 N 份三方合并 (xlsx 的每张工作表、csv 共用; docs/DOC-REVISE-MERGE-PLAN.md §4.2 §5)。

单元是单元格。每份交回稿都跟同一份底稿比, 不两两依次合。一张表这样对齐:
  · 认表头: 前 10 行里第一个至少有 2 个非空格子的行; 表头以上 (大标题等) 按地址逐格比。
  · 列按表头名对齐; 表头名重复 / 为空的按列位置; 交回稿里名字没对上的列, 跟底稿同一空档里的列内容像 (≥ 0.5)
    就当改了列名, 否则算新增 / 删除。
  · 行: 找主键列 —— 从左到右第一个在底稿和每份交回稿里都非空、不重复、不是公式、也不是 1,2,3… 连续整数的列
    (序号列别人插行后会重新编号, 不能当主键)。有主键按主键对齐 (排过序也认得); 没有就按整行内容
    SequenceMatcher 对齐, replace 块里按行相似度 (≥ 0.5) 配对, 配上的算改了这一行, 剩下的算增删。
每一行 / 列对齐后有个「身份」(rid / cid): 底稿和各交回稿里身份相同的就是同一行 / 列, 合并、冲突编号都按身份算,
所以 analyze 和 render 对同样的输入算出同样的冲突编号。

合并后的先后 (order_ids): 只有一个人调了行顺序就用他的顺序; 新插的行接在它在交回稿里的前一行后面,
两个人在同一处各插了新行都保留, 按交回先后排, 内容一模一样的只留一份 (身份相同)。单元格怎么合见 grid_merge.py。

格子的值由调用方给两个签名函数: asig (对齐用, 公式写成相对偏移) 和 sig (比较用, 公式已映射回底稿坐标)。
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from difflib import SequenceMatcher
from typing import Any, Callable


HEADER_SCAN = 10
PAIR_RATIO = 0.5


@dataclass
class Grid:
    name: str
    cells: dict[tuple[int, int], Any]          # (行, 列) 1 起 → 原始值 (公式是 "=…" 字符串)
    nrows: int = 0
    ncols: int = 0
    skip: set[tuple[int, int]] = field(default_factory=set)   # 不参与比较的格子 (数据透视表的输出区)

    def get(self, r: int, c: int) -> Any:
        return self.cells.get((r, c))


def blank(v: Any) -> bool:
    return v is None or v == ""


def display(v: Any) -> str:
    """给人看的显示值: 48.0 → 48, 日期 → 2026-11-14。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d") if v.time() == time(0) else v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, (date, time)):
        return v.isoformat()
    text = getattr(v, "text", None)       # ArrayFormula
    if isinstance(text, str):
        return "{" + text + "}"
    return str(v)


def value_sig(v: Any) -> str:
    """非公式值的比较签名: 类型 + 值 (数字 48 和 48.0 一样, 文字 "48" 跟数字 48 不一样)。"""
    if blank(v):
        return ""
    if isinstance(v, bool):
        return "b:" + display(v)
    if isinstance(v, (int, float)):
        return "n:" + display(v)
    if isinstance(v, (datetime, date, time)):
        return "d:" + v.isoformat()
    return "s:" + display(v)


@dataclass
class Side:
    """一份稿子 (底稿或某份交回稿) 在这张表上的对齐结果。"""
    who: str                       # "" = 底稿
    grid: Grid
    hdr: int = 0                   # 表头行 (0 = 没认出表头)
    rows: list[str] = field(default_factory=list)   # 第 r 行的身份 = rows[r-1]
    cols: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)  # 第 c 列的表头名

    def __post_init__(self) -> None:
        self._rpos: dict[str, int] | None = None
        self._cpos: dict[str, int] | None = None

    def rpos(self) -> dict[str, int]:
        if self._rpos is None:
            self._rpos = {x: i + 1 for i, x in enumerate(self.rows)}
        return self._rpos

    def cpos(self) -> dict[str, int]:
        if self._cpos is None:
            self._cpos = {x: i + 1 for i, x in enumerate(self.cols)}
        return self._cpos


@dataclass
class Aligned:
    base: Side
    vers: list[Side]
    key: str | None = None         # 主键列的身份 (None = 按整行内容对齐)
    serial: list[str] = field(default_factory=list)   # 序号列 (1,2,3… 连续往下编的): 不比, 合并版里重新编号


def header_row(g: Grid) -> int:
    for r in range(1, min(g.nrows, HEADER_SCAN) + 1):
        if sum(1 for c in range(1, g.ncols + 1) if not blank(g.get(r, c))) >= 2:
            return r
    return 0


def _names(g: Grid, hdr: int) -> list[str]:
    return [display(g.get(hdr, c)).strip() if hdr else "" for c in range(1, g.ncols + 1)]


def _col_values(g: Grid, hdr: int, c: int) -> list[str]:
    return [display(g.get(r, c)) for r in range(hdr + 1, g.nrows + 1)]


def _base_cols(names: list[str]) -> list[str]:
    cnt = Counter(names)
    return [f"h:{n}" if n and cnt[n] == 1 else f"#{c}" for c, n in enumerate(names, 1)]


def _ver_cols(base: Side, g: Grid, hdr: int, names: list[str], who: str) -> list[str]:
    cnt = Counter(names)
    bset = set(base.cols)
    ids: list[str] = []
    for c, n in enumerate(names, 1):
        if n and cnt[n] == 1:
            ids.append(f"h:{n}" if f"h:{n}" in bset else f"+h:{n}")
        elif f"#{c}" in bset and base.names[c - 1] == n:
            ids.append(f"#{c}")
        else:
            ids.append(f"+#{who}:{c}")
    # 改了列名: 没对上的新列, 跟底稿里同一空档 (前一列对上的那列的下一列) 内容相像 → 算同一列
    used = set(ids)
    for j, x in enumerate(ids):
        if not x.startswith("+"):
            continue
        prev = ids[j - 1] if j else None
        p = base.cpos().get(prev, 0) if prev else 0
        if p >= len(base.cols):
            continue
        cand = base.cols[p]
        if cand in used:
            continue
        a, b = _col_values(base.grid, base.hdr, p + 1), _col_values(g, hdr, j + 1)
        if SequenceMatcher(None, a, b, autojunk=False).ratio() >= PAIR_RATIO:
            ids[j] = cand
            used.add(cand)
    return ids


def _row_blank(g: Grid, r: int) -> bool:
    return all(blank(g.get(r, c)) for c in range(1, g.ncols + 1))


def int_of(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and float(v).is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _is_serial(vals: list[Any]) -> bool:
    nums = [int_of(v) for v in vals]
    if not nums or None in nums:
        return False
    s = sorted(nums)
    return s == list(range(s[0], s[0] + len(s)))


def _key_ok(side: Side, cid: str) -> bool:
    c = side.cpos().get(cid)
    if not c:
        return False
    g = side.grid
    vals = [g.get(r, c) for r in range(side.hdr + 1, g.nrows + 1) if not _row_blank(g, r)]
    if any(blank(v) or (isinstance(v, str) and v.startswith("=")) for v in vals):
        return False
    keys = [display(v).strip() for v in vals]
    return len(set(keys)) == len(keys) and not _is_serial(vals)


def _serial_ok(side: Side, cid: str) -> bool:
    """这一列 (空格子除外) 是从上往下连续递增的整数 —— 序号列。别人插行 / 删行后会重新编号, 编号变了不算改动。"""
    c = side.cpos().get(cid)
    if not c:
        return True
    g = side.grid
    nums = [int_of(g.get(r, c)) for r in range(side.hdr + 1, g.nrows + 1) if not blank(g.get(r, c))]
    if not nums or None in nums:
        return False
    return nums == list(range(nums[0], nums[0] + len(nums)))


def _pick_key(base: Side, vers: list[Side]) -> str | None:
    if not any(not _row_blank(base.grid, r) for r in range(base.hdr + 1, base.grid.nrows + 1)):
        return None
    for cid in base.cols:
        if all(_key_ok(s, cid) for s in [base] + vers):
            return cid
    return None


def _top_ids(hdr: int) -> list[str]:
    return [f"t{r}" for r in range(1, hdr)] + (["h"] if hdr else [])


def _keyed_rows(side: Side, key: str) -> list[str]:
    g, c = side.grid, side.cpos()[key]
    out, prev, n = _top_ids(side.hdr), "^", 0
    for r in range(side.hdr + 1, g.nrows + 1):
        if _row_blank(g, r):
            n += 1
            out.append(f"e:{prev}:{n}")
        else:
            prev, n = "k:" + display(g.get(r, c)).strip(), 0
            out.append(prev)
    return out


def _sim(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    total = sum(1 for x, y in zip(a, b) if x or y)
    if not total:
        return 1.0
    return sum(1 for x, y in zip(a, b) if x and x == y) / total


def _pair(a: list[tuple[str, ...]], b: list[tuple[str, ...]]) -> list[tuple[int, int]]:
    """replace 块里按行相似度配对 (保持先后顺序, 相似度总和最大)。"""
    n, m = len(a), len(b)
    if n * m > 250_000:     # 大块: 不做全局最优, 按位置一对一配
        return [(i, i) for i in range(min(n, m)) if _sim(a[i], b[i]) >= PAIR_RATIO]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            s = _sim(a[i], b[j])
            take = best[i + 1][j + 1] + s if s >= PAIR_RATIO else -1.0
            best[i][j] = max(take, best[i + 1][j], best[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        s = _sim(a[i], b[j])
        if s >= PAIR_RATIO and best[i][j] == best[i + 1][j + 1] + s:
            out.append((i, j))
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return out


def _seq_rows(base: Side, side: Side, asig: Callable[[Grid, int, int], str], serial: list[str]) -> list[str]:
    """没有主键: 按整行内容对齐 (序号列不算, 它会被重新编号)。
    底稿数据行的身份是 b<行号>, 新行 n:<前一个底稿行>:<内容>:<第几个>。"""
    common = [x for x in base.cols if x in side.cpos() and x not in serial]
    bc, vc = [base.cpos()[x] for x in common], [side.cpos()[x] for x in common]
    brows = list(range(base.hdr + 1, base.grid.nrows + 1))
    vrows = list(range(side.hdr + 1, side.grid.nrows + 1))
    bs = [tuple(asig(base.grid, r, c) for c in bc) for r in brows]
    vs = [tuple(asig(side.grid, r, c) for c in vc) for r in vrows]
    got: list[str | None] = [None] * len(vrows)
    sm = SequenceMatcher(None, bs, vs, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for d in range(i2 - i1):
                got[j1 + d] = f"b{brows[i1 + d]}"
        elif op == "replace":
            for i, j in _pair(bs[i1:i2], vs[j1:j2]):
                got[j1 + j] = f"b{brows[i1 + i]}"
    out, anchor, occ = _top_ids(side.hdr), "^", Counter()
    for j, r in enumerate(vrows):
        x = got[j]
        if x is not None:
            anchor = x
            out.append(x)
            continue
        content = repr([(cid, asig(side.grid, r, c)) for c, cid in enumerate(side.cols, 1)
                        if not blank(side.grid.get(r, c)) and cid not in serial])
        h = hashlib.sha1(content.encode("utf-8")).hexdigest()[:10]
        occ[(anchor, h)] += 1
        out.append(f"n:{anchor}:{h}:{occ[(anchor, h)]}")
    return out


def align(base_g: Grid, vers_g: list[tuple[str, Grid]], asig: Callable[[Grid, int, int], str]) -> Aligned:
    hb = header_row(base_g)
    bn = _names(base_g, hb)
    base = Side("", base_g, hb, cols=_base_cols(bn), names=bn)
    vers = []
    for who, g in vers_g:
        h = header_row(g)
        n = _names(g, h)
        vers.append(Side(who, g, h, cols=_ver_cols(base, g, h, n, who), names=n))
    key = _pick_key(base, vers)
    serial = [cid for cid in base.cols
              if len([1 for r in range(hb + 1, base_g.nrows + 1) if not blank(base_g.get(r, base.cpos()[cid]))]) >= 2
              and all(_serial_ok(s, cid) for s in [base] + vers)] if hb else []
    if key:
        for s in [base] + vers:
            s.rows = _keyed_rows(s, key)
    else:
        base.rows = _top_ids(hb) + [f"b{r}" for r in range(hb + 1, base_g.nrows + 1)]
        for s in vers:
            s.rows = _seq_rows(base, s, asig, serial)
    return Aligned(base, vers, key, serial)


def order_ids(base_ids: list[str], vers_ids: list[list[str]]) -> tuple[list[str], int | None, list[int]]:
    """合并后的先后: 底稿顺序 (只有一个人调了顺序就用他的), 新插的接在它在交回稿里的前一个后面, 同一处按交回先后。
    返回 (顺序, 调顺序的人的下标, 另外也调了顺序但没采用的人)。"""
    bset = set(base_ids)
    bpos = {x: i for i, x in enumerate(base_ids)}
    reorder, ignored = None, []
    for k, ids in enumerate(vers_ids):
        seq = [x for x in ids if x in bset]
        if seq != sorted(seq, key=bpos.__getitem__):
            if reorder is None:
                reorder = k
            elif seq != [x for x in vers_ids[reorder] if x in bset and x in set(seq)]:
                ignored.append(k)
    if reorder is None:
        start = list(base_ids)
    else:
        start = [x for x in vers_ids[reorder] if x in bset]
        have = set(start)
        for x in base_ids:          # 他删掉的底稿行 (可能别人还要) 接回它在底稿里前一行的后面
            if x not in have:
                i = bpos[x]
                prev = next((base_ids[p] for p in range(i - 1, -1, -1) if base_ids[p] in have), None)
                start.insert(start.index(prev) + 1 if prev else 0, x)
                have.add(x)
    after: dict[str | None, list[str]] = {}
    placed = set(bset)
    for ids in vers_ids:
        anchor = None
        for x in ids:
            if x in bset:
                anchor = x
            elif x not in placed:
                after.setdefault(anchor, []).append(x)
                placed.add(x)
    out = list(after.get(None, []))
    for x in start:
        out.append(x)
        out.extend(after.get(x, []))
    return out, reorder, ignored
