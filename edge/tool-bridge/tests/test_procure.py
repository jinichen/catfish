"""采购比价 (procure / procure_xlsx / procure_import): 统一口径、排序、对照历史、算省多少、提醒。

全部虚构供应商 (示例供应商甲乙丙), 临时 CATFISH_HOME。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from openpyxl import Workbook, load_workbook

from catfish_tool_bridge import customers, procure


def c(**args):
    return customers.dispatch("catfish_customer", args)


def ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


QUOTES = [
    {"supplier": "示例供应商甲", "price": 6380, "unit": "元/吨", "tax_included": True, "delivery": "7天", "payment": "货到30天", "source": "甲 报价.xlsx"},
    {"supplier": "示例供应商乙", "price": 5.55, "unit": "元/kg", "tax_included": False, "payment": "款到发货", "freight": "不含运费"},
    {"supplier": "示例供应商丙", "price": 6450, "unit": "吨", "valid_until": ago(3)},
]


def test_统一口径_排序_提醒():
    r = c(action="procure_compare", item="硅锰合金 FeMn65Si17", quotes=QUOTES, qty=200)
    assert r["ok"], r
    ranked = r["ranked"]
    # 乙: 5.55 元/kg 不含税 → 5550 元/吨 × 1.13 = 6271.5 含税, 最低
    assert [x["supplier"] for x in ranked] == ["示例供应商乙", "示例供应商甲", "示例供应商丙"]
    assert ranked[0]["price_tax"] == 6271.5
    assert ranked[1]["vs_best"] == round(6380 - 6271.5, 2)
    alerts = {x["supplier"]: "；".join(x["alerts"]) for x in ranked}
    assert "不含税" in alerts["示例供应商乙"] and "换成每吨" in alerts["示例供应商乙"]
    assert "看不出金额" in alerts["示例供应商乙"], "「不含运费」没写多少钱: 不加, 但要提醒"
    assert "已过" in alerts["示例供应商丙"] and "没写含不含税" in alerts["示例供应商丙"]
    s = r["summary"]
    assert s["save_vs_highest"] == round((6450 - 6271.5) * 200, 2)
    assert s["last_deal"] is None and "save_vs_last" not in s
    assert "不替员工" in r["note"] or "不要替员工" in r["note"]


def test_对照历史成交_算比上次省多少_最低价还涨了要提醒(monkeypatch):
    assert c(action="procure_deal", item="硅锰合金", supplier="示例供应商甲", price=6000, qty=150, date=ago(20), confirmed=True)["ok"]
    assert c(action="procure_deal", item="硅锰合金 FeMn65Si17", supplier="示例供应商丙", price=6200, date=ago(60), confirmed=True)["ok"]
    r = c(action="procure_compare", item="硅锰合金 FeMn65Si17", quotes=QUOTES, qty=200)
    s = r["summary"]
    assert s["last_deal"]["supplier"] == "示例供应商甲" and s["last_deal"]["price_conv"] == 6000
    assert s["history"]["count"] == 2 and s["history"]["avg"] == 6100
    assert s["save_vs_last"] == round((6000 - 6271.5) * 200, 2)   # 负数 = 比上次贵
    assert "上次成交" in s["alert"] and "压价" in s["alert"]
    assert r["ranked"][0]["vs_last"] == round(6271.5 - 6000, 2)
    # 涨不到 3% 不提醒 (正常波动)
    assert procure.RISE_ALERT == 0.03


def test_低得反常要核实_单位换不了不参与排名():
    qs = [{"supplier": "甲", "price": 6400, "unit": "吨"}, {"supplier": "乙", "price": 6500, "unit": "吨"},
          {"supplier": "丙", "price": 5000, "unit": "吨"}, {"supplier": "丁", "price": 30, "unit": "袋"}]
    r = c(action="procure_compare", item="石灰", quotes=qs)
    assert r["ranked"][0]["supplier"] == "丙" and "核实" in "；".join(r["ranked"][0]["alerts"])
    assert r["unranked"] == ["丁"]


def test_比价表_Excel_内容():
    r = c(action="procure_compare", item="硅锰合金", quotes=QUOTES, qty=200)
    wb = load_workbook(r["path"])
    ws = wb["比价"]
    text = "\n".join(str(v) for row in ws.iter_rows(values_only=True) for v in row if v is not None)
    assert "采购比价 · 硅锰合金" in text and "到厂价最低" in text and "示例供应商乙" in text
    assert "选最低价比选最高价省" in text
    assert "历史成交" in wb.sheetnames
    assert "没有历史成交记录" in str(wb["历史成交"]["A2"].value)


def test_记成交要确认_少于两家不比():
    r = c(action="procure_deal", item="废钢", supplier="甲", price=2500)
    assert r["ok"] is False and r["needs_confirmation"] and "废钢" in r["proposal"]
    assert procure._load("history.json") == []
    r = c(action="procure_compare", item="废钢", quotes=[QUOTES[0]])
    assert r["ok"] is False and "两家" in r["error"]
    r = c(action="procure_compare", item="", quotes=QUOTES)
    assert r["ok"] is False


def test_导入采购台账_认表头_去重_不含税换算(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["2026年采购台账 (示例)"])
    ws.append(["采购日期", "供应商名称", "物料名称", "规格型号", "计量单位", "不含税单价", "数量"])
    ws.append([ago(30), "示例供应商甲", "硅锰合金", "FeMn65Si17", "吨", 5500, 100])
    ws.append([ago(10), "示例供应商乙", "硅锰合金", "FeMn65Si17", "吨", 5450, 120])
    ws.append([None, None, None, None, None, None, None])
    p = tmp_path / "台账.xlsx"
    wb.save(p)
    r = c(action="procure_import", path=str(p))
    assert r["ok"] and r["added"] == 2, r
    h = c(action="procure_history", item="硅锰合金")
    assert h["count"] == 2 and h["last"]["supplier"] == "示例供应商乙"
    assert h["last"]["price_tax"] == round(5450 * 1.13, 2)
    r2 = c(action="procure_import", path=str(p))
    assert r2["added"] == 0, "同一份台账导两次不记两遍"


def test_台账认不出表头时念给员工(tmp_path):
    wb = Workbook()
    wb.active.append(["日期", "金额", "摘要"])
    p = tmp_path / "x.xlsx"
    wb.save(p)
    r = c(action="procure_import", path=str(p))
    assert r["ok"] is False and "认不出表头" in r["error"] and "金额" in r["error"]


@pytest.mark.parametrize("u,expect", [("元/吨", "吨"), ("/t", "吨"), ("KG", "千克"), ("每公斤", "千克"), ("件", "件")])
def test_单位写法(u, expect):
    assert procure.norm_unit(u) == expect


def test_运费另计按到厂价排名():
    qs = [{"supplier": "甲", "price": 6380, "unit": "吨", "freight": "含运费到厂"},
          {"supplier": "乙", "price": 6300, "unit": "吨", "freight": "运费另计, 约120元/吨"},
          {"supplier": "丙", "price": 6400, "unit": "吨", "freight": "送到厂"}]
    r = c(action="procure_compare", item="硅锰合金", quotes=qs, qty=100)
    order = [(x["supplier"], x["price_tax"], x["landed"]) for x in r["ranked"]]
    assert order == [("甲", 6380, 6380), ("丙", 6400, 6400), ("乙", 6300, 6420)], "乙报价最低, 加上运费反而最贵"
    assert "加进到厂价" in "；".join(r["ranked"][2]["alerts"])
    assert r["summary"]["best"]["supplier"] == "甲" and r["summary"]["save_vs_highest"] == 4000


@pytest.mark.parametrize("text,expect", [("含运费到厂", 0.0), ("送到厂", 0.0), ("运费另计约60元/吨", 60.0),
                                         ("不含运费, 加收 0.05 元/公斤", 50.0), ("不含运费", None), ("", None)])
def test_运费写法(text, expect):
    assert procure.freight_add(text, "吨") == expect
