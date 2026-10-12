"""发布销售漏斗给主管 (销售跟进阶段 3, docs/SALES-FOLLOWUP-PLAN.md §11)。

客户档案只在本机。员工在设置「🤝 销售跟进」里点「发布给主管」, 才把**结构化摘要**发到自己部门的
wiki-hub —— 走「员工主动 push」例外, 中央端从不来拉。摘要里只有:
  阶段 / 预计金额 / 最近联系日 / 下一步 / 跟进提醒类型 / 报价次数和最近一次金额
**没有**时间线原话、聊天内容、联系人电话邮箱。发出去之前 Companion 先给员工看 preview。

hub 端 (central/wiki-hub sales.py) 把这类条目对同部门同事隐藏, 只给本人 / 部门主管 / admin;
中央 web 部门页按部门汇总成全组漏斗。同一个人重发覆盖上一份 (file_id 记在 sales/publish_state.json)。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from . import customer_radar, customers, sales_pricebook, sales_quote, sales_weekly, skill_publish

SALES_FILENAME = "catfish/sales-summary.md"  # 跟 hub sales.SALES_FILENAME 一致, hub 靠它认
TITLE = "销售漏斗摘要"


def _state_path():
    return sales_pricebook.sales_dir() / "publish_state.json"


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data: dict[str, Any]) -> None:
    tmp = _state_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_state_path())


def build_summary(today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    week = sales_weekly.build(today)
    flags: dict[str, list[str]] = {}
    for card in customer_radar.scan(today)["cards"]:
        flags.setdefault(card["customer"], []).append(card["label"])
    ledger = sales_quote.load_ledger()
    rows = []
    for c in customers.all_customers():
        quotes = sorted((x for x in ledger if x.get("customer") == c["name"]), key=lambda x: x.get("version", 0))
        last = c.get("last_contact")
        try:
            silent = (today - date.fromisoformat(str(last)[:10])).days if last else None
        except ValueError:
            silent = None
        rows.append({
            "name": c["name"], "stage": c.get("stage"), "owner": c.get("owner") or "",
            "deal_size": c.get("deal_size") or "", "deal_value": sales_weekly.deal_value(c.get("deal_size")),
            "last_contact": last or "", "days_since_contact": silent,
            "next_action": c.get("next_action") or "", "next_action_due": c.get("next_action_due") or "",
            "flags": sorted(set(flags.get(c["name"], []))),
            "quotes": len(quotes), "latest_quote_total": quotes[-1]["total"] if quotes else None,
        })
    owners = sorted({r["owner"] for r in rows if r["owner"]})
    return {
        "schema": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "owner": "、".join(owners) or sales_pricebook.load_quote_settings()["contact"],
        "template": week["template"],
        "funnel": week["funnel"],
        "week": {"start": week["week_start"], "new_customers": len(week["new_customers"]),
                 "stage_changes": week["stage_changes"], "quotes_made": len(week["quotes_made"]),
                 "quotes_sent": len(week["quotes_sent"])},
        "followup": week["followup"],
        "customers": rows,
    }


def _body(s: dict[str, Any]) -> str:
    lines = [f"# {TITLE} · {s['owner'] or '未署名'}", "", f"生成于 {s['generated_at'].replace('T', ' ')}", "",
             "| 阶段 | 客户数 | 预计金额 |", "|---|---|---|"]
    lines += [f"| {f['stage']} | {f['count']} | {f['deal_total'] or ''} |" for f in s["funnel"]]
    lines += ["", "| 客户 | 阶段 | 预计金额 | 最近联系 | 下一步 | 截止 | 提醒 |", "|---|---|---|---|---|---|---|"]
    esc = lambda v: str(v or "").replace("|", "/")  # noqa: E731
    lines += [f"| {esc(r['name'])} | {esc(r['stage'])} | {esc(r['deal_size'])} | {esc(r['last_contact'])} | "
              f"{esc(r['next_action'])} | {esc(r['next_action_due'])} | {esc('、'.join(r['flags']))} |" for r in s["customers"]]
    return "\n".join(lines) + "\n"


def preview() -> dict[str, Any]:
    s = build_summary()
    st = load_state()
    return {"summary": s, "published_at": st.get("published_at"), "namespace": st.get("namespace")}


def _http(method: str, url: str, payload: dict[str, Any] | None) -> tuple[int, Any]:
    import httpx  # noqa: PLC0415

    token = skill_publish._read_id_token()
    if not token:
        raise ValueError("还没登录鲶鱼账号 (Companion 里先登录), 发布要用你的身份")
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.request(method, url, headers={"Authorization": f"Bearer {token}"}, json=payload)
    except httpx.RequestError as e:
        raise ValueError(f"连不上中央服务: {e}") from None
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"detail": r.text[:300]}


def publish() -> dict[str, Any]:
    s = build_summary()
    if not s["customers"]:
        raise ValueError("还没有客户档案, 没东西可发布")
    fm = "\n".join([f"title: {TITLE}", "type: query", "catfish_doc: sales_summary",
                    "sales_summary: " + json.dumps(s, ensure_ascii=False)])
    payload: dict[str, Any] = {"filename": SALES_FILENAME, "title": f"{TITLE} · {s['owner'] or '未署名'}", "kind": "query",
                               "frontmatter_yaml": fm, "body_md": _body(s)}
    st = load_state()
    if st.get("file_id"):
        payload["file_id"] = st["file_id"]
    code, data = _http("POST", f"{skill_publish.GATEWAY_URL}/v1/wiki/documents", payload)
    if code >= 400:
        raise ValueError(f"发布失败 ({code}): {data.get('detail') if isinstance(data, dict) else data}")
    st = {"file_id": data.get("file_id"), "namespace": data.get("namespace"), "published_at": s["generated_at"],
          "customers": len(s["customers"])}
    _save_state(st)
    return {"ok": True, **st}


def unpublish() -> dict[str, Any]:
    st = load_state()
    if not st.get("file_id") or not st.get("namespace"):
        return {"ok": True, "unpublished": False}
    from . import wiki_publish  # noqa: PLC0415

    url = wiki_publish.wiki_doc_url(st["namespace"], st["file_id"], "/unpublish")
    code, data = _http("POST", url, {"reason": "销售撤回漏斗摘要"})
    if code >= 400 and code != 404:
        raise ValueError(f"撤回失败 ({code}): {data.get('detail') if isinstance(data, dict) else data}")
    _state_path().unlink(missing_ok=True)
    return {"ok": True, "unpublished": True}
