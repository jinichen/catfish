"""跟进雷达 (销售跟进阶段 1, docs/SALES-FOLLOWUP-PLAN.md §3)。

只读客户档案 (customers.py) 的字段和时间线, 按销售模板里的天数出四类卡片:

  reply    客户在等我回: 最新一条是客户的问题, 之后我方没回, 超过 reply_wait_days 个工作日
  promise  我答应的事:   我方承诺到了截止日还没标 ✓; 或档案 next_action_due 过了
  cold     客户冷了:     last_contact (没有就用建档日 created) 超过该阶段的 cold_days (0 = 这个阶段不追)
  quote    报价没回音:   阶段 = quoted_stage, 最近一次我方报价之后客户没来过消息, 超过 quote_followup_days 天

判断"是不是问题 / 承诺"是模型记时间线时做的 (kind 字段); 这里是确定性规则, 同样的
档案永远出同样的卡, 每张卡带原话, 员工能当场核对。卡片 uid 稳定, 关掉后早安按 uid 过滤。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import customers

RULE_LABEL = {"reply": "客户在等你回复", "promise": "你答应的事到期了", "cold": "客户有段时间没动静了", "quote": "报价发出后没回音"}


def _d(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def workdays_between(start: date, end: date) -> int:
    """start 之后到 end (含) 有几个工作日 (周一到周五; 不认法定节假日, 宁可早提醒)。"""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _card(c: dict[str, Any], rule: str, anchor: str, quote: str, age: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "uid": f"cust:{c['name']}:{rule}:{anchor}",
        "rule": rule,
        "label": RULE_LABEL[rule],
        "customer": c["name"],
        "stage": c.get("stage"),
        "rel_path": c["rel_path"],
        "quote": quote,
        "age_days": age,
        "next_action": c.get("next_action"),
        **(extra or {}),
    }


def customer_cards(c: dict[str, Any], tpl: dict[str, Any], today: date) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    tl = [e for e in c["timeline"] if e["channel"] != customers.SYSTEM_CHANNEL]  # 改阶段 / 生成报价单不算回过客户
    stage = c.get("stage")
    cold_days = next((s["cold_days"] for s in tpl["stages"] if s["name"] == stage), 0)
    active = cold_days > 0  # 成交 / 输单 / 暂停 这类阶段不追

    # reply: 最新一条是客户的问题
    if tl and tl[0]["who"] == "客户" and tl[0]["kind"] == "问题":
        asked = _d(tl[0]["date"])
        if asked and workdays_between(asked, today) >= tpl["reply_wait_days"]:
            cards.append(_card(c, "reply", tl[0]["date"], tl[0]["text"], (today - asked).days, {"channel": tl[0]["channel"]}))

    # promise: 时间线里没标完成、截止已到的我方承诺
    for e in tl:
        due = _d(e.get("due"))
        if e["kind"] == "承诺" and not e["done"] and due and due <= today:
            cards.append(_card(c, "promise", e["due"], e["text"], (today - due).days, {"due": e["due"], "promise_text": e["text"]}))
    nad = _d(c.get("next_action_due"))
    if active and nad and nad <= today and c.get("next_action"):
        cards.append(_card(c, "promise", f"next:{c['next_action_due']}", str(c["next_action"]), (today - nad).days, {"due": c["next_action_due"]}))

    # quote: 报价阶段, 最近一次我方报价之后客户没来过消息
    if stage == tpl["quoted_stage"]:
        for i, e in enumerate(tl):
            if e["who"] == "我方" and e["kind"] == "报价":
                sent = _d(e["date"])
                customer_after = any(x["who"] == "客户" for x in tl[:i])
                if sent and not customer_after and (today - sent).days >= tpl["quote_followup_days"]:
                    cards.append(_card(c, "quote", e["date"], e["text"], (today - sent).days))
                break

    # cold: 客户最后一次来消息太久 (已经出了 reply / quote 的不重复报冷);
    # 建档后客户一次没来过消息的, 从建档日算 —— 否则这种客户永远不会被提醒
    anchor = c.get("last_contact") or c.get("created")
    last = _d(anchor)
    if active and last and (today - last).days >= cold_days and not any(x["rule"] in ("reply", "quote") for x in cards):
        latest = next((e["text"] for e in tl if e["who"] == "客户"), "") or "建档后客户还没来过消息"
        cards.append(_card(c, "cold", str(anchor)[:10], latest, (today - last).days, {"cold_days": cold_days}))
    return cards


def scan(today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    tpl = customers.load_template()
    cards: list[dict[str, Any]] = []
    all_c = customers.all_customers()
    for c in all_c:
        cards.extend(customer_cards(c, tpl, today))
    order = {"reply": 0, "promise": 1, "quote": 2, "cold": 3}
    cards.sort(key=lambda x: (order[x["rule"]], -x["age_days"]))
    return {"cards": cards, "customers": len(all_c), "template": tpl["name"]}
