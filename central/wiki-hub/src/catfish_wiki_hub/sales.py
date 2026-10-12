"""销售漏斗汇总 (销售跟进阶段 3, docs/SALES-FOLLOWUP-PLAN.md §11)。

客户档案只在销售本机。销售在 Companion 里点「发布给主管」, 把**结构化摘要** (阶段 / 金额 /
下一步 / 跟进提醒, 不含任何聊天原文) 当成一条普通 wiki 发到自己部门 —— 走员工主动 push 的例外,
中央端不去拉。这里做两件事:

1. 这类条目 (filename = SALES_FILENAME) 在部门知识库里**只给发布者本人、管这个部门的主管、admin 看**,
   同部门同事看不到彼此的客户和金额。
2. `GET /wiki/sales/dept/{部门}`: 把该部门每个销售最新的一份摘要解析出来给中央 web 的部门页画漏斗。
   只有 admin 和 managed_departments 含这个部门的 manager 能调。

摘要放在 frontmatter 的 `sales_summary:` 一行 JSON 里 (见 edge tool-bridge sales_publish.py),
hub 不建新表, 存储跟其它 wiki 一样。
"""
from __future__ import annotations

import json
from typing import Any

from .namespaces import dept_of, is_admin

SALES_FILENAME = "catfish/sales-summary.md"
SUMMARY_KEY = "sales_summary:"


def can_manage(user: dict, dept: str) -> bool:
    """跟 gateway User.can_manage_department 同口径: admin 全部, manager 限 managed_departments。"""
    if is_admin(user):
        return True
    return user.get("role") == "manager" and dept in (user.get("managed") or [])


def is_sales_summary(meta: dict) -> bool:
    return (meta.get("filename") or "") == SALES_FILENAME


def hidden_from(user: dict, meta: dict) -> bool:
    """销售摘要对同部门普通同事隐藏。"""
    if not is_sales_summary(meta):
        return False
    if meta.get("published_by") and meta.get("published_by") == user.get("sub"):
        return False
    dept = dept_of(meta.get("namespace") or "") or ""
    return not can_manage(user, dept)


def parse_summary(frontmatter_yaml: str) -> dict[str, Any] | None:
    for line in (frontmatter_yaml or "").splitlines():
        if line.startswith(SUMMARY_KEY):
            try:
                data = json.loads(line[len(SUMMARY_KEY):].strip())
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def department_funnel(docs: list[dict]) -> dict[str, Any]:
    """docs: 该部门的销售摘要全文 (含 frontmatter_yaml)。同一个人发过多份只取最新。"""
    latest: dict[str, dict] = {}
    for d in docs:
        who = d.get("published_by") or ""
        if who not in latest or (d.get("updated_at") or "") > (latest[who].get("updated_at") or ""):
            latest[who] = d
    members, stage_order, totals = [], [], {}
    for who, d in sorted(latest.items()):
        s = parse_summary(d.get("frontmatter_yaml") or "")
        if s is None:
            continue
        members.append({"published_by": who, "updated_at": d.get("updated_at"), "file_id": d.get("file_id"), **s})
        for st in s.get("funnel") or []:
            name = str(st.get("stage") or "")
            if name not in totals:
                stage_order.append(name)
                totals[name] = {"stage": name, "count": 0, "deal_total": 0.0}
            totals[name]["count"] += int(st.get("count") or 0)
            totals[name]["deal_total"] += float(st.get("deal_total") or 0)
    return {"members": members, "funnel": [totals[n] for n in stage_order]}
