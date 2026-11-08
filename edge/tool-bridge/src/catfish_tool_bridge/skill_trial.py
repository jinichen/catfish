"""分享治理 · Companion 侧 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §2.3 / §2.5 / §6): 装机验签 + 审批人「装到我这里试用」。

技能从 hub 装的时候 (catfish_tools_install._install_from_hub):
  · 按下载到的文件重算 bundle_sha256 (每行 `路径\\tsha256` 按路径排好、`\\n` 连起来再 sha256, 跟 hub skill_review.bundle_sha256 一字不差)
  · 已通过的: 用中央公钥 (`GET /v1/hub/experts/signing-key`, 第一次记在 ~/.catfish/hub_signing_key.json, 以后换了就拒) 验
    `review.signature` —— 消息 `catfish-skill-v1\\nns\\nname\\nver\\nbundle\\nreviewed_by\\nreviewed_at`
  · 上线前发的老版本 (`review.legacy`) 没签名, 照旧装; hub 没返 review (老 hub) 也照旧
  · 试用: 审批人拿 `/trial-token` (7 天, 消息 `catfish-skill-trial-v1\\nns/name\\nver\\nbundle\\nby\\nexpires`), 装到自己机器上,
    技能目录里多一个 trial.json —— 不给别人、不再发布; 市场刷新发现不在待审了 (通过 / 驳回 / 撤回) 就自动卸掉

专家包试用同一套 (消息 `catfish-expert-trial-v1\\nid\\nver\\nsha256\\nby\\nexpires`), 装进去的专家名字带「(试用)」, expert.yaml 的
source.trial 记着谁试的、什么时候到期。

试跑结果回传 (`POST .../trial-result`): 行不行 + 一句话 + (可选) 那次工作流运行的每步结果和产出文件, 记进中央审核记录。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from . import skill_publish, workflow_store

logger = logging.getLogger("catfish.tool_bridge.skill_trial")

#: 试用安装的标记文件 (技能目录第一层)
TRIAL_FILE = "trial.json"


# ── 中央 (走 gateway /v1/hub, 带员工自己的登录) ─────────────────────

def _hub(method: str, path: str, **kw: Any):
    import httpx  # noqa: PLC0415
    token = skill_publish._read_id_token()
    if not token:
        raise ValueError("还没登录公司账号 (设置里登录后再用资源市场)")
    try:
        with httpx.Client(timeout=kw.pop("timeout", 30.0)) as c:
            r = c.request(method, f"{skill_publish.GATEWAY_URL}/v1/hub{path}",
                          headers={"Authorization": f"Bearer {token}"}, **kw)
    except httpx.RequestError as e:
        raise ValueError(f"连不上中央: {e}") from None
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:200]
        if r.status_code == 403 and not detail:
            detail = "没有权限"
        raise ValueError(f"中央返回 {r.status_code}: {detail}")
    return r


def signing_key() -> str:
    """中央公钥: 记过的用记过的, 没记过就取一次记下 (expert_pack.pin_key: 换了就拒)。"""
    from . import expert_pack  # noqa: PLC0415
    return expert_pack.pinned_key() or expert_pack.pin_key(_hub("GET", "/experts/signing-key").json()["public_key"])


# ── 指纹 / 签名 (跟 hub skill_review.py 一字不差) ──────────────────

def bundle_sha256(files_sha256: dict[str, str]) -> str:
    text = "\n".join(f"{p}\t{files_sha256[p]}" for p in sorted(files_sha256))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def skill_message(ns: str, name: str, ver: str, bundle: str, reviewed_by: str, reviewed_at: str) -> bytes:
    return f"catfish-skill-v1\n{ns}\n{name}\n{ver}\n{bundle}\n{reviewed_by}\n{reviewed_at}".encode()


def skill_trial_message(ns: str, name: str, ver: str, bundle: str, by: str, expires: int) -> bytes:
    return f"catfish-skill-trial-v1\n{ns}/{name}\n{ver}\n{bundle}\n{by}\n{expires}".encode()


def expert_trial_message(pack_id: str, ver: str, sha256: str, by: str, expires: int) -> bytes:
    return f"catfish-expert-trial-v1\n{pack_id}\n{ver}\n{sha256}\n{by}\n{expires}".encode()


def verify(public_key_b64: str, signature_b64: str, message: bytes) -> None:
    """Ed25519 验签; 验不过抛 ValueError (一句人话)。"""
    from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64)).verify(base64.b64decode(signature_b64), message)
    except (InvalidSignature, ValueError, TypeError):
        raise ValueError("中央签名验不过 (文件跟审核通过的那份不一样, 或签名不是中央发的), 不装") from None


def check_skill_review(review: dict[str, Any] | None, ns: str, name: str, ver: str, bundle: str, label: str) -> dict[str, Any]:
    """正式安装: 看 hub 返回的 review。老 hub 没有 review / 老版本 legacy → 照旧; 没通过 → 拒; 通过 → 验签。"""
    if not review:
        return {"status": "unreviewed"}
    if review.get("legacy"):
        return {"status": "legacy"}
    status = str(review.get("status") or "")
    if status != "approved":
        raise ValueError(f"{label} 还没审核通过 (现在是「{_STATUS_CN.get(status, status)}」), 同事装不了; 审批人要试请点「装到我这里试用」")
    if review.get("bundle_sha256") and review["bundle_sha256"] != bundle:
        raise ValueError(f"{label} 下载到的文件跟中央审核时记的不一致 (指纹对不上), 不装")
    if not review.get("signature"):
        raise ValueError(f"{label} 审核通过了但中央没给签名, 不装 (联系管理员)")
    verify(signing_key(), review["signature"],
           skill_message(ns, name, ver, bundle, str(review.get("reviewed_by") or ""), str(review.get("reviewed_at") or "")))
    return {"status": "signed", "reviewed_by": review.get("reviewed_by"), "reviewed_at": review.get("reviewed_at")}


def check_skill_trial(token: dict[str, Any], ns: str, name: str, ver: str, bundle: str) -> dict[str, Any]:
    """试用安装: 令牌没过期、下载到的指纹等于令牌里的、签名是中央发的。"""
    expires = int(token.get("expires") or 0)
    if expires < time.time():
        raise ValueError("试用令牌已过期 (7 天), 重新点一次「装到我这里试用」")
    if token.get("bundle_sha256") != bundle:
        raise ValueError("下载到的文件跟中央记录的不一致 (指纹对不上), 不装")
    verify(signing_key(), str(token.get("signature") or ""),
           skill_trial_message(ns, name, ver, bundle, str(token.get("by") or ""), expires))
    return {"status": "trial", "by": token.get("by"), "expires": expires}


_STATUS_CN = {"pending": "待审核", "approved": "已通过", "rejected": "被驳回", "withdrawn": "已撤回"}


# ── 技能试用 ─────────────────────────────────────────────────────

def pending_skills() -> dict[str, Any]:
    return {"ok": True, "skills": _hub("GET", "/skills/review").json().get("skills") or []}


def install_skill_trial(ns: str, name: str, ver: str) -> dict[str, Any]:
    """审批人「装到我这里试用」: 拿令牌 → 走正常安装那条路 (下载 + 重算指纹 + 验令牌签名 + dry-run) → 目录里写 trial.json。"""
    from .catfish_tools_install import skill_install  # noqa: PLC0415
    if not (ns and name and ver):
        raise ValueError("要说清哪个技能的哪个版本 (namespace / name / version)")
    token = _hub("GET", f"/skills/{ns}/{name}/{ver}/trial-token").json()
    r = skill_install({"hub_skill": f"{ns}/{name}@{ver}", "hub_url": f"{skill_publish.GATEWAY_URL}/v1/hub",
                       "overwrite": True, "force_install": True, "_trial": token})
    if not r.get("ok"):
        raise ValueError(str(r.get("error") or "安装失败"))
    return {**r, "trial": {"by": token.get("by"), "expires": token.get("expires")},
            "summary": f"已以试用身份装到你这台电脑: {r.get('installed_path')} (7 天内有效; 只有你能用, 不会给同事、也不能再发布). "
                       "试过后回传一句结论, 审核页能看到。"}


def write_trial_marker(skill_dir: Path, token: dict[str, Any], *, namespace: str, name: str, version: str, hub_url: str,
                       bundle: str, replaced_backup: str | None = None) -> None:
    (skill_dir / TRIAL_FILE).write_text(json.dumps({
        "trial": True, "by": token.get("by"), "expires": int(token.get("expires") or 0),
        "hub": {"namespace": namespace, "name": name, "version": version, "url": hub_url},
        "bundle_sha256": bundle, "installed_at": workflow_store.now(), "replaced_backup": replaced_backup,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_marker(skill_dir: Path) -> dict[str, Any] | None:
    try:
        d = json.loads((skill_dir / TRIAL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("trial") else None


def list_skill_trials() -> dict[str, Any]:
    """本机所有试用装的技能 (扫每个 skills 根的 <ns>/<name>/trial.json)。"""
    from . import skill_names  # noqa: PLC0415
    from .catfish_tools_skill_ops import _skill_search_roots  # noqa: PLC0415
    out, seen = [], set()
    for root in _skill_search_roots():
        for tj in sorted(root.glob(f"*/*/{TRIAL_FILE}")):
            d = tj.parent
            sp = f"{d.parent.name}/{d.name}"
            marker = _read_marker(d)
            if marker is None or sp in seen:
                continue
            seen.add(sp)
            hub = marker.get("hub") or {}
            out.append({"skill_path": sp, "path": str(d), **skill_names.skill_display(d),
                        "namespace": hub.get("namespace"), "name": hub.get("name"), "version": hub.get("version"),
                        "by": marker.get("by"), "expires": marker.get("expires"), "installed_at": marker.get("installed_at"),
                        "expired": int(marker.get("expires") or 0) < time.time()})
    return {"ok": True, "trials": out}


def _trial_dir(skill_path: str) -> tuple[Path, dict[str, Any]]:
    from .catfish_tools_skill_ops import _find_skill_dir  # noqa: PLC0415
    d = _find_skill_dir(str(skill_path or ""))
    marker = _read_marker(d) if d is not None else None
    if d is None or marker is None:
        raise LookupError(f"{skill_path} 不是试用装的技能")
    return d, marker


def uninstall_skill_trial(skill_path: str) -> dict[str, Any]:
    """卸掉试用: 只动有 trial.json 的目录 (挪进 skill-trash); 试用时顶掉的正式版本挪回来。"""
    d, marker = _trial_dir(skill_path)
    trash = Path.home() / ".catfish" / "skill-trash"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / f"{int(time.time())}-{d.name}-trial"
    shutil.move(str(d), str(dest))
    restored = None
    backup = marker.get("replaced_backup")
    if backup and Path(backup).is_dir():
        shutil.move(backup, str(d))
        restored = str(d)
    return {"ok": True, "skill_path": skill_path, "trash": str(dest), "restored": restored}


def _run_evidence(run_id: str | None) -> tuple[list[str], list[str], str]:
    """那次工作流运行的每步结果 + 产出文件 (回传给审核记录), 没有 run_id 就空。"""
    if not run_id:
        return [], [], ""
    run = workflow_store.load_run(str(run_id))
    steps = []
    for s in run.get("steps") or []:
        line = f"{s.get('id')}: {s.get('status')}"
        if s.get("error"):
            line += f" — {str(s['error'])[:120]}"
        steps.append(line)
    files = [f for s in run.get("steps") or [] for f in (s.get("result") or {}).get("files") or [] if isinstance(f, str)]
    return steps, list(dict.fromkeys(files)), str(run.get("skill_path") or "")


def skill_trial_result(skill_path: str, ok: bool, summary: str, run_id: str | None = None) -> dict[str, Any]:
    _, marker = _trial_dir(skill_path)
    hub = marker.get("hub") or {}
    steps, files, _ = _run_evidence(run_id)
    body = {"ok": bool(ok), "summary": str(summary or "")[:1000], "files": files, "steps": steps}
    r = _hub("POST", f"/skills/{hub.get('namespace')}/{hub.get('name')}/{hub.get('version')}/trial-result", json=body)
    return {"ok": True, "hub": r.json(), "summary": "试用结果已回传, 审核页上能看到"}


def sync_skill_trials() -> dict[str, Any]:
    """市场刷新时调: 不在待审了 (通过 / 驳回 / 撤回 / 没了) 或令牌过期的试用安装自动卸掉。通过的告诉界面「可以转成正式安装」。"""
    local = list_skill_trials()["trials"]
    if not local:
        return {"ok": True, "removed": []}
    pending = {f"{r.get('namespace')}/{r.get('name')}/{r.get('version')}" for r in pending_skills()["skills"]}
    removed = []
    for t in local:
        key = f"{t['namespace']}/{t['name']}/{t['version']}"
        if key in pending and not t["expired"]:
            continue
        status = "expired" if t["expired"] else _skill_status(t["namespace"], t["name"], t["version"])
        try:
            uninstall_skill_trial(t["skill_path"])
        except (LookupError, OSError) as e:
            logger.warning("卸试用 %s 失败: %s", t["skill_path"], e)
            continue
        removed.append({**t, "status": status})
    return {"ok": True, "removed": removed}


def _skill_status(ns: str, name: str, ver: str) -> str:
    try:
        return str((_hub("GET", f"/skills/{ns}/{name}/{ver}").json().get("review") or {}).get("status") or "gone")
    except ValueError:
        return "gone"


# ── 专家包试用 ───────────────────────────────────────────────────

def pending_experts() -> dict[str, Any]:
    return {"ok": True, "experts": _hub("GET", "/experts/review").json().get("experts") or []}


def install_expert_trial(pack_id: str, ver: str, confirmed: bool = False) -> dict[str, Any]:
    from . import expert_pack  # noqa: PLC0415
    if not (pack_id and ver):
        raise ValueError("要说清哪个专家包的哪个版本")
    token = _hub("GET", f"/experts/{pack_id}/{ver}/trial-token").json()
    expires = int(token.get("expires") or 0)
    if expires < time.time():
        raise ValueError("试用令牌已过期 (7 天), 重新点一次「装到我这里试用」")
    data = _hub("GET", f"/experts/{pack_id}/{ver}/download", timeout=120.0).content
    sha = hashlib.sha256(data).hexdigest()
    if sha != token.get("sha256"):
        raise ValueError("下载到的包跟中央记录的不一致 (sha256 对不上), 不装")
    verify(signing_key(), str(token.get("signature") or ""), expert_trial_message(pack_id, ver, sha, str(token.get("by") or ""), expires))
    trial = {"by": token.get("by"), "expires": expires, "id": pack_id, "version": ver}
    r = expert_pack.install(data, confirmed=confirmed, origin="hub",
                            signature={"status": "trial", "by": token.get("by"), "expires": expires}, trial=trial)
    if r.get("ok"):
        r["summary"] = "已以试用身份装到你这台电脑 (「找谁」里带「(试用)」; 7 天内有效, 不会给同事). 试过后回传一句结论。"
    return r


def expert_trial_result(pack_id: str, ok: bool, summary: str, run_id: str | None = None) -> dict[str, Any]:
    from . import expert_local  # noqa: PLC0415
    src = (expert_local.load(pack_id).get("source") or {})
    trial = src.get("trial") or {}
    if not trial:
        raise LookupError(f"{pack_id} 不是试用装的专家")
    steps, files, workflow = _run_evidence(run_id)
    body = {"ok": bool(ok), "summary": str(summary or "")[:1000], "workflow": workflow, "files": files, "steps": steps}
    r = _hub("POST", f"/experts/{pack_id}/{trial.get('version') or src.get('version')}/trial-result", json=body)
    return {"ok": True, "hub": r.json(), "summary": "试用结果已回传, 审核页上能看到"}


def sync_expert_trials() -> dict[str, Any]:
    """不在待审了的专家包试用自动卸掉 (定义 + 包; hermes profile 由 Companion Rust 删)。"""
    from . import expert_local, expert_pack  # noqa: PLC0415
    local = [e for e in expert_local.list_all() if (e.get("pack") or {}).get("trial")]
    if not local:
        return {"ok": True, "removed": []}
    pending = {(m.get("id"), m.get("version")) for m in pending_experts()["experts"]}
    removed = []
    for e in local:
        t = e["pack"]["trial"]
        expired = int(t.get("expires") or 0) < time.time()
        if (e["id"], e["pack"].get("version")) in pending and not expired:
            continue
        status = "expired" if expired else _expert_status(e["id"], e["pack"].get("version"))
        try:
            expert_pack.uninstall(e["id"])
        except (LookupError, OSError) as ex:
            logger.warning("卸试用专家 %s 失败: %s", e["id"], ex)
            continue
        removed.append({"id": e["id"], "name": e.get("name"), "version": e["pack"].get("version"), "status": status})
    return {"ok": True, "removed": removed}


def _expert_status(pack_id: str, ver: str) -> str:
    try:
        return str(_hub("GET", f"/experts/{pack_id}/{ver}").json().get("status") or "gone")
    except ValueError:
        return "gone"


def trial_runs(prefix: str) -> dict[str, Any]:
    """回传结果时可挑的运行记录: skill_path 以 prefix 开头的 (技能 `ns/name`, 专家包 `pack-<id>/`)。"""
    from . import workflow_tool  # noqa: PLC0415
    runs = [r for r in workflow_tool.runs_for_ui(30, None) if str(r.get("skill_path") or "").startswith(str(prefix or ""))]
    return {"ok": True, "runs": runs[:10]}


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    ns, name, ver = (str(params.get(k) or "") for k in ("namespace", "name", "version"))
    sp = str(params.get("skill_path") or "")
    eid = str(params.get("id") or "")
    return {
        "pim/trial_skill_pending": pending_skills,
        "pim/trial_skill_install": lambda: install_skill_trial(ns, name, ver),
        "pim/trial_skill_list": list_skill_trials,
        "pim/trial_skill_uninstall": lambda: uninstall_skill_trial(sp),
        "pim/trial_skill_result": lambda: skill_trial_result(sp, bool(params.get("ok")), str(params.get("summary") or ""),
                                                             params.get("run_id")),
        "pim/trial_skill_sync": sync_skill_trials,
        "pim/trial_expert_pending": pending_experts,
        "pim/trial_expert_install": lambda: install_expert_trial(eid, ver, bool(params.get("confirmed"))),
        "pim/trial_expert_result": lambda: expert_trial_result(eid, bool(params.get("ok")), str(params.get("summary") or ""),
                                                               params.get("run_id")),
        "pim/trial_expert_sync": sync_expert_trials,
        "pim/trial_runs": lambda: trial_runs(str(params.get("prefix") or "")),
    }
