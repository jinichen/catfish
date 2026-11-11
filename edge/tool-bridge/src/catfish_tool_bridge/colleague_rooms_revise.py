"""同事群聊 · 群里一起改文档 (11/11, docs/DOC-REVISE-MERGE-PLAN.md)。

一轮修改 (rev_id) = 发起人在群里发一份文档请几位同事改 → 各自下载、用本机软件改、交回 → 发起人本机合并 → 群里出新版本。
全走现有的群消息和群文件 (端到端加密、中央只转交), 不开新的中央接口。群消息里多三种附加字段:

  revise         {rev_id, doc_id, title, version, fmt, name, owner, editors[], deadline, note}   发起人发, 带底稿文件
  revise_status  {rev_id, doc_id, title, state, version?}   state: editing 在改 / returned 交回了 / unchanged 看过没改 /
                 declined 不改了 / remind 催一下 / merged 发出合并版 (带合并版文件) / closed 结束
  revise_return  {rev_id, doc_id, version, unchanged, note}  修改人交回, **只发给发起人** (带修改稿); 其他人只收到 returned 状态

规矩:
  · 交回稿只有发起人和交回的人自己有; 群里其他人看到的是「王五交回了」和每一版合并稿。
  · 只有发起人能合并 (底稿和交回稿都在他电脑上)。全部交回后界面自动跑一次合并分析给他看, 不自动发。
  · 「下载修改」把文件写到 ~/Downloads/鲶鱼群文件/<群名>/<名字> (v1·待我修改).docx, docx / xlsx / pptx 里埋记号
    (doc_merge/marker.py), 同事直接把文件拖进群里也认得出是交回稿 (老版本鲶鱼发回来的普通文件同样认)。
    再点一次「下载修改」打开的还是那份 (不会盖掉改了一半的)。
  · 本机记录: store 的 revise 表 (每轮一行, 发起人 / 修改人各记各的) + revise_blobs (底稿、交回稿、合并版的副本,
    群文件按保留期删了也还能合并), 都跟群聊记录走同一个保留期限。
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

from . import colleague_rooms as cr
from . import colleague_rooms_files as rfiles
from . import doc_merge as dm
from . import forms_net
from .doc_merge import marker

REV_ID = re.compile(r"r_\d{8}_[0-9a-f]{6,16}")
DOC_ID = re.compile(r"d_[0-9a-f]{16}")
STATE_WORDS = {"editing": "在改", "returned": "交回了", "unchanged": "看过, 没改", "declined": "不改了",
               "remind": "催了一下", "merged": "发出了合并版", "closed": "结束了修改"}
EDITOR_STATES = ("editing", "returned", "unchanged", "declined")
OWNER_STATES = ("remind", "merged", "closed")
OOXML = (".docx", ".xlsx", ".pptx")
MERGED = "__merged__"   # 迟到的交回: 上一版合并稿当成一位「交回人」参与三方合并


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ── 本机记录 ──────────────────────────────────────────────────────

def _get(rev_id: str) -> dict[str, Any] | None:
    with cr.store.tx() as con:
        r = con.execute("SELECT data FROM revise WHERE rev_id = ?", (rev_id,)).fetchone()
    return json.loads(r["data"]) if r else None


def _put(rec: dict[str, Any]) -> None:
    with cr.store.tx() as con:
        con.execute("INSERT OR REPLACE INTO revise (rev_id, room_id, doc_id, msg_id, data) VALUES (?, ?, ?, ?, ?)",
                    (rec["rev_id"], rec["room_id"], rec["doc_id"], rec["msg_id"], json.dumps(rec, ensure_ascii=False)))


def _blob_put(doc_id: str, name: str, data: bytes) -> None:
    with cr.store.tx() as con:
        con.execute("INSERT OR REPLACE INTO revise_blobs (doc_id, name, data, at) VALUES (?, ?, ?, ?)", (doc_id, name, data, _now()))


def _blob(doc_id: str, name: str) -> bytes | None:
    with cr.store.tx() as con:
        r = con.execute("SELECT data FROM revise_blobs WHERE doc_id = ? AND name = ?", (doc_id, name)).fetchone()
    return bytes(r["data"]) if r else None


def file_bytes(room_id: str, file_id: str) -> bytes:
    """群文件的内容 (小文件在库里, 大文件本机是密文, 解一份临时的读出来)。"""
    from . import colleague_rooms_bigfiles as big  # noqa: PLC0415
    b = big.row(room_id, file_id)
    if b:
        if b["state"] not in ("ready", "sent", "local", "uploading"):
            raise ValueError("这个文件还没收下来, 稍等再试")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td, "f")
            big.write_plain(b, p)
            return p.read_bytes()
    with cr.store.tx() as con:
        r = con.execute("SELECT data FROM files WHERE room_id = ? AND file_id = ?", (room_id, file_id)).fetchone()
    if not r:
        raise ValueError("这个文件还没收齐 (或者已经过了保留期), 稍等再试")
    return bytes(r["data"])


def _card_msg(room_id: str, rev_id: str) -> dict[str, Any]:
    with cr.store.tx() as con:
        for r in con.execute("SELECT data FROM messages WHERE room_id = ? AND kind = 'revise' ORDER BY seq DESC", (room_id,)):
            m = json.loads(r["data"])
            if (m.get("revise") or {}).get("rev_id") == rev_id:
                return m
    raise LookupError("找不到这一轮修改 (可能已经过了保留期)")


def find_card(room_id: str, rev_id: str) -> dict[str, Any] | None:
    try:
        return _card_msg(room_id, rev_id)["revise"]
    except LookupError:
        return None


def _round_msgs(room_id: str, rev_id: str) -> list[dict[str, Any]]:
    out = []
    with cr.store.tx() as con:
        for r in con.execute("SELECT seq, data FROM messages WHERE room_id = ? AND kind IN ('revise_status', 'revise_return', 'text')"
                             " ORDER BY seq", (room_id,)):
            m = json.loads(r["data"])
            st = m.get("revise_status") or m.get("revise_return") or {}
            if st.get("rev_id") == rev_id or (m.get("kind") == "text" and m.get("files")):
                out.append(m)
    return out


# ── 收到的信: 核对 (colleague_rooms_tasks.classify 转过来) ─────────────────

def _clean_card(raw: Any, sender: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or str(raw.get("owner") or "").lower() != sender:
        return None   # 只认发起人自己发的
    rid, did = str(raw.get("rev_id") or ""), str(raw.get("doc_id") or "")
    if not REV_ID.fullmatch(rid) or not DOC_ID.fullmatch(did):
        return None
    try:
        version = max(1, min(9999, int(raw.get("version") or 1)))
    except (TypeError, ValueError):
        return None
    eds = [str(x).lower() for x in raw.get("editors") or [] if isinstance(x, str) and "@" in x][: cr.MAX_MEMBERS]
    name = PurePath(str(raw.get("name") or "")).name[:120]
    return {"rev_id": rid, "doc_id": did, "title": str(raw.get("title") or "")[:80], "version": version,
            "fmt": dm.fmt_of(name) or "whole", "name": name, "owner": sender, "editors": eds,
            "deadline": str(raw.get("deadline") or "")[:10] or None, "note": str(raw.get("note") or "")[:500]}


def classify(env: dict[str, Any], rid: str, sender: str, ments: list[str]) -> tuple[str, dict[str, Any], list[str]] | None:
    if "revise" in env:
        card = _clean_card(env.get("revise"), sender)
        return ("revise", {"revise": card}, ments) if card else None
    if "revise_status" in env:
        raw = env.get("revise_status")
        if not isinstance(raw, dict) or raw.get("state") not in STATE_WORDS:
            return None
        card = find_card(rid, str(raw.get("rev_id") or ""))
        if not card:
            return None
        if raw["state"] in OWNER_STATES and sender != card["owner"]:
            return None   # 催 / 合并 / 结束只认发起人
        if raw["state"] in EDITOR_STATES and sender not in card["editors"]:
            return None   # 不是这一轮请的人: 不收 (防冒名)
        st = {"rev_id": card["rev_id"], "doc_id": card["doc_id"], "title": card["title"], "state": raw["state"]}
        if raw["state"] == "merged":
            try:
                st["version"] = max(1, min(9999, int(raw.get("version") or 0)))
            except (TypeError, ValueError):
                return None
        return "revise_status", {"revise_status": st}, (ments if raw["state"] == "remind" else [])
    raw = env.get("revise_return")
    card = find_card(rid, str((raw or {}).get("rev_id") or "")) if isinstance(raw, dict) else None
    try:
        me = forms_net.me()
    except ValueError:
        return None
    if not card or sender not in card["editors"] or card["owner"] != me:
        return None   # 交回稿只发给发起人; 别人收到 / 不是请的人交的都不收
    return "revise_return", {"revise_return": {"rev_id": card["rev_id"], "doc_id": card["doc_id"], "version": card["version"],
                                                "unchanged": raw.get("unchanged") is True, "note": str(raw.get("note") or "")[:500]}}, []


# ── 发起 ─────────────────────────────────────────────────────────

def _versions_seen(room_id: str, doc_id: str) -> int:
    top = 0
    with cr.store.tx() as con:
        for r in con.execute("SELECT data FROM messages WHERE room_id = ? AND kind IN ('revise', 'revise_status')", (room_id,)):
            m = json.loads(r["data"])
            x = m.get("revise") or m.get("revise_status") or {}
            if x.get("doc_id") == doc_id:
                top = max(top, int(x.get("version") or 0))
    return top


def start(room_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """发起一轮: 文件来自群里已有的文件 (msg_id + file_id) 或刚选的 (files: [{name, stage_id}])。"""
    me = forms_net.me()
    room = cr.load(room_id)
    if room.get("my_state") != "joined":
        raise ValueError("你不在这个群里")
    others = cr._joined_others(room, me)
    eds = [e for e in dict.fromkeys(str(x).lower() for x in params.get("editors") or others) if e in others]
    if not eds:
        raise ValueError("请谁改? 群里已加入的同事里选几位")
    if params.get("path") and not params.get("file_id"):   # 小鲶 (catfish_form revise_start) 给的本机文件
        src = Path(str(params["path"])).expanduser()
        if not src.is_file():
            raise ValueError(f"找不到文件: {src}")
        if src.stat().st_size > 200 * 1024 * 1024:
            raise ValueError("文件太大 (最多 200MB)")
        params = {**params, "files": None}
        name, data = rfiles._clean_name(src.name), src.read_bytes()
    elif params.get("file_id"):
        msg = cr.store.get_message(room["room_id"], str(params.get("msg_id") or ""))
        f = next((f for f in (msg or {}).get("files") or [] if f.get("id") == params["file_id"]), None)
        if not f:
            raise ValueError("找不到这个文件")
        name, data = f["name"], file_bytes(room["room_id"], f["id"])
    else:
        items = params.get("files") or []
        if len(items) != 1:
            raise ValueError("一次请大家改一份文件")
        from . import colleague_rooms_bigfiles as big  # noqa: PLC0415
        name, data = rfiles._clean_name(items[0].get("name")), big.staged(str(items[0].get("stage_id") or "")).read_bytes()
    data = marker.strip(data)
    doc_id = str(params.get("doc_id") or "")
    if doc_id and DOC_ID.fullmatch(doc_id):
        version = max(1, _versions_seen(room["room_id"], doc_id))
    else:
        doc_id, version = f"d_{secrets.token_hex(8)}", 1
    rev_id = f"r_{datetime.now().strftime('%Y%m%d')}_{secrets.token_hex(4)}"
    title = str(params.get("title") or "").strip()[:80] or PurePath(name).stem[:80]
    dl = str(params.get("deadline") or "").strip()[:10] or None
    if dl and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dl):
        raise ValueError("截止日期格式不对")
    note = str(params.get("note") or "").strip()[:500]
    card = {"rev_id": rev_id, "doc_id": doc_id, "title": title, "version": version, "fmt": dm.fmt_of(name) or "whole",
            "name": name, "owner": me, "editors": eds, "deadline": dl, "note": note}
    if _blob(doc_id, f"v{version}") is None:
        _blob_put(doc_id, f"v{version}", data)
    body = f"📝 请修改《{title}》(v{version}{', ' + dl[5:].replace('-', '/') + ' 截止' if dl else ''})" + (f"\n{note}" if note else "")
    out = cr.send(room["room_id"], body[: cr.TEXT_MAX], eds, None, [{"name": name, "data": base64.b64encode(data).decode()}],
                  kind="revise", extra_in={"revise": card})
    _put({**card, "room_id": room["room_id"], "msg_id": out["message"]["msg_id"], "role": "owner", "at": out["message"]["at"],
          "decisions": {}, "suggestions": {}})
    hint = None
    if card["fmt"] == "whole":
        ext = PurePath(name).suffix.lower()
        hint = (f"{ext} 是老格式, 先另存为 {dm.OLD_OFFICE[ext]} 再发, 才能自动合并; 现在这样大家交回后只能整份选用"
                if ext in dm.OLD_OFFICE else "这种格式不能自动合并, 大家交回后只能整份选用一份")
    return {"ok": True, "rev_id": rev_id, "message": out["message"], "hint": hint}


# ── 修改人: 下载修改 / 交回 / 不改了 ────────────────────────────────────

def _status(room_id: str, card: dict[str, Any], state: str, *, to: list[str] | None = None, record: bool = True,
            ments: list[str] | None = None, body: str | None = None, files: list[dict[str, Any]] | None = None,
            version: int | None = None) -> dict[str, Any]:
    me = forms_net.me()
    room = cr.load(room_id)
    st = {"rev_id": card["rev_id"], "doc_id": card["doc_id"], "title": card["title"], "state": state,
          **({"version": version} if version else {})}
    text = body or f"{cr._label(room, me)} {STATE_WORDS[state]}《{card['title']}》"
    return cr.send(room_id, text[: cr.TEXT_MAX], ments or [], None, files, kind="revise_status", extra_in={"revise_status": st},
                   to=to, record=record)


def _editor_rec(room_id: str, card: dict[str, Any], msg_id: str) -> dict[str, Any]:
    if any((m.get("revise_status") or {}).get("state") == "closed" for m in _round_msgs(room_id, card["rev_id"])):
        raise ValueError(f"{card['title']} 这一轮已经结束了, 不能再改 / 交回")
    rec = _get(card["rev_id"])   # 发起人自己也改 (11/11 「我也改」): 用的就是他那条发起人记录
    return rec or {**card, "room_id": room_id, "msg_id": msg_id, "role": "editor"}


def _may_edit(me: str, card: dict[str, Any]) -> None:
    if me not in card["editors"] and me != card["owner"]:
        raise ValueError("这一轮没请你改 (可以先看原文件)")


# Mac 上 .pptx / .docx / .xlsx 默认常是 Keynote / Pages / Numbers 打开: 改完一存, 存成的是 .key / .pages / .numbers
# (11/11 鸿波实测: Keynote 存到了 ~/Downloads/…(v1·待我修改).key), 下载的那份 Office 文件一点没变, 交回时只会被判「没改」。
_APPLE = {".pptx": (".key", "Keynote", "PowerPoint"), ".docx": (".pages", "Pages", "Word"), ".xlsx": (".numbers", "Numbers", "Excel")}


def _saved_by_apple_app(p: Path) -> dict[str, str] | None:
    """下载的那份没变, 但同名的 .key / .pages / .numbers 在它之后存过 → 告诉员工改动存到哪去了、怎么导出回来。"""
    hit = _APPLE.get(p.suffix.lower())
    if not hit:
        return None
    ext, app, office = hit
    try:
        since = p.stat().st_mtime
    except OSError:
        return None
    found: list[Path] = []
    for d in dict.fromkeys([p.parent, Path.home() / "Downloads", Path.home() / "Documents", Path.home() / "Desktop"]):
        try:
            found += [f for f in d.iterdir() if f.name.startswith(p.stem) and f.suffix.lower() == ext and f.stat().st_mtime >= since]
        except OSError:
            continue
    if not found:
        return None
    f = max(found, key=lambda x: x.stat().st_mtime)
    return {"app": app, "office": office, "saved": str(f), "target": str(p)}


def _open_in_app(p: Path) -> bool:
    """文件还在 Word / Excel / PowerPoint / WPS / LibreOffice 里开着 (多半还没存或没关)。
    Office 和 WPS 打开文件时在同目录放 `~$名字` 锁文件 (名字长时开头两个字被 ~$ 顶掉), LibreOffice 放 `.~lock.名字#`。"""
    try:
        names = [x.name for x in p.parent.iterdir()]
    except OSError:
        return False
    n = p.name
    return any((x.startswith("~$") and len(x) > 3 and n.endswith(x[2:]) and len(x) >= len(n)) or x == f".~lock.{n}#"
               for x in names)


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")[:80] or "文档"


def open_copy(room_id: str, rev_id: str) -> dict[str, Any]:
    """「下载修改」: 写一份带记号的副本到下载目录, 返回路径给界面打开。已经下过就还是那份。"""
    me = forms_net.me()
    room = cr.load(room_id)
    msg = _card_msg(room["room_id"], rev_id)
    card = msg["revise"]
    _may_edit(me, card)
    rec = _editor_rec(room["room_id"], card, msg["msg_id"])
    if rec.get("local_path") and Path(rec["local_path"]).exists():
        return {"ok": True, "path": rec["local_path"], "again": True}
    f = (msg.get("files") or [None])[0]
    if not f:
        raise ValueError("这一轮的文件不见了")
    data = file_bytes(room["room_id"], f["id"])
    ext = PurePath(card["name"]).suffix
    if ext.lower() in OOXML:
        data = marker.embed(data, f"{rev_id}:v{card['version']}")
    folder = Path.home() / "Downloads" / "鲶鱼群文件" / rfiles._safe_dir(room.get("name") or room["room_id"])
    folder.mkdir(parents=True, exist_ok=True)
    target, k = folder / f"{_safe(PurePath(card['name']).stem)} (v{card['version']}·待我修改){ext}", 1
    while target.exists():
        target = folder / f"{_safe(PurePath(card['name']).stem)} (v{card['version']}·待我修改 {k}){ext}"
        k += 1
    target.write_bytes(data)
    first = not rec.get("state")
    rec.update({"local_path": str(target), "state": rec.get("state") or "editing", "opened_at": _now()})
    _put(rec)
    if first and me != card["owner"]:   # 发起人自己改不往群里报「在改」
        try:
            _status(room["room_id"], card, "editing")
        except ValueError:
            pass   # 状态发不出去不影响改
    return {"ok": True, "path": str(target)}


def _staged(stage_id: Any) -> Path:
    from . import colleague_rooms_bigfiles as big  # noqa: PLC0415
    return big.staged(str(stage_id or ""))


def detect(room_id: str, stage_id: Any, name: Any) -> dict[str, Any]:
    """员工把文件拖进 / 选进输入框: 带这个群里某一轮的记号、而且是请他改的 → 提示「这是《…》的修改, 交回给张三?」。"""
    if PurePath(str(name or "")).suffix.lower() not in OOXML:
        return {"ok": True, "rev_id": None}
    mk = marker.read(_staged(stage_id).read_bytes())
    room = cr.load(room_id)
    card = find_card(room["room_id"], mk.split(":")[0]) if mk else None
    if not card or forms_net.me() not in card["editors"]:
        return {"ok": True, "rev_id": None}
    return {"ok": True, "rev_id": card["rev_id"], "title": card["title"], "version": card["version"],
            "owner": card["owner"], "owner_name": cr._label(room, card["owner"])}


def give_back(room_id: str, rev_id: str, path: Any = None, note: Any = None, confirm_unchanged: bool = False,
              stage_id: Any = None, name: Any = None, confirm_open: bool = False) -> dict[str, Any]:
    """「交回」: 默认交刚才下载的那份; 也可以是员工另选 / 拖进来的文件 (stage_id + name)。
    先问一句的两种 (need_confirm): 文件还在 Word / WPS 里开着 (open, 改了没存就交上去的是旧的); 一点没改 (unchanged)。
    发起人自己也改的 (「我也改」): 不发消息, 存在本机, 合并时跟大家交回的一起合。"""
    me = forms_net.me()
    room = cr.load(room_id)
    msg = _card_msg(room["room_id"], rev_id)
    card = msg["revise"]
    _may_edit(me, card)
    rec = _editor_rec(room["room_id"], card, msg["msg_id"])
    if stage_id:
        p, suffix = _staged(stage_id), PurePath(str(name or "")).suffix
    else:
        p = Path(str(path or rec.get("local_path") or "")).expanduser()
        suffix = p.suffix
        if not str(path or rec.get("local_path") or "") or not p.is_file():
            raise ValueError("找不到要交回的文件: 先点「下载修改」, 或选一下改好的文件")
        if not confirm_open and _open_in_app(p):
            return {"ok": False, "need_confirm": "open"}
    ext = PurePath(card["name"]).suffix.lower()
    if suffix.lower() != ext:
        raise ValueError(f"交回的文件类型 ({suffix or '无后缀'}) 跟原文件 ({ext}) 不一样")
    data = p.read_bytes()
    mk = marker.read(data) if ext in OOXML else None
    if mk and not mk.startswith(rev_id + ":"):
        raise ValueError("这份文件是另一轮修改下载的, 不是这一轮的 —— 换成这一轮「下载修改」的那份")
    base = file_bytes(room["room_id"], (msg.get("files") or [{}])[0].get("id", ""))
    unchanged = marker.strip(data) == base
    if not unchanged and card["fmt"] != "whole":
        try:
            unchanged = me in dm.analyze(card["fmt"], base, [dm.Return(me, data)], dm.Ctx(card["owner"]))["same"]
        except Exception as e:  # noqa: BLE001  (文件坏了 / 根本不是同一类文件)
            raise ValueError(f"这份文件读不出来, 交不了: {e}") from None
    if unchanged and not confirm_unchanged:
        return {"ok": False, "need_confirm": "unchanged", **({"saved_elsewhere": got} if (got := _saved_by_apple_app(p)) else {})}
    if me == card["owner"]:
        rec.update({"state": "unchanged" if unchanged else "returned", "returned_at": _now()})
        rec.pop("self_return", None)
        if not unchanged:
            key = f"self:{rev_id}:{secrets.token_hex(4)}"
            _blob_put(card["doc_id"], key, marker.strip(data) if ext in OOXML else data)
            rec["self_return"] = {"key": key, "at": _now(), "after_version": (rec.get("merged") or {}).get("version")}
        _put(rec)
        return {"ok": True, "self": True}
    joined = cr._joined_others(room, me)
    if card["owner"] not in joined:
        raise ValueError("发起人已经不在群里了, 交不回去 —— 私下发给他吧")
    others = [e for e in joined if e != card["owner"]]
    label = cr._label(room, me)
    ret = {"rev_id": rev_id, "doc_id": card["doc_id"], "version": card["version"], "unchanged": unchanged,
           "note": str(note or "").strip()[:500]}
    body = f"{'看过《' + card['title'] + '》, 没改' if unchanged else '交回了《' + card['title'] + '》的修改'}" + (f"\n{ret['note']}" if ret["note"] else "")
    files = None if unchanged else [{"name": f"{_safe(PurePath(card['name']).stem)} (v{card['version']}·{_safe(label)}改){ext}",
                                     "data": base64.b64encode(marker.strip(data) if ext in OOXML else data).decode()}]
    out = cr.send(room["room_id"], body[: cr.TEXT_MAX], [], None, files, kind="revise_return",
                  extra_in={"revise_return": ret}, to=[card["owner"]])
    if others:
        try:
            _status(room["room_id"], card, "unchanged" if unchanged else "returned", to=others, record=False)
        except ValueError:
            pass
    rec.update({"state": "unchanged" if unchanged else "returned", "returned_at": _now()})
    _put(rec)
    return {"ok": True, "message": out["message"]}


def decline(room_id: str, rev_id: str, note: Any = None) -> dict[str, Any]:
    me = forms_net.me()
    room = cr.load(room_id)
    msg = _card_msg(room["room_id"], rev_id)
    card = msg["revise"]
    if me not in card["editors"]:
        raise ValueError("这一轮没请你改")
    rec = _editor_rec(room["room_id"], card, msg["msg_id"])
    reason = str(note or "").strip()[:200]
    out = _status(room["room_id"], card, "declined", body=f"{cr._label(room, me)} 不改《{card['title']}》了" + (f": {reason}" if reason else ""))
    rec.update({"state": "declined"})
    _put(rec)
    return {"ok": True, "message": out["message"]}


# ── 进度 ─────────────────────────────────────────────────────────

def _late(card: dict[str, Any], at: str) -> bool:
    return bool(card.get("deadline")) and at[:10] > card["deadline"]


def progress(room_id: str, rev_id: str) -> dict[str, Any]:
    """一轮修改的进度 (卡片上显示)。发起人看得到每个人交回的文件; 修改人看得到自己的状态和下载的那份在哪。"""
    me = forms_net.me()
    room = cr.load(room_id)
    msg = _card_msg(room["room_id"], rev_id)
    card = msg["revise"]
    owner = card["owner"] == me
    people = {e: {"email": e, "name": cr._label(room, e), "state": "pending", "at": None, "late": False} for e in card["editors"]}
    merged, closed, n_merged = None, False, 0
    rec = _get(rev_id) or {}
    seen = rec.setdefault("marker_seen", {}) if owner and rec else {}   # 群里普通文件读过记号的, 记住 (不每次重读大文件)
    seen_before = dict(seen)
    for m in _round_msgs(room["room_id"], rev_id):
        who = m.get("from") or me
        st = m.get("revise_status")
        if st:
            if st["state"] in EDITOR_STATES and who in people:
                p = people[who]
                if not (st["state"] == "editing" and p["state"] in ("returned", "unchanged")):
                    p.update({"state": st["state"], "at": m["at"]})
            elif st["state"] == "merged":
                merged = {"version": st.get("version"), "at": m["at"], "msg_id": m["msg_id"]}
                n_merged += 1
            elif st["state"] == "closed":
                closed = True
            continue
        ret = m.get("revise_return")
        if ret and who in people:
            people[who].update({"state": "unchanged" if ret.get("unchanged") else "returned", "at": m["at"], "epoch": n_merged,
                                "msg_id": m["msg_id"], "file": (m.get("files") or [None])[0], "note": ret.get("note") or ""})
            continue
        if owner and m.get("kind") == "text" and who in people and card["fmt"] in ("docx", "xlsx", "pptx"):
            f = next((f for f in m.get("files") or [] if PurePath(f["name"]).suffix.lower() == "." + card["fmt"]), None)
            if f:   # 老版本鲶鱼直接发回群里的普通文件: 带这一轮的记号就算交回 (记号只有下载修改之后才有, 不用再比时间)
                if f["id"] in seen:
                    mk = seen[f["id"]] or None
                else:
                    try:
                        mk = marker.read(file_bytes(room["room_id"], f["id"]))
                        seen[f["id"]] = mk or ""
                    except ValueError:
                        mk = None   # 还没收齐: 不记, 下次再读
                if mk and mk.startswith(rev_id + ":"):
                    people[who].update({"state": "returned", "at": m["at"], "msg_id": m["msg_id"], "file": f, "note": "", "plain": True,
                                        "epoch": n_merged})
    for p in people.values():
        p["late"] = bool(p["at"]) and p["state"] in ("returned", "unchanged") and _late(card, p["at"])
        if n_merged and p["state"] == "returned" and p.pop("epoch", 0) == n_merged:
            p["after_merge"] = True   # 最近一版合并稿发出之后才交回的 (按消息先后, 不比时间: 同一秒也分得清)
        p.pop("epoch", None)
        if not owner:
            p.pop("file", None)
            p.pop("note", None)
    if seen != seen_before:
        _put(rec)
    done = all(p["state"] in ("returned", "unchanged", "declined") for p in people.values())
    plist = list(people.values())
    if owner and rec.get("state"):   # 发起人「我也改」: 排在最后, 不算进「都交了」
        sr = rec.get("self_return") or {}
        plist.append({"email": me, "name": "我", "state": rec["state"], "at": sr.get("at") or rec.get("opened_at"), "late": False,
                      "self": True, **({"after_merge": True} if merged and sr and sr.get("after_version") == merged["version"] else {})})
    return {"ok": True, "card": card, "owner": owner, "me": me, "people": plist, "merged": merged, "closed": closed,
            "all_in": done, "can_merge": owner and any(p["state"] == "returned" for p in plist),
            "my_state": rec.get("state") if owner else (people.get(me) or {}).get("state"), "local_path": rec.get("local_path"),
            "file": (msg.get("files") or [None])[0], "msg_id": msg["msg_id"]}


PENDING_MAX_AGE_DAYS = 30


def pending() -> dict[str, Any]:
    """「协同事项」里的待办: 各群里请我改、我还没交回 / 没说不改、这一轮也没结束的 (30 天内发起的)。截止早的排前面。
    截止提醒在 Companion 本机弹 (今天 / 明天截止), 不往群里自动发 —— 群里的消息都得员工自己点了才发。"""
    me = forms_net.me()
    today = datetime.now().date()
    out = []
    for rid in cr._room_ids():
        room = cr._room(rid)
        if not room or room.get("my_state") != "joined":
            continue
        with cr.store.tx() as con:
            cards = [json.loads(r["data"]) for r in con.execute(
                "SELECT data FROM messages WHERE room_id = ? AND kind = 'revise' ORDER BY seq", (rid,))]
        for m in cards:
            card = m.get("revise") or {}
            if me not in card.get("editors", []):
                continue
            try:
                if (today - datetime.fromisoformat(m["at"]).date()).days > PENDING_MAX_AGE_DAYS:
                    continue
                pr = progress(rid, card["rev_id"])
            except (ValueError, LookupError):
                continue
            if pr["closed"] or pr["my_state"] not in ("pending", "editing"):
                continue
            dl = card.get("deadline")
            days = (datetime.fromisoformat(dl).date() - today).days if dl else None
            out.append({"room_id": rid, "room_name": room.get("name") or "", "rev_id": card["rev_id"], "title": card["title"],
                        "version": card["version"], "owner_name": cr._label(room, card["owner"]), "deadline": dl, "days_left": days,
                        "state": pr["my_state"], "local_path": pr["local_path"], "msg_id": m["msg_id"], "at": m["at"]})
    out.sort(key=lambda x: (x["days_left"] is None, x["days_left"] if x["days_left"] is not None else 0, x["at"]))
    return {"ok": True, "items": out}


def remind(room_id: str, rev_id: str) -> dict[str, Any]:
    pr = progress(room_id, rev_id)
    if not pr["owner"]:
        raise ValueError("只有发起人能催")
    wait = [p for p in pr["people"] if p["state"] in ("pending", "editing") and not p.get("self")]
    if not wait:
        raise ValueError("大家都交了 (或说了不改)")
    names = "、".join(p["name"] for p in wait)
    out = _status(cr.load(room_id)["room_id"], pr["card"], "remind", ments=[p["email"] for p in wait],
                  body=f"⏰ 《{pr['card']['title']}》还等 {names} 交回" + (f" ({pr['card']['deadline'][5:].replace('-', '/')} 截止)" if pr["card"].get("deadline") else ""))
    return {"ok": True, "message": out["message"]}


def close(room_id: str, rev_id: str) -> dict[str, Any]:
    pr = progress(room_id, rev_id)
    if not pr["owner"]:
        raise ValueError("只有发起人能结束")
    out = _status(cr.load(room_id)["room_id"], pr["card"], "closed")
    return {"ok": True, "message": out["message"]}


# ── 合并 (发起人本机) ───────────────────────────────────────────────

def _owner_rec(rev_id: str) -> dict[str, Any]:
    rec = _get(rev_id)
    if not rec or rec.get("role") != "owner":
        raise ValueError("只有发起人能合并")
    return rec


def _inputs(room_id: str, rev_id: str) -> tuple[dict[str, Any], dict[str, Any], bytes, list[dm.Return], dict[str, str], bool]:
    """(进度, 本机记录, 底稿, 参与合并的交回稿 (按交回先后), 名字, 是不是迟到的那批)。"""
    pr = progress(room_id, rev_id)
    rec = _owner_rec(rev_id)
    card = pr["card"]
    base = _blob(card["doc_id"], f"v{card['version']}")
    if base is None:
        raise ValueError("底稿找不到了 (过了保留期), 这一轮合不了")
    names = {p["email"]: p["name"] for p in pr["people"]}
    names[pr["me"]] = cr._label(cr.load(room_id), pr["me"])
    rows = [p for p in pr["people"] if p["state"] == "returned" and p.get("file")]
    late = bool(pr["merged"])
    if late:
        rows = [p for p in rows if p.get("after_merge")]
    rows.sort(key=lambda p: p["at"])
    rets = []
    if late:
        prev = _blob(card["doc_id"], f"v{pr['merged']['version']}")
        if prev is None:
            raise ValueError("上一版合并稿找不到了, 迟到的修改合不了")
        rets.append(dm.Return(MERGED, prev, pr["merged"]["at"]))
        names[MERGED] = f"v{pr['merged']['version']} 合并版"
    for p in rows:
        key = f"ret:{rev_id}:{p['email']}:{p['msg_id']}"
        data = _blob(card["doc_id"], key)
        if data is None:
            data = marker.strip(file_bytes(room_id, p["file"]["id"]))
            _blob_put(card["doc_id"], key, data)
        rets.append(dm.Return(p["email"], data, p["at"]))
    sr = rec.get("self_return")   # 发起人「我也改」的那份 (迟到那批只要合并版发出之后改的)
    if sr and (sr.get("after_version") == (pr["merged"] or {}).get("version")):
        data = _blob(card["doc_id"], sr["key"])
        if data is not None:
            rets.append(dm.Return(pr["me"], data, sr["at"]))
    rets.sort(key=lambda r: (r.who != MERGED, r.at))
    if not [r for r in rets if r.who != MERGED]:
        raise ValueError("还没有人交回修改" if not late else "合并版发出之后还没有新交回的")
    return pr, rec, base, rets, names, late


def _whole(rets: list[dm.Return], names: dict[str, str], why: str | None) -> dict[str, Any]:
    opts = [{"key": r.who, "who": [r.who], "text": f"{names.get(r.who, r.who)} 交回的版本"} for r in rets]
    return {"conflicts": [{"cid": "whole", "where": "整份文件", "kind": "whole", "base": "原文件", "options": opts, "combined": None}],
            "changes": {}, "same": [], "warnings": [why] if why else []}


def merge(room_id: str, rev_id: str) -> dict[str, Any]:
    """合并分析: 自动合了几处、哪些冲突要定。不出文件。"""
    pr, rec, base, rets, names, late = _inputs(room_id, rev_id)
    card = pr["card"]
    ctx = dm.Ctx(pr["me"], names, _now())
    if card["fmt"] == "whole":
        out = _whole(rets, names, None)
    else:
        try:
            out = dm.analyze(card["fmt"], base, rets, ctx)
        except Exception as e:  # noqa: BLE001
            out = _whole(rets, names, f"自动合并没成功 ({e}), 只能整份选用一份")
            rec["whole_reason"] = out["warnings"][0]
            _put(rec)
    for c in out["conflicts"]:
        for o in c["options"]:
            o["names"] = [names.get(x, x) for x in o["who"]]
        c["suggest"] = (rec.get("suggestions") or {}).get(c["cid"])
    return {"ok": True, **out, "names": names, "late": late, "decisions": rec.get("decisions") or {},
            "returned": [r.who for r in rets if r.who != MERGED]}


def decide(room_id: str, rev_id: str, cid: Any, pick: Any, text: Any = None) -> dict[str, Any]:
    rec = _owner_rec(rev_id)
    cid = str(cid or "")
    if not cid:
        raise ValueError("哪一处?")
    dec = rec.setdefault("decisions", {})
    if pick is None:
        dec.pop(cid, None)
    else:
        dec[cid] = {"pick": str(pick), **({"text": str(text)[:5000]} if pick == "text" else {})}
    _put(rec)
    return {"ok": True, "decisions": dec}


def _report(changes: dict[str, int], names: dict[str, str], n_conf: int, same: list[str]) -> str:
    parts = [f"{names.get(w, w)} {n} 处" for w, n in changes.items() if w != MERGED and n]
    out = ("合并了 " + "、".join(parts)) if parts else "没有可合并的改动"
    if n_conf:
        out += f"; 冲突 {n_conf} 处已定"
    if same:
        out += "; 看过没改: " + "、".join(names.get(w, w) for w in same)
    return out


def publish(room_id: str, rev_id: str, note: Any = None) -> dict[str, Any]:
    """发出合并版: 按定好的冲突出新版本, 发到群里 (Word / Excel / PPT 带一份修订版和一份干净版)。"""
    pr, rec, base, rets, names, late = _inputs(room_id, rev_id)
    card = pr["card"]
    dec = rec.get("decisions") or {}
    ctx = dm.Ctx(pr["me"], names, _now())
    whole = card["fmt"] == "whole" or rec.get("whole_reason")
    if whole:
        pick = (dec.get("whole") or {}).get("pick")
        got = next((r for r in rets if r.who == pick), None)
        if got is None:
            raise dm.Undecided("还没选用谁的版本")
        out = {"tracked": got.data, "clean": got.data, "changes": {pick: 1}, "warnings": []}
    else:
        out = dm.render(card["fmt"], base, rets, dec, ctx)
    version = max(_versions_seen(room_id, card["doc_id"]), card["version"]) + 1
    ext = PurePath(card["name"]).suffix
    stem = _safe(card["title"])
    clean = marker.strip(out["clean"])
    tracked = marker.strip(out["tracked"])
    files = [{"name": f"{stem} v{version}{ext}", "data": base64.b64encode(clean).decode()}]
    if tracked != clean:
        files.insert(0, {"name": f"{stem} v{version} 修订版{ext}", "data": base64.b64encode(tracked).decode()})
    _blob_put(card["doc_id"], f"v{version}", clean)
    an = merge(room_id, rev_id) if not whole else {"same": [], "conflicts": []}
    report = _report(out["changes"], names, len(an["conflicts"]), an.get("same") or [])
    body = (f"✅ 《{card['title']}》v{version} 合并版" + (" (迟到的修改)" if late else "") + f"\n{report}"
            + (f"\n{str(note).strip()[:300]}" if note and str(note).strip() else ""))
    sent = _status(room_id, card, "merged", body=body, files=files, version=version)
    rec["merged"] = {"version": version, "at": sent["message"]["at"]}
    rec.pop("whole_reason", None)
    _put(rec)
    return {"ok": True, "message": sent["message"], "version": version, "warnings": out["warnings"]}


def _docs():
    from . import colleague_rooms_revise_docs as d  # noqa: PLC0415
    return d


def pim_handlers(rid: str, params: dict[str, Any]) -> dict[str, Any]:
    rv = str(params.get("rev_id") or "")
    return {
        "pim/croom_revise_start": lambda: start(rid, params),
        "pim/croom_revise_open": lambda: open_copy(rid, rv),
        "pim/croom_revise_return": lambda: give_back(rid, rv, params.get("path"), params.get("note"), params.get("confirm_unchanged") is True,
                                                     params.get("stage_id"), params.get("name"), params.get("confirm_open") is True),
        "pim/croom_revise_pending": pending,
        "pim/croom_revise_detect": lambda: detect(rid, params.get("stage_id"), params.get("name")),
        "pim/croom_revise_decline": lambda: decline(rid, rv, params.get("note")),
        "pim/croom_revise_progress": lambda: progress(rid, rv),
        "pim/croom_revise_remind": lambda: remind(rid, rv),
        "pim/croom_revise_close": lambda: close(rid, rv),
        "pim/croom_revise_merge": lambda: merge(rid, rv),
        "pim/croom_revise_decide": lambda: decide(rid, rv, params.get("cid"), params.get("pick"), params.get("text")),
        "pim/croom_revise_publish": lambda: publish(rid, rv, params.get("note")),
        "pim/croom_revise_versions": lambda: _docs().versions(rid, str(params.get("doc_id") or "")),
        "pim/croom_revise_diff": lambda: _docs().diff(rid, str(params.get("doc_id") or ""), params.get("a"), params.get("b")),
    }


__all__ = ["classify", "start", "open_copy", "give_back", "decline", "progress", "pending", "merge", "decide", "publish",
           "pim_handlers"]
