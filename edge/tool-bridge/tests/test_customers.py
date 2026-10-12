"""销售跟进阶段 1: 客户档案 (customers.py) + 跟进雷达 (customer_radar.py)。

全部虚构客户, 临时 CATFISH_HOME, 不碰员工真知识库。
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from catfish_tool_bridge import customer_radar, customers, wiki_files
from catfish_tool_bridge.catfish_tool_schemas import CATFISH_NATIVE_TOOLS
from catfish_tool_bridge.catfish_tools import dispatch_native, is_native


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    return tmp_path


def c(**args):
    return customers.dispatch("catfish_customer", args)


def make(name="南岭建设", stage="线索", **extra):
    extra.setdefault("contacts", [{"name": "周工", "email": "zhou@nanling-build.cn", "wechat": "南岭周工"}])
    r = c(action="upsert", name=name, stage=stage, confirmed=True, **extra)
    assert r["ok"], r
    return r


# ── 销售模板 ──

def test_default_template_and_user_override(home):
    assert customers.stage_names()[:2] == ["线索", "需求确认"]
    customers.save_template({"name": "包装销售", "stages": [{"name": "询价", "cold_days": 10}, {"name": "打样", "cold_days": 5},
                                                         {"name": "报价", "cold_days": 7}], "quoted_stage": "报价"})
    tpl = customers.load_template()
    assert tpl["name"] == "包装销售" and customers.stage_names(tpl) == ["询价", "打样", "报价"]
    assert tpl["reply_wait_days"] == 1  # 没给的字段用默认补


def test_template_validation_and_broken_file_falls_back(home):
    with pytest.raises(ValueError):
        customers.save_template({"stages": [{"name": "A"}, {"name": "A"}], "quoted_stage": "A"})
    with pytest.raises(ValueError):
        customers.save_template({"stages": [{"name": "A"}], "quoted_stage": "B"})
    (home / "sales_template.json").write_text("{坏的", encoding="utf-8")
    assert customers.load_template()["name"] == "鲶鱼销售"


# ── 建档 / 改阶段要确认 ──

def test_new_customer_needs_confirmation(home):
    r = c(action="upsert", name="恒远检测", stage="线索")
    assert r["needs_confirmation"] and r["proposal"]["name"] == "恒远检测"
    assert c(action="list")["count"] == 0
    make("恒远检测")
    rows = c(action="list")["customers"]
    assert [x["name"] for x in rows] == ["恒远检测"] and rows[0]["stage"] == "线索"
    # 知识库里就是一个 customer 实体页
    listed = wiki_files.list_wiki_files("entity")
    assert any("恒远检测" in json.dumps(x, ensure_ascii=False) for x in listed.get("items", []))


def test_stage_change_needs_confirmation_and_must_be_in_template(home):
    make()
    assert c(action="upsert", name="南岭建设", stage="已报价")["needs_confirmation"]
    assert c(action="get", customer="南岭建设")["customer"]["stage"] == "线索"
    assert c(action="upsert", name="南岭建设", stage="已报价", confirmed=True)["customer"]["stage"] == "已报价"
    assert "不在销售模板" in c(action="upsert", name="南岭建设", stage="随便写", confirmed=True)["error"]


# ── 时间线 ──

def test_log_formats_dedups_and_updates_last_contact(home):
    make()
    r = c(action="log", customer="南岭建设", date="2026-10-10", channel="微信", who="客户", kind="问题", text="私有化部署要几台服务器?")
    assert r["logged"] == "- 2026-10-10 · 微信 · 客户 · 问题 · 私有化部署要几台服务器?"
    assert c(action="log", customer="南岭建设", date="2026-10-10", channel="微信", who="客户", kind="问题",
             text="私有化部署要几台服务器?")["duplicate"]
    c(action="log", customer="南岭建设", date="2026-10-11", channel="邮件", who="我方", kind="信息", text="发了部署说明")
    got = c(action="get", customer="南岭建设")["customer"]
    assert got["last_contact"] == "2026-10-10"  # 我方单方面发不算"联系上"
    assert [e["date"] for e in got["timeline"]] == ["2026-10-11", "2026-10-10"]  # 新的在上


def test_log_rejects_bad_fields_and_promise_without_due(home):
    make()
    assert "channel" in c(action="log", customer="南岭建设", channel="短信", who="客户", kind="问题", text="x")["error"]
    assert "due" in c(action="log", customer="南岭建设", channel="微信", who="我方", kind="承诺", text="明天发报价")["error"]


def test_unknown_customer_is_not_guessed(home):
    make("南岭建设")
    make("南岭物流")
    r = c(action="log", customer="南岭", channel="微信", who="客户", kind="信息", text="x")
    assert not r["ok"] and "南岭建设" in r["error"] and "不要把消息记到别的客户上" in r["error"]
    r = c(action="log", customer="科瑞认证", channel="微信", who="客户", kind="信息", text="x")
    assert not r["ok"] and "upsert" in r["error"]


def test_find_by_email_domain_but_not_public_mailboxes(home):
    make()
    assert customers.find(email="li@nanling-build.cn")[0]["name"] == "南岭建设"
    c(action="upsert", name="南岭建设", contacts=[{"name": "王总", "email": "wang@qq.com"}])
    make("恒远检测", contacts=[{"name": "陈立", "email": "chen@qq.com"}])
    assert customers.find(email="someone@qq.com")[0] is None  # qq.com 不能当成同一家公司
    assert customers.find(wechat="南岭周工")[0] is None  # 联系人已整组替换


def test_done_marks_promise(home):
    make()
    c(action="log", customer="南岭建设", channel="邮件", who="我方", kind="承诺", text="周三前发正式报价", due="2026-10-14")
    r = c(action="done", customer="南岭建设", text="正式报价")
    assert r["ok"] and r["done"].endswith("· 截止 2026-10-14 · ✓")
    assert not c(action="done", customer="南岭建设", text="正式报价")["ok"]  # 已完成的不再匹配


# ── 跟进雷达 ──

def scan(day):
    return customer_radar.scan(date.fromisoformat(day))["cards"]


def test_radar_reply_waits_one_workday(home):
    make()
    c(action="log", customer="南岭建设", date="2026-10-09", channel="微信", who="客户", kind="问题", text="能先试用吗?")
    assert scan("2026-10-09") == []  # 当天不催
    assert [x["rule"] for x in scan("2026-10-12")] == ["reply"]  # 周五问, 周一 = 1 个工作日
    c(action="log", customer="南岭建设", date="2026-10-12", channel="微信", who="我方", kind="信息", text="可以, 发了试用账号")
    assert scan("2026-10-13") == []  # 回过了


def test_radar_promise_due_and_quote_silence(home):
    make(stage="已报价")
    c(action="log", customer="南岭建设", date="2026-10-01", channel="邮件", who="我方", kind="报价", text="年费 12 万 (v1)")
    c(action="log", customer="南岭建设", date="2026-10-02", channel="微信", who="我方", kind="承诺", text="下周给部署方案", due="2026-10-09")
    rules = sorted(x["rule"] for x in scan("2026-10-10"))
    assert rules == ["promise", "quote"]
    card = next(x for x in scan("2026-10-10") if x["rule"] == "promise")
    assert card["quote"] == "下周给部署方案" and card["uid"] == "cust:南岭建设:promise:2026-10-09"
    c(action="log", customer="南岭建设", date="2026-10-10", channel="微信", who="客户", kind="信息", text="收到报价, 内部在评")
    assert [x["rule"] for x in scan("2026-10-10")] == ["promise"]  # 客户回了, 报价不再算没回音


def test_radar_cold_per_stage_and_closed_stages_ignored(home):
    make("南岭建设", stage="商务谈判")
    make("恒远检测", stage="成交")
    for n in ("南岭建设", "恒远检测"):
        c(action="log", customer=n, date="2026-10-01", channel="微信", who="客户", kind="信息", text="好的我们再看看")
    cards = scan("2026-10-05")  # 谈判 3 天算冷; 成交不追
    assert [(x["customer"], x["rule"]) for x in cards] == [("南岭建设", "cold")]
    assert cards[0]["quote"] == "好的我们再看看" and cards[0]["age_days"] == 4


def test_radar_uses_template_days(home):
    customers.save_template({"stages": [{"name": "询价", "cold_days": 30}], "quoted_stage": "询价"})
    make(stage="询价")
    c(action="log", customer="南岭建设", date="2026-10-01", channel="微信", who="客户", kind="信息", text="先发个目录")
    assert scan("2026-10-20") == []
    assert [x["rule"] for x in scan("2026-11-01")] == ["cold"]


# ── 接线 ──

def test_tool_is_registered_and_routed(home):
    assert any(t["name"] == "catfish_customer" for t in CATFISH_NATIVE_TOOLS)
    assert is_native("catfish_customer")
    assert dispatch_native("catfish_customer", {"action": "template"})["template"]["name"] == "鲶鱼销售"
    assert dispatch_native("catfish_customer", {"action": "nope"})["ok"] is False


# ── Companion 走的 RPC (早安「客户跟进」/ 设置「销售模板」) ──

def rpc(method, params=None):
    import asyncio

    from catfish_tool_bridge import server

    return asyncio.run(server._handle_request({"id": 1, "method": method, "params": params or {}}))


def test_rpc_radar_and_template_roundtrip(home):
    make(stage="商务谈判")
    c(action="log", customer="南岭建设", date="2000-01-03", channel="微信", who="客户", kind="信息", text="再说吧")
    r = rpc("pim/customer_radar")["result"]
    assert r["customers"] == 1 and [x["rule"] for x in r["cards"]] == ["cold"]
    assert rpc("pim/sales_template_get")["result"]["quoted_stage"] == "已报价"
    tpl = {"name": "包装", "stages": [{"name": "询价", "cold_days": 9}], "quoted_stage": "询价",
           "reply_wait_days": 2, "quote_followup_days": 4}
    assert rpc("pim/sales_template_set", {"template": tpl})["result"]["stages"] == [{"name": "询价", "cold_days": 9}]
    assert "error" in rpc("pim/sales_template_set", {"template": {"stages": [], "quoted_stage": "x"}})
