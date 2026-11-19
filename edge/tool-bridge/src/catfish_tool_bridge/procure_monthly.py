"""采购比价月度汇总 (11/19, procure.py procure_monthly)。

鸿波「做月度汇总, 算出每月省了多少」—— 给老板看的产出数字。只认**有据可查**的钱:
一笔成交 (procure_deal 记的) 能对上它之前 30 天内同品名的一次比价, 才算「按比价成交」:

  比报价均价省 = (那次比价各家到厂价的平均 − 成交价) × 成交数量      ← 主口径, 保守
  比最高报价省 = (那次比价最高到厂价 − 成交价) × 成交数量             ← 上限, 只作参考
  比上次成交   = (比价时的上次成交价 − 成交价) × 成交数量             ← 行情涨跌, 不算小鲶的功劳, 单列

没有数量的成交只计笔数不计金额 (不猜数量); 比了没记成交的单独列出, 让员工补记 —— 不补就不算钱。
导入的台账不算 (那是以前的历史价, 不是这个月比价的结果)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from . import procure

LINK_DAYS = 30


def _month_of(v: Any) -> tuple[str, date, date]:
    s = str(v or "").strip()
    try:
        d = date.fromisoformat(s[:10]) if len(s) >= 10 else date.fromisoformat(f"{s[:7]}-01") if len(s) >= 7 else date.today()
    except ValueError as e:
        raise ValueError(f"日期看不懂: {s!r} (用 YYYY-MM 或 YYYY-MM-DD)") from e
    start = d.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return start.strftime("%Y-%m"), start, end


def _link(deal: dict[str, Any], compares: list[dict[str, Any]]) -> dict[str, Any] | None:
    """这笔成交之前 LINK_DAYS 天内、同品名、最近的一次比价。"""
    try:
        dd = date.fromisoformat(str(deal.get("date"))[:10])
    except ValueError:
        return None
    best = None
    for c in compares:
        try:
            cd = datetime.fromisoformat(str(c.get("at"))).date()
        except ValueError:
            continue
        if cd <= dd and (dd - cd).days <= LINK_DAYS and procure._same_item(c.get("item", ""), deal.get("item", "")):
            if best is None or str(c["at"]) > str(best["at"]):
                best = c
    return best


def monthly(args: dict[str, Any]) -> dict[str, Any]:
    label, start, end = _month_of(args.get("date"))
    compares = procure._load("compares.json")
    deals = [d for d in procure._load("history.json")
             if d.get("source") == "手工记录" and start.isoformat() <= str(d.get("date", ""))[:10] < end.isoformat()]
    month_compares = [c for c in compares if start.isoformat() <= str(c.get("at", ""))[:10] < end.isoformat()]
    rows, used = [], set()
    tot = {"vs_avg": 0.0, "vs_highest": 0.0, "vs_last": 0.0}
    for d in sorted(deals, key=lambda x: str(x.get("date"))):
        c = _link(d, compares)
        row: dict[str, Any] = {"date": d["date"], "item": d["item"], "supplier": d["supplier"], "price": d["price_tax"],
                               "unit": d.get("unit"), "qty": d.get("qty"), "linked": bool(c)}
        if c:
            used.add(c.get("id"))
            p = procure.convert_price(float(d["price_tax"]), d.get("unit", ""), c.get("unit", "")) or float(d["price_tax"])
            row.update({"compare_at": c["at"][:10], "quotes": c.get("quotes"), "best_supplier": c["best"]["supplier"],
                        "picked_best": c["best"]["supplier"] == d["supplier"],
                        "avg_quote": c.get("avg_landed"), "highest_quote": c["highest"].get("landed", c["highest"].get("price_tax")),
                        "last_before": (c.get("last_deal") or {}).get("price_conv")})
            q = d.get("qty")
            if q:
                qty = float(q) * (procure.convert_price(1.0, c.get("unit", ""), d.get("unit", "")) or 1.0)
                for key, ref in (("vs_avg", row["avg_quote"]), ("vs_highest", row["highest_quote"]), ("vs_last", row["last_before"])):
                    if ref is not None:
                        row[key] = round((float(ref) - p) * qty, 2)
                        tot[key] += row[key]
        rows.append(row)
    unlinked_compares = [{"at": c["at"][:10], "item": c["item"], "best": c["best"]["supplier"]}
                         for c in month_compares if c.get("id") not in used]
    linked = [r for r in rows if r["linked"]]
    summary = {
        "month": label, "compares": len(month_compares), "deals": len(rows), "deals_linked": len(linked),
        "deals_without_qty": sum(1 for r in linked if not r.get("qty")),
        "picked_best": sum(1 for r in linked if r.get("picked_best")),
        "saved_vs_avg": round(tot["vs_avg"], 2), "saved_vs_highest": round(tot["vs_highest"], 2),
        "vs_last_total": round(tot["vs_last"], 2),
    }
    from . import procure_xlsx  # noqa: PLC0415
    path = procure_xlsx.write_monthly(summary, rows, unlinked_compares)
    return {"ok": True, "path": str(path), "summary": summary, "deals": rows, "compares_without_deal": unlinked_compares,
            "note": ("主口径是「比报价均价省」(保守); 「比最高报价省」是上限只作参考; 「比上次成交」是行情涨跌, 不算比价的功劳。"
                     "比了没记成交的不算钱, 请员工补记 (procure_deal)。")}
