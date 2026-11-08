"""技能的范围 / 审批 / 试用 / 签名 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §3.1)。

以前技能发到 hub 就上架, 没人审; 只有 namespace 一个桶 (department 是全公司共用的)。现在跟专家包 (experts.py) 一样:
  提交 (scope=department|company, 部门范围默认提交人自己的部门) → pending → 部门主管 / admin 审 → approved (中央签名) / rejected
  审批人可以拿「试用令牌」装到自己机器上先跑 (签名里带 trial + 到期时间, 7 天); 试跑结果回传记在审核记录里。

审核记录是文件系统上的 sidecar (<hub root>/skills-review/<ns>/<name>/<ver>.json), 不碰 storage.py 的 PG 表:
  · 没有记录的老版本 = 上线前发的, 一律算已通过、全公司可见 (不回溯)
  · 签名复用专家包那把 Ed25519 key (experts._private_key / public_key_b64)
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import experts, storage

SCOPES = ("department", "company")
STATUSES = ("pending", "approved", "rejected", "withdrawn")
TRIAL_TTL_S = 7 * 24 * 3600
_SEG = re.compile(r"^[a-zA-Z0-9._\-+]{1,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root() -> Path:
    d = storage.hub_root() / "skills-review"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(ns: str, name: str, ver: str) -> Path:
    for seg in (ns, name, ver):
        if not _SEG.match(seg):
            raise ValueError(f"路径段不合法: {seg!r}")
    return _root() / ns / name / f"{ver}.json"


def bundle_sha256(files_sha256: dict[str, str]) -> str:
    """整个版本的指纹: 每个文件的路径 + sha256 按路径排好拼起来再 sha256 (Companion 装机时按下载到的文件重算一遍)。"""
    text = "\n".join(f"{p}\t{files_sha256[p]}" for p in sorted(files_sha256))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def signed_message(ns: str, name: str, ver: str, bundle: str, approved_by: str, approved_at: str) -> bytes:
    return f"catfish-skill-v1\n{ns}\n{name}\n{ver}\n{bundle}\n{approved_by}\n{approved_at}".encode()


def trial_message(kind: str, ident: str, ver: str, digest: str, by: str, expires: int) -> bytes:
    """试用令牌签的内容: kind = skill / expert; 带 trial 字样和到期时间, 拿不去当正式签名用。"""
    return f"catfish-{kind}-trial-v1\n{ident}\n{ver}\n{digest}\n{by}\n{expires}".encode()


def _load(ns: str, name: str, ver: str) -> dict[str, Any] | None:
    try:
        return json.loads(_path(ns, name, ver).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(rec: dict[str, Any]) -> None:
    p = _path(rec["namespace"], rec["name"], rec["version"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _all() -> list[dict[str, Any]]:
    out = []
    for p in _root().glob("*/*/*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def legacy_record(ns: str, name: str, ver: str, published_by: str = "") -> dict[str, Any]:
    """上线前发的版本: 没有审核记录, 算已通过、全公司可见。"""
    return {"namespace": ns, "name": name, "version": ver, "scope": "company", "departments": [], "status": "approved",
            "submitted_by": published_by, "legacy": True, "trials": []}


def record_for(ns: str, name: str, ver: str, published_by: str = "") -> dict[str, Any]:
    return _load(ns, name, ver) or legacy_record(ns, name, ver, published_by)


# ── 谁能看 / 谁能审 (跟专家包同一条规则) ─────────────────────────────

_is_admin = experts._is_admin


def can_review(user: dict, rec: dict) -> bool:
    return experts.can_review(user, rec)


def can_see(user: dict, rec: dict) -> bool:
    if _is_admin(user) or rec.get("submitted_by") == user.get("sub") or can_review(user, rec):
        return True
    if rec.get("status") != "approved":
        return False
    return rec.get("scope") == "company" or user.get("dept") in (rec.get("departments") or [])


def public(rec: dict) -> dict:
    """给界面看的字段 (试用记录只给审批人 / 提交人看, 在 detail 里另给)。"""
    return {k: v for k, v in rec.items() if k != "trials"}


# ── 动作 ─────────────────────────────────────────────────────────

def submit(user: dict, ns: str, name: str, ver: str, files_sha256: dict[str, str], *, scope: str,
           departments: list[str], note: str = "") -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValueError("范围只能是 department (本部门) 或 company (全公司)")
    departments = [d for d in dict.fromkeys(str(x).strip() for x in departments or []) if d]
    if scope == "department" and not departments:
        departments = [user.get("dept")] if user.get("dept") else []
        if not departments:
            raise ValueError("部门范围的技能要写给哪些部门用 (你的账号没有部门)")
    rec = {"namespace": ns, "name": name, "version": ver, "scope": scope,
           "departments": departments if scope == "department" else [],
           "bundle_sha256": bundle_sha256(files_sha256), "file_count": len(files_sha256),
           "submitted_by": user.get("sub"), "submitted_dept": user.get("dept"), "submitted_at": _now(),
           "submit_note": str(note or "")[:300], "status": "pending", "trials": []}
    _save(rec)
    storage._write_audit({"ts": _now(), "event": "submit", "namespace": ns, "name": name, "version": ver,
                          "by_user": user.get("sub"), "scope": scope, "departments": rec["departments"], "line": "skill"})
    return public(rec)


def review(user: dict, ns: str, name: str, ver: str, decision: str, note: str = "") -> dict[str, Any]:
    rec = _load(ns, name, ver)
    if rec is None:
        raise LookupError("这个版本没有审核记录 (上线前发的老版本不用审)")
    if not can_review(user, rec):
        raise PermissionError("你不能审这个技能 (部门范围由该部门主管审, 全公司范围由管理员审)")
    if rec.get("status") != "pending":
        raise ValueError(f"这个版本的状态是 {rec.get('status')}, 不在待审核")
    if decision not in ("approve", "reject"):
        raise ValueError("decision 只能是 approve / reject")
    if decision == "reject" and not str(note or "").strip():
        raise ValueError("驳回要写原因, 会回给提交人")
    rec.update(reviewed_by=user.get("sub"), reviewed_at=_now(), review_note=str(note or "")[:500])
    if decision == "approve":
        rec["status"] = "approved"
        sig = experts._private_key().sign(signed_message(ns, name, ver, rec["bundle_sha256"], rec["reviewed_by"], rec["reviewed_at"]))
        rec["signature"] = base64.b64encode(sig).decode()
    else:
        rec["status"] = "rejected"
    _save(rec)
    storage._write_audit({"ts": _now(), "event": decision, "namespace": ns, "name": name, "version": ver,
                          "by_user": user.get("sub"), "note": rec["review_note"], "line": "skill"})
    return public(rec)


def withdraw(user: dict, ns: str, name: str, ver: str) -> dict[str, Any]:
    rec = _load(ns, name, ver)
    if rec is None:
        raise LookupError("这个版本没有审核记录")
    if rec.get("submitted_by") != user.get("sub") and not _is_admin(user) and not can_review(user, rec):
        raise PermissionError("只有提交人、审批人或管理员能撤回")
    if rec.get("status") not in ("pending", "approved"):
        raise ValueError("这个版本已经不在待审核 / 上架")
    rec.update(status="withdrawn", withdrawn_by=user.get("sub"), withdrawn_at=_now())
    _save(rec)
    storage._write_audit({"ts": _now(), "event": "withdraw", "namespace": ns, "name": name, "version": ver,
                          "by_user": user.get("sub"), "line": "skill"})
    return public(rec)


def trial_token(user: dict, ns: str, name: str, ver: str) -> dict[str, Any]:
    """审批人「装到我这里试用」: 临时签名, 7 天到期, 签名里带 trial —— Companion 装成试用身份。"""
    rec = _load(ns, name, ver)
    if rec is None:
        raise LookupError("这个版本没有审核记录")
    if not can_review(user, rec):
        raise PermissionError("只有审批人能试用待审核的技能")
    if rec.get("status") not in ("pending", "approved"):
        raise ValueError(f"这个版本的状态是 {rec.get('status')}, 不能试用")
    expires = int(time.time()) + TRIAL_TTL_S
    sig = experts._private_key().sign(trial_message("skill", f"{ns}/{name}", ver, rec["bundle_sha256"], user.get("sub") or "", expires))
    rec.setdefault("trials", []).append({"by": user.get("sub"), "at": _now(), "expires": expires})
    _save(rec)
    storage._write_audit({"ts": _now(), "event": "trial", "namespace": ns, "name": name, "version": ver,
                          "by_user": user.get("sub"), "line": "skill"})
    return {"namespace": ns, "name": name, "version": ver, "bundle_sha256": rec["bundle_sha256"], "trial": True,
            "by": user.get("sub"), "expires": expires, "signature": base64.b64encode(sig).decode(),
            "public_key": experts.public_key_b64()}


def trial_result(user: dict, ns: str, name: str, ver: str, result: dict[str, Any]) -> dict[str, Any]:
    """审批人试跑完, Companion 把结果回传 (哪步成了 / 产出了什么), 记进审核记录, 审核页能看。"""
    rec = _load(ns, name, ver)
    if rec is None:
        raise LookupError("这个版本没有审核记录")
    if not can_review(user, rec):
        raise PermissionError("只有审批人能回传试用结果")
    trials = rec.setdefault("trials", [])
    mine = next((t for t in reversed(trials) if t.get("by") == user.get("sub")), None)
    if mine is None:
        mine = {"by": user.get("sub"), "at": _now()}
        trials.append(mine)
    mine["result"] = {"ok": bool(result.get("ok")), "summary": str(result.get("summary") or "")[:1000],
                      "files": [str(f) for f in (result.get("files") or [])][:20],
                      "steps": [str(s)[:200] for s in (result.get("steps") or [])][:30], "at": _now()}
    _save(rec)
    storage._write_audit({"ts": _now(), "event": "trial_result", "namespace": ns, "name": name, "version": ver,
                          "by_user": user.get("sub"), "ok": mine["result"]["ok"], "line": "skill"})
    return {"ok": True, "trials": trials}


def detail(user: dict, ns: str, name: str, ver: str, published_by: str = "") -> dict[str, Any]:
    rec = record_for(ns, name, ver, published_by)
    out = public(rec)
    if can_review(user, rec) or rec.get("submitted_by") == user.get("sub"):
        out["trials"] = rec.get("trials") or []
    return out


def pending_for(user: dict) -> list[dict[str, Any]]:
    return sorted((public(r) for r in _all() if r.get("status") == "pending" and can_review(user, r)),
                  key=lambda r: r.get("submitted_at") or "")


def mine(user: dict) -> list[dict[str, Any]]:
    return sorted((public(r) for r in _all() if r.get("submitted_by") == user.get("sub")),
                  key=lambda r: r.get("submitted_at") or "", reverse=True)


def visible_versions(user: dict, ns: str, name: str) -> list[str]:
    """这个技能里用户看得见的版本 (给 list 用): 有记录的按记录, 没记录的 (老版本) 都算。"""
    out = []
    recs = {r["version"]: r for r in _all() if r["namespace"] == ns and r["name"] == name}
    for ver, r in recs.items():
        if can_see(user, r):
            out.append(ver)
    return out


def annotate_list(user: dict, skills: list[dict]) -> list[dict]:
    """/skills 列表: 每条带审核状态; 看不见的 (别的部门的待审 / 没通过) 去掉。
    PG 模式一条技能只列最新版 —— 最新版是待审的话, 普通员工该看到上一个已通过的版本;
    这里退一步: 最新版看不见就看 all_versions 里下一个看得见的。"""
    out = []
    recs_by_key: dict[tuple[str, str], dict[str, dict]] = {}
    for r in _all():
        recs_by_key.setdefault((r["namespace"], r["name"]), {})[r["version"]] = r
    for s in skills:
        key = (s["namespace"], s["name"])
        recs = recs_by_key.get(key, {})
        versions = list(dict.fromkeys([s.get("latest_version"), *(s.get("all_versions") or [])]))
        shown = None
        for ver in versions:
            if not ver:
                continue
            rec = recs.get(ver) or legacy_record(s["namespace"], s["name"], ver, s.get("published_by") or "")
            if can_see(user, rec):
                shown = (ver, rec)
                break
        if shown is None:
            continue
        ver, rec = shown
        out.append({**s, "latest_version": ver, "review": {k: rec.get(k) for k in
                    ("scope", "departments", "status", "submitted_by", "submitted_at", "reviewed_by", "reviewed_at",
                     "review_note", "legacy")}})
    return out
