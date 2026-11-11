"""doc_merge xlsx 三方合并 (docs/DOC-REVISE-MERGE-PLAN.md §4.2 §4.4 §5 §10 第 2 条)。

测试数据全是虚构的「报价表」, 用 openpyxl 现场生成。交回稿用同一个函数按改完后的行重建 —— 公式按行号重新生成,
相当于在 Excel 里插行 / 删行 / 排序后 Excel 自动改好的公式。
"""
from __future__ import annotations

import io
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.drawing.image import Image as XLImage
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from catfish_tool_bridge.doc_merge import Ctx, Return, analyze, render
from catfish_tool_bridge.doc_merge import core, excel, formula_refs, grid

WW, LS, ZL, ZS = "wangwu@example.com", "lisi@example.com", "zhaoliu@example.com", "zhangsan@example.com"
CTX = Ctx(owner=ZS, names={WW: "王五", LS: "李四", ZL: "赵六", ZS: "张三"}, now="2026-11-14T10:00:00")

ITEMS = [("平台", 48, 1, ""), ("培训", 5, 2, ""), ("实施", 20, 3, ""), ("运维", 8, 4, ""), ("硬件", 30, 1, "")]
FIRST = 3     # 第一行数据


def book(items=ITEMS, *, title="XX 项目报价单", extra=None) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "报价表"
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:F1")
    ws.append(["序号", "项目", "单价", "数量", "总价", "备注"])
    for i, (name, price, qty, note) in enumerate(items):
        r = FIRST + i
        ws.append([i + 1, name, price, qty, f"=C{r}*D{r}", note or None])
        ws.cell(r, 3).number_format = "#,##0.00"
    last = FIRST + len(items) - 1
    tr = last + 1
    ws.cell(tr, 2, "合计")
    ws.cell(tr, 5, f"=SUM(E{FIRST}:E{last})")
    ws.cell(tr, 5).fill = PatternFill("solid", fgColor="FFFF00")
    ws.column_dimensions["B"].width = 20
    ws.row_dimensions[tr].height = 24
    dv = DataValidation(type="whole", operator="greaterThan", formula1="0")
    ws.add_data_validation(dv)
    dv.add(f"D{FIRST}:D{last}")
    ws.conditional_formatting.add(f"E{FIRST}:E{last}",
                                  CellIsRule(operator="greaterThan", formula=["100"],
                                             fill=PatternFill("solid", fgColor="FFC7CE")))
    s2 = wb.create_sheet("汇总")
    s2["A1"], s2["B1"] = "总价", f"='报价表'!E{tr}"
    names = [it[0] for it in items]
    if "平台" in names:
        s2["A2"], s2["B2"] = "平台", f"=报价表!E{FIRST + names.index('平台')}"
    if extra:
        extra(wb)
    return wb


def data(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def items_with(**changes):
    """ITEMS 的副本, 改 {项目: (单价, 数量, 备注)} 里给的字段。"""
    out = []
    for name, p, q, n in ITEMS:
        if name in changes:
            c = changes[name]
            p, q, n = c.get("p", p), c.get("q", q), c.get("n", n)
        out.append((name, p, q, n))
    return out


def opened(b: bytes):
    return load_workbook(io.BytesIO(b))


def rows_of(ws):
    return [tuple(r) for r in ws.iter_rows(values_only=True)]


def table(ws):
    """报价表数据区: [(序号, 项目, 单价, 数量, 总价公式)]。"""
    out = []
    for r in range(FIRST, ws.max_row + 1):
        if ws.cell(r, 2).value in (None, "合计"):
            continue
        out.append(tuple(ws.cell(r, c).value for c in range(1, 6)))
    return out


def total_row(ws) -> int:
    return next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 2).value == "合计")


BASE = data(book())


# ── 1~3 单元格 ────────────────────────────────────────────────────────────────

def test_two_people_change_different_cells():
    a = data(book(items_with(平台={"p": 52})))
    b = data(book(items_with(实施={"q": 4})))
    rets = [Return(WW, a), Return(LS, b)]
    res = analyze("xlsx", BASE, rets, CTX)
    assert res["conflicts"] == []
    assert res["changes"] == {WW: 1, LS: 1}
    out = render("xlsx", BASE, rets, {}, CTX)
    ws = opened(out["clean"])["报价表"]
    assert ws["C3"].value == 52 and ws["D5"].value == 4
    assert ws["E3"].value == "=C3*D3" and ws["E8"].value == "=SUM(E3:E7)"


def test_same_cell_conflict_and_decisions():
    a = data(book(items_with(平台={"p": 52})))
    b = data(book(items_with(平台={"p": 55})))
    rets = [Return(WW, a), Return(LS, b)]
    res = analyze("xlsx", BASE, rets, CTX)
    assert len(res["conflicts"]) == 1
    c = res["conflicts"][0]
    assert c["kind"] == "value" and c["base"] == "48"
    assert c["where"] == "报价表 · C3（项目=平台，列「单价」）"
    assert [(o["key"], o["text"]) for o in c["options"]] == [(WW, "52"), (LS, "55")]
    with pytest.raises(core.Undecided):
        render("xlsx", BASE, rets, {}, CTX)

    def c3(dec):
        return opened(render("xlsx", BASE, rets, {c["cid"]: dec}, CTX)["clean"])["报价表"]["C3"].value
    assert c3({"pick": WW}) == 52
    assert c3({"pick": LS}) == 55
    assert c3({"pick": "base"}) == 48
    assert c3({"pick": "text", "text": "60.5"}) == 60.5
    assert c3({"pick": "text", "text": "1,200"}) == 1200
    v = c3({"pick": "text", "text": "2026-11-14"})
    assert isinstance(v, datetime) and v.date().isoformat() == "2026-11-14"
    assert c3({"pick": "text", "text": "待定"}) == "待定"
    out = render("xlsx", BASE, rets, {c["cid"]: {"pick": "text", "text": "50"}}, CTX)
    assert out["changes"] == {ZS: 1}


def test_same_value_twice_is_not_conflict():
    a = data(book(items_with(平台={"p": 52})))
    b = data(book(items_with(平台={"p": 52})))
    res = analyze("xlsx", BASE, [Return(WW, a), Return(LS, b)], CTX)
    assert res["conflicts"] == []
    assert res["changes"] == {WW: 1, LS: 1}
    out = render("xlsx", BASE, [Return(WW, a), Return(LS, b)], {}, CTX)
    assert opened(out["clean"])["报价表"]["C3"].value == 52


# ── 4~7 行对齐 / 主键 / 公式 ──────────────────────────────────────────────────

def test_insert_delete_modify_rows_by_key():
    ins = ITEMS[:2] + [("咨询", 10, 1, ""), ("差旅", 3, 2, "")] + ITEMS[2:]
    a = data(book(ins))                                                 # 王五插两行
    b = data(book([it for it in ITEMS if it[0] != "运维"]))             # 李四删运维
    c = data(book(items_with(硬件={"q": 2})))                          # 赵六改硬件数量
    rets = [Return(WW, a), Return(LS, b), Return(ZL, c)]
    res = analyze("xlsx", BASE, rets, CTX)
    assert res["conflicts"] == []
    assert res["changes"] == {WW: 2, LS: 1, ZL: 1}
    wb = opened(render("xlsx", BASE, rets, {}, CTX)["clean"])
    ws = wb["报价表"]
    assert table(ws) == [
        (1, "平台", 48, 1, "=C3*D3"), (2, "培训", 5, 2, "=C4*D4"), (3, "咨询", 10, 1, "=C5*D5"),
        (4, "差旅", 3, 2, "=C6*D6"), (5, "实施", 20, 3, "=C7*D7"), (6, "硬件", 30, 2, "=C8*D8")]
    tr = total_row(ws)
    assert tr == 9 and ws.cell(tr, 5).value == "=SUM(E3:E8)"
    assert wb["汇总"]["B1"].value == "='报价表'!E9"
    assert wb["汇总"]["B2"].value == "=报价表!E3"


def test_reorder_plus_value_change_with_key():
    by_price = sorted(ITEMS, key=lambda it: -it[1])
    a = data(book(by_price))                                    # 王五按单价排序
    b = data(book(items_with(培训={"q": 6})))                  # 李四改培训数量
    rets = [Return(WW, a), Return(LS, b)]
    res = analyze("xlsx", BASE, rets, CTX)
    assert res["conflicts"] == []
    ws = opened(render("xlsx", BASE, rets, {}, CTX)["clean"])["报价表"]
    names = [r[1] for r in table(ws)]
    assert names == ["平台", "硬件", "实施", "运维", "培训"]
    row = next(r for r in table(ws) if r[1] == "培训")
    assert row[3] == 6
    # 行内公式跟着行走
    for r in range(FIRST, FIRST + 5):
        assert ws.cell(r, 5).value == f"=C{r}*D{r}"


def test_serial_column_is_not_key():
    a = book(ITEMS[:1] + [("咨询", 10, 1, "")] + ITEMS[1:])       # 插行后序号重新编号
    g_base, g_a = excel.to_grid(book()["报价表"]), excel.to_grid(a["报价表"])
    al = grid.align(g_base, [(WW, g_a)], excel.asig)
    assert al.key == "h:项目"
    assert "h:序号" in al.serial
    # 只有序号 + 重复值列的表: 没有主键, 按整行内容对齐
    wb = Workbook()
    ws = wb.active
    ws.append(["序号", "类别", "金额"])
    for i, (k, v) in enumerate([("办公", 1), ("办公", 2), ("差旅", 3)], 1):
        ws.append([i, k, v])
    assert grid.align(excel.to_grid(ws), [], excel.asig).key is None
    # 插行后序号变了不算改动, 合并版里重新编号
    res = analyze("xlsx", BASE, [Return(WW, data(a))], CTX)
    assert res["changes"] == {WW: 1}
    ws = opened(render("xlsx", BASE, [Return(WW, data(a))], {}, CTX)["clean"])["报价表"]
    assert [r[0] for r in table(ws)] == [1, 2, 3, 4, 5, 6]


def test_formulas_follow_inserted_rows():
    a = data(book(ITEMS[:1] + [("咨询", 10, 1, "")] + ITEMS[1:]))   # 第 4 行上面插一行
    b = data(book(ITEMS + [("差旅", 3, 2, "")]))                     # 最后加一行 (SUM 也扩到新行)
    rets = [Return(WW, a), Return(LS, b)]
    res = analyze("xlsx", BASE, rets, CTX)
    assert res["conflicts"] == []
    # 王五的 =C5*D5 (原来 =C4*D4)、SUM(E3:E8) 都不算改了公式: 只有一处 (插的那行)
    assert res["changes"][WW] == 1
    out = render("xlsx", BASE, rets, {}, CTX)
    wb = opened(out["clean"])
    ws = wb["报价表"]
    assert [r[1] for r in table(ws)] == ["平台", "咨询", "培训", "实施", "运维", "硬件", "差旅"]
    for r in range(FIRST, FIRST + 7):
        assert ws.cell(r, 5).value == f"=C{r}*D{r}"
    tr = total_row(ws)
    assert ws.cell(tr, 5).value == "=SUM(E3:E9)"
    assert wb["汇总"]["B1"].value == f"='报价表'!E{tr}"
    tracked = opened(out["tracked"])
    log = rows_of(tracked["修改记录"])
    assert [r[4] for r in log[1:] if r[3] == "王五"] == [None]            # 王五只有「新增行」一条
    # 李四把 SUM 扩到了他加的行, 这是真改了公式
    assert ("报价表", f"E{tr}", "合计", "李四", "=SUM(E3:E7)", "=SUM(E3:E8)") in log


def test_formula_change_after_insert_is_detected():
    """插了行, 又真改了某个公式 → 只有改的那个算。"""
    wb = book(ITEMS[:1] + [("咨询", 10, 1, "")] + ITEMS[1:])
    wb["报价表"]["E7"] = "=C7*D7*0.9"              # 运维 (底稿第 6 行) 打九折
    res = analyze("xlsx", BASE, [Return(WW, data(wb))], CTX)
    assert res["changes"] == {WW: 2}
    ws = opened(render("xlsx", BASE, [Return(WW, data(wb))], {}, CTX)["clean"])["报价表"]
    assert ws["E7"].value == "=C7*D7*0.9" and ws["B7"].value == "运维"


# ── 8 格式保留 ────────────────────────────────────────────────────────────────

def test_merged_cells_and_formats_kept():
    a = data(book(ITEMS[:1] + [("咨询", 10, 1, "")] + ITEMS[1:]))
    out = render("xlsx", BASE, [Return(WW, a)], {}, CTX)
    ws = opened(out["clean"])["报价表"]
    assert {str(r) for r in ws.merged_cells.ranges} == {"A1:F1"}
    assert ws["A1"].font.bold and ws["A1"].font.size == 14
    tr = total_row(ws)
    assert tr == 9
    assert ws.cell(tr, 5).fill.fgColor.rgb.endswith("FFFF00")
    assert ws.row_dimensions[tr].height == 24
    assert ws.column_dimensions["B"].width == 20
    assert ws["C4"].number_format == "#,##0.00"       # 新插的行从交回稿复制样式
    assert [str(r) for dv in ws.data_validations.dataValidation for r in dv.sqref.ranges] == ["D3:D8"]
    assert [str(cf.sqref) for cf in ws.conditional_formatting] == ["E3:E8"]


def test_format_only_change_is_warned_not_merged():
    wb = book()
    wb["报价表"]["C5"].font = Font(color="FF0000", bold=True)
    res = analyze("xlsx", BASE, [Return(WW, data(wb))], CTX)
    assert res["same"] == [WW] and res["changes"] == {}
    assert any("王五对「报价表」C5 的格式调整没合进来" in w for w in res["warnings"])
    ws = opened(render("xlsx", BASE, [Return(WW, data(wb))], {}, CTX)["clean"])["报价表"]
    assert not ws["C5"].font.bold


# ── 9~10 删 / 改、同处插入 ───────────────────────────────────────────────────

def test_delete_row_vs_modify_row_conflict():
    a = data(book([it for it in ITEMS if it[0] != "运维"]))
    b = data(book(items_with(运维={"p": 9})))
    rets = [Return(WW, a), Return(LS, b)]
    res = analyze("xlsx", BASE, rets, CTX)
    assert len(res["conflicts"]) == 1
    c = res["conflicts"][0]
    assert c["where"] == "报价表 · 第 6 行（项目=运维）"
    assert [o["key"] for o in c["options"]] == [WW, LS]
    assert c["options"][0]["text"] == "删除这一行"
    assert "单价：9" in c["options"][1]["text"]
    ws = opened(render("xlsx", BASE, rets, {c["cid"]: {"pick": WW}}, CTX)["clean"])["报价表"]
    assert "运维" not in [r[1] for r in table(ws)]
    assert ws.cell(total_row(ws), 5).value == "=SUM(E3:E6)"
    ws = opened(render("xlsx", BASE, rets, {c["cid"]: {"pick": LS}}, CTX)["clean"])["报价表"]
    assert next(r for r in table(ws) if r[1] == "运维")[2] == 9


def test_two_inserts_at_same_place_both_kept_in_order():
    a = data(book(ITEMS[:2] + [("咨询", 10, 1, "")] + ITEMS[2:]))
    b = data(book(ITEMS[:2] + [("差旅", 3, 2, "")] + ITEMS[2:]))
    c = data(book(ITEMS[:2] + [("咨询", 10, 1, "")] + ITEMS[2:]))    # 跟王五插的一模一样
    rets = [Return(WW, a), Return(LS, b), Return(ZL, c)]
    res = analyze("xlsx", BASE, rets, CTX)
    assert res["conflicts"] == []
    ws = opened(render("xlsx", BASE, rets, {}, CTX)["clean"])["报价表"]
    assert [r[1] for r in table(ws)] == ["平台", "培训", "咨询", "差旅", "实施", "运维", "硬件"]
    # 先交回的李四排前面
    rets2 = [Return(LS, b), Return(WW, a)]
    ws = opened(render("xlsx", BASE, rets2, {}, CTX)["clean"])["报价表"]
    assert [r[1] for r in table(ws)][2:4] == ["差旅", "咨询"]


def test_same_key_added_twice_with_different_values_conflicts():
    a = data(book(ITEMS + [("差旅", 3, 2, "")]))
    b = data(book(ITEMS + [("差旅", 4, 2, "")]))
    res = analyze("xlsx", BASE, [Return(WW, a), Return(LS, b)], CTX)
    assert len(res["conflicts"]) == 1
    assert "项目=差旅" in res["conflicts"][0]["where"]


# ── 11 冲突编号一致 ───────────────────────────────────────────────────────────

def test_cids_stable_between_analyze_and_render():
    a = data(book(items_with(平台={"p": 52}, 硬件={"q": 2})))
    b = data(book(items_with(平台={"p": 55}, 硬件={"q": 3})))
    rets = [Return(WW, a), Return(LS, b)]
    r1 = analyze("xlsx", BASE, rets, CTX)
    r2 = analyze("xlsx", BASE, rets, CTX)
    cids = [c["cid"] for c in r1["conflicts"]]
    assert len(cids) == 2 and cids == [c["cid"] for c in r2["conflicts"]]
    with pytest.raises(core.Undecided):
        render("xlsx", BASE, rets, {cids[0]: {"pick": WW}}, CTX)
    out = render("xlsx", BASE, rets, {cids[0]: {"pick": WW}, cids[1]: {"pick": LS}}, CTX)
    ws = opened(out["clean"])["报价表"]
    assert ws["C3"].value == 52 and ws["D7"].value == 3


# ── 12 tracked ────────────────────────────────────────────────────────────────

def test_tracked_has_comments_and_change_log():
    a = data(book(items_with(平台={"p": 52})))
    b = data(book(ITEMS[:2] + [("咨询", 10, 1, "")] + ITEMS[2:]))
    c = data(book(items_with(平台={"p": 55}, 硬件={"q": 2})))
    rets = [Return(WW, a), Return(LS, b), Return(ZL, c)]
    cid = analyze("xlsx", BASE, rets, CTX)["conflicts"][0]["cid"]
    out = render("xlsx", BASE, rets, {cid: {"pick": "text", "text": "50"}}, CTX)
    clean, tracked = opened(out["clean"]), opened(out["tracked"])
    assert "修改记录" not in clean.sheetnames and tracked.sheetnames[-1] == "修改记录"
    ws = tracked["报价表"]
    assert ws["C3"].value == 50 and ws["C3"].comment.text == "张三: 原值 48 → 50"
    assert ws["C3"].comment.author == "张三"
    assert ws["D8"].comment.text == "赵六: 原值 1 → 2"
    assert "李四: 新增这一行" in ws["A5"].comment.text
    assert clean["报价表"]["C3"].comment is None
    log = rows_of(tracked["修改记录"])
    assert log[0] == ("表名", "单元格", "行主键", "谁", "原值", "新值")
    assert ("报价表", "C3", "平台", "张三", "48", "50") in log
    assert ("报价表", "D8", "硬件", "赵六", "1", "2") in log
    assert any(r[0] == "报价表" and r[2] == "咨询" and r[3] == "李四" for r in log)
    assert out["changes"] == {ZS: 1, LS: 1, ZL: 1}


# ── 其它: same / docProps / 表 / 列 / 图表 / 透视表 / 丢失提示 ────────────────

def test_unchanged_return_and_docprops_marker_ignored():
    from openpyxl.packaging.custom import StringProperty
    wb = book()
    wb.custom_doc_props.append(StringProperty(name="catfish_rev", value="r_20261111_ab12cd:v1"))
    wb.properties.creator = "王五"
    res = analyze("xlsx", BASE, [Return(WW, data(wb)), Return(LS, data(book(items_with(平台={"p": 52}))))], CTX)
    assert res["same"] == [WW] and res["changes"] == {LS: 1}


def test_sheets_added_deleted_and_delete_vs_modify():
    def add_sheet(wb):
        s = wb.create_sheet("说明")
        s["A1"], s["B1"] = "付款方式", "分两期"
    a = data(book(extra=add_sheet))
    b = book()
    del b["汇总"]
    res = analyze("xlsx", BASE, [Return(WW, a), Return(LS, data(b))], CTX)
    assert res["conflicts"] == []
    wb = opened(render("xlsx", BASE, [Return(WW, a), Return(LS, data(b))], {}, CTX)["clean"])
    assert wb.sheetnames == ["报价表", "说明"]
    assert wb["说明"]["B1"].value == "分两期"
    # 一个删表、一个改了这张表 → 冲突
    c = book()
    c["汇总"]["A1"] = "总价 (含税)"
    rets = [Return(LS, data(b)), Return(ZL, data(c))]
    res = analyze("xlsx", BASE, rets, CTX)
    sc = [x for x in res["conflicts"] if x["where"] == "工作表「汇总」"]
    assert len(sc) == 1 and [o["key"] for o in sc[0]["options"]] == [LS, ZL]
    assert opened(render("xlsx", BASE, rets, {sc[0]["cid"]: {"pick": LS}}, CTX)["clean"]).sheetnames == ["报价表"]
    wb = opened(render("xlsx", BASE, rets, {sc[0]["cid"]: {"pick": ZL}}, CTX)["clean"])
    assert wb["汇总"]["A1"].value == "总价 (含税)"


def test_new_column_inserted_after_its_left_neighbour():
    wb = Workbook()
    ws = wb.active
    ws.title = "报价表"
    ws.append(["编号", "项目", "单价", "数量", "折扣", "总价"])
    for i, (n, p, q, _) in enumerate(ITEMS):
        r = i + 2
        ws.append([f"P{i + 1:02d}", n, p, q, 0.9, f"=C{r}*D{r}*E{r}"])
    base = Workbook()
    bs = base.active
    bs.title = "报价表"
    bs.append(["编号", "项目", "单价", "数量", "总价"])
    for i, (n, p, q, _) in enumerate(ITEMS):
        bs.append([f"P{i + 1:02d}", n, p, q, f"=C{i + 2}*D{i + 2}"])
    other = Workbook()
    os_ = other.active
    os_.title = "报价表"
    for row in bs.iter_rows(values_only=True):
        os_.append(list(row))
    os_["C2"] = 50
    rets = [Return(WW, data(wb)), Return(LS, data(other))]
    res = analyze("xlsx", data(base), rets, CTX)
    assert res["conflicts"] == [] and res["changes"] == {WW: 6, LS: 1}     # 新增一列 + 改了 5 个总价公式
    out = opened(render("xlsx", data(base), rets, {}, CTX)["clean"]).active
    assert [c.value for c in out[1]] == ["编号", "项目", "单价", "数量", "折扣", "总价"]
    assert [c.value for c in out[2]] == ["P01", "平台", 50, 1, 0.9, "=C2*D2*E2"]


def test_chart_image_pivot_kept_and_ranges_shift(tmp_path):
    from openpyxl.pivot.cache import CacheDefinition, CacheField, CacheSource, SharedItems, WorksheetSource
    from openpyxl.pivot.fields import Text
    from openpyxl.pivot.record import RecordList
    from openpyxl.pivot.table import DataField, FieldItem, Location, PivotField, RowColField, TableDefinition
    from PIL import Image as PILImage

    png = tmp_path / "logo.png"
    PILImage.new("RGB", (30, 20), (200, 30, 30)).save(png)

    def extra(wb):
        ws = wb["报价表"]
        n = ws.max_row - 1
        ch = BarChart()
        ch.add_data(Reference(ws, min_col=5, min_row=2, max_row=n), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=2, min_row=3, max_row=n))
        ws.add_chart(ch, "H2")
        ws.add_image(XLImage(str(png)), "H20")
        dst = wb.create_sheet("透视")
        cache = CacheDefinition(
            cacheSource=CacheSource(type="worksheet", worksheetSource=WorksheetSource(ref=f"B2:D{n}", sheet="报价表")),
            cacheFields=[CacheField(name="项目", sharedItems=SharedItems(count=1, _fields=[Text(v="平台")])),
                         CacheField(name="单价", sharedItems=SharedItems()),
                         CacheField(name="数量", sharedItems=SharedItems())],
            refreshOnLoad=True, recordCount=0)
        cache.records = RecordList()
        pt = TableDefinition(name="p1", cacheId=1, dataCaption="值",
                             location=Location(ref="A1:B3", firstHeaderRow=1, firstDataRow=1, firstDataCol=1),
                             pivotFields=[PivotField(axis="axisRow", showAll=False,
                                                     items=[FieldItem(x=0), FieldItem(t="default")]),
                                          PivotField(dataField=True, showAll=False), PivotField(showAll=False)],
                             rowFields=[RowColField(x=0)], dataFields=[DataField(name="求和项:单价", fld=1)])
        pt.cache = cache
        dst.add_pivot(pt)
        dst["A1"] = "项目"
    base = data(book(extra=extra))
    a = data(book(ITEMS[:1] + [("咨询", 10, 1, "")] + ITEMS[1:], extra=extra))
    res = analyze("xlsx", base, [Return(WW, a)], CTX)
    assert res["changes"] == {WW: 1}
    assert not any("图表" in w and "王五" in w for w in res["warnings"])
    out = render("xlsx", base, [Return(WW, a)], {}, CTX)
    wb = opened(out["clean"])
    ws = wb["报价表"]
    assert len(ws._charts) == 1 and len(ws._images) == 1
    refs = [s.val.numRef.f for s in ws._charts[0].series]
    assert refs == ["'报价表'!$E$3:$E$8"]
    assert ws._charts[0].series[0].cat.numRef.f == "'报价表'!$B$3:$B$8" or \
        ws._charts[0].series[0].cat.strRef.f == "'报价表'!$B$3:$B$8"
    piv = wb["透视"]._pivots
    assert len(piv) == 1 and piv[0].cache.cacheSource.worksheetSource.ref == "B2:D8"
    assert piv[0].cache.refreshOnLoad
    assert any("数据透视表" in w for w in out["warnings"])
    # 交回稿改了图表 → 提示
    def extra2(wb):
        extra(wb)
        wb["报价表"]._charts[0].title = "报价"
        wb["报价表"]._charts.append(BarChart())
    res = analyze("xlsx", base, [Return(LS, data(book(extra=extra2)))], CTX)
    assert any("李四改了 / 加了工作表「报价表」里的图表" in w for w in res["warnings"])


def test_lost_shapes_are_warned():
    xlsxwriter = pytest.importorskip("xlsxwriter")
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf)
    ws = wb.add_worksheet("汇总")
    ws.write_row(0, 0, ["项目", "金额"])
    ws.write_row(1, 0, ["平台", 48])
    ws.insert_textbox("D2", "请财务复核")
    wb.close()
    base = buf.getvalue()
    res = analyze("xlsx", base, [Return(WW, base)], CTX)
    assert any("工作表「汇总」里的形状 / 文本框" in w and "没有带进合并版" in w for w in res["warnings"])


def test_formula_refs_rewrite_forms():
    def mp(sheet, axis, idx, mode):
        if axis == "r" and sheet in (None, "表 1"):
            return idx + 2 if idx >= 5 else idx
        return idx
    rw = formula_refs.rewrite
    assert rw("=SUM(C2:C10)+$A$5", mp) == "=SUM(C2:C12)+$A$7"
    assert rw("='表 1'!A1:B6+Sheet1!A6", mp) == "='表 1'!A1:B8+Sheet1!A6"
    assert rw("=SUM(A:A)+SUM(5:6)", mp) == "=SUM(A:A)+SUM(7:8)"
    assert rw("=表1[单价]+[1]Sheet1!A9", mp) == "=表1[单价]+[1]Sheet1!A9"
    assert rw("=A6", lambda s, a, i, m: None) == "=#REF!"
    assert formula_refs.relative("=C6*D6", 6, 5) == formula_refs.relative("=C5*D5", 5, 5)
    assert formula_refs.relative("=$C$6", 6, 5) != formula_refs.relative("=$C$5", 5, 5)


def test_output_opens_in_libreoffice(tmp_path):
    soffice = shutil.which("soffice") or "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if not Path(soffice).exists():
        pytest.skip("没装 LibreOffice")
    a = data(book(ITEMS[:1] + [("咨询", 10, 1, "")] + ITEMS[1:]))
    out = render("xlsx", BASE, [Return(WW, a), Return(LS, data(book(items_with(平台={"p": 52}))))], {}, CTX)
    for kind in ("clean", "tracked"):
        p = tmp_path / f"{kind}.xlsx"
        p.write_bytes(out[kind])
        subprocess.run([soffice, f"-env:UserInstallation=file://{tmp_path}/lo", "--headless",
                        "--convert-to", "pdf", "--outdir", str(tmp_path), str(p)],
                       check=True, capture_output=True, timeout=180)
        pdf = tmp_path / f"{kind}.pdf"
        assert pdf.exists() and pdf.stat().st_size > 1000


def test_row_conflict_owner_writes_row():
    a = data(book([it for it in ITEMS if it[0] != "运维"]))
    b = data(book(items_with(运维={"p": 9})))
    rets = [Return(WW, a), Return(LS, b)]
    c = analyze("xlsx", BASE, rets, CTX)["conflicts"][0]
    out = render("xlsx", BASE, rets, {c["cid"]: {"pick": "text", "text": "单价：10 | 数量：5"}}, CTX)
    ws = opened(out["clean"])["报价表"]
    assert next(r for r in table(ws) if r[1] == "运维")[2:4] == (10, 5)
    assert out["changes"] == {ZS: 2}


def test_no_key_table_aligns_by_row_content():
    def mk(rows):
        wb = Workbook()
        ws = wb.active
        ws.title = "费用"
        ws.append(["类别", "说明", "金额"])
        for r in rows:
            ws.append(list(r))
        return data(wb)
    rows = [("办公", "打印纸", 120), ("办公", "墨盒", 300), ("差旅", "高铁", 553), ("差旅", "酒店", 800)]
    base = mk(rows)
    a = mk(rows[:2] + [("办公", "文件夹", 45)] + rows[2:])                 # 插一行
    b = mk([rows[0], ("办公", "墨盒", 350)] + rows[2:])                    # 改墨盒金额
    c = mk(rows[:3])                                                         # 删酒店
    rets = [Return(WW, a), Return(LS, b), Return(ZL, c)]
    res = analyze("xlsx", base, rets, CTX)
    assert res["conflicts"] == [] and res["changes"] == {WW: 1, LS: 1, ZL: 1}
    ws = opened(render("xlsx", base, rets, {}, CTX)["clean"]).active
    assert rows_of(ws)[1:] == [("办公", "打印纸", 120), ("办公", "墨盒", 350), ("办公", "文件夹", 45),
                               ("差旅", "高铁", 553)]
    # 没主键时两个人在同一处插了一模一样的行: 只留一份
    d = mk(rows[:2] + [("办公", "文件夹", 45)] + rows[2:])
    ws = opened(render("xlsx", base, [Return(WW, a), Return(LS, d)], {}, CTX)["clean"]).active
    assert [r[1] for r in rows_of(ws)[1:]].count("文件夹") == 1


def test_title_cell_by_address_and_column_delete():
    a = data(book(title="XX 项目报价单 (第二版)"))
    b = book()
    b["报价表"].unmerge_cells("A1:F1")               # Excel 删列时会把合并区缩成 A1:E1
    b["报价表"].delete_cols(6)                       # 删「备注」列 (没人改过)
    b["报价表"].merge_cells("A1:E1")
    rets = [Return(WW, a), Return(LS, data(b))]
    res = analyze("xlsx", BASE, rets, CTX)
    assert res["conflicts"] == [] and res["changes"] == {WW: 1, LS: 1}
    ws = opened(render("xlsx", BASE, rets, {}, CTX)["clean"])["报价表"]
    assert ws["A1"].value == "XX 项目报价单 (第二版)"
    assert [c.value for c in ws[2]] == ["序号", "项目", "单价", "数量", "总价"]
    # 一个删列、一个在这列填了东西 → 冲突
    c = data(book(items_with(平台={"n": "含一年质保"})))
    res = analyze("xlsx", BASE, [Return(LS, data(b)), Return(ZL, c)], CTX)
    assert len(res["conflicts"]) == 1 and res["conflicts"][0]["where"] == "报价表 · F 列「备注」"
    cid = res["conflicts"][0]["cid"]
    ws = opened(render("xlsx", BASE, [Return(LS, data(b)), Return(ZL, c)], {cid: {"pick": ZL}}, CTX)["clean"])["报价表"]
    assert ws["F3"].value == "含一年质保"


def test_table_freeze_links_names_print_area_shift():
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.table import Table

    def extra(wb):
        ws = wb["报价表"]
        n = ws.max_row - 1
        ws.add_table(Table(displayName="报价明细", ref=f"A2:F{n}"))
        ws.freeze_panes = "A3"
        hw = next(r for r in range(1, n + 1) if ws.cell(r, 2).value == "硬件")
        ws.cell(hw, 6).hyperlink = "https://example.com/hw"
        ws.print_area = f"A1:F{n + 1}"
        wb.defined_names["合计"] = DefinedName("合计", attr_text=f"'报价表'!$E${n + 1}")
    base = data(book(extra=extra))
    a = data(book(ITEMS[:1] + [("咨询", 10, 1, "")] + ITEMS[1:], extra=extra))
    wb = opened(render("xlsx", base, [Return(WW, a)], {}, CTX)["clean"])
    ws = wb["报价表"]
    assert ws.tables["报价明细"].ref == "A2:F8"
    assert [c.name for c in ws.tables["报价明细"].tableColumns] == ["序号", "项目", "单价", "数量", "总价", "备注"]
    assert ws.freeze_panes == "A3"
    assert ws["F8"].hyperlink.target == "https://example.com/hw" and ws["F8"].hyperlink.ref == "F8"
    assert ws.print_area == "'报价表'!$A$1:$F$9"
    assert wb.defined_names["合计"].attr_text == "'报价表'!$E$9"


def test_added_sheet_with_chart_is_copied():
    def extra(wb):
        s = wb.create_sheet("分析")
        s.append(["项目", "金额"])
        s.append(["平台", 48])
        s.append(["实施", 60])
        ch = BarChart()
        ch.add_data(Reference(s, min_col=2, min_row=1, max_row=3), titles_from_data=True)
        s.add_chart(ch, "D2")
        s["B5"] = "=SUM(B2:B3)+'报价表'!E8"
    a = data(book(extra=extra))
    b = data(book(ITEMS[:1] + [("咨询", 10, 1, "")] + ITEMS[1:]))
    out = render("xlsx", BASE, [Return(WW, a), Return(LS, b)], {}, CTX)
    wb = opened(out["clean"])
    s = wb["分析"]
    assert s["B3"].value == 60 and len(s._charts) == 1
    assert s["B5"].value == "=SUM(B2:B3)+'报价表'!E9"          # 引用的报价表合计行下移了
    assert s._charts[0].series[0].val.numRef.f == "'分析'!$B$2:$B$3"


# ── LibreOffice / WPS 另存: 显式写出的默认格式不算改 ─────────────────────────

def _soffice() -> str:
    exe = shutil.which("soffice") or "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if not Path(exe).exists():
        pytest.skip("没装 LibreOffice")
    return exe


def _lo_resave(b: bytes, tmp_path: Path, name: str) -> bytes:
    """用 LibreOffice 原样另存一遍 (它会把默认字体 / 字号 / 行高全部显式写出来)。"""
    src = tmp_path / f"{name}.xlsx"
    src.write_bytes(b)
    out = tmp_path / f"out_{name}"
    subprocess.run([_soffice(), f"-env:UserInstallation=file://{tmp_path}/lo", "--headless",
                    "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(out), str(src)],
                   check=True, capture_output=True, timeout=180)
    return (out / f"{name}.xlsx").read_bytes()


def _quote_sheet() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "报价表"
    ws.append(["编号", "项目", "单价", "数量", "金额"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for i, (n, p, q) in enumerate([("平台", 48, 1), ("培训", 5, 2), ("实施", 20, 3), ("运维", 8, 4)]):
        r = i + 2
        ws.append([f"P{i + 2:03d}", n, p, q, f"=C{r}*D{r}"])
    ws.append(["", "合计", "", "", "=SUM(E2:E5)"])
    return data(wb)


def test_libreoffice_resave_is_same_without_warnings(tmp_path):
    base = _quote_sheet()
    resaved = _lo_resave(base, tmp_path, "resave")
    res = analyze("xlsx", base, [Return(WW, resaved)], CTX)
    assert res["same"] == [WW] and res["changes"] == {} and res["conflicts"] == []
    assert res["warnings"] == []


def test_real_format_change_after_libreoffice_resave_is_reported(tmp_path):
    base = _quote_sheet()
    resaved = _lo_resave(base, tmp_path, "resave")
    edits = {
        "bold": lambda ws: setattr(ws["B3"], "font", Font(bold=True)),
        "fill": lambda ws: setattr(ws["C4"], "fill", PatternFill("solid", fgColor="FFFF00")),
        "numfmt": lambda ws: setattr(ws["C5"], "number_format", "#,##0.00"),
    }
    expect = {"bold": "B3", "fill": "C4", "numfmt": "C5"}
    for name, edit in edits.items():
        wb = load_workbook(io.BytesIO(resaved))
        edit(wb["报价表"])
        again = _lo_resave(data(wb), tmp_path, name)          # 改完再用 LibreOffice 存一遍
        res = analyze("xlsx", base, [Return(WW, again)], CTX)
        assert res["same"] == [WW], name
        fmt_warns = [w for w in res["warnings"] if "格式调整没合进来" in w]
        assert fmt_warns == [f"王五对「报价表」{expect[name]} 的格式调整没合进来"], (name, res["warnings"])


def test_effective_format_ignores_spelling_differences():
    """不靠 soffice: 显式默认值 / 主题色写成 RGB / bold=False / 行高写成默认值, 都不算改格式。"""
    from openpyxl.styles.colors import Color
    from catfish_tool_bridge.doc_merge import xlsx_fmt
    a, b = Workbook(), Workbook()
    wa, wb_ = a.active, b.active
    wa["A1"], wb_["A1"] = 1, 1
    wb_["A1"].font = Font(name="思源黑体", sz=11, b=False, i=False, u="none", color=Color(theme=1))
    wa["B1"], wb_["B1"] = 2, 2
    wa["B1"].font = Font(color=Color(theme=4))
    wb_["B1"].font = Font(color="FF4472C4")                   # 默认主题 accent1 的 RGB 写法
    wb_.row_dimensions[1].height = 15
    ba, bb = xlsx_fmt.Book(a), xlsx_fmt.Book(b)
    assert xlsx_fmt.same_fmt(wa["A1"], ba, wb_["A1"], bb)
    assert xlsx_fmt.same_fmt(wa["B1"], ba, wb_["B1"], bb)
    assert xlsx_fmt.same_row_height(wa, 1, wb_, 1)
    wb_["A1"].font = Font(b=True)
    assert not xlsx_fmt.same_fmt(wa["A1"], ba, wb_["A1"], bb)
    wb_.row_dimensions[1].height = 30
    assert not xlsx_fmt.same_row_height(wa, 1, wb_, 1)
