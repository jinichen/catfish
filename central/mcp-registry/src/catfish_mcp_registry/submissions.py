"""员工提交 MCP 连接器 → 审批 → 上架 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §3.4)。

以前只有管理员往 manifests/ 目录里放 yaml, 员工不能自己发。现在:
  POST /v1/mcp/submit  员工交一份 manifest (yaml 文本) + 范围 (department / company) + 说明 + 测试账号怎么拿
  → <manifests dir>/_submissions/<id>.yaml + <id>.json (审核记录, status=pending)
  → 部门主管 (公司范围由 admin) 审: approve → 写进 manifests/<id>.yaml (allowed_dept 按范围填), registry 重新加载; reject 写原因
审批人试用: 没有 trial_credentials (测试账号怎么拿) 的不能试 —— 不允许审批人用自己的生产凭据试别人交的连接器。
试用 = 审批人订阅还在待审的连接器 (app.subscribe 里放行), 订阅记录标 trial。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .models import McpManifest

SCOPES = ("department", "company")
_ID = re.compile(r"^[a-z][a-z0-9\-]{1,40}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_admin(user: dict) -> bool:
    return user.get("role") in ("admin", "sysadmin")


def can_review(user: dict, rec: dict) -> bool:
    if is_admin(user):
        return True
    if rec.get("scope") == "company":
        return False
    return user.get("role") == "manager" and bool(set(user.get("managed") or []) & set(rec.get("departments") or []))


class Submissions:
    def __init__(self, manifests_dir: Path):
        self.dir = manifests_dir / "_submissions"

    def _paths(self, sid: str) -> tuple[Path, Path]:
        if not _ID.match(sid):
            raise ValueError("连接器 id 只能小写字母 / 数字 / -")
        return self.dir / f"{sid}.yaml", self.dir / f"{sid}.json"

    def load(self, sid: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._paths(sid)[1].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def manifest(self, sid: str) -> McpManifest | None:
        try:
            raw = yaml.safe_load(self._paths(sid)[0].read_text(encoding="utf-8"))
            return McpManifest.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None

    def _save(self, rec: dict[str, Any]) -> None:
        _, p = self._paths(rec["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def all(self) -> list[dict[str, Any]]:
        out = []
        if not self.dir.is_dir():
            return out
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    # ── 动作 ──

    def submit(self, user: dict, manifest_yaml: str, *, scope: str, departments: list[str], note: str,
               trial_credentials: str, existing_ids: set[str]) -> dict[str, Any]:
        if scope not in SCOPES:
            raise ValueError("范围只能是 department (本部门) 或 company (全公司)")
        try:
            raw = yaml.safe_load(manifest_yaml)
            man = McpManifest.model_validate(raw)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"manifest 不合法: {e}") from None
        departments = [d for d in dict.fromkeys(str(x).strip() for x in departments or []) if d]
        if scope == "department" and not departments:
            departments = [user.get("dept")] if user.get("dept") else []
            if not departments:
                raise ValueError("部门范围的连接器要写给哪些部门用 (你的账号没有部门)")
        if man.id in existing_ids:
            raise ValueError(f"连接器 {man.id} 已经上架了, 换个 id 或者请管理员改")
        prev = self.load(man.id)
        if prev and prev.get("status") == "pending":
            raise ValueError(f"连接器 {man.id} 已有一份在待审核 ({prev.get('submitted_by')} 提交)")
        yp, _ = self._paths(man.id)
        yp.parent.mkdir(parents=True, exist_ok=True)
        yp.write_text(manifest_yaml, encoding="utf-8")
        rec = {"id": man.id, "name": man.name, "version": man.version, "description": man.description,
               "auth_type": man.auth_type, "tools": [t.name for t in man.tools], "scope": scope,
               "departments": departments if scope == "department" else [],
               "trial_credentials": str(trial_credentials or "")[:500],
               "submitted_by": user.get("sub"), "submitted_dept": user.get("dept"), "submitted_at": _now(),
               "submit_note": str(note or "")[:300], "status": "pending", "trials": []}
        self._save(rec)
        return rec

    def review(self, user: dict, sid: str, decision: str, note: str, *, manifests_dir: Path) -> dict[str, Any]:
        rec = self.load(sid)
        if rec is None:
            raise LookupError("没有这份提交")
        if not can_review(user, rec):
            raise PermissionError("你不能审这个连接器 (部门范围由该部门主管审, 全公司范围由管理员审)")
        if rec.get("status") != "pending":
            raise ValueError(f"这份提交的状态是 {rec.get('status')}, 不在待审核")
        if decision not in ("approve", "reject"):
            raise ValueError("decision 只能是 approve / reject")
        if decision == "reject" and not str(note or "").strip():
            raise ValueError("驳回要写原因, 会回给提交人")
        rec.update(reviewed_by=user.get("sub"), reviewed_at=_now(), review_note=str(note or "")[:500])
        if decision == "approve":
            man = self.manifest(sid)
            if man is None:
                raise ValueError("提交的 manifest 读不回来了")
            data = man.model_dump(mode="json")
            data["allowed_dept"] = list(rec.get("departments") or []) if rec.get("scope") == "department" else []
            data["status"] = "active"
            (manifests_dir / f"{sid}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            rec["status"] = "approved"
        else:
            rec["status"] = "rejected"
        self._save(rec)
        return rec

    def can_trial(self, user: dict, sid: str) -> McpManifest | None:
        """审批人试用待审的连接器: 要有测试账号说明, 且本人能审。返回 manifest 或 None。"""
        rec = self.load(sid)
        if not rec or rec.get("status") != "pending" or not can_review(user, rec) or not rec.get("trial_credentials"):
            return None
        return self.manifest(sid)

    def note_trial(self, user: dict, sid: str) -> None:
        rec = self.load(sid)
        if rec:
            rec.setdefault("trials", []).append({"by": user.get("sub"), "at": _now()})
            self._save(rec)

    def pending_for(self, user: dict) -> list[dict[str, Any]]:
        return [r for r in self.all() if r.get("status") == "pending" and can_review(user, r)]

    def mine(self, user: dict) -> list[dict[str, Any]]:
        return [r for r in self.all() if r.get("submitted_by") == user.get("sub")]
