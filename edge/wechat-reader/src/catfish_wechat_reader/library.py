"""微信导出 ZIP 的本机导入库 (9/23)。

合并转发一次只导一小段, 同一个群会越导越多, 所以数据源不能再是「一个文件」::

    <library>/
      groups.json        {"version": 1, "groups": {group_id: {name, members, self_name, ...}}}
      <sha256>.zip       员工导入的原包, 按内容命名 —— 同一个包导两次只存一份
      <sha256>.json      这个包属于哪个群; **写它是导入的提交点**

库里只有员工自己导出的原包和群名 / 昵称这类元数据, 不另建聊天明文索引
(沿用本 reader 一直以来的约定)。查询时每次从原包现解析。

# 群怎么认

TXT 里没有群名。拿新包的发送人跟每个已有群的成员比, 先把「我」(所有群里记过的
self_name) 去掉 —— 「我」出现在每个群里, 不去掉会让任意两个群都显得重合。

- 至少 2 人重合且 重合人数 / 较小一方人数 ≥ 0.6 → 同一个群
- 或者去掉「我」之后两边名单完全相同 (私聊就靠这条)
- 同时满足的群不止一个 → 不自动归, 交给员工选

# 去重

两个包的时间段会重叠。消息 ID = hash(群, 发送人, 分钟, 正文, 同一包里同样内容的第几条),
同一条消息在两个包里算出来一样, 合并时只留一条。
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from typing import Iterator

from .readers import ReaderFailure
from .wechat_zip import ParsedExport, read_export

GROUPS_FILE = "groups.json"
LIBRARY_VERSION = 1
MATCH_MIN_SHARED = 2
MATCH_MIN_RATIO = 0.6
MAX_NAME_CHARS = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: object) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _library_dir(library: str | Path, create: bool = False) -> Path:
    path = Path(library).expanduser()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ReaderFailure("导入库目录不存在", "source_missing")
    return path.resolve()


def load_groups(library: Path) -> dict[str, dict]:
    path = library / GROUPS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReaderFailure("导入库的 groups.json 损坏", "invalid_source") from exc
    if not isinstance(data, dict) or data.get("version") != LIBRARY_VERSION:
        raise ReaderFailure("导入库版本不认识", "invalid_source")
    groups = data.get("groups")
    return groups if isinstance(groups, dict) else {}


def _save_groups(library: Path, groups: dict[str, dict]) -> None:
    _write_json_atomic(library / GROUPS_FILE, {"version": LIBRARY_VERSION, "groups": groups})


def _sidecars(library: Path) -> Iterator[tuple[Path, dict]]:
    for path in sorted(library.glob("*.json")):
        if path.name == GROUPS_FILE:
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReaderFailure(f"导入库记录损坏: {path.name}", "invalid_source") from exc
        if isinstance(meta, dict) and meta.get("sha256") == path.stem:
            yield path, meta


def _known_selves(groups: dict[str, dict]) -> set[str]:
    return {g["self_name"] for g in groups.values() if g.get("self_name")}


def match_groups(groups: dict[str, dict], senders: set[str]) -> tuple[str | None, list[dict]]:
    """返回 (唯一命中的 group_id 或 None, 按重合度排序的候选)。"""
    selves = _known_selves(groups)
    others = senders - selves
    scored: list[dict] = []
    for group_id, group in groups.items():
        members = set(group.get("members") or []) - selves
        shared = others & members
        if not others or not members or not shared:
            continue
        ratio = len(shared) / min(len(others), len(members))
        confident = others == members or (
            len(shared) >= MATCH_MIN_SHARED and ratio >= MATCH_MIN_RATIO
        )
        scored.append({
            "group_id": group_id,
            "name": group.get("name", ""),
            "shared": len(shared),
            "ratio": round(ratio, 3),
            "confident": confident,
        })
    scored.sort(key=lambda item: (not item["confident"], -item["ratio"], -item["shared"]))
    confident = [item for item in scored if item["confident"]]
    return (confident[0]["group_id"] if len(confident) == 1 else None), scored[:5]


def suggest_name(parsed: ParsedExport, self_name: str | None) -> str:
    others = [name for name, _ in parsed.senders if name != self_name]
    total = len(parsed.senders)
    if not others:
        return "我的聊天记录"
    if len(others) == 1:
        return f"与{others[0]}的聊天"
    if total <= 3:
        return "、".join(others) + "的聊天"
    return f"{others[0]}、{others[1]}等 {total} 人"


def _clean_name(value: str) -> str:
    name = " ".join(str(value or "").split())[:MAX_NAME_CHARS]
    if not name:
        raise ReaderFailure("群名不能为空", "invalid_scope")
    return name


def _self_for(groups: dict[str, dict], parsed: ParsedExport, group_id: str | None) -> str | None:
    """已记过的「我」: 先看这个群自己的, 再看别的群里记过、这个包里也出现的昵称。"""
    senders = {name for name, _ in parsed.senders}
    if group_id and groups.get(group_id, {}).get("self_name") in senders:
        return groups[group_id]["self_name"]
    hits = sorted(_known_selves(groups) & senders)
    return hits[0] if len(hits) == 1 else None


def inspect(source: str | Path, library: str | Path | None) -> dict[str, object]:
    parsed = read_export(source)
    groups: dict[str, dict] = {}
    lib: Path | None = None
    if library and Path(library).expanduser().is_dir():
        lib = _library_dir(library)
        groups = load_groups(lib)
    senders = {name for name, _ in parsed.senders}
    matched, candidates = match_groups(groups, senders)
    already = None
    if lib and (lib / f"{parsed.sha256}.json").exists():
        meta = json.loads((lib / f"{parsed.sha256}.json").read_text(encoding="utf-8"))
        already = meta.get("group_id")
    known_self = _self_for(groups, parsed, already or matched)
    return {
        "format": "wechat_zip",
        "sha256": parsed.sha256,
        "message_count": len(parsed.messages),
        "start": parsed.start.isoformat(),
        "end": parsed.end.isoformat(),
        "senders": [{"name": name, "count": count} for name, count in parsed.senders],
        "attachments": parsed.attachment_summary(),
        "already_imported_group_id": already,
        "matched_group_id": already or matched,
        "candidates": candidates,
        "suggested_name": (
            groups.get(already or matched or "", {}).get("name")
            or suggest_name(parsed, known_self)
        ),
        "known_self_name": known_self,
    }


def import_export(
    source: str | Path,
    library: str | Path,
    group_id: str | None = None,
    group_name: str | None = None,
    self_name: str | None = None,
) -> dict[str, object]:
    """导入一个包。`self_name=""` 表示「我不在这些发送人里」, None 表示沿用已记的。"""
    lib = _library_dir(library, create=True)
    parsed = read_export(source)
    groups = load_groups(lib)
    sidecar = lib / f"{parsed.sha256}.json"
    if sidecar.exists():
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        return {"imported": False, "already_imported": True, "group_id": meta.get("group_id")}

    senders = {name for name, _ in parsed.senders}
    if group_id:
        if group_id not in groups:
            raise ReaderFailure("指定的群不存在", "invalid_scope")
    elif group_name:
        group_id = uuid.uuid4().hex[:12]
        groups[group_id] = {"name": _clean_name(group_name), "members": [], "self_name": None,
                            "created_at": _now()}
    else:
        group_id, _ = match_groups(groups, senders)
        if not group_id:
            raise ReaderFailure("认不出是哪个群, 需要指定群或新群名", "group_required")
    group = groups[group_id]
    if group_name and group.get("name") != group_name:
        group["name"] = _clean_name(group_name)
    if self_name is not None:
        if self_name and self_name not in senders:
            raise ReaderFailure("「我」必须是这份记录里的发送人之一", "invalid_scope")
        group["self_name"] = self_name or None
    elif not group.get("self_name"):
        group["self_name"] = _self_for(groups, parsed, group_id)
    group["members"] = sorted(set(group.get("members") or []) | senders)
    group["updated_at"] = _now()

    # 顺序: 原包 → groups.json → sidecar。sidecar 是提交点 —— 中途断掉最多留一个
    # 没人引用的 zip, 不会出现「记录说有、包却没有」。
    target = lib / f"{parsed.sha256}.zip"
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=lib)
    os.close(fd)
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _save_groups(lib, groups)
    _write_json_atomic(sidecar, {
        "version": LIBRARY_VERSION,
        "sha256": parsed.sha256,
        "size": parsed.size,
        "group_id": group_id,
        "imported_at": _now(),
        "original_name": Path(source).name[:MAX_NAME_CHARS],
        "message_count": len(parsed.messages),
        "start": parsed.start.isoformat(),
        "end": parsed.end.isoformat(),
    })
    return {"imported": True, "already_imported": False, "group_id": group_id,
            "name": group["name"], "self_name": group.get("self_name")}


def _message_id(group_id: str, sender: str, minute: datetime, text: str, occurrence: int) -> str:
    key = f"{group_id}\0{sender}\0{minute.isoformat()}\0{text}\0{occurrence}"
    return "wx-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def records_from_export(
    parsed: ParsedExport, session_id: str, session_name: str, self_name: str | None,
) -> Iterator[dict[str, object]]:
    seen: dict[tuple, int] = {}
    for message in parsed.messages:
        key = (message.sender, message.minute, message.text)
        seen[key] = seen.get(key, 0) + 1
        record: dict[str, object] = {
            "message_id": _message_id(session_id, *key, seen[key]),
            "session_id": session_id,
            "session_name": session_name,
            "sender_id": message.sender,
            "sender_name": message.sender,
            "timestamp": message.minute.isoformat(),
            "_timestamp": message.minute,
            "type": message.type,
            "text": message.text,
            "is_self": bool(self_name) and message.sender == self_name,
        }
        if message.attachment_name:
            record["attachment_name"] = message.attachment_name
            record["attachment_present"] = message.attachment_present
        yield record


def _group_exports(lib: Path, groups: dict[str, dict]) -> dict[str, list[Path]]:
    by_group: dict[str, list[Path]] = {}
    for path, meta in _sidecars(lib):
        group_id = meta.get("group_id")
        if group_id not in groups:
            continue
        archive = lib / f"{path.stem}.zip"
        if not archive.is_file():
            raise ReaderFailure(f"导入库缺少原包: {archive.name}", "source_missing")
        by_group.setdefault(group_id, []).append(archive)
    return by_group


def iter_library_records(library: str | Path) -> Iterator[dict[str, object]]:
    lib = _library_dir(library)
    groups = load_groups(lib)
    for group_id, archives in _group_exports(lib, groups).items():
        group = groups[group_id]
        merged: dict[str, dict[str, object]] = {}
        for archive in archives:
            parsed = read_export(archive, compute_hash=False)
            for record in records_from_export(
                parsed, group_id, group.get("name") or group_id, group.get("self_name"),
            ):
                merged.setdefault(str(record["message_id"]), record)
        yield from sorted(merged.values(), key=lambda item: item["_timestamp"])


def list_groups(library: str | Path) -> list[dict[str, object]]:
    lib = _library_dir(library)
    groups = load_groups(lib)
    exports = _group_exports(lib, groups)
    stats: dict[str, dict[str, object]] = {}
    for record in iter_library_records(lib):
        item = stats.setdefault(str(record["session_id"]), {"count": 0, "start": None, "end": None})
        item["count"] = int(item["count"]) + 1
        stamp = str(record["timestamp"])
        item["start"] = min(filter(None, [item["start"], stamp]))
        item["end"] = max(filter(None, [item["end"], stamp]))
    result = []
    for group_id, group in groups.items():
        item = stats.get(group_id, {})
        result.append({
            "group_id": group_id,
            "name": group.get("name", ""),
            "self_name": group.get("self_name"),
            "member_count": len(group.get("members") or []),
            "export_count": len(exports.get(group_id, [])),
            "message_count": item.get("count", 0),
            "start": item.get("start"),
            "end": item.get("end"),
        })
    result.sort(key=lambda item: str(item["end"] or ""), reverse=True)
    return result


def update_group(
    library: str | Path, group_id: str, name: str | None, self_name: str | None,
) -> dict[str, object]:
    lib = _library_dir(library)
    groups = load_groups(lib)
    group = groups.get(group_id)
    if group is None:
        raise ReaderFailure("指定的群不存在", "invalid_scope")
    if name is not None:
        group["name"] = _clean_name(name)
    if self_name is not None:
        if self_name and self_name not in (group.get("members") or []):
            raise ReaderFailure("「我」必须是这个群的发送人之一", "invalid_scope")
        group["self_name"] = self_name or None
    group["updated_at"] = _now()
    _save_groups(lib, groups)
    return {"group_id": group_id, "name": group["name"], "self_name": group.get("self_name")}


def remove_group(library: str | Path, group_id: str) -> dict[str, object]:
    """删掉一个群的全部导入: 先删 sidecar (撤销提交), 再删原包, 最后删群档案。"""
    lib = _library_dir(library)
    groups = load_groups(lib)
    if group_id not in groups:
        raise ReaderFailure("指定的群不存在", "invalid_scope")
    removed = 0
    for path, meta in list(_sidecars(lib)):
        if meta.get("group_id") != group_id:
            continue
        path.unlink(missing_ok=True)
        (lib / f"{path.stem}.zip").unlink(missing_ok=True)
        removed += 1
    del groups[group_id]
    _save_groups(lib, groups)
    return {"group_id": group_id, "removed_exports": removed}
