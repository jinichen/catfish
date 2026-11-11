"""群里一起改文档 (colleague_rooms_revise.py) 的版本记录和任意两版对比。从那个文件拆出来 (守 800 行)。"""
from __future__ import annotations

import json
from pathlib import Path, PurePath
from typing import Any

from . import colleague_rooms as cr
from . import colleague_rooms_files as rfiles
from . import doc_merge as dm
from . import forms_net
from .colleague_rooms_revise import _blob, _now, _safe, file_bytes
from .doc_merge import marker


def versions(room_id: str, doc_id: str) -> dict[str, Any]:
    """这份文档在群里的每一版 (发起的那份 + 每次合并版)。"""
    rid = cr.load(room_id)["room_id"]
    out, title = [], ""
    with cr.store.tx() as con:
        rows = [json.loads(r["data"]) for r in con.execute(
            "SELECT data FROM messages WHERE room_id = ? AND kind IN ('revise', 'revise_status') ORDER BY seq", (rid,))]
    room = cr.load(room_id)
    seen = set()
    for m in rows:
        c = m.get("revise")
        st = m.get("revise_status") or {}
        if c and c["doc_id"] == doc_id and c["version"] not in seen:
            seen.add(c["version"])
            title = c["title"]
            out.append({"version": c["version"], "at": m["at"], "by": cr._label(room, c["owner"]), "kind": "start",
                        "msg_id": m["msg_id"], "files": m.get("files") or []})
        elif st.get("doc_id") == doc_id and st.get("state") == "merged":
            out.append({"version": st.get("version"), "at": m["at"], "by": cr._label(room, m.get("from") or ""), "kind": "merged",
                        "msg_id": m["msg_id"], "files": m.get("files") or [], "text": m.get("text") or ""})
    out.sort(key=lambda v: (v["version"] or 0, v["at"]), reverse=True)
    return {"ok": True, "doc_id": doc_id, "title": title, "versions": out}


def _version_bytes(room_id: str, doc_id: str, v: int) -> bytes:
    data = _blob(doc_id, f"v{v}")
    if data is not None:
        return data
    ver = next((x for x in versions(room_id, doc_id)["versions"] if x["version"] == v), None)
    f = next((f for f in (ver or {}).get("files") or [] if "修订版" not in f["name"]), None)
    if not f:
        raise ValueError(f"v{v} 找不到了 (可能过了保留期)")
    return marker.strip(file_bytes(cr.load(room_id)["room_id"], f["id"]))


def diff(room_id: str, doc_id: str, a: Any, b: Any) -> dict[str, Any]:
    """任意两版对比: Word / Excel / PPT 出一份带修订痕迹 (批注 / 备注) 的文件, 文本出一份对比文本。写到下载目录, 返回路径。"""
    va, vb = int(a), int(b)
    vs = versions(room_id, doc_id)
    first = next((x for x in vs["versions"] if x["kind"] == "start"), None)
    name = (first or {}).get("files", [{}])[0].get("name", "") if first else ""
    fmt = dm.fmt_of(name)
    if not fmt:
        raise ValueError("这种格式没法对比, 两版分别打开看")
    old, new = _version_bytes(room_id, doc_id, va), _version_bytes(room_id, doc_id, vb)
    room = cr.load(room_id)
    key = f"v{vb}"
    ext = PurePath(name).suffix
    if fmt == "text":
        import difflib  # noqa: PLC0415

        from .doc_merge.text import decode  # noqa: PLC0415
        a_t, b_t = decode(old)[0].splitlines(), decode(new)[0].splitlines()
        data = "\n".join(difflib.unified_diff(a_t, b_t, f"v{va}", f"v{vb}", lineterm="")).encode("utf-8")
        ext = ".diff.txt"
    else:
        out = dm.render(fmt, old, [dm.Return(key, new, _now())], {}, dm.Ctx(forms_net.me(), {key: f"v{vb}"}, _now()))
        data = out["tracked"]
    folder = Path.home() / "Downloads" / "鲶鱼群文件" / rfiles._safe_dir(room.get("name") or room["room_id"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{_safe(vs['title'] or PurePath(name).stem)} v{va}→v{vb} 对比{ext}"
    target.write_bytes(data)
    return {"ok": True, "path": str(target)}


