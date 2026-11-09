"""知识页的「待审」标 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §3.2)。

知识页是纯文字, 不强审: 发了同部门立刻可见、可装, 但带「待审」标; 部门主管 (或 admin) 看过打钩变「已审」,
也能一键撤下 (撤下 = 原来的 unpublish + 标 withdrawn, 装过的人那边显示「已撤回」)。重发 (内容变了) 回到待审。

状态存文件系统 sidecar <hub root>/wiki-review/<namespace>/<file_id>.json, 不碰 PG 表:
  {review_status: pending | approved | withdrawn, reviewed_by, reviewed_at, review_note, withdrawn_by, withdrawn_at,
   scope: department | company, company_status: pending | approved | rejected,
   company_requested_by/at, company_reviewed_by/at, company_note}
没有 sidecar 的 = 上线前发的, 算已审 (不回溯)。

全公司范围 (11/09, 鸿波「WIKI 也应该支持部门 / 公司分享才合理吧」):
  页面始终存在发布人自己部门的 namespace 里 (链接、安装路径、同事那边的副本都不变), scope 只是多一档可见范围。
  选「全公司」= 本部门照常马上可见 (带待审), 同时向管理员申请全公司可见; 管理员通过前其他部门看不到 ——
  知识页常带经营数字, 不能发了就全公司可见。跟技能 / 专家包「全公司由管理员审」同一个口径。
  内容改了重发 → 全公司那一档也回到待审 (管理员看过的不是现在这份了)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import storage
from .namespaces import dept_of, is_admin

STATUSES = ("pending", "approved", "withdrawn")
SCOPES = ("department", "company")
COMPANY_DECISIONS = ("approve_company", "reject_company")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root() -> Path:
    d = storage._wiki_dir().parent / "wiki-review"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(namespace: str, file_id: str) -> Path:
    storage._validate_namespace(namespace)
    storage._validate_seg(file_id, "file_id")
    return _root() / namespace / f"{file_id}.json"


def load(namespace: str, file_id: str) -> dict[str, Any]:
    try:
        return json.loads(_path(namespace, file_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"review_status": "approved", "legacy": True}


def _save(namespace: str, file_id: str, rec: dict[str, Any]) -> None:
    p = _path(namespace, file_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def can_review(user: dict, namespace: str) -> bool:
    dept = dept_of(namespace)
    return is_admin(user) or (user.get("role") == "manager" and bool(dept) and dept in (user.get("managed") or []))


def can_review_company(user: dict) -> bool:
    return is_admin(user)


def company_visible(rec: dict[str, Any]) -> bool:
    return rec.get("scope") == "company" and rec.get("company_status") == "approved" \
        and rec.get("review_status") != "withdrawn"


def _company_request(rec: dict[str, Any], by: str) -> None:
    rec.update(scope="company", company_status="pending", company_requested_by=by, company_requested_at=_now())
    for k in ("company_reviewed_by", "company_reviewed_at", "company_note"):
        rec.pop(k, None)


def _drop_company(rec: dict[str, Any]) -> None:
    rec["scope"] = "department"
    for k in list(rec):
        if k.startswith("company_"):
            rec.pop(k)


def mark_pending(namespace: str, file_id: str, by: str, scope: str = "department") -> dict[str, Any]:
    """发布 / 重发之后: 回到待审 (主管看过的内容变了, 要再看一眼); 选了全公司的, 全公司那档也回到待审。"""
    if scope not in SCOPES:
        raise ValueError("范围只能是 department (本部门) 或 company (全公司)")
    rec: dict[str, Any] = {"review_status": "pending", "submitted_by": by, "submitted_at": _now(), "scope": "department"}
    if scope == "company":
        _company_request(rec, by)
    _save(namespace, file_id, rec)
    if scope == "company":
        storage._write_audit({"ts": _now(), "event": "company_request", "namespace": namespace, "file_id": file_id,
                              "by_user": by, "line": "wiki"})
    return rec


def set_scope(user: dict, namespace: str, file_id: str, scope: str) -> dict[str, Any]:
    """不重发, 只改范围: 申请全公司可见 / 收回到只在本部门。调用方已经核过是作者或 admin。"""
    if scope not in SCOPES:
        raise ValueError("范围只能是 department (本部门) 或 company (全公司)")
    rec = load(namespace, file_id)
    rec.pop("legacy", None)
    if rec.get("review_status") == "withdrawn":
        raise ValueError("这一页已经撤下了, 不能再改范围")
    if scope == "company":
        if rec.get("scope") == "company" and rec.get("company_status") in ("pending", "approved"):
            return rec   # 已经在申请 / 已经全公司可见
        _company_request(rec, user.get("sub") or "")
    else:
        if rec.get("scope") != "company":
            return rec
        _drop_company(rec)
    _save(namespace, file_id, rec)
    storage._write_audit({"ts": _now(), "event": "company_request" if scope == "company" else "company_revoke",
                          "namespace": namespace, "file_id": file_id, "by_user": user.get("sub"), "line": "wiki"})
    return rec


def decide(user: dict, namespace: str, file_id: str, decision: str, note: str = "") -> dict[str, Any]:
    if decision in COMPANY_DECISIONS:
        return _decide_company(user, namespace, file_id, decision, note)
    if not can_review(user, namespace):
        raise PermissionError("只有这个部门的主管或管理员能审知识页")
    if decision not in ("approve", "withdraw"):
        raise ValueError("decision 只能是 approve (看过, 没问题) / withdraw (撤下) / approve_company / reject_company")
    rec = load(namespace, file_id)
    rec.pop("legacy", None)
    note = str(note or "")[:500]
    if decision == "approve":
        rec.update(review_status="approved", reviewed_by=user.get("sub"), reviewed_at=_now(), review_note=note)
    else:
        if not note.strip():
            raise ValueError("撤下要写原因, 会显示给发布人和装过的同事")
        r = storage.unpublish_document(namespace=namespace, file_id=file_id, unpublished_by=user.get("sub") or "", reason=note)
        if not r.get("ok"):
            raise ValueError(r.get("error") or "撤下失败")
        rec.update(review_status="withdrawn", withdrawn_by=user.get("sub"), withdrawn_at=_now(), review_note=note)
    _save(namespace, file_id, rec)
    storage._write_audit({"ts": _now(), "event": "review_" + decision, "namespace": namespace, "file_id": file_id,
                          "by_user": user.get("sub"), "note": note, "line": "wiki"})
    return rec


def _decide_company(user: dict, namespace: str, file_id: str, decision: str, note: str) -> dict[str, Any]:
    if not can_review_company(user):
        raise PermissionError("全公司范围由管理员审")
    rec = load(namespace, file_id)
    if rec.get("scope") != "company" or rec.get("company_status") != "pending":
        raise ValueError("这一页没有在申请全公司可见")
    note = str(note or "")[:500]
    if decision == "reject_company" and not note.strip():
        raise ValueError("驳回要写原因, 会显示给发布人")
    rec.update(company_status="approved" if decision == "approve_company" else "rejected",
               company_reviewed_by=user.get("sub"), company_reviewed_at=_now(), company_note=note)
    _save(namespace, file_id, rec)
    storage._write_audit({"ts": _now(), "event": "review_" + decision, "namespace": namespace, "file_id": file_id,
                          "by_user": user.get("sub"), "note": note, "line": "wiki"})
    return rec


_COPY_KEYS = ("reviewed_by", "reviewed_at", "review_note", "withdrawn_by", "withdrawn_at", "legacy",
              "company_status", "company_requested_by", "company_requested_at",
              "company_reviewed_by", "company_reviewed_at", "company_note")


def annotate(doc: dict[str, Any], rec: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = rec if rec is not None else load(doc["namespace"], doc["file_id"])
    out = {**doc, "review_status": rec.get("review_status", "approved"), "scope": rec.get("scope") or "department",
           # 现在谁看得到: company = 全公司 (管理员已通过); department = 只有发布人所在部门
           "visible_to": "company" if company_visible(rec) else "department"}
    for k in _COPY_KEYS:
        if k in rec:
            out[k] = rec[k]
    return out


def pending_for(user: dict) -> list[dict[str, Any]]:
    """待我审的知识页 (主管: 管的部门; admin: 全部 + 申请全公司可见的)。

    每条带 needs: ["department"] / ["company"] / 两个都有 —— 一页可能部门那档和全公司那档都等着审。"""
    out = []
    for p in _root().glob("**/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        namespace = p.parent.relative_to(_root()).as_posix()
        needs = []
        if rec.get("review_status") == "pending" and can_review(user, namespace):
            needs.append("department")
        if rec.get("scope") == "company" and rec.get("company_status") == "pending" \
                and rec.get("review_status") != "withdrawn" and can_review_company(user):
            needs.append("company")
        if not needs:
            continue
        doc = storage.get_document(namespace, p.stem)
        if doc is None or doc.get("stale_after_unpublish"):
            continue
        doc.pop("body_md", None)
        out.append({**annotate(doc, rec), "needs": needs,
                    **{k: rec[k] for k in ("submitted_by", "submitted_at") if k in rec}})
    return sorted(out, key=lambda d: d.get("submitted_at") or d.get("company_requested_at") or "")


def company_keys() -> set[tuple[str, str]]:
    """全公司可见 (管理员已通过) 的 (namespace, file_id)。"""
    out = set()
    for p in _root().glob("**/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if company_visible(rec):
            out.add((p.parent.relative_to(_root()).as_posix(), p.stem))
    return out
