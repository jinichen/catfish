"""知识页的「待审」标 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §3.2)。

知识页是纯文字, 不强审: 发了同部门立刻可见、可装, 但带「待审」标; 部门主管 (或 admin) 看过打钩变「已审」,
也能一键撤下 (撤下 = 原来的 unpublish + 标 withdrawn, 装过的人那边显示「已撤回」)。重发 (内容变了) 回到待审。

状态存文件系统 sidecar <hub root>/wiki-review/<namespace>/<file_id>.json, 不碰 PG 表:
  {review_status: pending | approved | withdrawn, reviewed_by, reviewed_at, review_note, withdrawn_by, withdrawn_at}
没有 sidecar 的 = 上线前发的, 算已审 (不回溯)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import storage
from .namespaces import dept_of, is_admin

STATUSES = ("pending", "approved", "withdrawn")


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


def mark_pending(namespace: str, file_id: str, by: str) -> dict[str, Any]:
    """发布 / 重发之后: 回到待审 (主管看过的内容变了, 要再看一眼)。"""
    rec = {"review_status": "pending", "submitted_by": by, "submitted_at": _now()}
    _save(namespace, file_id, rec)
    return rec


def decide(user: dict, namespace: str, file_id: str, decision: str, note: str = "") -> dict[str, Any]:
    if not can_review(user, namespace):
        raise PermissionError("只有这个部门的主管或管理员能审知识页")
    if decision not in ("approve", "withdraw"):
        raise ValueError("decision 只能是 approve (看过, 没问题) / withdraw (撤下)")
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


def annotate(doc: dict[str, Any]) -> dict[str, Any]:
    rec = load(doc["namespace"], doc["file_id"])
    out = {**doc, "review_status": rec.get("review_status", "approved")}
    for k in ("reviewed_by", "reviewed_at", "review_note", "withdrawn_by", "withdrawn_at", "legacy"):
        if k in rec:
            out[k] = rec[k]
    return out


def pending_for(user: dict) -> list[dict[str, Any]]:
    """待我审的知识页 (主管: 管的部门; admin: 全部)。"""
    out = []
    for p in _root().glob("**/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("review_status") != "pending":
            continue
        namespace = p.parent.relative_to(_root()).as_posix()
        if not can_review(user, namespace):
            continue
        doc = storage.get_document(namespace, p.stem)
        if doc is None or doc.get("stale_after_unpublish"):
            continue
        doc.pop("body_md", None)
        out.append({**doc, **{k: rec[k] for k in ("review_status", "submitted_by", "submitted_at") if k in rec}})
    return sorted(out, key=lambda d: d.get("submitted_at") or "")
