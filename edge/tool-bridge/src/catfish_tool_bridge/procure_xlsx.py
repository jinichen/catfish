"""采购比价表 Excel (11/19, procure.py 调)。

~/.catfish/outputs/<日期>/比价-<品名>-<时分>.xlsx, 两张表:
  比价    摘要 (最低 / 最高 / 上次成交 / 预计节省) + 每家一行 (统一成含税每单位) + 提醒
  历史成交  近半年这个品名的成交, 对照用
老板 / 采购主管拿到就能看, 不用再加工。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import advisor_io

_HEAD = ["排名", "供应商", "原报价", "原单位", "原价含税?", "含税单价(统一)", "到厂价(含运费)", "比最低价高", "比上次成交", "交货期", "付款条件", "运费", "有效期", "来源", "提醒"]
_WIDTH = [6, 18, 10, 8, 10, 14, 14, 11, 11, 12, 14, 14, 12, 22, 40]


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "", name)[:30] or "物料"


def write_compare(item: str, unit: str, qty: float | None, rows: list[dict[str, Any]], ranked: list[dict[str, Any]],
                  summary: dict[str, Any], hist: dict[str, Any]) -> Path:
    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415

    path = advisor_io.today_outputs_dir() / f"比价-{_safe(item)}-{datetime.now().strftime('%H%M%S')}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "比价"
    ws["A1"] = f"采购比价 · {item}"
    ws["A1"].font = Font(size=15, bold=True)
    ws["A2"] = f"统一口径: 含税、每{unit}, 按到厂价 (另计的运费加上) 排名。生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}。只比价格, 质量 / 交期 / 账期 / 运费请一并权衡。"
    ws["A2"].font = Font(size=9, color="666666")
    lines = [
        ("到厂价最低", f"{summary['best']['supplier']}  {summary['best']['landed']:,.2f} 元/{unit}"),
        ("到厂价最高", f"{summary['highest']['supplier']}  {summary['highest']['landed']:,.2f} 元/{unit} (差 {summary['spread']:,.2f})"),
    ]
    last = summary.get("last_deal")
    lines.append(("上次成交", f"{last['date']} {last['supplier']}  {last['price_conv']:,.2f} 元/{unit}" if last else "没有记录"))
    if summary["history"]["avg"] is not None:
        lines.append((f"近{summary['history']['days']}天均价", f"{summary['history']['avg']:,.2f} 元/{unit} (共 {summary['history']['count']} 笔)"))
    if qty:
        lines.append(("采购数量", f"{qty:,g} {unit}"))
        lines.append(("选最低价比选最高价省", f"{summary['save_vs_highest']:,.2f} 元"))
        if "save_vs_last" in summary:
            v = summary["save_vs_last"]
            lines.append(("比按上次成交价买", f"{'省' if v >= 0 else '多花'} {abs(v):,.2f} 元"))
    if summary.get("alert"):
        lines.append(("注意", summary["alert"]))
    r = 4
    for k, v in lines:
        ws.cell(r, 1, k).font = Font(bold=True)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        ws.cell(r, 3, v)
        r += 1
    r += 1
    fill = PatternFill("solid", fgColor="DDEBF7")
    for i, h in enumerate(_HEAD, 1):
        c = ws.cell(r, i, h)
        c.font, c.fill = Font(bold=True), fill
        ws.column_dimensions[c.column_letter].width = _WIDTH[i - 1]
    best_fill = PatternFill("solid", fgColor="E2EFDA")
    for q in ranked + [x for x in rows if x["price_tax"] is None]:
        r += 1
        vals = [q.get("rank", "—"), q["supplier"], q["price"], q["unit"],
                {True: "含税", False: "不含税", None: "未注明"}[q["tax_included"]],
                q["price_tax"] if q["price_tax"] is not None else "换算不了",
                q["landed"] if q["landed"] is not None else "—",
                q.get("vs_best"), q.get("vs_last"), q["delivery"], q["payment"], q["freight"],
                q["valid_until"], q["source"], "；".join(q["alerts"] + ([q["note"]] if q["note"] else []))]
        for i, v in enumerate(vals, 1):
            c = ws.cell(r, i, v)
            c.alignment = Alignment(wrap_text=i in (12, 14, 15), vertical="top")
            if i in (3, 6, 7, 8, 9) and isinstance(v, (int, float)):
                c.number_format = "#,##0.00"
            if q.get("rank") == 1:
                c.fill = best_fill
    hs = wb.create_sheet("历史成交")
    for i, h in enumerate(["日期", "供应商", "规格", f"含税单价(元/{unit})", "数量", "来源"], 1):
        c = hs.cell(1, i, h)
        c.font, c.fill = Font(bold=True), fill
        hs.column_dimensions[c.column_letter].width = [12, 18, 14, 16, 10, 14][i - 1]
    for j, d in enumerate(hist["deals"], 2):
        for i, v in enumerate([d.get("date"), d.get("supplier"), d.get("spec"), d.get("price_conv"), d.get("qty"), d.get("source")], 1):
            hs.cell(j, i, v)
    if not hist["deals"]:
        hs.cell(2, 1, "没有历史成交记录 (可以在设置里导入采购台账, 或成交后让小鲶记一笔)")
    wb.save(path)
    return path
