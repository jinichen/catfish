"""导入采购台账 Excel 当历史成交价 (11/19, procure.py procure_import)。

小厂的采购台账格式五花八门, 跟销售价格表 (sales_pricebook) 一样按常见叫法认列:
日期 / 供应商 / 品名 / 规格 / 单位 / 含税单价 (或 单价 + 是否含税) / 数量。认不出「品名」「供应商」「单价」
就报错并把表头念给员工; 重复导入同一份 (同日期 + 供应商 + 品名 + 单价) 不会记两遍。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import procure

COLUMNS: dict[str, tuple[str, ...]] = {
    "date": ("日期", "采购日期", "入库日期", "成交日期", "订单日期", "到货日期"),
    "supplier": ("供应商", "供货商", "供应单位", "厂家", "供应商名称"),
    "item": ("品名", "物料", "物料名称", "名称", "材料名称", "商品", "货物名称"),
    "spec": ("规格", "型号", "规格型号", "牌号"),
    "unit": ("单位", "计量单位"),
    "price_tax": ("含税单价", "含税价", "单价(含税)", "含税单价(元)"),
    "price": ("单价", "价格", "不含税单价", "单价(元)"),
    "qty": ("数量", "采购数量", "重量", "吨数"),
}
REQUIRED = ("supplier", "item")


def _cell_date(v: Any) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v or "").strip().replace("/", "-").replace(".", "-")
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return ""


def import_ledger(path: str) -> dict[str, Any]:
    from openpyxl import load_workbook  # noqa: PLC0415

    p = Path(path).expanduser()
    if not p.is_file() or p.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError("要给采购台账的 .xlsx 文件路径")
    ws = load_workbook(p, data_only=True, read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    head_i, cols = -1, {}
    for i, row in enumerate(rows[:10]):  # 表头可能不在第一行 (上面有标题)
        names = [str(c or "").replace(" ", "").strip() for c in row]
        found = {k: names.index(a) for k, alias in COLUMNS.items() for a in alias if a in names}
        if all(k in found for k in REQUIRED) and ("price_tax" in found or "price" in found):
            head_i, cols = i, found
            break
    if head_i < 0:
        header = [str(c) for c in (rows[0] if rows else []) if c is not None]
        raise ValueError(f"认不出表头 (要有 供应商 / 品名 / 单价 这几列)。第一行是: {header}")
    hist = procure._load("history.json")
    seen = {(h.get("date"), h.get("supplier"), procure.item_key(h.get("item", "")), h.get("price_tax")) for h in hist}
    added = skipped = 0
    for row in rows[head_i + 1:]:
        def get(k: str) -> Any:
            return row[cols[k]] if k in cols and cols[k] < len(row) else None
        item, sup = str(get("item") or "").strip(), str(get("supplier") or "").strip()
        try:
            if "price_tax" in cols and get("price_tax") not in (None, ""):
                price = float(get("price_tax"))
            else:
                price = float(get("price")) * (1 + procure.DEFAULT_TAX) if "不含税" in str(rows[head_i][cols["price"]]) else float(get("price"))
        except (TypeError, ValueError, KeyError):
            skipped += 1
            continue
        if not item or not sup:
            skipped += 1
            continue
        rec = {"date": _cell_date(get("date")) or date.today().isoformat(), "item": item, "spec": str(get("spec") or ""),
               "supplier": sup, "unit": procure.norm_unit(get("unit") or "吨"), "price_tax": round(price, 2)}
        key = (rec["date"], sup, procure.item_key(item), rec["price_tax"])
        if key in seen:
            skipped += 1
            continue
        try:
            qty = float(get("qty")) if get("qty") not in (None, "") else None
        except (TypeError, ValueError):
            qty = None
        hist.append({"id": uuid.uuid4().hex[:10], **rec, "qty": qty, "source": f"台账 {p.name}"})
        seen.add(key)
        added += 1
    procure._save("history.json", hist)
    return {"ok": True, "added": added, "skipped": skipped, "total": len(hist), "columns": sorted(cols)}
