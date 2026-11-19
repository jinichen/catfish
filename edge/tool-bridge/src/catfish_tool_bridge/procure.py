"""采购比价 (11/19, docs/PROCURE-COMPARE-PLAN.md)。

鸿波: 小钢厂客户「对投资产出的要求高, 没有有效的产出意愿很低」—— 采购比价能直接算出省了多少钱。
流程: 小鲶读供应商报价 (邮件正文 / 附件 Excel / Word / PDF) → 抽成结构化报价 → 这里**确定性**地
统一口径、排序、对照历史成交价、算差额 → 出比价表 Excel; 员工定了跟谁买, 再记一笔成交进历史。

走 catfish_customer 的 procure_* 动作 (网关工具上限只剩 1 格, 不另起工具):
  procure_compare  比价: item + quotes[] (+ qty) → 比价表 + 摘要
  procure_deal     记成交: 员工说定了才记 (历史价从这里来)
  procure_history  查某品名的历史成交
  procure_import   导入现成的采购台账 Excel 当历史价

口径: 一律换成「含税、每 目标单位」的单价比。不含税的按 tax_rate (默认 13%) 换成含税;
单位认 吨 / 千克 / 克 (及常见写法), 认不出又跟目标单位不同的**不参与排名**, 原样列出并标明。
金额只做加减乘除, 不替员工下结论: 质量、交期、账期、运费由员工权衡 (表里列出, 提醒栏写明)。
不替员工给供应商发任何东西。
"""
from __future__ import annotations

import json
import re
import statistics
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import wiki_files

DEFAULT_TAX = 0.13
# 单位 → 换成千克的倍数
_UNIT_KG = {"吨": 1000.0, "千克": 1.0, "克": 0.001}  # norm_unit 之后的叫法 → 千克倍数
HISTORY_DAYS = 180
LOW_OUTLIER = 0.85     # 低于其余报价中位数这么多, 提醒核实 (规格 / 质量 / 是否含运费)
RISE_ALERT = 0.03      # 最低价仍比上次成交高这么多, 提醒


def procure_dir() -> Path:
    d = wiki_files._catfish_home() / "procure"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(name: str) -> list[dict[str, Any]]:
    p = procure_dir() / name
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(name: str, rows: list[dict[str, Any]]) -> None:
    p = procure_dir() / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def norm_unit(u: Any) -> str:
    """「元/吨」「/t」「每公斤」「KG」→ 吨 / 千克 / 克; 别的单位 (件 / 个 / 米 …) 原样。"""
    s = re.sub(r"^(元|人民币|rmb)?\s*/\s*", "", str(u or "").strip(), flags=re.I).replace("每", "").strip()
    return {"t": "吨", "公吨": "吨", "kg": "千克", "公斤": "千克", "g": "克"}.get(s.lower(), s)


def convert_price(price: float, from_unit: str, to_unit: str) -> float | None:
    """每 from_unit 的价 → 每 to_unit 的价; 换不了返回 None。"""
    a, b = norm_unit(from_unit), norm_unit(to_unit)
    if a == b:
        return price
    fa, fb = _UNIT_KG.get(a), _UNIT_KG.get(b)
    if fa is None or fb is None:
        return None
    return price / fa * fb


def _num(v: Any, what: str) -> float:
    try:
        x = float(str(v).replace(",", "").replace("，", "").strip())
    except (TypeError, ValueError) as e:
        raise ValueError(f"{what} 不是数字: {v!r}") from e
    if x < 0:
        raise ValueError(f"{what} 不能是负数")
    return x


def item_key(name: str) -> str:
    return re.sub(r"[\s\-_()（）]", "", str(name or "")).lower()


def _same_item(a: str, b: str) -> bool:
    ka, kb = item_key(a), item_key(b)
    return bool(ka and kb) and (ka == kb or ka in kb or kb in ka)


def history_for(item: str, unit: str, days: int = HISTORY_DAYS, today: date | None = None) -> dict[str, Any]:
    """某品名的历史成交 (含税、换到 unit), 最近 days 天。"""
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    deals = []
    for d in _load("history.json"):
        if not _same_item(d.get("item", ""), item) or str(d.get("date", "")) < since:
            continue
        p = convert_price(float(d["price_tax"]), d.get("unit", ""), unit)
        if p is not None:
            deals.append({**d, "price_conv": round(p, 2)})
    deals.sort(key=lambda d: str(d.get("date", "")), reverse=True)
    prices = [d["price_conv"] for d in deals]
    return {
        "deals": deals[:20],
        "last": deals[0] if deals else None,
        "avg": round(statistics.mean(prices), 2) if prices else None,
        "min": min(prices) if prices else None,
        "count": len(deals),
        "days": days,
    }


def freight_add(text: str, target_unit: str) -> float | None:
    """运费文字 → 每目标单位要另加的钱。含运费 / 到厂 → 0; 「另计约 60 元/吨」→ 60; 说不清 → None (不加, 提醒)。"""
    t = str(text or "").replace(" ", "")
    if not t:
        return None
    extra = re.search(r"(另计|不含|自提|另付|加收|需加)", t)
    m = re.search(r"(\d+(?:\.\d+)?)元?/(吨|t|公斤|千克|kg)", t, flags=re.I)
    if extra and m:
        return convert_price(float(m.group(1)), m.group(2), target_unit)
    if not extra and re.search(r"(含运费|含运|到厂|送到|送货上门|包运)", t):
        return 0.0
    return None


def _norm_quote(q: dict[str, Any], target_unit: str, default_tax: float) -> dict[str, Any]:
    sup = str(q.get("supplier") or "").strip()
    if not sup:
        raise ValueError("每条报价要有 supplier (供应商)")
    price = _num(q.get("price"), f"{sup} 的报价")
    unit = norm_unit(q.get("unit") or target_unit)
    tax_known = q.get("tax_included") is not None
    tax_inc = bool(q.get("tax_included")) if tax_known else True
    rate = _num(q.get("tax_rate"), "税率") if q.get("tax_rate") not in (None, "") else default_tax
    with_tax = price if tax_inc else price * (1 + rate)
    conv = convert_price(with_tax, unit, target_unit)
    notes = []
    if not tax_known:
        notes.append("没写含不含税, 按含税算, 请核实")
    if not tax_inc:
        notes.append(f"原价不含税, 按 {rate:.0%} 换成含税")
    if conv is None:
        notes.append(f"单位「{unit}」换不成「{target_unit}」, 没参与排名")
    elif unit != target_unit:
        notes.append(f"原价按「{unit}」报, 已换成每{target_unit}")
    freight = str(q.get("freight") or "")
    fadd = freight_add(freight, target_unit)
    if fadd:
        notes.append(f"运费另计, 按 {fadd:,.2f} 元/{target_unit} 加进到厂价 (运费是否含税请核实)")
    elif fadd is None and freight:
        notes.append("运费写法看不出金额, 没加进到厂价, 请核实")
    vu = str(q.get("valid_until") or "").strip()
    if vu and vu[:10] < date.today().isoformat():
        notes.append(f"报价有效期 {vu[:10]} 已过, 要重新确认")
    return {
        "supplier": sup, "price": price, "unit": unit, "tax_included": tax_inc if tax_known else None,
        "tax_rate": rate, "price_tax": round(conv, 2) if conv is not None else None,
        "landed": round(conv + (fadd or 0), 2) if conv is not None else None,
        "spec": str(q.get("spec") or ""), "delivery": str(q.get("delivery") or ""),
        "payment": str(q.get("payment") or ""), "freight": freight,
        "valid_until": vu, "source": str(q.get("source") or ""), "note": str(q.get("note") or ""), "alerts": notes,
    }


def compare(args: dict[str, Any]) -> dict[str, Any]:
    item = str(args.get("item") or "").strip()
    if not item:
        raise ValueError("item (采购品名) 不能为空")
    quotes = args.get("quotes") or []
    if not isinstance(quotes, list) or len(quotes) < 2:
        raise ValueError("至少要两家报价才能比; 只有一家可以用 procure_history 对照历史价")
    target = norm_unit(args.get("unit") or (quotes[0] or {}).get("unit") or "吨")
    tax = _num(args["tax_rate"], "税率") if args.get("tax_rate") not in (None, "") else DEFAULT_TAX
    qty = _num(args["qty"], "数量") if args.get("qty") not in (None, "") else None
    rows = [_norm_quote(q, target, tax) for q in quotes]
    # 按到厂价 (含税单价 + 另计运费) 排; 没有另计运费时跟含税单价一样
    ranked = sorted([r for r in rows if r["landed"] is not None], key=lambda r: r["landed"])
    if not ranked:
        raise ValueError(f"没有一条报价能换算成每{target}的价格, 检查单位")
    # 历史成交是含税到厂价 (台账一般就是这个口径), 跟 landed 比
    best = ranked[0]
    hist = history_for(item, target)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
        r["vs_best"] = round(r["landed"] - best["landed"], 2)
        r["vs_last"] = round(r["landed"] - hist["last"]["price_conv"], 2) if hist["last"] else None
    if len(ranked) >= 3:
        others = statistics.median(r["landed"] for r in ranked[1:])
        if best["landed"] < others * LOW_OUTLIER:
            best["alerts"].append(f"比其余报价中位数低 {1 - best['landed'] / others:.0%}, 先核实规格 / 质量 / 是否含运费")
    summary: dict[str, Any] = {
        "item": item, "unit": target, "qty": qty, "quotes": len(rows), "ranked": len(ranked),
        "best": {"supplier": best["supplier"], "price_tax": best["price_tax"], "landed": best["landed"]},
        "highest": {"supplier": ranked[-1]["supplier"], "price_tax": ranked[-1]["price_tax"], "landed": ranked[-1]["landed"]},
        "spread": round(ranked[-1]["landed"] - best["landed"], 2),
        "history": {k: hist[k] for k in ("avg", "min", "count", "days")},
        "last_deal": ({k: hist["last"][k] for k in ("date", "supplier", "price_conv")} if hist["last"] else None),
    }
    if qty:
        summary["save_vs_highest"] = round(summary["spread"] * qty, 2)
        if hist["last"]:
            summary["save_vs_last"] = round((hist["last"]["price_conv"] - best["landed"]) * qty, 2)
        if hist["avg"]:
            summary["save_vs_avg"] = round((hist["avg"] - best["landed"]) * qty, 2)
    if hist["last"] and best["landed"] > hist["last"]["price_conv"] * (1 + RISE_ALERT):
        summary["alert"] = (f"最低报价仍比上次成交 ({hist['last']['date']} {hist['last']['supplier']}) 高 "
                            f"{best['landed'] / hist['last']['price_conv'] - 1:.1%}, 可以拿上次价格去压价")
    from . import procure_xlsx  # noqa: PLC0415
    path = procure_xlsx.write_compare(item, target, qty, rows, ranked, summary, hist)
    rec = {"id": uuid.uuid4().hex[:10], "at": datetime.now().isoformat(timespec="seconds"), "path": str(path), **summary}
    log = _load("compares.json")
    log.append(rec)
    _save("compares.json", log[-500:])
    unranked = [r["supplier"] for r in rows if r["price_tax"] is None]
    return {"ok": True, "path": str(path), "summary": summary, "ranked": [
        {k: r[k] for k in ("rank", "supplier", "price_tax", "landed", "vs_best", "vs_last", "delivery", "payment", "freight", "alerts")}
        for r in ranked], "unranked": unranked,
        "note": "比价表只算价格。质量、交期、账期、运费请员工权衡; 定了跟谁买, 员工确认后用 procure_deal 记成交。不要替员工联系供应商。"}


def deal(args: dict[str, Any]) -> dict[str, Any]:
    item = str(args.get("item") or "").strip()
    sup = str(args.get("supplier") or "").strip()
    if not item or not sup:
        raise ValueError("记成交要有 item 和 supplier")
    if not args.get("confirmed"):
        return {"ok": False, "needs_confirmation": True,
                "proposal": f"记一笔成交: {item} · {sup} · {args.get('price')} 元/{args.get('unit') or '吨'} × {args.get('qty') or '?'}",
                "error": "员工明确说定了才记 (confirmed=true), 记进去就是以后比价的历史价"}
    unit = norm_unit(args.get("unit") or "吨")
    price = _num(args.get("price"), "成交价")
    tax_inc = args.get("tax_included") is not False
    rate = _num(args["tax_rate"], "税率") if args.get("tax_rate") not in (None, "") else DEFAULT_TAX
    row = {"id": uuid.uuid4().hex[:10], "date": str(args.get("date") or date.today().isoformat())[:10], "item": item,
           "spec": str(args.get("spec") or ""), "supplier": sup, "unit": unit,
           "price_tax": round(price if tax_inc else price * (1 + rate), 2),
           "qty": _num(args["qty"], "数量") if args.get("qty") not in (None, "") else None, "source": "手工记录"}
    hist = _load("history.json")
    hist.append(row)
    _save("history.json", hist)
    return {"ok": True, "recorded": row}


def history(args: dict[str, Any]) -> dict[str, Any]:
    item = str(args.get("item") or args.get("text") or "").strip()
    if not item:
        raise ValueError("要给 item (品名)")
    days = int(args.get("days") or 365)
    return {"ok": True, "item": item, "unit": norm_unit(args.get("unit") or "吨"),
            **history_for(item, norm_unit(args.get("unit") or "吨"), days=days)}


def dispatch(action: str, args: dict[str, Any]) -> dict[str, Any]:
    if action == "procure_compare":
        return compare(args)
    if action == "procure_deal":
        return deal(args)
    if action == "procure_history":
        return history(args)
    if action == "procure_import":
        from . import procure_import  # noqa: PLC0415
        return procure_import.import_ledger(str(args.get("path") or ""))
    raise ValueError("采购动作只有 procure_compare / procure_deal / procure_history / procure_import")
