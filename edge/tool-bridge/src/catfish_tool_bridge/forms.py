"""一起填表 (收集表) —— docs/FORM-COLLECT-PLAN.md。

发起人给表头 → 字段 → 发给同事 → 同事本机预填、本人点提交 → 中央只转交 (取走即清空)
→ 发起人本机收齐、核对、催填、导出 Excel。

本机账本 (`~/.catfish/forms/`):
  outgoing/<form_id>/form.json           我发起的: 字段 + 收件人 + 每人进度
  outgoing/<form_id>/template.xlsx       从 Excel 建的表留一份模板, 导出按它的列顺序
  outgoing/<form_id>/responses/<邮箱>.json  回来的回答 (后到覆盖)
  incoming/<form_id>.json                别人发给我的: 字段 + 草稿 + 我交了什么

邮筒里的信封 (JSON, 中央看不懂也不看):
  form_invite   {v, form_id, form: {title, note, deadline, fields}}   —— 不含收件人名单、不含别人的回答
  form_response {v, form_id, status: submitted|declined, values?, reason?, at}
  form_remind   {v, form_id, text?}
  form_close    {v, form_id}
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import forms_net, wiki_files

ENVELOPE_VERSION = 1
FIELD_TYPES = ("text", "longtext", "number", "date", "choice", "multichoice")
MAX_FIELDS = 60
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── 本机账本 ─────────────────────────────────────────────────────

def forms_dir() -> Path:
    d = wiki_files._catfish_home() / "forms"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(form_id: str) -> str:
    fid = str(form_id or "").strip()
    if not re.fullmatch(r"f_[0-9]{8}_[0-9a-f]{6,16}", fid):
        raise ValueError(f"表单编号不对: {form_id!r}")
    return fid


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _out_dir(form_id: str) -> Path:
    return forms_dir() / "outgoing" / _safe_id(form_id)


def _in_path(form_id: str) -> Path:
    return forms_dir() / "incoming" / f"{_safe_id(form_id)}.json"


def load_outgoing(form_id: str) -> dict[str, Any]:
    form = _read(_out_dir(form_id) / "form.json")
    if not form:
        raise LookupError(f"没有这张我发起的表: {form_id}")
    return form


def load_incoming(form_id: str) -> dict[str, Any]:
    rec = _read(_in_path(form_id))
    if not rec:
        raise LookupError(f"没有这张收到的表: {form_id}")
    return rec


def _save_outgoing(form: dict[str, Any]) -> None:
    _write(_out_dir(form["form_id"]) / "form.json", form)


def responses_of(form_id: str) -> dict[str, dict[str, Any]]:
    d = _out_dir(form_id) / "responses"
    out = {}
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        r = _read(p)
        if r and r.get("from"):
            out[r["from"]] = r
    return out


# ── 字段 / 值校验 ─────────────────────────────────────────────────

def _key_for(label: str, used: set[str], i: int) -> str:
    base = re.sub(r"[^0-9a-zA-Z_]+", "_", label).strip("_").lower() or f"f{i + 1}"
    key, n = base[:40], 2
    while key in used:
        key, n = f"{base[:36]}_{n}", n + 1
    used.add(key)
    return key


def normalize_fields(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("表里至少要有一个字段")
    if len(raw) > MAX_FIELDS:
        raise ValueError(f"字段太多 ({len(raw)} 个), 一张表最多 {MAX_FIELDS} 个")
    used: set[str] = set()
    out = []
    for i, f in enumerate(raw):
        if isinstance(f, str):
            f = {"label": f}
        if not isinstance(f, dict):
            raise ValueError(f"第 {i + 1} 个字段格式不对")
        label = str(f.get("label") or "").strip()
        if not label:
            raise ValueError(f"第 {i + 1} 个字段没有名字")
        ftype = str(f.get("type") or "text").strip()
        if ftype not in FIELD_TYPES:
            raise ValueError(f"字段「{label}」的类型 {ftype} 不支持, 只能是 {' / '.join(FIELD_TYPES)}")
        field: dict[str, Any] = {"key": _key_for(str(f.get("key") or label), used, i), "label": label[:60],
                                 "type": ftype, "required": bool(f.get("required"))}
        if ftype in ("choice", "multichoice"):
            opts = [str(o).strip() for o in (f.get("options") or []) if str(o).strip()]
            if len(opts) < 2:
                raise ValueError(f"字段「{label}」是选择题, 至少要两个选项")
            field["options"] = list(dict.fromkeys(opts))[:50]
        if f.get("hint"):
            field["hint"] = str(f["hint"]).strip()[:200]
        if isinstance(f.get("col"), int):
            field["col"] = int(f["col"])   # 从 Excel 模板来的: 原列号 (1 起), 导出写回同一列
        out.append(field)
    return out


def check_values(fields: list[dict[str, Any]], values: Any, *, strict: bool) -> tuple[dict[str, Any], list[str]]:
    """按字段类型规整回答。strict=提交: 必填缺了、格式不对都算问题; 草稿只规整不拦。"""
    values = values if isinstance(values, dict) else {}
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for f in fields:
        v = values.get(f["key"], values.get(f["label"]))
        empty = v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)
        if empty:
            if strict and f.get("required"):
                problems.append(f"「{f['label']}」必填")
            continue
        t = f["type"]
        try:
            if t == "number":
                num = float(str(v).replace(",", "").strip())
                clean[f["key"]] = int(num) if num.is_integer() else num
            elif t == "date":
                clean[f["key"]] = date.fromisoformat(str(v).strip()[:10]).isoformat()
            elif t == "choice":
                if str(v).strip() not in f["options"]:
                    raise ValueError(f"只能选 {' / '.join(f['options'])}")
                clean[f["key"]] = str(v).strip()
            elif t == "multichoice":
                picked = [str(x).strip() for x in (v if isinstance(v, list) else str(v).split(","))]
                bad = [x for x in picked if x and x not in f["options"]]
                if bad:
                    raise ValueError(f"{'、'.join(bad)} 不在选项里")
                clean[f["key"]] = [x for x in f["options"] if x in picked]
            else:
                clean[f["key"]] = str(v).strip()[:4000 if t == "longtext" else 500]
        except ValueError as e:
            msg = str(e) if t in ("choice", "multichoice") else {"number": "要填数字", "date": "要填日期 (年-月-日)"}.get(t, str(e))
            problems.append(f"「{f['label']}」{msg}")
    return clean, problems


def _is_late(deadline: str | None, at: str | None) -> bool:
    if not deadline or not at:
        return False
    try:
        return datetime.fromisoformat(at).date() > date.fromisoformat(deadline)
    except ValueError:
        return False


def _public_form(form: dict[str, Any]) -> dict[str, Any]:
    """发给收件人的那部分: 不带收件人名单、进度、模板路径。"""
    return {k: form.get(k) for k in ("title", "note", "deadline", "fields")}


# ── 发起人 ───────────────────────────────────────────────────────

def _norm_recipients(raw: Any, owner: str | None = None) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[\s,;，；]+", str(raw or ""))
    out = []
    for r in items:
        e = str(r).strip().lower()
        if not e:
            continue
        if not _EMAIL.match(e):
            raise ValueError(f"「{r}」不是邮箱")
        if e != owner and e not in out:
            out.append(e)
    return out


def create(args: dict[str, Any]) -> dict[str, Any]:
    """建表 (还没发)。template_path: 从 Excel 建的表, 留一份模板给导出用。"""
    title = str(args.get("title") or "").strip()
    if not title:
        raise ValueError("表要有个名字")
    deadline = str(args.get("deadline") or "").strip() or None
    if deadline:
        date.fromisoformat(deadline)
    owner = forms_net.me()
    form_id = f"f_{datetime.now():%Y%m%d}_{secrets.token_hex(4)}"
    form = {"form_id": form_id, "title": title[:80], "owner": owner, "created_at": _now(),
            "deadline": deadline, "note": str(args.get("note") or "").strip()[:500],
            "fields": normalize_fields(args.get("fields")), "status": "draft", "template": None,
            "recipients": {e: {"invited_at": None, "reminded_at": None}
                           for e in _norm_recipients(args.get("recipients"), owner)}}
    tpl = str(args.get("template_path") or "").strip()
    if tpl:
        dest = _out_dir(form_id) / "template.xlsx"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tpl, dest)
        form["template"] = "template.xlsx"
    _save_outgoing(form)
    return {"ok": True, "form": form}


def update_draft(form_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """还没发的表可以改名字 / 字段 / 截止 / 说明 / 收件人; 发出去以后只能加人。"""
    form = load_outgoing(form_id)
    if form["status"] == "draft":
        for k in ("title", "note"):
            if k in args:
                form[k] = str(args[k] or "").strip()[:80 if k == "title" else 500]
        if "deadline" in args:
            dl = str(args["deadline"] or "").strip() or None
            if dl:
                date.fromisoformat(dl)
            form["deadline"] = dl
        if "fields" in args:
            form["fields"] = normalize_fields(args["fields"])
    if "recipients" in args:
        keep = {e: v for e, v in form["recipients"].items() if v.get("invited_at")}
        for e in _norm_recipients(args["recipients"], form["owner"]):
            keep.setdefault(e, {"invited_at": None, "reminded_at": None})
        form["recipients"] = keep
    _save_outgoing(form)
    return {"ok": True, "form": form}


def send(form_id: str) -> dict[str, Any]:
    """给还没邀请过的收件人投邀请。一个人投失败不影响别人, 失败的下次再点发送会重试。"""
    form = load_outgoing(form_id)
    if form["status"] == "closed":
        raise ValueError("这张表已经结束收集了")
    todo = [e for e, v in form["recipients"].items() if not v.get("invited_at")]
    if not todo:
        raise ValueError("没有要发的人 (都发过了, 或还没加收件人)")
    env = {"v": ENVELOPE_VERSION, "form_id": form_id, "form": _public_form(form)}
    ttl = forms_net.ttl_seconds(form.get("deadline"))
    sent, failed = [], []
    for e in todo:
        try:
            forms_net.post(e, "form_invite", env, ttl)
            form["recipients"][e]["invited_at"] = _now()
            sent.append(e)
        except ValueError as err:
            failed.append({"to": e, "error": str(err)})
    if sent:
        form["status"] = "open"
    _save_outgoing(form)
    return {"ok": bool(sent), "sent": sent, "failed": failed}


def remind(form_id: str, text: str = "") -> dict[str, Any]:
    form = load_outgoing(form_id)
    if form["status"] != "open":
        raise ValueError("只有收集中的表能催")
    answered = responses_of(form_id)
    todo = [e for e, v in form["recipients"].items() if v.get("invited_at") and e not in answered]
    if not todo:
        return {"ok": True, "reminded": [], "note": "都交了 (或都回了不填), 不用催"}
    env = {"v": ENVELOPE_VERSION, "form_id": form_id, "text": str(text or "").strip()[:200]}
    ttl = forms_net.ttl_seconds(form.get("deadline"))
    done, failed = [], []
    for e in todo:
        try:
            forms_net.post(e, "form_remind", env, ttl)
            form["recipients"][e]["reminded_at"] = _now()
            done.append(e)
        except ValueError as err:
            failed.append({"to": e, "error": str(err)})
    _save_outgoing(form)
    return {"ok": bool(done), "reminded": done, "failed": failed}


def close(form_id: str) -> dict[str, Any]:
    form = load_outgoing(form_id)
    if form["status"] == "closed":
        return {"ok": True, "note": "已经结束了"}
    failed = []
    ttl = forms_net.ttl_seconds(None)
    for e, v in form["recipients"].items():
        if not v.get("invited_at"):
            continue
        try:
            forms_net.post(e, "form_close", {"v": ENVELOPE_VERSION, "form_id": form_id}, ttl)
        except ValueError as err:
            failed.append({"to": e, "error": str(err)})
    form["status"], form["closed_at"] = "closed", _now()
    _save_outgoing(form)
    return {"ok": True, "failed": failed}


def progress(form: dict[str, Any]) -> dict[str, Any]:
    answered = responses_of(form["form_id"])
    rows = []
    for e, v in form["recipients"].items():
        r = answered.get(e)
        if r is None:
            state = "未发" if not v.get("invited_at") else "未提交"
        elif r.get("status") == "declined":
            state = "不填"
        else:
            state = "逾期" if _is_late(form.get("deadline"), r.get("at")) else "已提交"
        rows.append({"email": e, "state": state, "at": (r or {}).get("at"), "reason": (r or {}).get("reason"),
                     "invited_at": v.get("invited_at"), "reminded_at": v.get("reminded_at")})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"rows": rows, "counts": counts, "total": len(rows)}


def responses_table(form_id: str) -> dict[str, Any]:
    """给小鲶读的: 字段 + 每个已提交的人一行 (按字段名)。"""
    form = load_outgoing(form_id)
    rows = []
    for e, r in responses_of(form_id).items():
        if r.get("status") != "submitted":
            continue
        vals = r.get("values") or {}
        rows.append({"填写人": e, "提交时间": r.get("at"),
                     "逾期": _is_late(form.get("deadline"), r.get("at")),
                     **{f["label"]: vals.get(f["key"]) for f in form["fields"]}})
    return {"ok": True, "title": form["title"], "deadline": form.get("deadline"),
            "fields": [f["label"] for f in form["fields"]], "rows": rows, "progress": progress(form)}


# ── 收件人 ───────────────────────────────────────────────────────

def save_draft(form_id: str, values: Any, sources: Any = None) -> dict[str, Any]:
    """草稿 (小鲶预填 / 本人改了一半)。不发出去。"""
    rec = load_incoming(form_id)
    clean, _ = check_values(rec["form"]["fields"], values, strict=False)
    src = {k: str(v)[:200] for k, v in (sources or {}).items() if k in clean} if isinstance(sources, dict) else {}
    rec["draft"] = {"values": clean, "sources": src, "updated_at": _now()}
    _write(_in_path(form_id), rec)
    return {"ok": True, "draft": rec["draft"], "filled": len(clean), "total": len(rec["form"]["fields"])}


def _respond(rec: dict[str, Any], env: dict[str, Any]) -> None:
    ttl = forms_net.ttl_seconds(rec["form"].get("deadline"), minimum=forms_net.MIN_RESPONSE_TTL_SECONDS)
    forms_net.post(rec["owner"], "form_response", env, ttl)


def submit(form_id: str, values: Any) -> dict[str, Any]:
    """本人点了提交才调 (Companion 表单按钮; 小鲶的工具里没有这个动作)。"""
    rec = load_incoming(form_id)
    if rec.get("status") == "closed":
        raise ValueError("发起人已经结束收集了, 交不了")
    clean, problems = check_values(rec["form"]["fields"], values, strict=True)
    if problems:
        return {"ok": False, "problems": problems}
    at = _now()
    _respond(rec, {"v": ENVELOPE_VERSION, "form_id": form_id, "status": "submitted", "values": clean, "at": at})
    rec["submitted"] = {"values": clean, "at": at}
    rec.pop("declined", None)
    _write(_in_path(form_id), rec)
    return {"ok": True, "submitted_at": at, "late": _is_late(rec["form"].get("deadline"), at)}


def decline(form_id: str, reason: str) -> dict[str, Any]:
    rec = load_incoming(form_id)
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("不填也说一句原因, 发起人好知道")
    at = _now()
    _respond(rec, {"v": ENVELOPE_VERSION, "form_id": form_id, "status": "declined", "reason": reason[:300], "at": at})
    rec["declined"] = {"reason": reason[:300], "at": at}
    _write(_in_path(form_id), rec)
    return {"ok": True}


# ── 收邮筒 ───────────────────────────────────────────────────────

def _apply(msg: dict[str, Any]) -> str | None:
    """把一封邮件落进本机账本。返回落成了什么 (给同步结果计数), 不认识的返回 None。"""
    env, kind, sender = msg["env"], msg["kind"], msg["from"]
    try:
        fid = _safe_id(env.get("form_id"))
    except ValueError:
        return None
    if kind == "form_invite":
        form = env.get("form") or {}
        try:
            fields = normalize_fields(form.get("fields"))
        except ValueError:
            return None
        old = _read(_in_path(fid)) or {}
        if old and old.get("owner") != sender:
            return None   # 同编号别人发的: 不覆盖
        rec = {**old, "form_id": fid, "owner": sender, "received_at": old.get("received_at") or _now(),
               "status": "open", "form": {"title": str(form.get("title") or "")[:80],
                                          "note": str(form.get("note") or "")[:500],
                                          "deadline": form.get("deadline"), "fields": fields}}
        _write(_in_path(fid), rec)
        return "invite"
    if kind in ("form_remind", "form_close"):
        rec = _read(_in_path(fid))
        if not rec or rec.get("owner") != sender:
            return None
        if kind == "form_remind":
            rec["reminded_at"], rec["remind_text"] = _now(), str(env.get("text") or "")[:200]
        else:
            rec["status"], rec["closed_at"] = "closed", _now()
        _write(_in_path(fid), rec)
        return "remind" if kind == "form_remind" else "close"
    if kind == "form_response":
        form = _read(_out_dir(fid) / "form.json")
        if not form or sender not in form.get("recipients", {}):
            return None   # 不是我这张表的收件人: 不收
        status = env.get("status")
        if status == "submitted":
            clean, _ = check_values(form["fields"], env.get("values"), strict=False)
            resp = {"from": sender, "status": "submitted", "values": clean, "at": env.get("at") or _now()}
        elif status == "declined":
            resp = {"from": sender, "status": "declined", "reason": str(env.get("reason") or "")[:300],
                    "at": env.get("at") or _now()}
        else:
            return None
        resp["received_at"] = _now()
        name = re.sub(r"[^0-9A-Za-z@._-]", "_", sender)
        _write(_out_dir(fid) / "responses" / f"{name}.json", resp)
        return "response"
    return None


def sync() -> dict[str, Any]:
    counts = {"invite": 0, "response": 0, "remind": 0, "close": 0}
    new_invites = []
    for msg in forms_net.take():
        got = _apply(msg)
        if got:
            counts[got] += 1
            if got == "invite":
                new_invites.append({"form_id": msg["env"].get("form_id"), "from": msg["from"],
                                    "title": (msg["env"].get("form") or {}).get("title")})
    return {"ok": True, **counts, "new_invites": new_invites}


# ── 列表 / 详情 ──────────────────────────────────────────────────

def _incoming_state(rec: dict[str, Any]) -> str:
    if rec.get("declined"):
        return "不填"
    if rec.get("submitted"):
        return "已提交"
    if rec.get("status") == "closed":
        return "已结束"
    return "草稿" if rec.get("draft") else "待填"


def list_all() -> dict[str, Any]:
    incoming = []
    for p in sorted((forms_dir() / "incoming").glob("*.json")):
        rec = _read(p)
        if rec:
            incoming.append({"form_id": rec["form_id"], "title": rec["form"]["title"], "owner": rec["owner"],
                             "deadline": rec["form"].get("deadline"), "state": _incoming_state(rec),
                             "received_at": rec.get("received_at"), "reminded_at": rec.get("reminded_at"),
                             "closed": rec.get("status") == "closed", "fields": len(rec["form"]["fields"])})
    outgoing = []
    for p in sorted((forms_dir() / "outgoing").glob("*/form.json")):
        form = _read(p)
        if form:
            outgoing.append({"form_id": form["form_id"], "title": form["title"], "status": form["status"],
                             "deadline": form.get("deadline"), "created_at": form.get("created_at"),
                             "progress": progress(form)["counts"], "recipients": len(form["recipients"])})
    incoming.sort(key=lambda r: r.get("received_at") or "", reverse=True)
    outgoing.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"ok": True, "incoming": incoming, "outgoing": outgoing}


def get(form_id: str) -> dict[str, Any]:
    if (_out_dir(form_id) / "form.json").exists():
        form = load_outgoing(form_id)
        return {"ok": True, "role": "owner", "form": form, "progress": progress(form)}
    rec = load_incoming(form_id)
    return {"ok": True, "role": "recipient", "record": rec, "state": _incoming_state(rec)}
