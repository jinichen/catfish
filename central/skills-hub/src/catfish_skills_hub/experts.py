"""专家包的提交 / 审核 / 上架 / 下载 / 签名 (10/18, docs/EXPERT-PACK-PLAN.md §5 §6)。

流程: 任何员工提交 → 待审核 → 目标部门的 manager (company 范围由 admin) 审核 → 通过才上架 (中央签名) / 驳回写原因。

存放 (不建表, 跟 skill 文件同一个 hub 根; 元数据就几十条, 文件够用):
  <hub>/experts/<pack_id>/<version>/pack.catfish-expert   包本身 (员工主动 push 的例外, 同 skill 发布)
  <hub>/experts/<pack_id>/<version>/meta.json             清单摘要 + 审核记录 + 签名
  <hub>/experts/audit.jsonl                               谁提交 / 谁审 / 结论 / 时间
  <hub>/signing/ed25519.key                               中央签名私钥 (第一次用时生成, 0600)

**审核摘要是服务器自己从包里拆出来的** (清单、选路、每个工作流的步骤、脚本里会打开的网站), 不信提交方自报。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .storage import hub_root

MAX_PACK_BYTES = 20 * 1024 * 1024
_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_VER = re.compile(r"^\d+\.\d+\.\d+$")
_URL = re.compile(r"""https?://[^\s'"<>)\]]+""")
SCOPES = ("department", "company")


def _root() -> Path:
    d = hub_root() / "experts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _audit(event: dict[str, Any]) -> None:
    with open(_root() / "audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now(), **event}, ensure_ascii=False) + "\n")


# ── 签名 ─────────────────────────────────────────────────────────

def _key_path() -> Path:
    return hub_root() / "signing" / "ed25519.key"


def _private_key():
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: PLC0415

    p = _key_path()
    if p.exists():
        return serialization.load_pem_private_key(p.read_bytes(), password=None)
    p.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


def public_key_b64() -> str:
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    raw = _private_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def signed_message(pack_id: str, version: str, sha256: str, approved_by: str, approved_at: str) -> bytes:
    """被签的内容 (Companion 按同样格式拼出来验)。"""
    return f"catfish-expert-v1\n{pack_id}\n{version}\n{sha256}\n{approved_by}\n{approved_at}".encode()


# ── 拆包: 服务器自己看包里是什么 ─────────────────────────────────

def inspect_pack(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_PACK_BYTES:
        raise ValueError(f"包太大 ({len(data) // 1024 // 1024}MB), 上限 {MAX_PACK_BYTES // 1024 // 1024}MB")
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except tarfile.TarError as e:
        raise ValueError(f"不是有效的专家包 (tar.gz): {e}") from None
    files: dict[str, bytes] = {}
    with tf:
        for m in tf.getmembers():
            name = m.name[2:] if m.name.startswith("./") else m.name  # 不能 lstrip("./"): 会把 ../x 吃成 x
            if m.issym() or m.islnk() or name.startswith("/") or ".." in name.split("/"):
                raise ValueError(f"包里有不安全的路径: {m.name}")
            if m.isfile():
                f = tf.extractfile(m)
                files[name] = f.read() if f else b""
    if "expert.yaml" not in files:
        raise ValueError("包里没有 expert.yaml")
    try:
        man = yaml.safe_load(files["expert.yaml"].decode("utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise ValueError(f"expert.yaml 读不了: {e}") from None
    if man.get("kind") != "catfish-expert":
        raise ValueError("expert.yaml 的 kind 不是 catfish-expert")
    pid, ver = str(man.get("id") or ""), str(man.get("version") or "")
    if not _ID.match(pid):
        raise ValueError(f"专家包 id 不对: {pid!r}")
    if not _VER.match(ver):
        raise ValueError(f"版本号要写成 x.y.z: {ver!r}")
    workflows = {}
    sites: set[str] = set()
    for name, body in files.items():
        if name.endswith("workflow.yaml"):
            try:
                wf = yaml.safe_load(body.decode("utf-8")) or {}
                workflows[name.split("/")[-2]] = [
                    {"id": s.get("id"), "block": "skill" if "skill" in s else "action" if "action" in s else "xiaonian",
                     "what": s.get("skill") or s.get("action") or str(s.get("xiaonian") or "")[:80],
                     "confirm": bool(s.get("confirm"))}
                    for s in wf.get("steps") or [] if isinstance(s, dict)]
            except (yaml.YAMLError, UnicodeDecodeError):
                workflows[name.split("/")[-2]] = []
        if name.endswith((".py", ".yaml", ".md")):
            sites |= {u.rstrip(".,;'\"") for u in _URL.findall(body.decode("utf-8", errors="ignore"))}
    return {
        "id": pid, "version": ver, "name": str(man.get("name") or pid)[:40],
        "description": str(man.get("description") or "")[:300],
        "routes": man.get("routes") or [], "checks": man.get("checks") or [],
        "tool_groups": man.get("tool_groups") or [], "allow_web": bool(man.get("allow_web")),
        "allow_code": bool(man.get("allow_code")), "quick_prompts": man.get("quick_prompts") or [],
        "summary": man.get("summary") or {},
        "workflows": workflows, "skills": sorted({n.split("/")[1] for n in files if n.startswith("skills/") and n.count("/") >= 2}),
        "sites": sorted(sites)[:50], "file_count": len(files),
    }


# ── 元数据 ───────────────────────────────────────────────────────

def _dir(pid: str, ver: str) -> Path:
    if not _ID.match(pid) or not _VER.match(ver):
        raise ValueError("专家包 id / 版本不对")
    return _root() / pid / ver


def _load(pid: str, ver: str) -> dict[str, Any] | None:
    try:
        return json.loads((_dir(pid, ver) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(meta: dict[str, Any]) -> None:
    d = _dir(meta["id"], meta["version"])
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "meta.json")


def _all() -> list[dict[str, Any]]:
    out = []
    for p in _root().glob("*/*/meta.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _is_admin(user: dict) -> bool:
    return user.get("role") in ("admin", "sysadmin")


def can_review(user: dict, meta: dict) -> bool:
    if _is_admin(user):
        return True
    if meta.get("scope") == "company":
        return False
    return user.get("role") == "manager" and bool(set(user.get("managed") or []) & set(meta.get("departments") or []))


def can_see(user: dict, meta: dict) -> bool:
    if _is_admin(user) or meta.get("submitted_by") == user.get("sub") or can_review(user, meta):
        return True
    if meta.get("status") != "approved":
        return False
    return meta.get("scope") == "company" or user.get("dept") in (meta.get("departments") or [])


def _public(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if k != "file"}


# ── 动作 ─────────────────────────────────────────────────────────

def submit(user: dict, data: bytes, scope: str, departments: list[str], note: str = "") -> dict[str, Any]:
    info = inspect_pack(data)
    if scope not in SCOPES:
        raise ValueError("范围只能是 department (本部门) 或 company (全公司)")
    departments = [d for d in dict.fromkeys(str(x).strip() for x in departments or []) if d]
    if scope == "department" and not departments:
        departments = [user.get("dept")] if user.get("dept") else []
        if not departments:
            raise ValueError("部门范围的专家包要写给哪些部门用")
    same = [m for m in _all() if m["id"] == info["id"]]
    if same and not _is_admin(user) and all(m.get("submitted_by") != user.get("sub") for m in same):
        raise ValueError(f"专家包 {info['id']} 是别人发布的, 只有原作者或管理员能提交新版本")
    for m in same:
        if m.get("status") in ("pending", "approved") and _ver_tuple(m["version"]) >= _ver_tuple(info["version"]):
            raise ValueError(f"版本 {info['version']} 不比已有的 {m['version']} 新 —— 改了内容请把版本号往上加")
    d = _dir(info["id"], info["version"])
    d.mkdir(parents=True, exist_ok=False)
    (d / "pack.catfish-expert").write_bytes(data)
    meta = {**info, "scope": scope, "departments": departments if scope == "department" else [],
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "submitted_by": user.get("sub"), "submitted_dept": user.get("dept"), "submitted_at": _now(),
            "submit_note": str(note or "")[:300], "status": "pending", "file": "pack.catfish-expert"}
    _save(meta)
    _audit({"event": "submit", "id": info["id"], "version": info["version"], "by": user.get("sub"), "scope": scope,
            "departments": meta["departments"]})
    return _public(meta)


def review(user: dict, pid: str, ver: str, decision: str, note: str = "") -> dict[str, Any]:
    meta = _load(pid, ver)
    if meta is None:
        raise LookupError("没有这个专家包")
    if not can_review(user, meta):
        raise PermissionError("你不能审这个专家包 (部门范围由该部门主管审, 全公司范围由管理员审)")
    if meta.get("status") != "pending":
        raise ValueError(f"这个版本的状态是 {meta.get('status')}, 不在待审核")
    if decision not in ("approve", "reject"):
        raise ValueError("decision 只能是 approve / reject")
    if decision == "reject" and not str(note or "").strip():
        raise ValueError("驳回要写原因, 会回给提交人")
    meta.update(reviewed_by=user.get("sub"), reviewed_at=_now(), review_note=str(note or "")[:500])
    if decision == "approve":
        meta["status"] = "approved"
        sig = _private_key().sign(signed_message(pid, ver, meta["sha256"], meta["reviewed_by"], meta["reviewed_at"]))
        meta["signature"] = base64.b64encode(sig).decode()
    else:
        meta["status"] = "rejected"
    _save(meta)
    _audit({"event": decision, "id": pid, "version": ver, "by": user.get("sub"), "note": meta["review_note"]})
    return _public(meta)


def withdraw(user: dict, pid: str, ver: str) -> dict[str, Any]:
    meta = _load(pid, ver)
    if meta is None:
        raise LookupError("没有这个专家包")
    if meta.get("submitted_by") != user.get("sub") and not _is_admin(user):
        raise PermissionError("只有提交人能撤回")
    if meta.get("status") not in ("pending", "approved"):
        raise ValueError("这个版本已经不在待审核 / 上架")
    meta.update(status="withdrawn", withdrawn_at=_now())
    _save(meta)
    _audit({"event": "withdraw", "id": pid, "version": ver, "by": user.get("sub")})
    return _public(meta)


def market(user: dict) -> list[dict[str, Any]]:
    """每个专家包已上架的最新版 (按部门过滤)。"""
    best: dict[str, dict] = {}
    for m in _all():
        if m.get("status") != "approved" or not can_see(user, m):
            continue
        if m["id"] not in best or _ver_tuple(m["version"]) > _ver_tuple(best[m["id"]]["version"]):
            best[m["id"]] = m
    return sorted((_public(m) for m in best.values()), key=lambda m: m.get("name") or "")


def mine(user: dict) -> list[dict[str, Any]]:
    return sorted((_public(m) for m in _all() if m.get("submitted_by") == user.get("sub")),
                  key=lambda m: m.get("submitted_at") or "", reverse=True)


def pending_for(user: dict) -> list[dict[str, Any]]:
    return sorted((_public(m) for m in _all() if m.get("status") == "pending" and can_review(user, m)),
                  key=lambda m: m.get("submitted_at") or "")


def detail(user: dict, pid: str, ver: str) -> dict[str, Any]:
    meta = _load(pid, ver)
    if meta is None or not can_see(user, meta):
        raise LookupError("没有这个专家包, 或者你看不到")
    return _public(meta)


def download(user: dict, pid: str, ver: str) -> tuple[bytes, dict[str, Any]]:
    meta = _load(pid, ver)
    if meta is None or not can_see(user, meta):
        raise LookupError("没有这个专家包, 或者你看不到")
    if meta.get("status") != "approved" and not (can_review(user, meta) or meta.get("submitted_by") == user.get("sub")):
        raise PermissionError("还没审核通过")
    return (_dir(pid, ver) / meta["file"]).read_bytes(), _public(meta)
