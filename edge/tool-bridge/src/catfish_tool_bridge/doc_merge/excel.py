"""xlsx 的三方合并 (docs/DOC-REVISE-MERGE-PLAN.md §4.2 §4.4 第 3 条 §5 xlsx 行)。接口见 doc_merge/__init__.py。

单元是单元格。每张工作表按表名对齐: 有人新增的整张表加进来 (几个人加了同名但内容不一样的表 = 冲突);
有人删了的表, 别人没改过就删, 有人改过就是冲突。表里的对齐 / 合并在 grid.py / grid_merge.py。

公式比的是公式文本, 不比计算结果 (不用 data_only 加载)。比较前先把交回稿里公式的行列号映射回底稿坐标
(formula_refs.py), 交回人插行后跟着行走的公式不算改动; 写合并版时再映射到合并版坐标。

写合并版以底稿 workbook 为起点 (excel_write.py), 保留格式 / 列宽 / 行高 / 合并单元格 / 数据验证 / 条件格式 /
图表 / 图片 / 数据透视表; openpyxl 带不进来的东西和只改了格式的格子都写进 warnings (excel_check.py)。
docProps/ 下的属性 (catfish_rev 记号等) 不比。
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils.cell import range_boundaries

from . import Ctx, Return, core, excel_check, excel_write
from .formula_refs import relative, rewrite
from .grid import Aligned, Grid, Side, align, display, order_ids, value_sig
from .grid_merge import Plan, merge, stats


def load(data: bytes):
    return load_workbook(io.BytesIO(data), data_only=False, keep_links=True)


def formula_text(v: Any) -> str | None:
    """公式单元格 → 公式文本 (数组公式取里面的), 不是公式 → None。"""
    if isinstance(v, str) and v.startswith("=") and len(v) > 1:
        return v
    text = getattr(v, "text", None)
    if isinstance(text, str) and hasattr(v, "ref"):
        return text if text.startswith("=") else "=" + text
    return None


def to_grid(ws) -> Grid:
    cells = {}
    for (r, c), cell in ws._cells.items():
        if isinstance(cell, MergedCell) or cell.value is None or cell.value == "":
            continue
        cells[(r, c)] = cell.value
    # 行列只算到最后一个有值的格子: 后面只有格式的空行 / 空列不参与对齐 (合并版里按偏移原样带着)
    g = Grid(ws.title, cells, max((r for r, _ in cells), default=0), max((c for _, c in cells), default=0))
    for p in getattr(ws, "_pivots", []) or []:     # 数据透视表的输出区是算出来的, 不比
        try:
            c1, r1, c2, r2 = range_boundaries(p.location.ref)
        except (TypeError, ValueError, AttributeError):
            continue
        g.skip |= {(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)}
    return g


def asig(g: Grid, r: int, c: int) -> str:
    v = g.get(r, c)
    f = formula_text(v)
    return "f:" + relative(f, r, c) if f else value_sig(v)


_NUM = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+")
_DATE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")


def parse_value(text: str) -> Any:
    """发起人自己填的值: 数字 / 百分数 / 日期 / TRUE FALSE / 公式 (=…, 按底稿坐标写) / 文本。"""
    t = text.strip()
    if not t:
        return None
    if t.startswith("=") and len(t) > 1:
        return t
    if _NUM.fullmatch(t):
        n = float(t.replace(",", ""))
        return int(n) if n.is_integer() and "." not in t else n
    if t.endswith("%") and _NUM.fullmatch(t[:-1].strip()):
        return float(t[:-1].replace(",", "")) / 100
    m = _DATE.fullmatch(t)
    if m:
        try:
            return datetime(*(int(x) for x in m.groups() if x is not None))
        except ValueError:
            pass
    if t.upper() in ("TRUE", "FALSE"):
        return t.upper() == "TRUE"
    return text


class Run:
    """一次合并的全部中间结果 (analyze 和 render 共用同一套计算, 冲突编号一致)。"""

    def __init__(self, base: bytes, returns: list[Return], ctx: Ctx, decisions: dict[str, Any] | None):
        self.base_bytes, self.returns, self.ctx, self.decisions = base, returns, ctx, decisions
        self.whos = [r.who for r in returns]
        self.bwb = load(base)
        self.vwbs = [load(r.data) for r in returns]
        self.conflicts: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.changed: set[str] = set()
        self.undecided = 0
        self.sheet_events: list[tuple[str, str, list[str]]] = []   # (表名, "sheet+" / "sheet-", 谁)
        self.order: list[tuple[str, int]] = []     # 合并版的表: (表名, 来源 -1=底稿 / k=第 k 份交回稿)
        self.plans: dict[str, Plan] = {}
        self.aligned: dict[str, Aligned] = {}
        self.present: dict[str, list[int]] = {}    # 表名 → 有这张表的交回稿下标 (Aligned.vers 的顺序)
        self._norm_cache: dict[tuple[int, str, int, int], str] = {}
        self._run()

    # ── 对齐 ──
    def sheets(self, wb) -> list[str]:
        return [ws.title for ws in wb.worksheets]

    def side(self, k: int, name: str) -> Side | None:
        al = self.aligned.get(name)
        if al is None:
            return None
        if k < 0:
            return al.base
        ks = self.present[name]
        return al.vers[ks.index(k)] if k in ks else None

    def _norm_mapper(self, k: int, cur: str):
        """第 k 份交回稿的引用 → 底稿坐标; 引用到底稿里没有的行 / 列给个记号 (比较时就跟底稿不一样)。"""
        def mp(sheet: str | None, axis: str, idx: int, mode: str):
            name = sheet or cur
            vs, bs = self.side(k, name), self.side(-1, name)
            if vs is None or bs is None:
                return idx
            ids = vs.rows if axis == "r" else vs.cols
            bpos = bs.rpos() if axis == "r" else bs.cpos()
            if idx > len(ids):
                return len(bpos) + idx - len(ids)
            x = ids[idx - 1]
            if x in bpos:
                return bpos[x]
            if mode != "one":
                rng = range(idx, len(ids)) if mode == "lo" else range(idx - 2, -1, -1)
                for i in rng:
                    if ids[i] in bpos:
                        return f"{bpos[ids[i]]}~"
            return f"<{x}>"
        return mp

    def sig(self, k: int, name: str, r: int, c: int) -> str:
        wb = self.bwb if k < 0 else self.vwbs[k]
        v = wb[name]._cells.get((r, c))
        v = None if v is None or isinstance(v, MergedCell) else v.value
        f = formula_text(v)
        if not f:
            return value_sig(v)
        if k < 0:
            return "f:" + f
        key = (k, name, r, c)
        if key not in self._norm_cache:
            self._norm_cache[key] = "f:" + rewrite(f, self._norm_mapper(k, name))
        return self._norm_cache[key]

    def _run(self) -> None:
        bnames = self.sheets(self.bwb)
        vnames = [self.sheets(wb) for wb in self.vwbs]
        grids = {n: to_grid(self.bwb[n]) for n in bnames}
        for n in bnames:
            ks = [k for k in range(len(self.vwbs)) if n in vnames[k]]
            self.present[n] = ks
            self.aligned[n] = align(grids[n], [(self.whos[k], to_grid(self.vwbs[k][n])) for k in ks], asig)
        order, reorder, _ = order_ids(bnames, vnames)
        if reorder is not None:
            self.changed.add(self.whos[reorder])
        keep: dict[str, int] = {}
        for n in order:
            if n in grids:
                if self._plan_base_sheet(n):
                    keep[n] = -1
            else:
                k = self._plan_new_sheet(n, vnames)
                if k is not None:
                    keep[n] = k
        self.order = [(n, keep[n]) for n in order if n in keep]
        self.warnings += excel_check.check(self)

    def _plan_base_sheet(self, name: str) -> bool:
        """底稿里有的表: 合并表里的格子; 有人删了这张表就看别人改没改。返回合并版要不要这张表。"""
        ks = self.present[name]
        al = self.aligned[name]
        local_whos = [self.whos[k] for k in ks]
        plan = merge(al, lambda i, r, c: self.sig(-1 if i < 0 else ks[i], name, r, c), label=name,
                     owner=self.ctx.owner, decisions=self.decisions, parse=parse_value)
        self.changed |= plan.changed
        deleters = [self.whos[k] for k in range(len(self.vwbs)) if k not in ks]
        self.changed |= set(deleters)
        mods = [w for w in local_whos if w in plan.changed]
        if deleters and not mods:
            self.sheet_events.append((name, "sheet-", deleters))
            return False
        self.conflicts += plan.conflicts
        self.warnings += plan.warnings
        if not deleters:
            self.plans[name] = plan
            self.undecided += plan.undecided
            return True
        opts = [(deleters, "del"), (mods, "keep")]
        digest = hashlib.sha1(repr(sorted((k, repr(v)) for k, v in al.base.grid.cells.items())).encode()).hexdigest()
        cid = core.cid_of(f"sheet\x1f{name}", 0, 0, [core.Unit(digest, "")],
                          [core.Option(w, [core.Unit(p, "")]) for w, p in opts])
        self.conflicts.append({
            "cid": cid, "where": f"工作表「{name}」", "kind": "value", "base": f"工作表「{name}」(底稿)",
            "combined": None,
            "options": [{"key": "+".join(deleters), "who": deleters, "text": "删除这张工作表"},
                        {"key": "+".join(mods), "who": mods, "text": f"保留这张工作表 (含 {'、'.join(mods)} 的修改)"}]})
        d = (self.decisions or {}).get(cid) or {}
        pick = d.get("pick")
        if self.decisions is None:
            self.plans[name] = plan
            return True
        if pick == "+".join(deleters):
            self.sheet_events.append((name, "sheet-", deleters))
            return False
        if pick == core.BASE:
            self.plans[name] = Plan(rows=[(x, -1, i + 1) for i, x in enumerate(al.base.rows)],
                                    cols=[(x, -1, i + 1) for i, x in enumerate(al.base.cols)])
            return True
        if pick != "+".join(mods):
            self.undecided += 1
        self.plans[name] = plan
        self.undecided += plan.undecided
        return True

    def _plan_new_sheet(self, name: str, vnames: list[list[str]]) -> int | None:
        """有人新增的表: 一个人加的 (或几个人加得一模一样) 直接加; 加得不一样 = 冲突。返回用哪份稿子的。"""
        adders = [k for k in range(len(self.vwbs)) if name in vnames[k]]
        groups: dict[str, list[int]] = {}
        for k in adders:
            g = to_grid(self.vwbs[k][name])
            key = hashlib.sha1(repr(sorted((rc, asig(g, *rc)) for rc in g.cells)).encode()).hexdigest()
            groups.setdefault(key, []).append(k)
        self.changed |= {self.whos[k] for k in adders}
        if len(groups) == 1:
            self.sheet_events.append((name, "sheet+", [self.whos[k] for k in adders]))
            return adders[0]
        opts = [([self.whos[k] for k in ks], h) for h, ks in groups.items()]
        cid = core.cid_of(f"sheet+\x1f{name}", 0, 0, [core.Unit("", "")],
                          [core.Option(w, [core.Unit(h, "")]) for w, h in opts])
        self.conflicts.append({
            "cid": cid, "where": f"工作表「{name}」", "kind": "value", "base": "(底稿里没有这张表)", "combined": None,
            "options": [{"key": "+".join(w), "who": w, "text": f"用 {'、'.join(w)} 加的这张表"} for w, _ in opts]})
        if self.decisions is None:
            return None
        pick = ((self.decisions.get(cid) or {}).get("pick"))
        if pick == core.BASE:
            return None
        for (w, _), ks in zip(opts, groups.values()):
            if pick == "+".join(w):
                self.sheet_events.append((name, "sheet+", w))
                return ks[0]
        self.undecided += 1
        return None

    # ── 结果 ──
    def changes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for name, _ in self.order:
            plan = self.plans.get(name)
            if plan:
                for w, n in stats(plan).items():
                    out[w] = out.get(w, 0) + n
        for _, _, who in self.sheet_events:
            for w in who:
                out[w] = out.get(w, 0) + 1
        return out

    def same(self) -> list[str]:
        return [w for w in dict.fromkeys(self.whos) if w not in self.changed]


def analyze(base: bytes, returns: list[Return], ctx: Ctx) -> dict[str, Any]:
    run = Run(base, returns, ctx, None)
    return {"conflicts": run.conflicts, "changes": run.changes(), "same": run.same(), "warnings": run.warnings}


def render(base: bytes, returns: list[Return], decisions: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    run = Run(base, returns, ctx, decisions or {})
    if run.undecided:
        raise core.Undecided(f"还有 {run.undecided} 处冲突没定")
    clean = excel_write.write_clean(run)
    tracked = excel_write.write_tracked(run, clean)
    for data in (clean, tracked):      # 自检: 再打开一遍
        load(data)
    return {"tracked": tracked, "clean": clean, "changes": run.changes(), "warnings": run.warnings}


__all__ = ["analyze", "render", "display", "parse_value"]
